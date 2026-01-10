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
    CONF_NOTIFY_ON_PAST_DUE,
    CONF_ASSIGNEE_NOTIFICATIONS,
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
        self._circle_members = []  # Store circle members for notification config
        self._api_client = None

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
                
                # Fetch circle members for notification config
                try:
                    self._circle_members = await client.async_get_circle_members()
                except Exception as e:
                    _LOGGER.warning("Could not fetch circle members: %s", e)
                    self._circle_members = []

                # Store credentials and proceed to options step
                self._server_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                self._server_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
                self._api_client = client
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
                CONF_NOTIFY_ON_PAST_DUE: user_input.get(CONF_NOTIFY_ON_PAST_DUE, False),
            }
            
            # If notifications enabled and we have circle members, proceed to notification config
            if user_input.get(CONF_NOTIFY_ON_PAST_DUE, False) and self._circle_members:
                self._server_data = final_data
                return await self.async_step_notifications()
            
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
                vol.Optional(CONF_NOTIFY_ON_PAST_DUE, default=False): bool,
            }),
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure notification services for each assignee."""
        if user_input is not None:
            # Build assignee notifications mapping
            assignee_notifications = {}
            for member in self._circle_members:
                if member.is_active:
                    key = f"notify_{member.user_id}"
                    notify_service = user_input.get(key)
                    if notify_service:
                        assignee_notifications[str(member.user_id)] = notify_service
            
            final_data = {
                **self._server_data,
                CONF_ASSIGNEE_NOTIFICATIONS: assignee_notifications,
            }
            
            return self.async_create_entry(
                title="Donetick",
                data=final_data,
            )

        # Build schema with a notify service selector for each active member
        schema_dict = {}
        
        # Get available notify services
        notify_services = []
        for service in self.hass.services.async_services().get("notify", {}):
            notify_services.append({"value": f"notify.{service}", "label": f"notify.{service}"})
        
        if not notify_services:
            notify_services = [{"value": "", "label": "No notify services found"}]
        
        for member in self._circle_members:
            if member.is_active:
                schema_dict[vol.Optional(f"notify_{member.user_id}")] = SelectSelector(
                    SelectSelectorConfig(
                        options=notify_services,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
        
        # Build description placeholders for member names
        member_descriptions = {
            f"member_{m.user_id}": m.display_name 
            for m in self._circle_members if m.is_active
        }

        return self.async_show_form(
            step_id="notifications",
            data_schema=vol.Schema(schema_dict),
            description_placeholders=member_descriptions,
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
        self._updated_data = {}
        self._circle_members = []

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
                CONF_NOTIFY_ON_PAST_DUE: user_input.get(CONF_NOTIFY_ON_PAST_DUE, False),
            }
            
            # Preserve auth credentials based on auth type
            auth_type = self.entry.data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
            if auth_type == AUTH_TYPE_JWT:
                data[CONF_USERNAME] = self.entry.data.get(CONF_USERNAME)
                data[CONF_PASSWORD] = self.entry.data.get(CONF_PASSWORD)
            else:
                data[CONF_TOKEN] = self.entry.data.get(CONF_TOKEN)

            # If notifications enabled, proceed to notification config
            if user_input.get(CONF_NOTIFY_ON_PAST_DUE, False):
                self._updated_data = data
                # Fetch circle members
                await self._fetch_circle_members()
                if self._circle_members:
                    return await self.async_step_notifications()
            
            # Preserve existing assignee notifications if not reconfiguring
            if not user_input.get(CONF_NOTIFY_ON_PAST_DUE, False):
                data[CONF_ASSIGNEE_NOTIFICATIONS] = {}
            else:
                data[CONF_ASSIGNEE_NOTIFICATIONS] = self.entry.data.get(CONF_ASSIGNEE_NOTIFICATIONS, {})

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
                vol.Optional(
                    CONF_NOTIFY_ON_PAST_DUE,
                    default=self.entry.data.get(CONF_NOTIFY_ON_PAST_DUE, False)
                ): bool,
            }),
            description_placeholders={
                "auth_type": auth_type_label,
                "url": self.entry.data.get(CONF_URL, ""),
            },
        )

    async def _fetch_circle_members(self) -> None:
        """Fetch circle members from the API."""
        try:
            session = async_get_clientsession(self.hass)
            auth_type = self.entry.data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
            
            if auth_type == AUTH_TYPE_JWT:
                client = DonetickApiClient(
                    self.entry.data[CONF_URL],
                    session,
                    username=self.entry.data.get(CONF_USERNAME),
                    password=self.entry.data.get(CONF_PASSWORD),
                    auth_type=AUTH_TYPE_JWT,
                )
            else:
                client = DonetickApiClient(
                    self.entry.data[CONF_URL],
                    session,
                    api_token=self.entry.data.get(CONF_TOKEN),
                    auth_type=AUTH_TYPE_API_KEY,
                )
            
            self._circle_members = await client.async_get_circle_members()
        except Exception as e:
            _LOGGER.warning("Could not fetch circle members: %s", e)
            self._circle_members = []

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure notification services for each assignee."""
        if user_input is not None:
            # Build assignee notifications mapping
            assignee_notifications = {}
            for member in self._circle_members:
                if member.is_active:
                    key = f"notify_{member.user_id}"
                    notify_service = user_input.get(key)
                    if notify_service:
                        assignee_notifications[str(member.user_id)] = notify_service
            
            data = {
                **self._updated_data,
                CONF_ASSIGNEE_NOTIFICATIONS: assignee_notifications,
            }
            
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.entry, data=data, options=self.entry.options
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.entry.entry_id)
            )
            self.async_abort(reason="configuration updated")
            return self.async_create_entry(title="", data={})

        # Build schema with a notify service selector for each active member
        schema_dict = {}
        
        # Get available notify services
        notify_services = [{"value": "", "label": "(None)"}]
        for service in self.hass.services.async_services().get("notify", {}):
            notify_services.append({"value": f"notify.{service}", "label": f"notify.{service}"})
        
        # Get existing mappings
        existing_mappings = self.entry.data.get(CONF_ASSIGNEE_NOTIFICATIONS, {})
        
        for member in self._circle_members:
            if member.is_active:
                default_value = existing_mappings.get(str(member.user_id), "")
                schema_dict[vol.Optional(f"notify_{member.user_id}", default=default_value)] = SelectSelector(
                    SelectSelectorConfig(
                        options=notify_services,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )

        return self.async_show_form(
            step_id="notifications",
            data_schema=vol.Schema(schema_dict),
        )
