"""Config flow for Perplexity Web integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
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
        self._name: str | None = None
        self._email: str | None = None
        self._csrf_token: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Request email and dispatch OTP, or reuse existing account."""
        errors: dict[str, str] = {}

        accounts: dict[str, str] = {
            entry.data[CONF_EMAIL]: entry.data[CONF_SESSION_TOKEN]
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_SESSION_TOKEN) and entry.data.get(CONF_EMAIL)
        }

        if user_input is not None:
            name = user_input.get(CONF_NAME)
            self._name = name
            existing_account = user_input.get("existing_account")

            if existing_account:
                if existing_account in accounts:
                    return self._create_entry_helper(
                        title=name or f"Perplexity ({existing_account})",
                        email=existing_account,
                        session_token=accounts[existing_account],
                    )
                errors["base"] = "invalid_auth"
            else:
                email = (user_input.get(CONF_EMAIL) or "").strip()
                if not email:
                    errors["base"] = "account_or_email_required"
                else:
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

        if accounts:
            account_options = {
                "": "None (add new account)",
                **{acc: acc for acc in accounts},
            }
            data_schema = vol.Schema(
                {
                    vol.Optional(CONF_NAME, default="Perplexity Web"): str,
                    vol.Optional("existing_account"): vol.In(account_options),
                    vol.Optional(CONF_EMAIL): str,
                }
            )
        else:
            data_schema = vol.Schema(
                {
                    vol.Optional(CONF_NAME, default="Perplexity Web"): str,
                    vol.Required(CONF_EMAIL): str,
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Verify OTP code and finish setup."""
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

                return self._create_entry_helper(
                    title=self._name or f"Perplexity ({self._email})",
                    email=self._email,
                    session_token=session_token,
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

    def _create_entry_helper(
        self, title: str, email: str, session_token: str
    ) -> ConfigFlowResult:
        """Helper to create entry with subentries if supported."""
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
            title=title,
            data={
                CONF_EMAIL: email,
                CONF_SESSION_TOKEN: session_token,
            },
            **extra_kwargs,
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return PerplexityOptionsFlow()


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

        return self.async_show_form(
            step_id="set_options",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async_step_user = async_step_set_options
    async_step_reconfigure = async_step_set_options


class PerplexityOptionsFlow(OptionsFlow):
    """Handle options for Perplexity Web."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Perplexity options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        session = async_get_clientsession(self.hass)
        token = self.config_entry.data.get(CONF_SESSION_TOKEN, "")
        client = PerplexityClient(session, token)

        current_prompt = self.config_entry.options.get(CONF_PROMPT, DEFAULT_PROMPT)
        current_reasoning = self.config_entry.options.get(
            CONF_REASONING, DEFAULT_REASONING
        )
        current_model = self.config_entry.options.get(
            CONF_MODEL_PREFERENCE, DEFAULT_MODEL
        )
        current_focus = self.config_entry.options.get(
            CONF_SEARCH_FOCUS, DEFAULT_SEARCH_FOCUS
        )

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

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PROMPT,
                    default=current_prompt,
                ): TemplateSelector(),
                vol.Optional(
                    CONF_MODEL_PREFERENCE,
                    default=current_model,
                ): vol.In(model_options),
                vol.Optional(
                    CONF_SEARCH_FOCUS,
                    default=current_focus,
                ): vol.In(SEARCH_FOCUS_OPTIONS),
                vol.Optional(
                    CONF_REASONING,
                    default=current_reasoning,
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
