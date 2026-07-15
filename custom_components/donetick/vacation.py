"""Vacation mode synchronization for Donetick."""
import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .api import DonetickApiClient
from .const import CONF_VACATION_MODE_ENTITY

_LOGGER = logging.getLogger(__name__)


class VacationModeManager:
    """Keep Donetick vacation mode aligned with a Home Assistant entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: DonetickApiClient,
    ) -> None:
        """Initialize the vacation mode manager."""
        self._hass = hass
        self._client = client
        self._entity_id = config_entry.options.get(
            CONF_VACATION_MODE_ENTITY,
            config_entry.data.get(CONF_VACATION_MODE_ENTITY, ""),
        )
        self._active: bool | None = None
        self._coordinator: Any = None
        self._state_unsub: Callable[[], None] | None = None
        self._coordinator_unsub: Callable[[], None] | None = None
        self._sync_lock = asyncio.Lock()
        self._pending_tasks: set[asyncio.Task] = set()
        self._stopped = False
        self._suppress_coordinator_reconcile = False

    @property
    def active(self) -> bool | None:
        """Return the last explicit Home Assistant vacation mode state."""
        return self._active

    async def async_start(self) -> None:
        """Start listening and perform the initial Donetick sync."""
        if not self._entity_id:
            return

        self._state_unsub = async_track_state_change_event(
            self._hass,
            self._entity_id,
            self._handle_state_change,
        )
        state = self._hass.states.get(self._entity_id)
        active = self._state_to_active(state)
        if active is None:
            return
        self._active = active
        await self.async_reconcile()

    def attach_coordinator(self, coordinator: Any) -> None:
        """Attach the task coordinator for visibility updates and reconciliation."""
        self._coordinator = coordinator
        if self._active is not None:
            self._set_coordinator_state(self._active)
        self._coordinator_unsub = coordinator.async_add_listener(
            self._handle_coordinator_update
        )

    @staticmethod
    def _state_to_active(state: Any) -> bool | None:
        """Convert only explicit on/off states to vacation mode values."""
        if state is None:
            return None
        if state.state == STATE_ON:
            return True
        if state.state == STATE_OFF:
            return False
        return None

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle a Home Assistant vacation entity state change."""
        new_state = event.data.get("new_state")
        active = self._state_to_active(new_state)
        if active is None:
            return
        if active != self._active:
            self._active = active
            self._set_coordinator_state(active)
        self._schedule_reconcile()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reconcile Donetick after each bounded coordinator refresh."""
        if not self._suppress_coordinator_reconcile:
            self._schedule_reconcile()

    def _set_coordinator_state(self, active: bool) -> None:
        if self._coordinator is None:
            return

        self._suppress_coordinator_reconcile = True
        try:
            self._coordinator.set_vacation_active(active)
        finally:
            self._suppress_coordinator_reconcile = False

    def _schedule_reconcile(self) -> None:
        if self._stopped or not self._entity_id or self._active is None:
            return

        task = self._hass.async_create_task(self.async_reconcile())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def async_reconcile(self) -> None:
        """Push the current Home Assistant state to Donetick."""
        if self._stopped or not self._entity_id or self._active is None:
            return

        async with self._sync_lock:
            try:
                await self._client.async_set_vacation_mode(self._active)
            except Exception as err:
                _LOGGER.warning(
                    "Could not synchronize Donetick vacation mode to %s: %s",
                    self._active,
                    err,
                )

    @callback
    def stop(self) -> None:
        """Synchronously stop listeners and cancel pending reconciliations."""
        self._stopped = True
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        if self._coordinator_unsub:
            self._coordinator_unsub()
            self._coordinator_unsub = None

        pending = list(self._pending_tasks)
        for task in pending:
            task.cancel()

    async def async_stop(self) -> None:
        """Stop listeners and wait for pending reconciliation tasks."""
        self.stop()
        pending = list(self._pending_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending_tasks.clear()
