"""Tests for notification functionality."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant

from custom_components.donetick.todo import NotificationManager, _notification_reminders
from custom_components.donetick.const import (
    DOMAIN,
    CONF_NOTIFY_ON_PAST_DUE,
    CONF_ASSIGNEE_NOTIFICATIONS,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    PRIORITY_P4,
    NOTIFICATION_REMINDER_INTERVAL,
)
from custom_components.donetick.model import DonetickTask


# ==================== Fixtures ====================

@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.config = MagicMock()
    hass.config.time_zone = "America/New_York"
    hass.data = {DOMAIN: {}}
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {
        CONF_NOTIFY_ON_PAST_DUE: True,
        CONF_ASSIGNEE_NOTIFICATIONS: {
            "1": "notify.mobile_app_user1",
            "2": "notify.mobile_app_user2",
        },
    }
    return entry


@pytest.fixture
def mock_config_entry_disabled():
    """Create a mock config entry with notifications disabled."""
    entry = MagicMock()
    entry.entry_id = "test_entry_disabled"
    entry.data = {
        CONF_NOTIFY_ON_PAST_DUE: False,
        CONF_ASSIGNEE_NOTIFICATIONS: {},
    }
    return entry


@pytest.fixture
def sample_task_p1():
    """Create a sample P1 priority task."""
    return DonetickTask(
        id=101,
        name="Critical Task",
        description="This is urgent",
        frequency_type="once",
        next_due_date=datetime.now(ZoneInfo("UTC")) - timedelta(hours=1),
        assigned_to=1,
        is_active=True,
        priority=PRIORITY_P1,
    )


@pytest.fixture
def sample_task_p2():
    """Create a sample P2 priority task."""
    return DonetickTask(
        id=102,
        name="Important Task",
        description="Time sensitive",
        frequency_type="once",
        next_due_date=datetime.now(ZoneInfo("UTC")) - timedelta(hours=2),
        assigned_to=2,
        is_active=True,
        priority=PRIORITY_P2,
    )


@pytest.fixture
def sample_task_p3():
    """Create a sample P3 priority task."""
    return DonetickTask(
        id=103,
        name="Normal Task",
        description="Regular priority",
        frequency_type="once",
        next_due_date=datetime.now(ZoneInfo("UTC")) - timedelta(hours=3),
        assigned_to=1,
        is_active=True,
        priority=PRIORITY_P3,
    )


@pytest.fixture
def sample_task_no_assignee():
    """Create a sample task without an assignee."""
    return DonetickTask(
        id=104,
        name="Unassigned Task",
        description="No one assigned",
        frequency_type="once",
        next_due_date=datetime.now(ZoneInfo("UTC")) - timedelta(hours=1),
        assigned_to=None,
        is_active=True,
        priority=PRIORITY_P3,
    )


# ==================== NotificationManager Tests ====================

class TestNotificationManagerInit:
    """Tests for NotificationManager initialization."""

    def test_init(self, mock_hass, mock_config_entry):
        """Test NotificationManager initialization."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager._hass == mock_hass
        assert manager._config_entry == mock_config_entry

    def test_is_enabled_true(self, mock_hass, mock_config_entry):
        """Test is_enabled returns True when notifications are enabled."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager.is_enabled() is True

    def test_is_enabled_false(self, mock_hass, mock_config_entry_disabled):
        """Test is_enabled returns False when notifications are disabled."""
        manager = NotificationManager(mock_hass, mock_config_entry_disabled)
        assert manager.is_enabled() is False


class TestNotificationManagerGetNotifyService:
    """Tests for getting notify service for assignees."""

    def test_get_notify_service_valid_assignee(self, mock_hass, mock_config_entry):
        """Test getting notify service for a valid assignee."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager.get_notify_service(1) == "notify.mobile_app_user1"
        assert manager.get_notify_service(2) == "notify.mobile_app_user2"

    def test_get_notify_service_unknown_assignee(self, mock_hass, mock_config_entry):
        """Test getting notify service for an unknown assignee."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager.get_notify_service(999) is None

    def test_get_notify_service_none_assignee(self, mock_hass, mock_config_entry):
        """Test getting notify service when assignee is None."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager.get_notify_service(None) is None


