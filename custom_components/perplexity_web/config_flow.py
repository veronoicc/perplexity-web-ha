"""Config flow for Perplexity Web integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TemplateSelector

from .api import AuthClient, PerplexityAuthError, PerplexityClient
from .const import (
    CONF_MODEL_PREFERENCE,
    CONF_PROMPT,
    CONF_REASONING,
    CONF_SEARCH_FOCUS,
    CONF_SESSION_TOKEN,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    DEFAULT_REASONING,
    DEFAULT_SEARCH_FOCUS,
    DOMAIN,
    SEARCH_FOCUS_OPTIONS,
)

try:
    from homeassistant.config_entries import (
        ConfigSubentryFlow,
        SubentryFlowResult,
    )

    HAS_SUBENTRIES = True
except ImportError:
    HAS_SUBENTRIES = False

    class ConfigSubentryFlow:  # type: ignore[no-redef]
        """Fallback when ConfigSubentryFlow is not available."""

    SubentryFlowResult = Any  # type: ignore[assignment,misc]

_LOGGER = logging.getLogger(__name__)

DEFAULT_OPTIONS = {
    CONF_PROMPT: DEFAULT_PROMPT,
    CONF_MODEL_PREFERENCE: DEFAULT_MODEL,
    CONF_SEARCH_FOCUS: DEFAULT_SEARCH_FOCUS,
    CONF_REASONING: DEFAULT_REASONING,
}


class PerplexityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Perplexity Web."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._email: str | None = None
        self._csrf_token: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Request email and dispatch OTP to add a Perplexity account."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()

            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            auth_client = AuthClient(session)

            try:
                csrf_token = await auth_client.get_csrf_token()
                await auth_client.send_email_otp(email, csrf_token)
                self._email = email
                self._csrf_token = csrf_token
                return await self.async_step_otp()
            except PerplexityAuthError as err:
                _LOGGER.error("Perplexity auth error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during auth: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Verify OTP code and finish account setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            otp = user_input["otp"].strip()
            session = async_get_clientsession(self.hass)
            auth_client = AuthClient(session)

            try:
                assert self._email is not None
                assert self._csrf_token is not None
                session_token = await auth_client.verify_otp(
                    self._email, otp, self._csrf_token
                )

                extra_kwargs: dict[str, Any] = {}
                if HAS_SUBENTRIES:
                    extra_kwargs["subentries"] = [
                        {
                            "subentry_type": "conversation",
                            "data": DEFAULT_OPTIONS.copy(),
                            "title": "Perplexity Web",
                            "unique_id": None,
                        }
                    ]
                else:
                    extra_kwargs["options"] = DEFAULT_OPTIONS.copy()

                return self.async_create_entry(
                    title=f"Perplexity ({self._email})",
                    data={
                        CONF_EMAIL: self._email,
                        CONF_SESSION_TOKEN: session_token,
                    },
                    **extra_kwargs,
                )
            except PerplexityAuthError as err:
                _LOGGER.error("OTP verification failed: %s", err)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("Unexpected error verifying OTP: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        if not HAS_SUBENTRIES:
            return {}
        return {
            "conversation": PerplexitySubentryFlowHandler,
        }


class PerplexitySubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing conversation subentries."""

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return getattr(self, "source", None) == "user"

    async def async_step_set_options(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Set conversation agent options."""
        errors: dict[str, str] = {}

        if user_input is None:
            if self._is_new:
                options = DEFAULT_OPTIONS.copy()
            else:
                reconfigure_subentry = getattr(self, "_get_reconfigure_subentry", None)
                if reconfigure_subentry:
                    options = (reconfigure_subentry().data or {}).copy()
                else:
                    options = DEFAULT_OPTIONS.copy()
        else:
            if self._is_new:
                chosen_account = user_input.pop("account", None)
                if chosen_account:
                    self.handler = chosen_account
                title = user_input.pop(CONF_NAME, "Perplexity Web")
                return self.async_create_entry(
                    title=title,
                    data=user_input,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        entry = self._get_entry()
        session = async_get_clientsession(self.hass)
        token = entry.data.get(CONF_SESSION_TOKEN, "")
        client = PerplexityClient(session, token)

        current_prompt = options.get(CONF_PROMPT, DEFAULT_PROMPT)
        current_reasoning = options.get(CONF_REASONING, DEFAULT_REASONING)
        current_model = options.get(CONF_MODEL_PREFERENCE, DEFAULT_MODEL)
        current_focus = options.get(CONF_SEARCH_FOCUS, DEFAULT_SEARCH_FOCUS)

        model_options: dict[str, str] = {}
        try:
            available_models = await client.get_available_models()
            for item in available_models:
                model_id = item.best_model(current_reasoning) or item.label
                desc = f" ({item.description})" if item.description else ""
                model_options[model_id] = f"{item.label}{desc}"
        except Exception as err:
            _LOGGER.warning("Could not fetch available models: %s", err)

        if not model_options:
            model_options[DEFAULT_MODEL] = "Default (Experimental)"

        if current_model not in model_options:
            model_options[current_model] = current_model

        schema: dict[Any, Any] = {}

        # If multiple accounts exist, allow picking the account
        accounts = {
            e.entry_id: e.data.get(CONF_EMAIL, e.title)
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.data.get(CONF_SESSION_TOKEN)
        }
        if self._is_new and len(accounts) > 1:
            schema[vol.Required("account", default=entry.entry_id)] = vol.In(accounts)

        if self._is_new:
            schema[vol.Required(CONF_NAME, default="Perplexity Web")] = str

        schema[vol.Optional(CONF_PROMPT, default=current_prompt)] = TemplateSelector()
        schema[vol.Optional(CONF_MODEL_PREFERENCE, default=current_model)] = vol.In(
            model_options
        )
        schema[vol.Optional(CONF_SEARCH_FOCUS, default=current_focus)] = vol.In(
            SEARCH_FOCUS_OPTIONS
        )
        schema[vol.Optional(CONF_REASONING, default=current_reasoning)] = bool

        current_email = entry.data.get(CONF_EMAIL, "")
        return self.async_show_form(
            step_id="set_options",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"account": current_email},
        )

    async_step_user = async_step_set_options
    async_step_reconfigure = async_step_set_options
