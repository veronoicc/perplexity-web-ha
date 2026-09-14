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
from homeassistant.helpers import template
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import ulid

from .api import PerplexityApiError, PerplexityClient
from .const import (
    CONF_MODEL_PREFERENCE,
    CONF_PROMPT,
    CONF_SEARCH_FOCUS,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
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
    added = False

    subentries = getattr(entry, "subentries", None)
    if subentries:
        for subentry in subentries.values():
            if getattr(subentry, "subentry_type", None) == "conversation":
                entity = PerplexityWebConversationEntity(
                    entry, client, subentry=subentry
                )
                try:
                    async_add_entities(
                        [entity], config_subentry_id=subentry.subentry_id
                    )
                except TypeError:
                    async_add_entities([entity])
                added = True

    if not added:
        async_add_entities(
            [PerplexityWebConversationEntity(entry, client, subentry=None)]
        )


class PerplexityWebConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """Perplexity Web conversation agent entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        client: PerplexityClient,
        subentry: Any = None,
    ) -> None:
        """Initialize the conversation entity."""
        self.entry = entry
        self.client = client
        self.subentry = subentry
        self.history: dict[str, list[dict[str, str]]] = {}

        if subentry:
            self._attr_unique_id = f"{entry.entry_id}_{subentry.subentry_id}"
            self._attr_name = subentry.title
            self._attr_device_info = dr.DeviceInfo(
                identifiers={(DOMAIN, subentry.subentry_id)},
                name=subentry.title,
                manufacturer="Perplexity AI",
                model="Perplexity Web",
                entry_type=dr.DeviceEntryType.SERVICE,
            )
        else:
            self._attr_unique_id = entry.entry_id
            self._attr_name = entry.title
            self._attr_device_info = dr.DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.title,
                manufacturer="Perplexity AI",
                model="Perplexity Web",
                entry_type=dr.DeviceEntryType.SERVICE,
            )

    @property
    def _options(self) -> dict[str, Any]:
        """Return options dictionary for this agent."""
        if self.subentry and getattr(self.subentry, "data", None):
            return dict(self.subentry.data)
        return dict(self.entry.options)

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

    async def _async_render_prompt(
        self, user_input: conversation.ConversationInput
    ) -> str:
        """Render prompt template with Home Assistant context."""
        raw_prompt = self._options.get(CONF_PROMPT, DEFAULT_PROMPT)
        if not raw_prompt:
            return ""

        user_name: str | None = None
        if user_input.context and user_input.context.user_id:
            user = await self.hass.auth.async_get_user(user_input.context.user_id)
            if user:
                user_name = user.name

        try:
            return template.Template(raw_prompt, self.hass).async_render(
                {
                    "ha_name": self.hass.config.location_name,
                    "user_name": user_name,
                },
                parse_result=False,
            )
        except template.TemplateError as err:
            _LOGGER.error("Error rendering prompt template: %s", err)
            return raw_prompt

    def _format_history_query(
        self,
        messages: list[dict[str, str]],
        latest_prompt: str,
        system_prompt: str | None = None,
    ) -> str:
        """Format multi-turn conversation history into a single query string."""
        formatted_lines: list[str] = []

        if system_prompt and system_prompt.strip():
            formatted_lines.append(f"System: {system_prompt.strip()}")

        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "").strip()
            if content:
                formatted_lines.append(f"{role}: {content}")

        if formatted_lines:
            formatted_lines.append(f"User: {latest_prompt}")
            return "\n\n".join(formatted_lines)

        return latest_prompt

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

        model_preference = self._options.get(CONF_MODEL_PREFERENCE, DEFAULT_MODEL)
        search_focus = self._options.get(CONF_SEARCH_FOCUS, DEFAULT_SEARCH_FOCUS)
        params = {
            "model_preference": model_preference,
            "search_focus": search_focus,
            "mode": "concise",
            "is_incognito": True,
        }

        system_prompt = await self._async_render_prompt(user_input)
        query_str = self._format_history_query(
            messages, user_input.text, system_prompt=system_prompt
        )

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
