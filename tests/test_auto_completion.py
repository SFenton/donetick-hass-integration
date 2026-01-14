"""Tests for the auto-completion of past-due recurrent tasks."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from custom_components.donetick.model import DonetickTask
from custom_components.donetick.todo import (
    _is_recurrent_task,
    _calculate_next_recurrence_date,
    _get_midnight_of_date,
    AutoCompletionManager,
)
from custom_components.donetick.const import (
    DOMAIN,
    CONF_AUTO_COMPLETE_PAST_DUE,
    FREQUENCY_ONCE,
    FREQUENCY_NO_REPEAT,
    FREQUENCY_DAILY,
    FREQUENCY_WEEKLY,
    FREQUENCY_MONTHLY,
    FREQUENCY_YEARLY,
    FREQUENCY_INTERVAL,
    FREQUENCY_DAYS_OF_WEEK,
    FREQUENCY_DAY_OF_MONTH,
)


# Test timezone
TEST_TZ = ZoneInfo("America/New_York")

# Sentinel value to distinguish "no argument" from "explicitly None"
_USE_DEFAULT = object()


def create_test_task(
    task_id: int = 1,
    name: str = "Test Task",
    frequency_type: str = FREQUENCY_DAILY,
    frequency: int = 1,
    frequency_metadata: dict = None,
    next_due_date: datetime = _USE_DEFAULT,
    is_active: bool = True,
) -> DonetickTask:
    """Create a test task with default values."""
    if next_due_date is _USE_DEFAULT:
        next_due_date = datetime(2026, 1, 10, 8, 0, 0, tzinfo=TEST_TZ)
    
    return DonetickTask(
        id=task_id,
        name=name,
        next_due_date=next_due_date,
        status=0,
        priority=0,
        labels=None,
        is_active=is_active,
        frequency_type=frequency_type,
        frequency=frequency,
        frequency_metadata=frequency_metadata,
    )


class TestIsRecurrentTask:
    """Tests for _is_recurrent_task function."""
    
    def test_daily_is_recurrent(self):
        """Daily tasks should be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_DAILY)
        assert _is_recurrent_task(task) is True
    
    def test_weekly_is_recurrent(self):
        """Weekly tasks should be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_WEEKLY)
        assert _is_recurrent_task(task) is True
    
    def test_monthly_is_recurrent(self):
        """Monthly tasks should be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_MONTHLY)
        assert _is_recurrent_task(task) is True
    
    def test_yearly_is_recurrent(self):
        """Yearly tasks should be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_YEARLY)
        assert _is_recurrent_task(task) is True
    
    def test_interval_is_recurrent(self):
        """Interval tasks should be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_INTERVAL)
        assert _is_recurrent_task(task) is True
    
    def test_once_is_not_recurrent(self):
        """Once tasks should NOT be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_ONCE)
        assert _is_recurrent_task(task) is False
    
    def test_no_repeat_is_not_recurrent(self):
        """No-repeat tasks should NOT be recurrent."""
        task = create_test_task(frequency_type=FREQUENCY_NO_REPEAT)
        assert _is_recurrent_task(task) is False
    
    def test_none_frequency_is_not_recurrent(self):
        """Tasks with None frequency should NOT be recurrent."""
        task = create_test_task(frequency_type=None)
        assert _is_recurrent_task(task) is False
    
    def test_empty_frequency_is_not_recurrent(self):
        """Tasks with empty frequency should NOT be recurrent."""
        task = create_test_task(frequency_type="")
        assert _is_recurrent_task(task) is False


class TestCalculateNextRecurrenceDate:
    """Tests for _calculate_next_recurrence_date function."""
    
    def test_daily_recurrence(self):
        """Daily task should recur the next day."""
        due_date = datetime(2026, 1, 10, 8, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_DAILY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 1, 11, 8, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_daily_recurrence_every_2_days(self):
        """Daily task every 2 days should recur in 2 days."""
        due_date = datetime(2026, 1, 10, 8, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_DAILY,
            frequency=2,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 1, 12, 8, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_weekly_recurrence(self):
        """Weekly task should recur in 1 week."""
        due_date = datetime(2026, 1, 5, 17, 0, 0, tzinfo=TEST_TZ)  # Monday
        task = create_test_task(
            frequency_type=FREQUENCY_WEEKLY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 1, 12, 17, 0, 0, tzinfo=TEST_TZ)  # Next Monday
        assert next_date == expected
    
    def test_weekly_recurrence_every_2_weeks(self):
        """Weekly task every 2 weeks should recur in 2 weeks."""
        due_date = datetime(2026, 1, 5, 17, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_WEEKLY,
            frequency=2,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 1, 19, 17, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_monthly_recurrence(self):
        """Monthly task should recur in 1 month."""
        due_date = datetime(2026, 1, 15, 10, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_MONTHLY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 2, 15, 10, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_monthly_recurrence_end_of_month(self):
        """Monthly task on Jan 31 should recur on Feb 28."""
        due_date = datetime(2026, 1, 31, 10, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_MONTHLY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        # 2026 is not a leap year, so Feb has 28 days
        expected = datetime(2026, 2, 28, 10, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_yearly_recurrence(self):
        """Yearly task should recur in 1 year."""
        due_date = datetime(2026, 1, 15, 10, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_YEARLY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2027, 1, 15, 10, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_interval_days(self):
        """Interval task with days unit should work correctly."""
        due_date = datetime(2026, 1, 8, 9, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_INTERVAL,
            frequency=3,
            frequency_metadata={"unit": "days"},
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 1, 11, 9, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_interval_weeks(self):
        """Interval task with weeks unit should work correctly."""
        due_date = datetime(2026, 1, 5, 9, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_INTERVAL,
            frequency=2,
            frequency_metadata={"unit": "weeks"},
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 1, 19, 9, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_interval_months(self):
        """Interval task with months unit should work correctly."""
        due_date = datetime(2026, 1, 15, 9, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            frequency_type=FREQUENCY_INTERVAL,
            frequency=3,
            frequency_metadata={"unit": "months"},
            next_due_date=due_date,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is not None
        expected = datetime(2026, 4, 15, 9, 0, 0, tzinfo=TEST_TZ)
        assert next_date == expected
    
    def test_non_recurrent_returns_none(self):
        """Non-recurrent tasks should return None."""
        task = create_test_task(frequency_type=FREQUENCY_ONCE)
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is None
    
    def test_no_due_date_returns_none(self):
        """Tasks without due date should return None."""
        task = create_test_task(
            frequency_type=FREQUENCY_DAILY,
            next_due_date=None,
        )
        
        next_date = _calculate_next_recurrence_date(task, TEST_TZ)
        
        assert next_date is None


class TestGetMidnightOfDate:
    """Tests for _get_midnight_of_date function."""
    
    def test_returns_midnight(self):
        """Should return midnight (00:00:00) of the date."""
        dt = datetime(2026, 1, 15, 14, 30, 45, tzinfo=TEST_TZ)
        
        midnight = _get_midnight_of_date(dt, TEST_TZ)
        
        assert midnight.hour == 0
        assert midnight.minute == 0
        assert midnight.second == 0
        assert midnight.microsecond == 0
        assert midnight.day == 15
        assert midnight.month == 1
        assert midnight.year == 2026
    
    def test_preserves_date(self):
        """Should preserve the date while setting time to midnight."""
        dt = datetime(2026, 12, 31, 23, 59, 59, tzinfo=TEST_TZ)
        
        midnight = _get_midnight_of_date(dt, TEST_TZ)
        
        assert midnight.day == 31
        assert midnight.month == 12
        assert midnight.year == 2026


class TestAutoCompletionManager:
    """Tests for AutoCompletionManager class."""
    
    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {"coordinator": MagicMock()}}}
        hass.async_create_task = MagicMock(side_effect=lambda coro: coro)
        return hass
    
    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {CONF_AUTO_COMPLETE_PAST_DUE: True}
        return entry
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock API client."""
        client = MagicMock()
        client.async_complete_task = AsyncMock(return_value=MagicMock())
        return client
    
    @pytest.fixture
    def manager(self, mock_hass, mock_config_entry, mock_client):
        """Create an AutoCompletionManager instance."""
        return AutoCompletionManager(mock_hass, mock_config_entry, mock_client)
    
    def test_is_enabled_true(self, mock_hass, mock_client):
        """Manager should be enabled when config option is True."""
        entry = MagicMock()
        entry.data = {CONF_AUTO_COMPLETE_PAST_DUE: True}
        
        manager = AutoCompletionManager(mock_hass, entry, mock_client)
        
        assert manager.is_enabled() is True
    
    def test_is_enabled_false(self, mock_hass, mock_client):
        """Manager should be disabled when config option is False."""
        entry = MagicMock()
        entry.data = {CONF_AUTO_COMPLETE_PAST_DUE: False}
        
        manager = AutoCompletionManager(mock_hass, entry, mock_client)
        
        assert manager.is_enabled() is False
    
    def test_is_enabled_default_false(self, mock_hass, mock_client):
        """Manager should be disabled by default."""
        entry = MagicMock()
        entry.data = {}
        
        manager = AutoCompletionManager(mock_hass, entry, mock_client)
        
        assert manager.is_enabled() is False
    
    @pytest.mark.asyncio
    async def test_process_tasks_skips_when_disabled(self, mock_hass, mock_client):
        """Should not process tasks when feature is disabled."""
        entry = MagicMock()
        entry.data = {CONF_AUTO_COMPLETE_PAST_DUE: False}
        manager = AutoCompletionManager(mock_hass, entry, mock_client)
        
        task = create_test_task()
        
        # This should not raise any errors and should be a no-op
        await manager.process_tasks([task])
        
        # No schedules should be created
        assert len(manager._scheduled_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_process_tasks_skips_inactive_tasks(self, manager):
        """Should skip inactive tasks."""
        task = create_test_task(is_active=False)
        
        with patch('custom_components.donetick.todo.async_track_point_in_time'):
            await manager.process_tasks([task])
        
        assert len(manager._scheduled_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_process_tasks_skips_non_recurrent(self, manager):
        """Should skip non-recurrent tasks."""
        task = create_test_task(frequency_type=FREQUENCY_ONCE)
        
        with patch('custom_components.donetick.todo.async_track_point_in_time'):
            await manager.process_tasks([task])
        
        assert len(manager._scheduled_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_process_tasks_skips_tasks_without_due_date(self, manager):
        """Should skip tasks without due dates."""
        task = create_test_task(next_due_date=None)
        
        with patch('custom_components.donetick.todo.async_track_point_in_time'):
            await manager.process_tasks([task])
        
        assert len(manager._scheduled_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_process_tasks_skips_not_past_due(self, manager):
        """Should skip tasks that are not past due."""
        # Due date in the future
        future_date = datetime.now(TEST_TZ) + timedelta(days=5)
        task = create_test_task(next_due_date=future_date)
        
        with patch('custom_components.donetick.todo.async_track_point_in_time'):
            await manager.process_tasks([task])
        
        assert len(manager._scheduled_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_process_tasks_schedules_past_due_recurrent(self, manager):
        """Should schedule auto-completion for past-due recurrent tasks."""
        # Due date in the past
        past_date = datetime.now(TEST_TZ) - timedelta(days=2)
        task = create_test_task(
            frequency_type=FREQUENCY_DAILY,
            frequency=1,
            next_due_date=past_date,
        )
        
        mock_cancel = MagicMock()
        with patch(
            'custom_components.donetick.todo.async_track_point_in_time',
            return_value=mock_cancel
        ) as mock_track:
            await manager.process_tasks([task])
            
            # Should have scheduled the task
            assert mock_track.called
            assert task.id in manager._scheduled_tasks
    
    @pytest.mark.asyncio
    async def test_execute_auto_completion(self, manager, mock_client):
        """Should complete task and refresh coordinator."""
        task_id = 123
        task_name = "Test Task"
        
        # Add to scheduled tasks so cleanup can happen
        manager._scheduled_tasks[task_id] = MagicMock()
        
        await manager._execute_auto_completion(task_id, task_name)
        
        # Should have called complete_task
        mock_client.async_complete_task.assert_called_once_with(task_id)
        
        # Should have cleaned up
        assert task_id not in manager._scheduled_tasks
    
    def test_cancel_all(self, manager):
        """Should cancel all scheduled auto-completions."""
        # Add some mock schedules
        mock_cancel_1 = MagicMock()
        mock_cancel_2 = MagicMock()
        manager._scheduled_tasks[1] = mock_cancel_1
        manager._scheduled_tasks[2] = mock_cancel_2
        
        manager.cancel_all()
        
        # All cancels should have been called
        mock_cancel_1.assert_called_once()
        mock_cancel_2.assert_called_once()
        
        # Scheduled tasks should be empty
        assert len(manager._scheduled_tasks) == 0


class TestScenarios:
    """Test the scenarios from the requirements."""
    
    def test_scenario_1_daily_missed_one_day(self):
        """Scenario 1: Daily task missed one day.
        
        Task: "Take Vitamins"
        Frequency: Daily
        Original Due: Jan 10, 2026 @ 8:00 AM
        Current: Jan 12, 2026 @ 10:00 PM
        Expected: Auto-complete at midnight Jan 13
        """
        due_date = datetime(2026, 1, 10, 8, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            name="Take Vitamins",
            frequency_type=FREQUENCY_DAILY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_recurrence = _calculate_next_recurrence_date(task, TEST_TZ)
        
        # Next recurrence is Jan 11
        assert next_recurrence == datetime(2026, 1, 11, 8, 0, 0, tzinfo=TEST_TZ)
        
        # But we're on Jan 12, so we'd need to iterate to find Jan 13
        # The manager will detect that Jan 11 midnight is in the past
        # and schedule for immediate completion or next available
    
    def test_scenario_2_weekly_missed_previous_week(self):
        """Scenario 2: Weekly task missed previous week.
        
        Task: "Weekly Review"
        Frequency: Weekly (every Monday)
        Original Due: Jan 5, 2026 (Monday) @ 5:00 PM
        Next Recurrence: Jan 12, 2026 (Monday)
        """
        due_date = datetime(2026, 1, 5, 17, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            name="Weekly Review",
            frequency_type=FREQUENCY_WEEKLY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_recurrence = _calculate_next_recurrence_date(task, TEST_TZ)
        
        # Next recurrence should be Jan 12 (next Monday)
        expected = datetime(2026, 1, 12, 17, 0, 0, tzinfo=TEST_TZ)
        assert next_recurrence == expected
    
    def test_scenario_3_interval_every_3_days(self):
        """Scenario 3: Interval task every 3 days.
        
        Task: "Water Plants"
        Frequency: Every 3 days
        Original Due: Jan 8, 2026 @ 9:00 AM
        Next recurrence from Jan 8: Jan 11
        """
        due_date = datetime(2026, 1, 8, 9, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            name="Water Plants",
            frequency_type=FREQUENCY_INTERVAL,
            frequency=3,
            frequency_metadata={"unit": "days"},
            next_due_date=due_date,
        )
        
        next_recurrence = _calculate_next_recurrence_date(task, TEST_TZ)
        
        # Next recurrence should be Jan 11 (8 + 3 = 11)
        expected = datetime(2026, 1, 11, 9, 0, 0, tzinfo=TEST_TZ)
        assert next_recurrence == expected
    
    def test_scenario_4_monthly_pay_rent(self):
        """Scenario 4: Monthly task.
        
        Task: "Pay Rent"
        Frequency: Monthly (1st of each month)
        Original Due: Jan 1, 2026 @ 10:00 AM
        Next Recurrence: Feb 1, 2026
        """
        due_date = datetime(2026, 1, 1, 10, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            name="Pay Rent",
            frequency_type=FREQUENCY_MONTHLY,
            frequency=1,
            next_due_date=due_date,
        )
        
        next_recurrence = _calculate_next_recurrence_date(task, TEST_TZ)
        
        # Next recurrence should be Feb 1
        expected = datetime(2026, 2, 1, 10, 0, 0, tzinfo=TEST_TZ)
        assert next_recurrence == expected
    
    def test_scenario_5_non_recurrent_no_auto_completion(self):
        """Scenario 5: Non-recurrent task should not auto-complete.
        
        Task: "Call Doctor"
        Frequency: Once / No Repeat
        """
        due_date = datetime(2026, 1, 10, 15, 0, 0, tzinfo=TEST_TZ)
        task = create_test_task(
            name="Call Doctor",
            frequency_type=FREQUENCY_ONCE,
            next_due_date=due_date,
        )
        
        # Should not be recurrent
        assert _is_recurrent_task(task) is False
        
        # Should return None for next recurrence
        next_recurrence = _calculate_next_recurrence_date(task, TEST_TZ)
        assert next_recurrence is None