class TestNotificationManagerInterruptionLevel:
    """Tests for interruption level based on priority."""

    def test_interruption_level_p1_critical(self, mock_hass, mock_config_entry):
        """Test P1 priority returns critical interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager._get_interruption_level(PRIORITY_P1) == "critical"

    def test_interruption_level_p2_time_sensitive(self, mock_hass, mock_config_entry):
        """Test P2 priority returns time-sensitive interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager._get_interruption_level(PRIORITY_P2) == "time-sensitive"

    def test_interruption_level_p3_passive(self, mock_hass, mock_config_entry):
        """Test P3 priority returns passive interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager._get_interruption_level(PRIORITY_P3) == "passive"

    def test_interruption_level_p4_passive(self, mock_hass, mock_config_entry):
        """Test P4 priority returns passive interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager._get_interruption_level(PRIORITY_P4) == "passive"

    def test_interruption_level_none_passive(self, mock_hass, mock_config_entry):
        """Test None priority returns passive interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        assert manager._get_interruption_level(None) == "passive"


class TestNotificationManagerSendNotification:
    """Tests for sending notifications."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self, mock_hass, mock_config_entry, sample_task_p1):
        """Test successfully sending a notification."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        result = await manager.send_past_due_notification(sample_task_p1)
        
        assert result is True
        mock_hass.services.async_call.assert_called_once()
        call_args = mock_hass.services.async_call.call_args
        assert call_args[0][0] == "notify"
        assert call_args[0][1] == "mobile_app_user1"
        
        # Check notification data
        data = call_args[0][2]
        assert "Critical Task" in data["title"]
        assert "Past Due" in data["title"]
        assert data["data"]["push"]["interruption-level"] == "critical"
        assert "actions" in data["data"]
        assert len(data["data"]["actions"]) == 3

    @pytest.mark.asyncio
    async def test_send_notification_reminder(self, mock_hass, mock_config_entry, sample_task_p1):
        """Test sending a reminder notification includes 'Reminder' prefix."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        result = await manager.send_past_due_notification(sample_task_p1, is_reminder=True)
        
        assert result is True
        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        assert data["title"].startswith("Reminder:")

    @pytest.mark.asyncio
    async def test_send_notification_disabled(self, mock_hass, mock_config_entry_disabled, sample_task_p1):
        """Test notification not sent when disabled."""
        manager = NotificationManager(mock_hass, mock_config_entry_disabled)
        
        result = await manager.send_past_due_notification(sample_task_p1)
        
        assert result is False
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_notification_no_assignee(self, mock_hass, mock_config_entry, sample_task_no_assignee):
        """Test notification not sent when task has no assignee."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        result = await manager.send_past_due_notification(sample_task_no_assignee)
        
        assert result is False
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_notification_unknown_assignee(self, mock_hass, mock_config_entry, sample_task_p1):
        """Test notification not sent when assignee has no configured notify service."""
        sample_task_p1.assigned_to = 999  # Unknown assignee
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        result = await manager.send_past_due_notification(sample_task_p1)
        
        assert result is False
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_notification_actions_include_task_id(self, mock_hass, mock_config_entry, sample_task_p1):
        """Test notification actions include the task ID."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        await manager.send_past_due_notification(sample_task_p1)
        
        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        actions = data["data"]["actions"]
        
        assert actions[0]["action"] == f"DONETICK_COMPLETE_{sample_task_p1.id}"
        assert actions[1]["action"] == f"DONETICK_SNOOZE_1H_{sample_task_p1.id}"
        assert actions[2]["action"] == f"DONETICK_SNOOZE_1D_{sample_task_p1.id}"

    @pytest.mark.asyncio
    async def test_send_notification_p2_time_sensitive(self, mock_hass, mock_config_entry, sample_task_p2):
        """Test P2 task gets time-sensitive interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        await manager.send_past_due_notification(sample_task_p2)
        
        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        assert data["data"]["push"]["interruption-level"] == "time-sensitive"

    @pytest.mark.asyncio
    async def test_send_notification_p3_passive(self, mock_hass, mock_config_entry, sample_task_p3):
        """Test P3 task gets passive interruption level."""
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        await manager.send_past_due_notification(sample_task_p3)
        
        call_args = mock_hass.services.async_call.call_args
        data = call_args[0][2]
        assert data["data"]["push"]["interruption-level"] == "passive"

    @pytest.mark.asyncio
    async def test_send_notification_service_error(self, mock_hass, mock_config_entry, sample_task_p1):
        """Test notification returns False on service error."""
        mock_hass.services.async_call.side_effect = Exception("Service failed")
        manager = NotificationManager(mock_hass, mock_config_entry)
        
        result = await manager.send_past_due_notification(sample_task_p1)
        
        assert result is False


class TestNotificationManagerReminders:
    """Tests for reminder scheduling and cancellation."""

    def setup_method(self):
        """Clear the global reminders dict before each test."""
        _notification_reminders.clear()

    def test_cancel_reminder_exists(self, mock_hass, mock_config_entry, sample_task_p1):
        """Test cancelling an existing reminder."""
        # Set up a mock reminder
        cancel_mock = MagicMock()
        _notification_reminders[sample_task_p1.id] = (cancel_mock, datetime.now())
        
        NotificationManager.cancel_reminder(sample_task_p1.id)
        
        cancel_mock.assert_called_once()
        assert sample_task_p1.id not in _notification_reminders

    def test_cancel_reminder_not_exists(self, mock_hass, mock_config_entry):
        """Test cancelling a non-existent reminder does not error."""
        # Should not raise
        NotificationManager.cancel_reminder(999)
        assert 999 not in _notification_reminders

    def test_schedule_reminder_disabled(self, mock_hass, mock_config_entry_disabled, sample_task_p1):
        """Test scheduling reminder when notifications disabled returns None."""
        manager = NotificationManager(mock_hass, mock_config_entry_disabled)
        
        result = manager.schedule_reminder(sample_task_p1, datetime.now())
        
        assert result is None


# ==================== Notification Action Handler Tests ====================

class TestNotificationActionHandlers:
    """Tests for notification action handlers."""

    @pytest.fixture
    def mock_event_complete(self):
        """Create a mock complete action event."""
        event = MagicMock()
        event.data = {"action": "DONETICK_COMPLETE_101"}
        return event

    @pytest.fixture
    def mock_event_snooze_1h(self):
        """Create a mock snooze 1 hour action event."""
        event = MagicMock()
        event.data = {"action": "DONETICK_SNOOZE_1H_101"}
        return event

    @pytest.fixture
    def mock_event_snooze_1d(self):
        """Create a mock snooze 1 day action event."""
        event = MagicMock()
        event.data = {"action": "DONETICK_SNOOZE_1D_101"}
        return event

    @pytest.fixture
    def mock_event_non_donetick(self):
        """Create a mock event from another integration."""
        event = MagicMock()
        event.data = {"action": "OTHER_ACTION_123"}
        return event

    @pytest.mark.asyncio
    async def test_handle_complete_action(self, mock_hass, mock_config_entry, mock_event_complete):
        """Test handling complete action from notification."""
        from custom_components.donetick import async_handle_notification_action, _get_api_client
        
        # Set up mocks
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            "coordinator": MagicMock(),
        }
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client, \
             patch("custom_components.donetick._refresh_todo_entities") as mock_refresh, \
             patch("custom_components.donetick.todo.NotificationManager.cancel_reminder") as mock_cancel:
            
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            
            await async_handle_notification_action(mock_hass, mock_event_complete, mock_config_entry)
            
            # Verify reminder was cancelled
            mock_cancel.assert_called_once_with(101)
            
            # Verify task was completed
            mock_client.async_complete_task.assert_called_once_with(101)
            
            # Verify refresh was triggered
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_snooze_1h_action(self, mock_hass, mock_config_entry, mock_event_snooze_1h):
        """Test handling snooze 1 hour action from notification."""
        from custom_components.donetick import async_handle_notification_action
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            "coordinator": MagicMock(),
        }
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client, \
             patch("custom_components.donetick._refresh_todo_entities") as mock_refresh, \
             patch("custom_components.donetick.todo.NotificationManager.cancel_reminder") as mock_cancel:
            
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            
            await async_handle_notification_action(mock_hass, mock_event_snooze_1h, mock_config_entry)
            
            # Verify reminder was cancelled
            mock_cancel.assert_called_once_with(101)
            
            # Verify task was updated with new due date
            mock_client.async_update_task.assert_called_once()
            call_kwargs = mock_client.async_update_task.call_args[1]
            assert call_kwargs["task_id"] == 101
            assert "due_date" in call_kwargs
            
            # Verify refresh was triggered
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_snooze_1d_action(self, mock_hass, mock_config_entry, mock_event_snooze_1d):
        """Test handling snooze 1 day action from notification."""
        from custom_components.donetick import async_handle_notification_action
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            "coordinator": MagicMock(),
        }
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client, \
             patch("custom_components.donetick._refresh_todo_entities") as mock_refresh, \
             patch("custom_components.donetick.todo.NotificationManager.cancel_reminder") as mock_cancel:
            
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            
            await async_handle_notification_action(mock_hass, mock_event_snooze_1d, mock_config_entry)
            
            # Verify reminder was cancelled
            mock_cancel.assert_called_once_with(101)
            
            # Verify task was updated
            mock_client.async_update_task.assert_called_once()
            
            # Verify refresh was triggered
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignore_non_donetick_action(self, mock_hass, mock_config_entry, mock_event_non_donetick):
        """Test non-Donetick actions are ignored."""
        from custom_components.donetick import async_handle_notification_action
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client:
            await async_handle_notification_action(mock_hass, mock_event_non_donetick, mock_config_entry)
            
            # Should not attempt to get API client
            mock_get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_invalid_action_format(self, mock_hass, mock_config_entry):
        """Test invalid action format is handled gracefully."""
        from custom_components.donetick import async_handle_notification_action
        
        event = MagicMock()
        event.data = {"action": "DONETICK_INVALID"}  # Missing task_id
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client:
            # Should not raise
            await async_handle_notification_action(mock_hass, event, mock_config_entry)
            
            # Should not attempt to get API client
            mock_get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_action_api_error(self, mock_hass, mock_config_entry, mock_event_complete):
        """Test API errors during action handling are caught."""
        from custom_components.donetick import async_handle_notification_action
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            "coordinator": MagicMock(),
        }
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client, \
             patch("custom_components.donetick.todo.NotificationManager.cancel_reminder"):
            
            mock_client = AsyncMock()
            mock_client.async_complete_task.side_effect = Exception("API Error")
            mock_get_client.return_value = mock_client
            
            # Should not raise
            await async_handle_notification_action(mock_hass, mock_event_complete, mock_config_entry)


class TestSnoozeTimeDelta:
    """Tests for snooze time calculations."""

    @pytest.mark.asyncio
    async def test_snooze_1h_correct_time_delta(self, mock_hass, mock_config_entry):
        """Test snooze 1 hour calculates correct time delta."""
        from custom_components.donetick import _handle_snooze_action
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            "coordinator": MagicMock(),
        }
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client, \
             patch("custom_components.donetick._refresh_todo_entities"), \
             patch("custom_components.donetick.todo.NotificationManager.cancel_reminder"):
            
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            
            before = datetime.now(ZoneInfo("America/New_York"))
            await _handle_snooze_action(mock_hass, mock_config_entry, 101, hours=1)
            after = datetime.now(ZoneInfo("America/New_York"))
            
            # Check the due_date was set approximately 1 hour from now
            call_kwargs = mock_client.async_update_task.call_args[1]
            due_date_str = call_kwargs["due_date"]
            due_date = datetime.fromisoformat(due_date_str)
            
            expected_min = before + timedelta(hours=1)
            expected_max = after + timedelta(hours=1)
            
            assert expected_min <= due_date <= expected_max

    @pytest.mark.asyncio
    async def test_snooze_1d_correct_time_delta(self, mock_hass, mock_config_entry):
        """Test snooze 1 day calculates correct time delta."""
        from custom_components.donetick import _handle_snooze_action
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            "coordinator": MagicMock(),
        }
        
        with patch("custom_components.donetick._get_api_client") as mock_get_client, \
             patch("custom_components.donetick._refresh_todo_entities"), \
             patch("custom_components.donetick.todo.NotificationManager.cancel_reminder"):
            
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            
            before = datetime.now(ZoneInfo("America/New_York"))
            await _handle_snooze_action(mock_hass, mock_config_entry, 101, hours=24)
            after = datetime.now(ZoneInfo("America/New_York"))
            
            # Check the due_date was set approximately 24 hours from now
            call_kwargs = mock_client.async_update_task.call_args[1]
            due_date_str = call_kwargs["due_date"]
            due_date = datetime.fromisoformat(due_date_str)
            
            expected_min = before + timedelta(hours=24)
            expected_max = after + timedelta(hours=24)
            
            assert expected_min <= due_date <= expected_max
