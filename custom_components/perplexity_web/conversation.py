"""Conversation entity for Perplexity Web."""

from __future__ import annotations

import logging
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import ulid

from .api import PerplexityApiError, PerplexityClient
from .const import (
    CONF_MODEL_PREFERENCE,
    CONF_SEARCH_FOCUS,
    DEFAULT_MODEL,
    DEFAULT_SEARCH_FOCUS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Perplexity conversation entities."""
    client: PerplexityClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PerplexityWebConversationEntity(entry, client)])


class PerplexityWebConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """Perplexity Web conversation agent entity."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry, client: PerplexityClient) -> None:
        """Initialize the conversation entity."""
        self.entry = entry
        self.client = client
        self.history: dict[str, list[dict[str, str]]] = {}
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Perplexity AI",
            model="Perplexity Web",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register entity when added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister entity before removal."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    def _format_history_query(
        self, messages: list[dict[str, str]], latest_prompt: str
    ) -> str:
        """Format multi-turn conversation history into a single query string."""
        if not messages:
            return latest_prompt

        formatted_lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "").strip()
            if content:
                formatted_lines.append(f"{role}: {content}")

        formatted_lines.append(f"User: {latest_prompt}")
        return "\n\n".join(formatted_lines)

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: Any = None,
    ) -> conversation.ConversationResult:
        """Process incoming chat message and return conversation result."""
        if user_input.conversation_id is None:
            conversation_id = ulid.ulid_now()
            messages: list[dict[str, str]] = []
        elif user_input.conversation_id in self.history:
            conversation_id = user_input.conversation_id
            messages = self.history[conversation_id]
        else:
            try:
                ulid.ulid_to_bytes(user_input.conversation_id)
                conversation_id = ulid.ulid_now()
            except ValueError:
                conversation_id = user_input.conversation_id
            messages = []

        if chat_log is not None and hasattr(chat_log, "content") and chat_log.content:
            messages = []
            for item in chat_log.content:
                role = getattr(item, "role", None) or "user"
                content = getattr(item, "content", None) or str(item)
                messages.append({"role": str(role), "content": str(content)})

        model_preference = self.entry.options.get(CONF_MODEL_PREFERENCE, DEFAULT_MODEL)
        search_focus = self.entry.options.get(CONF_SEARCH_FOCUS, DEFAULT_SEARCH_FOCUS)
        params = {
            "model_preference": model_preference,
            "search_focus": search_focus,
            "mode": "concise",
            "is_incognito": True,
        }

        query_str = self._format_history_query(messages, user_input.text)

        intent_response = intent.IntentResponse(language=user_input.language)

        try:
            result = await self.client.ask_complete(query_str, params)
            response_text = result.text.strip()
            if not response_text:
                response_text = "I received an empty response from Perplexity."
        except PerplexityApiError as err:
            _LOGGER.error("Perplexity API error: %s", err)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Perplexity API error: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response,
                conversation_id=conversation_id,
            )
        except Exception as err:
            _LOGGER.exception("Unexpected error querying Perplexity: %s", err)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with Perplexity: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response,
                conversation_id=conversation_id,
            )

        messages.append({"role": "user", "content": user_input.text})
        messages.append({"role": "assistant", "content": response_text})
        self.history[conversation_id] = messages

        if chat_log is not None:
            if hasattr(chat_log, "async_add_assistant_content_without_tools"):
                try:
                    from homeassistant.components.conversation import AssistantContent

                    chat_log.async_add_assistant_content_without_tools(
                        AssistantContent(
                            agent_id=user_input.agent_id,
                            content=response_text,
                        )
                    )
                except Exception:
                    pass

        intent_response.async_set_speech(response_text)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
        )

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process a conversation utterance."""
        return await self._async_handle_message(user_input, chat_log=None)
