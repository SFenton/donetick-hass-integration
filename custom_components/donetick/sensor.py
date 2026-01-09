"""Donetick sensor platform."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_AUTH_TYPE,
    AUTH_TYPE_JWT,
    AUTH_TYPE_API_KEY,
)
from .api import DonetickApiClient
from .thing import async_setup_entry as thing_async_setup_entry

_LOGGER = logging.getLogger(__name__)


class DonetickWebhookUrlSensor(SensorEntity):
    """Sensor that displays the webhook URL for Donetick."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:webhook"

    def __init__(self, config_entry: ConfigEntry, webhook_url: str) -> None:
        """Initialize the webhook URL sensor."""
        self._config_entry = config_entry
        self._webhook_url = webhook_url
        self._attr_unique_id = f"dt_{config_entry.entry_id}_webhook_url"
        self._attr_name = "Webhook URL"

    @property
    def native_value(self) -> str:
        """Return the webhook URL."""
        return self._webhook_url

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        return {
            "config_entry_id": self._config_entry.entry_id,
            "instructions": "Copy this URL and set it as your Donetick webhook URL in the Donetick app settings.",
        }


class DonetickCircleMembersSensor(SensorEntity):
    """Sensor that exposes circle members for easy reference in automations."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-group"

    def __init__(self, config_entry: ConfigEntry, hass: HomeAssistant) -> None:
        """Initialize the circle members sensor."""
        self._config_entry = config_entry
        self._hass = hass
        self._attr_unique_id = f"dt_{config_entry.entry_id}_circle_members"
        self._attr_name = "Circle Members"
        self._members: list[dict[str, Any]] = []
        self._member_count = 0

    @property
    def native_value(self) -> int:
        """Return the number of circle members."""
        return self._member_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return circle members as attributes for easy template access."""
        # Build a lookup dict for easy access by name
        by_name = {m["display_name"]: m["user_id"] for m in self._members}
        by_username = {m["username"]: m["user_id"] for m in self._members if m.get("username")}
        
        return {
            "members": self._members,
            "member_ids_by_name": by_name,
            "member_ids_by_username": by_username,
            "config_entry_id": self._config_entry.entry_id,
            "usage_hint": "Use member_ids_by_name['John Doe'] to get user ID for assignees",
        }

    async def async_update(self) -> None:
        """Fetch circle members from API."""
        entry_data = self._hass.data[DOMAIN].get(self._config_entry.entry_id, {})
        
        session = async_get_clientsession(self._hass)
        auth_type = entry_data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
        
        if auth_type == AUTH_TYPE_JWT:
            client = DonetickApiClient(
                entry_data[CONF_URL],
                session,
                username=entry_data.get(CONF_USERNAME),
                password=entry_data.get(CONF_PASSWORD),
                auth_type=AUTH_TYPE_JWT,
            )
        else:
            client = DonetickApiClient(
                entry_data[CONF_URL],
                session,
                api_token=entry_data.get(CONF_TOKEN),
                auth_type=AUTH_TYPE_API_KEY,
            )
        
        try:
            members = await client.async_get_circle_members()
            self._members = [
                {
                    "user_id": m.user_id,
                    "display_name": m.display_name,
                    "username": m.username,
                    "is_active": m.is_active,
                    "role": m.role,
                }
                for m in members
                if m.is_active  # Only show active members
            ]
            self._member_count = len(self._members)
        except Exception as e:
            _LOGGER.error("Failed to fetch circle members: %s", e)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Donetick sensor entities."""
    entities = []
    
    # Add webhook URL sensor
    entry_data = hass.data[DOMAIN].get(config_entry.entry_id, {})
    webhook_url = entry_data.get("webhook_url")
    if webhook_url:
        entities.append(DonetickWebhookUrlSensor(config_entry, webhook_url))
    
    # Add circle members sensor
    entities.append(DonetickCircleMembersSensor(config_entry, hass))
    
    if entities:
        async_add_entities(entities, update_before_add=True)
    
    # Set up thing sensors
    await thing_async_setup_entry(hass, config_entry, async_add_entities, "sensor")