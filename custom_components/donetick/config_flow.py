"""Config flow for Donetick integration."""
from typing import Any
import logging
import voluptuous as vol
import aiohttp
from datetime import timedelta

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv

from homeassistant.helpers.selector import (
    DurationSelector,
    DurationSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    DOMAIN,
    CONF_URL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_AUTH_TYPE,
    AUTH_TYPE_JWT,
    AUTH_TYPE_API_KEY,
    CONF_SHOW_DUE_IN,
    CONF_CREATE_UNIFIED_LIST,
    CONF_CREATE_ASSIGNEE_LISTS,
    CONF_CREATE_DATE_FILTERED_LISTS,
    CONF_REFRESH_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
)
from .api import DonetickApiClient, AuthenticationError

_LOGGER = logging.getLogger(__name__)


def _seconds_to_time_config(total_seconds: int):
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return {
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
    }


def _config_to_seconds(config: dict[str, int]):
    return timedelta(
        hours=config["hours"],
        minutes=config["minutes"],
        seconds=config["seconds"],
    ).total_seconds()


class DonetickConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Donetick."""

    VERSION = 2  # Bump version for auth type migration
    
    def __init__(self):
        """Initialize the config flow."""
        self._server_data = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - choose auth type."""
        errors = {}

        if user_input is not None:
            auth_type = user_input.get(CONF_AUTH_TYPE, AUTH_TYPE_JWT)
            self._server_data = {
                CONF_URL: user_input[CONF_URL],
                CONF_AUTH_TYPE: auth_type,
            }
            
            if auth_type == AUTH_TYPE_JWT:
                return await self.async_step_jwt_auth()
            else:
                return await self.async_step_api_key_auth()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_URL): str,
                vol.Required(CONF_AUTH_TYPE, default=AUTH_TYPE_JWT): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": AUTH_TYPE_JWT, "label": "Username & Password (Full Features)"},
                            {"value": AUTH_TYPE_API_KEY, "label": "API Key (Limited Features)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_jwt_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle JWT authentication with username/password."""
        errors = {}

        if user_input is not None:
            try:
                session = async_get_clientsession(self.hass)
                client = DonetickApiClient(
                    self._server_data[CONF_URL],
                    session,
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    auth_type=AUTH_TYPE_JWT,
                )
                # Test the API connection
                await client.async_get_tasks()

                # Store credentials and proceed to options step
                self._server_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                self._server_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
                return await self.async_step_options()
                
            except AuthenticationError as err:
                _LOGGER.error("Authentication failed: %s", err)
                if "MFA" in str(err):
                    errors["base"] = "mfa_not_supported"
                else:
                    errors["base"] = "invalid_auth"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception as ex:
                _LOGGER.exception("Unexpected exception: %s", ex)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="jwt_auth",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={
                "url": self._server_data.get(CONF_URL, ""),
            },
        )

    async def async_step_api_key_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle API key authentication."""
        errors = {}

        if user_input is not None:
            try:
                session = async_get_clientsession(self.hass)
                client = DonetickApiClient(
                    self._server_data[CONF_URL],
                    session,
                    api_token=user_input[CONF_TOKEN],
                    auth_type=AUTH_TYPE_API_KEY,
                )
                # Test the API connection
                await client.async_get_tasks()

                # Store API key and proceed to options step
                self._server_data[CONF_TOKEN] = user_input[CONF_TOKEN]
                return await self.async_step_options()
                
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception as ex:
                _LOGGER.exception("Unexpected exception: %s", ex)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="api_key_auth",
            data_schema=vol.Schema({
                vol.Required(CONF_TOKEN): str,
            }),
            errors=errors,
            description_placeholders={
                "url": self._server_data.get(CONF_URL, ""),
            },
        )

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the options step."""
        if user_input is not None:
            refresh_interval = DEFAULT_REFRESH_INTERVAL
            if (refresh_interval_input := user_input.get(CONF_REFRESH_INTERVAL)) is not None:
                refresh_interval = _config_to_seconds(refresh_interval_input)
            
            final_data = {
                **self._server_data,
                CONF_SHOW_DUE_IN: user_input.get(CONF_SHOW_DUE_IN, 7),
                CONF_CREATE_UNIFIED_LIST: user_input.get(CONF_CREATE_UNIFIED_LIST, True),
                CONF_CREATE_ASSIGNEE_LISTS: user_input.get(CONF_CREATE_ASSIGNEE_LISTS, False),
                CONF_CREATE_DATE_FILTERED_LISTS: user_input.get(CONF_CREATE_DATE_FILTERED_LISTS, False),
                CONF_REFRESH_INTERVAL: refresh_interval,
            }
            
            return self.async_create_entry(
                title="Donetick",
                data=final_data,
            )

        return self.async_show_form(
            step_id="options",
            data_schema=vol.Schema({
                vol.Optional(CONF_SHOW_DUE_IN, default=7): vol.Coerce(int),
                vol.Optional(CONF_CREATE_UNIFIED_LIST, default=True): bool,
                vol.Optional(CONF_CREATE_ASSIGNEE_LISTS, default=False): bool,
                vol.Optional(CONF_CREATE_DATE_FILTERED_LISTS, default=False): bool,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=_seconds_to_time_config(DEFAULT_REFRESH_INTERVAL)
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False, allow_negative=False)
                ),
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return DonetickOptionsFlowHandler(config_entry)


class DonetickOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Donetick options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            refresh_interval = DEFAULT_REFRESH_INTERVAL
            if (refresh_interval_input := user_input.get(CONF_REFRESH_INTERVAL)) is not None:
                refresh_interval = _config_to_seconds(refresh_interval_input)
            
            # Preserve auth credentials from original entry
            data = {
                CONF_URL: self.entry.data.get(CONF_URL),
                CONF_AUTH_TYPE: self.entry.data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY),
                CONF_SHOW_DUE_IN: user_input.get(CONF_SHOW_DUE_IN, 7),
                CONF_CREATE_UNIFIED_LIST: user_input.get(CONF_CREATE_UNIFIED_LIST, True),
                CONF_CREATE_ASSIGNEE_LISTS: user_input.get(CONF_CREATE_ASSIGNEE_LISTS, False),
                CONF_CREATE_DATE_FILTERED_LISTS: user_input.get(CONF_CREATE_DATE_FILTERED_LISTS, False),
                CONF_REFRESH_INTERVAL: refresh_interval,
            }
            
            # Preserve auth credentials based on auth type
            auth_type = self.entry.data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
            if auth_type == AUTH_TYPE_JWT:
                data[CONF_USERNAME] = self.entry.data.get(CONF_USERNAME)
                data[CONF_PASSWORD] = self.entry.data.get(CONF_PASSWORD)
            else:
                data[CONF_TOKEN] = self.entry.data.get(CONF_TOKEN)

            # Workaround to update config entry data from options flow
            self.hass.config_entries.async_update_entry(
                self.entry, data=data, options=self.entry.options
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.entry.entry_id)
            )
            self.async_abort(reason="configuration updated")
            return self.async_create_entry(title="", data={})

        # Determine current auth type for display
        auth_type = self.entry.data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
        auth_type_label = "Username/Password" if auth_type == AUTH_TYPE_JWT else "API Key"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SHOW_DUE_IN,
                    default=self.entry.data.get(CONF_SHOW_DUE_IN, 7)
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_CREATE_UNIFIED_LIST,
                    default=self.entry.data.get(CONF_CREATE_UNIFIED_LIST, True)
                ): bool,
                vol.Optional(
                    CONF_CREATE_ASSIGNEE_LISTS,
                    default=self.entry.data.get(CONF_CREATE_ASSIGNEE_LISTS, False)
                ): bool,
                vol.Optional(
                    CONF_CREATE_DATE_FILTERED_LISTS,
                    default=self.entry.data.get(CONF_CREATE_DATE_FILTERED_LISTS, False)
                ): bool,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=_seconds_to_time_config(
                        self.entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
                    )
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False, allow_negative=False)
                ),
            }),
            description_placeholders={
                "auth_type": auth_type_label,
                "url": self.entry.data.get(CONF_URL, ""),
            },
        )
