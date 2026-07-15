"""Tests for Donetick vacation mode behavior."""
import asyncio
import inspect
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.donetick.todo as todo_module
from custom_components.donetick.const import (
    CONF_URL,
    CONF_VACATION_MODE_ENTITY,
)
from custom_components.donetick.model import DonetickMember, DonetickTask
from custom_components.donetick.todo import (
    DonetickAllTasksList,
    DonetickAssigneeTasksList,
    DonetickDateFilteredTasksList,
    DonetickDateFilteredWithUnassignedList,
    DonetickInternalAllTasksList,
    DonetickTodoListBase,
    DonetickTodoListEntity,
    DonetickTaskCoordinator,
    DonetickTimeOfDayTasksList,
    DonetickTimeOfDayWithUnassignedList,
    DonetickUpcomingTodayByTimeAndFutureList,
    DonetickUpcomingTodayByTimeAndFutureWithUnassignedList,
    DonetickUpcomingTodayByTimeList,
    DonetickUpcomingTodayByTimeWithUnassignedList,
)
from custom_components.donetick.vacation import VacationModeManager


def _member() -> DonetickMember:
    return DonetickMember(
        id=1,
        user_id=42,
        circle_id=100,
        role="member",
        is_active=True,
        username="user",
        display_name="User",
    )


def _entity_factories():
    member = _member()
    return [
        lambda c, e, h: DonetickAllTasksList(c, e, h),
        lambda c, e, h: DonetickAssigneeTasksList(c, e, member, h),
        lambda c, e, h: DonetickDateFilteredTasksList(
            c, e, h, "upcoming", member
        ),
        lambda c, e, h: DonetickDateFilteredWithUnassignedList(
            c, e, h, "upcoming", member
        ),
        lambda c, e, h: DonetickTimeOfDayTasksList(
            c, e, h, "morning", member
        ),
        lambda c, e, h: DonetickTimeOfDayWithUnassignedList(
            c, e, h, "morning", member
        ),
        lambda c, e, h: DonetickUpcomingTodayByTimeList(c, e, h, member),
        lambda c, e, h: DonetickUpcomingTodayByTimeWithUnassignedList(
            c, e, h, member
        ),
        lambda c, e, h: DonetickUpcomingTodayByTimeAndFutureList(
            c, e, h, member
        ),
        lambda c, e, h: DonetickUpcomingTodayByTimeAndFutureWithUnassignedList(
            c, e, h, member
        ),
        lambda c, e, h: DonetickTodoListEntity(c, e, h),
    ]


def test_every_todo_list_subclass_is_covered_by_visibility_audit():
    """Keep the family audit complete when new todo list subclasses are added."""
    discovered = set()
    pending = list(DonetickTodoListBase.__subclasses__())
    while pending:
        subclass = pending.pop()
        discovered.add(subclass)
        pending.extend(subclass.__subclasses__())

    assert discovered == {
        DonetickAllTasksList,
        DonetickAssigneeTasksList,
        DonetickDateFilteredTasksList,
        DonetickDateFilteredWithUnassignedList,
        DonetickInternalAllTasksList,
        DonetickTimeOfDayTasksList,
        DonetickTimeOfDayWithUnassignedList,
        DonetickUpcomingTodayByTimeList,
        DonetickUpcomingTodayByTimeWithUnassignedList,
        DonetickUpcomingTodayByTimeAndFutureList,
        DonetickUpcomingTodayByTimeAndFutureWithUnassignedList,
        DonetickTodoListEntity,
    }

    source = inspect.getsource(todo_module)
    assert source.count("self.coordinator.tasks_list") == 1


def test_internal_all_tasks_bypasses_vacation_visibility():
    """The automation entity remains complete while user lists are filtered."""
    hass = MagicMock()
    hass.config.time_zone = "America/New_York"
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {CONF_URL: "https://donetick.example.com"}
    entry.options = {}
    coordinator = DonetickTaskCoordinator(
        hass,
        AsyncMock(),
        update_interval=timedelta(minutes=15),
    )
    hidden = DonetickTask.from_json(
        {
            "id": 1,
            "name": "Hidden on vacation",
            "isActive": True,
            "hideOnVacation": True,
        }
    )
    visible = DonetickTask.from_json(
        {
            "id": 2,
            "name": "Always visible",
            "isActive": True,
            "hideOnVacation": False,
        }
    )
    coordinator.data = {1: hidden, 2: visible}
    coordinator._data_version = 1
    coordinator.set_vacation_active(True)
    user_entity = DonetickAllTasksList(coordinator, entry, hass)
    internal_entity = DonetickInternalAllTasksList(coordinator, entry, hass)

    assert [item.summary for item in user_entity.todo_items] == [
        "Always visible"
    ]
    assert user_entity.state == 1
    assert {item.summary for item in internal_entity.todo_items} == {
        "Hidden on vacation",
        "Always visible",
    }
    assert internal_entity.state == 2


