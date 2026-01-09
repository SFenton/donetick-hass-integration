"""Webhook handler for Donetick integration."""
import logging
from typing import Any

from aiohttp import web
from homeassistant.components.webhook import (
    async_generate_id,
    async_register,
    async_unregister,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

from .const import (
    DOMAIN,
    WEBHOOK_EVENT_TASK_COMPLETED,
    WEBHOOK_EVENT_TASK_SKIPPED,
    WEBHOOK_EVENT_TASK_REMINDER,
    WEBHOOK_EVENT_SUBTASK_COMPLETED,
    WEBHOOK_EVENT_THING_CHANGED,
    EVENT_DONETICK_TASK_COMPLETED,
    EVENT_DONETICK_TASK_SKIPPED,
    EVENT_DONETICK_TASK_REMINDER,
    EVENT_DONETICK_SUBTASK_COMPLETED,
    EVENT_DONETICK_THING_CHANGED,
)

_LOGGER = logging.getLogger(__name__)

# Mapping from Donetick event types to Home Assistant event types
EVENT_TYPE_MAP = {
    WEBHOOK_EVENT_TASK_COMPLETED: EVENT_DONETICK_TASK_COMPLETED,
    WEBHOOK_EVENT_TASK_SKIPPED: EVENT_DONETICK_TASK_SKIPPED,
    WEBHOOK_EVENT_TASK_REMINDER: EVENT_DONETICK_TASK_REMINDER,
    WEBHOOK_EVENT_SUBTASK_COMPLETED: EVENT_DONETICK_SUBTASK_COMPLETED,
    WEBHOOK_EVENT_THING_CHANGED: EVENT_DONETICK_THING_CHANGED,
}

# Events that should trigger a coordinator refresh
REFRESH_TRIGGER_EVENTS = {
    WEBHOOK_EVENT_TASK_COMPLETED,
    WEBHOOK_EVENT_TASK_SKIPPED,
    WEBHOOK_EVENT_SUBTASK_COMPLETED,
}


def generate_webhook_id() -> str:
    """Generate a unique webhook ID."""
    return async_generate_id()


def get_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Get the full webhook URL for the given webhook ID."""
    try:
        base_url = get_url(hass, allow_internal=True, prefer_external=True)
    except Exception:
        # Fallback if external URL is not configured
        try:
            base_url = get_url(hass, allow_internal=True, prefer_external=False)
        except Exception:
            base_url = "http://your-home-assistant:8123"
    
    return f"{base_url}/api/webhook/{webhook_id}"


async def async_register_webhook(
    hass: HomeAssistant,
    webhook_id: str,
    entry_id: str,
) -> None:
    """Register the webhook for receiving Donetick events."""
    async_register(
        hass,
        DOMAIN,
        f"Donetick Webhook ({entry_id[:8]})",
        webhook_id,
        handle_webhook,
        allowed_methods=["POST"],
    )
    _LOGGER.info("Registered Donetick webhook: %s", webhook_id)


async def async_unregister_webhook(hass: HomeAssistant, webhook_id: str) -> None:
    """Unregister the webhook."""
    async_unregister(hass, webhook_id)
    _LOGGER.info("Unregistered Donetick webhook: %s", webhook_id)


async def handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: web.Request
) -> web.Response:
    """Handle incoming webhook from Donetick."""
    try:
        data = await request.json()
    except ValueError:
        _LOGGER.error("Received invalid JSON in Donetick webhook")
        return web.Response(status=400, text="Invalid JSON")

    event_type = data.get("type")
    timestamp = data.get("timestamp")
    event_data = data.get("data", {})

    _LOGGER.debug(
        "Received Donetick webhook event: type=%s, timestamp=%s",
        event_type,
        timestamp,
    )

    if not event_type:
        _LOGGER.warning("Received Donetick webhook without event type")
        return web.Response(status=400, text="Missing event type")

    # Find the config entry associated with this webhook
    entry_id = None
    for eid, entry_data in hass.data.get(DOMAIN, {}).items():
        if isinstance(entry_data, dict) and entry_data.get("webhook_id") == webhook_id:
            entry_id = eid
            break

    # Fire a Home Assistant event for automations
    ha_event_type = EVENT_TYPE_MAP.get(event_type)
    if ha_event_type:
        event_payload = {
            "webhook_id": webhook_id,
            "entry_id": entry_id,
            "timestamp": timestamp,
            "event_type": event_type,
            **event_data,
        }
        hass.bus.async_fire(ha_event_type, event_payload)
        _LOGGER.debug("Fired Home Assistant event: %s", ha_event_type)

    # Trigger coordinator refresh for relevant events
    if event_type in REFRESH_TRIGGER_EVENTS:
        await _trigger_coordinator_refresh(hass, entry_id)

    # For thing.changed events, we might want to refresh thing entities
    if event_type == WEBHOOK_EVENT_THING_CHANGED:
        await _trigger_thing_refresh(hass, entry_id, event_data)

    return web.Response(status=200, text="OK")


async def _trigger_coordinator_refresh(hass: HomeAssistant, entry_id: str | None) -> None:
    """Trigger a refresh of the todo coordinator."""
    if not entry_id:
        _LOGGER.debug("No entry_id found, refreshing all Donetick coordinators")
        # Refresh all coordinators if we can't determine which entry
        for eid in hass.data.get(DOMAIN, {}):
            await _refresh_entry_coordinator(hass, eid)
    else:
        await _refresh_entry_coordinator(hass, entry_id)


async def _refresh_entry_coordinator(hass: HomeAssistant, entry_id: str) -> None:
    """Refresh the coordinator for a specific entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if entry_data and isinstance(entry_data, dict):
        coordinator = entry_data.get("coordinator")
        if coordinator:
            _LOGGER.debug("Triggering coordinator refresh for entry %s", entry_id)
            await coordinator.async_request_refresh()


async def _trigger_thing_refresh(
    hass: HomeAssistant, entry_id: str | None, event_data: dict[str, Any]
) -> None:
    """Handle thing state change events."""
    thing_id = event_data.get("id")
    new_state = event_data.get("to_state")
    
    _LOGGER.debug(
        "Thing state changed: id=%s, new_state=%s",
        thing_id,
        new_state,
    )
    
    # The thing entities will be updated via their own coordinator
    # or we can trigger a specific update if needed
    if entry_id:
        entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
        if entry_data and isinstance(entry_data, dict):
            thing_coordinator = entry_data.get("thing_coordinator")
            if thing_coordinator:
                await thing_coordinator.async_request_refresh()
