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
                    return self.async_create_entry(
                        title=name or f"Perplexity ({existing_account})",
                        data={
                            CONF_EMAIL: existing_account,
                            CONF_SESSION_TOKEN: accounts[existing_account],
                        },
                        options=DEFAULT_OPTIONS,
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

                return self.async_create_entry(
                    title=self._name or f"Perplexity ({self._email})",
                    data={
                        CONF_EMAIL: self._email,
                        CONF_SESSION_TOKEN: session_token,
                    },
                    options=DEFAULT_OPTIONS,
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return PerplexityOptionsFlow()


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