@pytest.mark.parametrize("entity_factory", _entity_factories())
def test_all_todo_list_families_hide_and_restore_tasks(entity_factory):
    """Every generated todo family uses the shared vacation visibility rule."""
    hass = MagicMock()
    hass.config.time_zone = "America/New_York"
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {CONF_URL: "https://donetick.example.com"}
    entry.options = {}
    client = AsyncMock()
    coordinator = DonetickTaskCoordinator(
        hass,
        client,
        update_interval=timedelta(minutes=15),
    )
    hidden = DonetickTask.from_json(
        {
            "id": 1,
            "name": "Hidden on vacation",
            "isActive": True,
            "assignedTo": 42,
            "hideOnVacation": True,
        }
    )
    visible = DonetickTask.from_json(
        {
            "id": 2,
            "name": "Always visible",
            "isActive": True,
            "assignedTo": 42,
            "hideOnVacation": False,
        }
    )
    coordinator.data = {1: hidden, 2: visible}
    coordinator._data_version = 1
    entity = entity_factory(coordinator, entry, hass)
    entity._filter_tasks = lambda tasks: tasks
    if hasattr(entity, "_schedule_next_transition"):
        entity._schedule_next_transition = MagicMock()

    initial_items = entity.todo_items
    assert {item.summary for item in initial_items} == {
        "Hidden on vacation",
        "Always visible",
    }
    assert entity.state == 2

    coordinator.set_vacation_active(True)
    vacation_items = entity.todo_items
    assert [item.summary for item in vacation_items] == ["Always visible"]
    assert vacation_items is not initial_items
    assert entity.state == 1
    assert coordinator.data_version == 1

    coordinator.set_vacation_active(False)
    restored_items = entity.todo_items
    assert {item.summary for item in restored_items} == {
        "Hidden on vacation",
        "Always visible",
    }
    assert restored_items is not vacation_items
    assert entity.state == 2


@pytest.mark.asyncio
async def test_manager_startup_state_change_refresh_and_unload():
    """Manager syncs startup and changes, reconciles on refresh, and cleans up."""
    hass = MagicMock()
    hass.states.get.return_value = MagicMock(state="on")
    hass.async_create_task.side_effect = asyncio.create_task
    entry = MagicMock()
    entry.data = {
        CONF_VACATION_MODE_ENTITY: "input_boolean.vacation_mode",
    }
    entry.options = {}
    client = AsyncMock()
    state_unsub = MagicMock()
    coordinator_unsub = MagicMock()
    coordinator = MagicMock()
    coordinator.async_add_listener.return_value = coordinator_unsub

    with patch(
        "custom_components.donetick.vacation.async_track_state_change_event",
        return_value=state_unsub,
    ) as track_state:
        manager = VacationModeManager(hass, entry, client)
        await manager.async_start()
        manager.attach_coordinator(coordinator)

        assert manager.active is True
        client.async_set_vacation_mode.assert_awaited_once_with(True)
        coordinator.set_vacation_active.assert_called_once_with(True)
        track_state.assert_called_once()

        state_callback = track_state.call_args.args[2]
        state_callback(
            MagicMock(data={"new_state": MagicMock(state="off")})
        )
        await asyncio.gather(*list(manager._pending_tasks))

        assert manager.active is False
        coordinator.set_vacation_active.assert_called_with(False)
        client.async_set_vacation_mode.assert_awaited_with(False)

        coordinator_callback = coordinator.async_add_listener.call_args.args[0]
        coordinator_callback()
        await asyncio.gather(*list(manager._pending_tasks))
        assert client.async_set_vacation_mode.await_count == 3

        await manager.async_stop()

    state_unsub.assert_called_once()
    coordinator_unsub.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [None, "unknown", "unavailable"])
async def test_manager_defers_sync_until_explicit_state(initial_state):
    """Missing and indeterminate initial states must not be treated as off."""
    hass = MagicMock()
    hass.states.get.return_value = (
        None if initial_state is None else MagicMock(state=initial_state)
    )
    hass.async_create_task.side_effect = asyncio.create_task
    entry = MagicMock()
    entry.data = {
        CONF_VACATION_MODE_ENTITY: "input_boolean.vacation_mode",
    }
    entry.options = {}
    client = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_add_listener.return_value = MagicMock()

    with patch(
        "custom_components.donetick.vacation.async_track_state_change_event",
        return_value=MagicMock(),
    ) as track_state:
        manager = VacationModeManager(hass, entry, client)
        await manager.async_start()
        manager.attach_coordinator(coordinator)

        assert manager.active is None
        client.async_set_vacation_mode.assert_not_awaited()
        coordinator.set_vacation_active.assert_not_called()

        state_callback = track_state.call_args.args[2]
        state_callback(MagicMock(data={"new_state": MagicMock(state="on")}))
        await asyncio.gather(*list(manager._pending_tasks))

        assert manager.active is True
        client.async_set_vacation_mode.assert_awaited_once_with(True)
        coordinator.set_vacation_active.assert_called_once_with(True)
        await manager.async_stop()


@pytest.mark.asyncio
async def test_manager_retains_last_state_through_unavailable_transitions():
    """Unavailable transitions retain the last known explicit state."""
    hass = MagicMock()
    hass.states.get.return_value = MagicMock(state="on")
    hass.async_create_task.side_effect = asyncio.create_task
    entry = MagicMock()
    entry.data = {
        CONF_VACATION_MODE_ENTITY: "input_boolean.vacation_mode",
    }
    entry.options = {}
    client = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_add_listener.return_value = MagicMock()

    with patch(
        "custom_components.donetick.vacation.async_track_state_change_event",
        return_value=MagicMock(),
    ) as track_state:
        manager = VacationModeManager(hass, entry, client)
        await manager.async_start()
        manager.attach_coordinator(coordinator)
        state_callback = track_state.call_args.args[2]
        client.reset_mock()
        coordinator.set_vacation_active.reset_mock()

        state_callback(
            MagicMock(data={"new_state": MagicMock(state="unavailable")})
        )
        state_callback(MagicMock(data={"new_state": None}))

        assert manager.active is True
        client.async_set_vacation_mode.assert_not_awaited()
        coordinator.set_vacation_active.assert_not_called()
        await manager.async_stop()
