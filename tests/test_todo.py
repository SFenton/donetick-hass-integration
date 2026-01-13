"""Unit tests for custom_components.donetick.todo module."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.todo import (
    DonetickTaskCoordinator,
    DonetickTodoListBase,
    DonetickAllTasksList,
    DonetickAssigneeTasksList,
    DonetickDateFilteredTasksList,
    DonetickTimeOfDayTasksList,
    DonetickTimeOfDayWithUnassignedList,
    _is_frequent_recurrence,
    _get_recurrence_advance_days,
)
from custom_components.donetick.model import DonetickTask, DonetickMember
from custom_components.donetick.const import (
    DOMAIN,
    CONF_URL,
    CONF_AUTH_TYPE,
    AUTH_TYPE_JWT,
    CONF_SHOW_DUE_IN,
    CONF_REFRESH_INTERVAL,
    CONF_MORNING_CUTOFF,
    CONF_AFTERNOON_CUTOFF,
    DEFAULT_MORNING_CUTOFF,
    DEFAULT_AFTERNOON_CUTOFF,
    FREQUENCY_DAILY,
    FREQUENCY_WEEKLY,
    FREQUENCY_INTERVAL,
    FREQUENCY_ONCE,
    FREQUENCY_NO_REPEAT,
)

from homeassistant.components.todo import TodoItem, TodoItemStatus


class TestDonetickTaskCoordinator:
    """Tests for DonetickTaskCoordinator."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {}}
        return hass

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def coordinator(self, mock_hass, mock_client):
        """Create coordinator for testing."""
        return DonetickTaskCoordinator(
            mock_hass,
            mock_client,
            update_interval=timedelta(seconds=300),
        )

    def test_init(self, coordinator):
        """Test coordinator initialization."""
        assert coordinator._tasks_by_id == {}
        assert coordinator._task_hashes == {}
        assert coordinator._data_version == 0

    def test_hash_task_consistent(self, coordinator, sample_chore_json):
        """Test that hash_task produces consistent hashes."""
        task = DonetickTask.from_json(sample_chore_json)
        
        hash1 = coordinator._hash_task(task)
        hash2 = coordinator._hash_task(task)
        
        assert hash1 == hash2

    def test_hash_task_different_for_different_tasks(self, coordinator, sample_chore_json):
        """Test that hash_task produces different hashes for different tasks."""
        task1 = DonetickTask.from_json(sample_chore_json)
        
        modified_json = sample_chore_json.copy()
        modified_json["name"] = "Different Task Name"
        task2 = DonetickTask.from_json(modified_json)
        
        hash1 = coordinator._hash_task(task1)
        hash2 = coordinator._hash_task(task2)
        
        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_async_update_data_increments_version_on_change(self, coordinator, mock_client, sample_chore_json):
        """Test that data version increments when tasks change."""
        task1 = DonetickTask.from_json(sample_chore_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1])
        
        initial_version = coordinator._data_version
        await coordinator._async_update_data()
        
        assert coordinator._data_version == initial_version + 1
        assert len(coordinator._tasks_by_id) == 1

    @pytest.mark.asyncio
    async def test_async_update_data_no_version_change_when_same(self, coordinator, mock_client, sample_chore_json):
        """Test that data version doesn't change when tasks are the same."""
        task1 = DonetickTask.from_json(sample_chore_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1])
        
        # First update
        await coordinator._async_update_data()
        version_after_first = coordinator._data_version
        
        # Second update with same data
        await coordinator._async_update_data()
        version_after_second = coordinator._data_version
        
        assert version_after_second == version_after_first

    @pytest.mark.asyncio
    async def test_async_update_data_detects_added_tasks(self, coordinator, mock_client, sample_chore_json):
        """Test that coordinator detects added tasks."""
        task1 = DonetickTask.from_json(sample_chore_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1])
        
        await coordinator._async_update_data()
        version_after_first = coordinator._data_version
        
        # Add a second task
        modified_json = sample_chore_json.copy()
        modified_json["id"] = 999
        modified_json["name"] = "New Task"
        task2 = DonetickTask.from_json(modified_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1, task2])
        
        await coordinator._async_update_data()
        
        assert coordinator._data_version == version_after_first + 1
        assert len(coordinator._tasks_by_id) == 2

    @pytest.mark.asyncio
    async def test_async_update_data_detects_removed_tasks(self, coordinator, mock_client, sample_chore_json):
        """Test that coordinator detects removed tasks."""
        task1 = DonetickTask.from_json(sample_chore_json)
        
        modified_json = sample_chore_json.copy()
        modified_json["id"] = 999
        task2 = DonetickTask.from_json(modified_json)
        
        mock_client.async_get_tasks = AsyncMock(return_value=[task1, task2])
        await coordinator._async_update_data()
        version_after_first = coordinator._data_version
        
        # Remove second task
        mock_client.async_get_tasks = AsyncMock(return_value=[task1])
        await coordinator._async_update_data()
        
        assert coordinator._data_version == version_after_first + 1
        assert len(coordinator._tasks_by_id) == 1

    @pytest.mark.asyncio
    async def test_async_update_data_detects_updated_tasks(self, coordinator, mock_client, sample_chore_json):
        """Test that coordinator detects updated tasks."""
        task1 = DonetickTask.from_json(sample_chore_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1])
        
        await coordinator._async_update_data()
        version_after_first = coordinator._data_version
        
        # Update the task
        modified_json = sample_chore_json.copy()
        modified_json["name"] = "Updated Task Name"
        task1_updated = DonetickTask.from_json(modified_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1_updated])
        
        await coordinator._async_update_data()
        
        assert coordinator._data_version == version_after_first + 1

    def test_tasks_list_empty_when_no_data(self, coordinator):
        """Test tasks_list returns empty list when no data."""
        coordinator.data = None
        assert coordinator.tasks_list == []

    @pytest.mark.asyncio
    async def test_tasks_list_returns_list(self, coordinator, mock_client, sample_chore_json):
        """Test tasks_list returns list of tasks."""
        task1 = DonetickTask.from_json(sample_chore_json)
        mock_client.async_get_tasks = AsyncMock(return_value=[task1])
        
        # Set data directly to simulate after update
        coordinator.data = {task1.id: task1}
        
        tasks = coordinator.tasks_list
        assert len(tasks) == 1
        assert tasks[0].id == task1.id

    def test_get_task_returns_none_when_no_data(self, coordinator):
        """Test get_task returns None when no data."""
        coordinator.data = None
        assert coordinator.get_task(1) is None

    @pytest.mark.asyncio
    async def test_get_task_returns_task(self, coordinator, mock_client, sample_chore_json):
        """Test get_task returns correct task."""
        task1 = DonetickTask.from_json(sample_chore_json)
        
        # Set data directly
        coordinator.data = {task1.id: task1}
        
        result = coordinator.get_task(task1.id)
        assert result is not None
        assert result.id == task1.id


class TestDonetickTodoListBase:
    """Tests for DonetickTodoListBase."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        return entry

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = {}
        coordinator.data_version = 1
        coordinator.tasks_list = []
        return coordinator

    def test_device_info(self, mock_coordinator, mock_config_entry, mock_hass):
        """Test device_info is None (todo entities don't define device_info)."""
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        device_info = entity.device_info
        
        # Todo entities don't override device_info, so it should be None
        assert device_info is None


class TestDonetickAllTasksList:
    """Tests for DonetickAllTasksList entity."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        return entry

    @pytest.fixture
    def mock_coordinator(self, sample_chore_json):
        """Create mock coordinator with sample data."""
        coordinator = MagicMock()
        task = DonetickTask.from_json(sample_chore_json)
        coordinator.data = {task.id: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        return coordinator

    def test_unique_id(self, mock_coordinator, mock_config_entry, mock_hass):
        """Test unique_id is correct."""
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        assert "all_tasks" in entity.unique_id
        assert mock_config_entry.entry_id in entity.unique_id

    def test_name(self, mock_coordinator, mock_config_entry, mock_hass):
        """Test name is correct."""
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        assert entity.name == "All Tasks"


class TestDonetickAssigneeTasksList:
    """Tests for DonetickAssigneeTasksList entity."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        return entry

    @pytest.fixture
    def mock_coordinator(self, sample_chore_json):
        """Create mock coordinator with sample data."""
        coordinator = MagicMock()
        task = DonetickTask.from_json(sample_chore_json)
        coordinator.data = {task.id: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        return coordinator

    @pytest.fixture
    def sample_member(self, sample_circle_member_json):
        """Create sample member."""
        return DonetickMember.from_json(sample_circle_member_json)

    def test_unique_id_includes_member_id(self, mock_coordinator, mock_config_entry, mock_hass, sample_member):
        """Test unique_id includes member ID."""
        # Constructor order: coordinator, config_entry, member, hass
        entity = DonetickAssigneeTasksList(mock_coordinator, mock_config_entry, sample_member, mock_hass)
        
        assert str(sample_member.user_id) in entity.unique_id

    def test_name_includes_member_name(self, mock_coordinator, mock_config_entry, mock_hass, sample_member):
        """Test name includes member display name."""
        # Constructor order: coordinator, config_entry, member, hass
        entity = DonetickAssigneeTasksList(mock_coordinator, mock_config_entry, sample_member, mock_hass)
        
        assert sample_member.display_name in entity.name


class TestDonetickDateFilteredTasksList:
    """Tests for DonetickDateFilteredTasksList entity."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_SHOW_DUE_IN: 7,
        }
        return entry

    @pytest.fixture
    def mock_coordinator(self, sample_chore_json):
        """Create mock coordinator."""
        coordinator = MagicMock()
        task = DonetickTask.from_json(sample_chore_json)
        coordinator.data = {task.id: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        return coordinator

    def test_unique_id(self, mock_coordinator, mock_config_entry, mock_hass):
        """Test unique_id is correct."""
        # Constructor: coordinator, config_entry, hass, list_type, member=None
        entity = DonetickDateFilteredTasksList(mock_coordinator, mock_config_entry, mock_hass, "past_due")
        
        assert "past_due" in entity.unique_id

    def test_name(self, mock_coordinator, mock_config_entry, mock_hass):
        """Test name is correct."""
        # Constructor: coordinator, config_entry, hass, list_type, member=None
        entity = DonetickDateFilteredTasksList(mock_coordinator, mock_config_entry, mock_hass, "due_today")
        
        assert "Due Today" in entity.name

    def test_time_migration_upcoming_to_due_today(self, mock_config_entry, mock_hass):
        """Test task migrates from upcoming to due_today when time passes."""
        # Create a task due tomorrow
        tomorrow = datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": tomorrow.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        # Create upcoming entity
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "upcoming")
        
        # Initially, task should be in upcoming
        items = entity.todo_items
        assert len(items) == 1
        assert entity._cached_task_ids == {1}
        
        # Simulate time passing - task now due today
        today = datetime.now(ZoneInfo("America/New_York")).replace(hour=14, minute=0, second=0)
        task_data["nextDueDate"] = today.isoformat()
        task = DonetickTask.from_json(task_data)
        coordinator.tasks_list = [task]
        coordinator.data = {1: task}
        # Note: data_version doesn't change - server didn't change, only time passed
        
        # Now check upcoming - task should have migrated out
        items = entity.todo_items
        assert len(items) == 0
        assert entity._cached_task_ids == set()
        
    def test_time_migration_due_today_to_past_due(self, mock_config_entry, mock_hass):
        """Test task migrates from due_today to past_due when time passes."""
        tz = ZoneInfo("America/New_York")
        # Fixed time: 2024-06-15 at 10:00 AM
        now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=tz)
        
        # Task due at 11:00 AM today (in 1 hour)
        due_soon = datetime(2024, 6, 15, 11, 0, 0, tzinfo=tz)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": due_soon.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        # Create due_today entity with mocked time
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Initially, task should be in due_today (due at 11 AM, now is 10 AM)
        items = entity.todo_items
        assert len(items) == 1
        
        # Simulate time passing - now it's 12:00, task is past due
        later = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        entity._get_local_now = lambda: later
        entity._get_local_today_start = lambda: later.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: later.replace(hour=23, minute=59, second=59, microsecond=999999)
        entity._cached_todo_items = None  # Force rebuild
        
        # Now check due_today - task should have migrated out (it's past due now)
        items = entity.todo_items
        assert len(items) == 0

    def test_no_rebuild_when_no_changes(self, mock_config_entry, mock_hass):
        """Test that todo_items cache is used when nothing changed."""
        tomorrow = datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": tomorrow.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "upcoming")
        
        # First access - builds cache
        items1 = entity.todo_items
        
        # Second access - same data, same time window - should return same object
        items2 = entity.todo_items
        
        # Should be the exact same list object (cached)
        assert items1 is items2

    def test_server_change_triggers_rebuild(self, mock_config_entry, mock_hass):
        """Test that server data changes trigger a rebuild."""
        tomorrow = datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": tomorrow.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "upcoming")
        
        # First access
        items1 = entity.todo_items
        
        # Server data changes (new task added)
        task2_data = {
            "id": 2,
            "name": "New Task",
            "frequencyType": "once",
            "nextDueDate": (tomorrow + timedelta(days=1)).isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task2 = DonetickTask.from_json(task2_data)
        coordinator.tasks_list = [task, task2]
        coordinator.data = {1: task, 2: task2}
        coordinator.data_version = 2  # Version incremented
        
        # Second access - should rebuild due to version change
        items2 = entity.todo_items
        
        # Should be a new list with 2 items
        assert items1 is not items2
        assert len(items2) == 2


class TestScheduledTransitions:
    """Tests for scheduled transition timing in date-filtered lists."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_SHOW_DUE_IN: 7,
        }
        return entry

    def test_calculate_transition_past_due_entry(self, mock_config_entry, mock_hass):
        """Test calculation of when task enters past_due list."""
        tz = ZoneInfo("America/New_York")
        # Fixed time: 2024-06-15 at 10:00 AM
        now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=tz)
        
        # Task due at 12:00 PM today (in 2 hours) - currently in due_today
        due_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": due_time.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "past_due")
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Calculate next transition
        next_transition = entity._calculate_next_transition_time()
        
        # Should be approximately due_time + 1 second buffer (when task becomes past due)
        assert next_transition is not None
        # The method adds 1 second buffer
        expected = due_time + timedelta(seconds=1)
        time_diff = abs((next_transition - expected).total_seconds())
        assert time_diff <= 1  # Within 1 second tolerance

    def test_calculate_transition_due_today_exit(self, mock_config_entry, mock_hass):
        """Test calculation of when task exits due_today (becomes past_due)."""
        tz = ZoneInfo("America/New_York")
        # Fixed time: 2024-06-15 at 10:00 AM
        now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=tz)
        
        # Task due at 1:00 PM today (in 3 hours)
        due_time = datetime(2024, 6, 15, 13, 0, 0, tzinfo=tz)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": due_time.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        next_transition = entity._calculate_next_transition_time()
        
        # Should be when task becomes past due (due_time + 1 second buffer)
        assert next_transition is not None
        expected = due_time + timedelta(seconds=1)
        time_diff = abs((next_transition - expected).total_seconds())
        assert time_diff <= 1

    def test_calculate_transition_upcoming_exit(self, mock_config_entry, mock_hass):
        """Test calculation of when task exits upcoming (becomes due_today at midnight)."""
        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)
        
        # Create a task due tomorrow at 2pm
        tomorrow = now + timedelta(days=1)
        tomorrow_2pm = tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
        task_data = {
            "id": 1,
            "name": "Test Task",
            "frequencyType": "once",
            "nextDueDate": tomorrow_2pm.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "upcoming")
        
        next_transition = entity._calculate_next_transition_time()
        
        # Should be at midnight tomorrow (start of tomorrow)
        assert next_transition is not None
        tomorrow_midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        time_diff = (next_transition - tomorrow_midnight).total_seconds()
        assert 0 <= time_diff <= 5

    def test_calculate_transition_no_tasks(self, mock_config_entry, mock_hass):
        """Test that no transition is scheduled when no relevant tasks exist."""
        coordinator = MagicMock()
        coordinator.data = {}
        coordinator.data_version = 1
        coordinator.tasks_list = []
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "past_due")
        
        next_transition = entity._calculate_next_transition_time()
        
        assert next_transition is None

    def test_calculate_transition_multiple_tasks_picks_earliest(self, mock_config_entry, mock_hass):
        """Test that the earliest transition time is chosen when multiple tasks exist."""
        tz = ZoneInfo("America/New_York")
        # Fixed time: 2024-06-15 at 10:00 AM
        now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=tz)
        
        # Task due at 11:00 AM (in 1 hour)
        task1_due = datetime(2024, 6, 15, 11, 0, 0, tzinfo=tz)
        task1_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": task1_due.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        # Task due at 1:00 PM (in 3 hours)
        task2_due = datetime(2024, 6, 15, 13, 0, 0, tzinfo=tz)
        task2_data = {
            "id": 2,
            "name": "Task 2",
            "frequencyType": "once",
            "nextDueDate": task2_due.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task1 = DonetickTask.from_json(task1_data)
        task2 = DonetickTask.from_json(task2_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task1, 2: task2}
        coordinator.data_version = 1
        coordinator.tasks_list = [task1, task2]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "past_due")
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        next_transition = entity._calculate_next_transition_time()
        
        # Should pick task1's due time (earlier) + 1 second buffer
        expected = task1_due + timedelta(seconds=1)
        assert next_transition is not None
        time_diff = abs((next_transition - expected).total_seconds())
        assert time_diff <= 1

    def test_schedule_transition_called_on_server_change(self, mock_config_entry, mock_hass):
        """Test that _schedule_next_transition is called when server data changes."""
        now = datetime.now(ZoneInfo("America/New_York"))
        task_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": (now + timedelta(hours=2)).isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        
        # Mock the scheduling method
        with patch.object(entity, '_schedule_next_transition') as mock_schedule:
            # First access - builds cache
            entity.todo_items
            
            # Should have been called once during initial build
            mock_schedule.assert_called_once()
            mock_schedule.reset_mock()
            
            # Server data changes
            coordinator.data_version = 2
            
            # Second access - should trigger reschedule
            entity.todo_items
            
            mock_schedule.assert_called_once()

    def test_schedule_transition_cancels_previous(self, mock_config_entry, mock_hass):
        """Test that scheduling a new transition cancels the previous one."""
        now = datetime.now(ZoneInfo("America/New_York"))
        task_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": (now + timedelta(hours=2)).isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        
        # Simulate an existing cancel callback
        mock_cancel = MagicMock()
        entity._scheduled_transition_cancel = mock_cancel
        
        with patch('custom_components.donetick.todo.async_track_point_in_time', return_value=MagicMock()):
            entity._schedule_next_transition()
        
        # Previous callback should have been called to cancel
        mock_cancel.assert_called_once()

    def test_transition_filters_by_assignee(self, mock_config_entry, mock_hass):
        """Test that transition calculation respects assignee filtering."""
        tz = ZoneInfo("America/New_York")
        # Fixed time: 2024-06-15 at 10:00 AM
        now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=tz)
        
        # Task assigned to user 1, due at 12:00 PM (in 2 hours)
        task1_due = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        task1_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": task1_due.isoformat(),
            "isActive": True,
            "assignedTo": 1,
        }
        # Unassigned task, due at 11:00 AM (in 1 hour)
        task2_due = datetime(2024, 6, 15, 11, 0, 0, tzinfo=tz)
        task2_data = {
            "id": 2,
            "name": "Task 2",
            "frequencyType": "once",
            "nextDueDate": task2_due.isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task1 = DonetickTask.from_json(task1_data)
        task2 = DonetickTask.from_json(task2_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task1, 2: task2}
        coordinator.data_version = 1
        coordinator.tasks_list = [task1, task2]
        
        # Create entity for unassigned tasks
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "past_due")
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        next_transition = entity._calculate_next_transition_time()
        
        # Should only consider task2 (unassigned), due at 11:00 AM + 1 second buffer
        expected = task2_due + timedelta(seconds=1)
        assert next_transition is not None
        time_diff = abs((next_transition - expected).total_seconds())
        assert time_diff <= 1

    @pytest.mark.asyncio
    async def test_async_added_to_hass_schedules_transition(self, mock_config_entry, mock_hass):
        """Test that transition is scheduled when entity is added to hass."""
        now = datetime.now(ZoneInfo("America/New_York"))
        task_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": (now + timedelta(hours=2)).isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        
        with patch.object(entity, '_schedule_next_transition') as mock_schedule:
            await entity.async_added_to_hass()
            mock_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_will_remove_cancels_transition(self, mock_config_entry, mock_hass):
        """Test that scheduled transition is cancelled when entity is removed."""
        now = datetime.now(ZoneInfo("America/New_York"))
        task_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": (now + timedelta(hours=2)).isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        coordinator.async_remove_listener = MagicMock()
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        
        # Simulate having a scheduled callback
        mock_cancel = MagicMock()
        entity._scheduled_transition_cancel = mock_cancel
        
        await entity.async_will_remove_from_hass()
        
        mock_cancel.assert_called_once()
        assert entity._scheduled_transition_cancel is None

    @pytest.mark.asyncio
    async def test_transition_callback_triggers_state_update(self, mock_config_entry, mock_hass):
        """Test that the transition callback triggers a state update and reschedules."""
        now = datetime.now(ZoneInfo("America/New_York"))
        task_data = {
            "id": 1,
            "name": "Task 1",
            "frequencyType": "once",
            "nextDueDate": (now + timedelta(hours=2)).isoformat(),
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        
        with patch.object(entity, 'async_write_ha_state') as mock_write_state:
            with patch.object(entity, '_schedule_next_transition') as mock_schedule:
                await entity._handle_transition_callback(now)
                
                mock_write_state.assert_called_once()
                mock_schedule.assert_called_once()

    def test_inactive_tasks_ignored_for_transitions(self, mock_config_entry, mock_hass):
        """Test that inactive tasks don't trigger transitions."""
        now = datetime.now(ZoneInfo("America/New_York"))
        task_data = {
            "id": 1,
            "name": "Inactive Task",
            "frequencyType": "once",
            "nextDueDate": (now + timedelta(hours=1)).isoformat(),
            "isActive": False,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "past_due")
        
        next_transition = entity._calculate_next_transition_time()
        
        # No transition should be scheduled for inactive task
        assert next_transition is None

    def test_tasks_without_due_date_ignored(self, mock_config_entry, mock_hass):
        """Test that tasks without due dates don't trigger transitions."""
        task_data = {
            "id": 1,
            "name": "No Due Date Task",
            "frequencyType": "once",
            "nextDueDate": None,
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "past_due")
        
        next_transition = entity._calculate_next_transition_time()
        
        assert next_transition is None


# =============================================================================
# Tests for Recurrence Filtering Helper Functions
# =============================================================================

class TestIsFrequentRecurrence:
    """Tests for the _is_frequent_recurrence helper function."""

    def test_daily_recurrence_is_frequent(self):
        """Daily recurrence should be considered frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_DAILY
        task.frequency = 1
        task.frequency_metadata = None
        
        assert _is_frequent_recurrence(task) is True

    def test_weekly_recurrence_is_not_frequent(self):
        """Weekly recurrence should NOT be considered frequent (shown with advance window)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_WEEKLY
        task.frequency = 1
        task.frequency_metadata = None
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_days_1_is_frequent(self):
        """Custom interval of 1 day should be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 1
        task.frequency_metadata = {"unit": "days"}
        
        assert _is_frequent_recurrence(task) is True

    def test_interval_days_4_is_frequent(self):
        """Custom interval of 4 days should be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 4
        task.frequency_metadata = {"unit": "days"}
        
        assert _is_frequent_recurrence(task) is True

    def test_interval_days_5_is_not_frequent(self):
        """Custom interval of 5 days should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 5
        task.frequency_metadata = {"unit": "days"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_days_10_is_not_frequent(self):
        """Custom interval of 10 days should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 10
        task.frequency_metadata = {"unit": "days"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_weeks_1_is_not_frequent(self):
        """Custom interval of 1 week should NOT be frequent (shown with advance window)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 1
        task.frequency_metadata = {"unit": "weeks"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_weeks_2_is_not_frequent(self):
        """Custom interval of 2 weeks should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 2
        task.frequency_metadata = {"unit": "weeks"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_weeks_3_is_not_frequent(self):
        """Custom interval of 3 weeks should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 3
        task.frequency_metadata = {"unit": "weeks"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_months_1_is_not_frequent(self):
        """Custom interval of 1 month should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 1
        task.frequency_metadata = {"unit": "months"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_months_6_is_not_frequent(self):
        """Custom interval of 6 months should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 6
        task.frequency_metadata = {"unit": "months"}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_years_1_is_not_frequent(self):
        """Custom interval of 1 year should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 1
        task.frequency_metadata = {"unit": "years"}
        
        assert _is_frequent_recurrence(task) is False

    def test_once_is_not_frequent(self):
        """One-time tasks should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_ONCE
        task.frequency = None
        task.frequency_metadata = None
        
        assert _is_frequent_recurrence(task) is False

    def test_no_repeat_is_not_frequent(self):
        """No-repeat tasks should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_NO_REPEAT
        task.frequency = None
        task.frequency_metadata = None
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_missing_metadata_is_not_frequent(self):
        """Interval with missing metadata (defaults to days) should NOT be frequent when freq > 4."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 10  # > 4, so not frequent even defaulting to days
        task.frequency_metadata = None
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_empty_metadata_is_not_frequent(self):
        """Interval with empty metadata (defaults to days) should NOT be frequent when freq > 4."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 10  # > 4, so not frequent even defaulting to days
        task.frequency_metadata = {}
        
        assert _is_frequent_recurrence(task) is False

    def test_interval_unknown_unit_is_not_frequent(self):
        """Interval with unknown unit should NOT be frequent."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 2
        task.frequency_metadata = {"unit": "unknown"}
        
        assert _is_frequent_recurrence(task) is False


class TestGetRecurrenceAdvanceDays:
    """Tests for the _get_recurrence_advance_days helper function."""

    def test_once_returns_none(self):
        """One-time tasks should return None (no advance limit)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_ONCE
        task.frequency = None
        task.frequency_metadata = None
        
        assert _get_recurrence_advance_days(task) is None

    def test_no_repeat_returns_none(self):
        """No-repeat tasks should return None (no advance limit)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_NO_REPEAT
        task.frequency = None
        task.frequency_metadata = None
        
        assert _get_recurrence_advance_days(task) is None

    def test_interval_10_days_returns_5(self):
        """10 days recurrence → 5 days advance (half of 10)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 10
        task.frequency_metadata = {"unit": "days"}
        
        assert _get_recurrence_advance_days(task) == 5

    def test_interval_8_days_returns_4(self):
        """8 days recurrence → 4 days advance (half of 8)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 8
        task.frequency_metadata = {"unit": "days"}
        
        assert _get_recurrence_advance_days(task) == 4

    def test_interval_9_days_returns_4(self):
        """9 days recurrence → 4 days advance (floor of 4.5)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 9
        task.frequency_metadata = {"unit": "days"}
        
        assert _get_recurrence_advance_days(task) == 4

    def test_interval_20_days_returns_7(self):
        """20 days recurrence → 7 days advance (min of 10, 7)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 20
        task.frequency_metadata = {"unit": "days"}
        
        assert _get_recurrence_advance_days(task) == 7

    def test_interval_2_weeks_returns_7(self):
        """2 weeks (14 days) → 7 days advance (min of 7, 7)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 2
        task.frequency_metadata = {"unit": "weeks"}
        
        assert _get_recurrence_advance_days(task) == 7

    def test_interval_3_weeks_returns_7(self):
        """3 weeks (21 days) → 7 days advance (min of 10.5, 7)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 3
        task.frequency_metadata = {"unit": "weeks"}
        
        assert _get_recurrence_advance_days(task) == 7

    def test_interval_1_month_returns_7(self):
        """1 month (30 days) → 7 days advance (min of 15, 7)."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 1
        task.frequency_metadata = {"unit": "months"}
        
        assert _get_recurrence_advance_days(task) == 7

    def test_interval_6_months_returns_7(self):
        """6 months (180 days) → 7 days advance."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 6
        task.frequency_metadata = {"unit": "months"}
        
        assert _get_recurrence_advance_days(task) == 7

    def test_interval_1_year_returns_7(self):
        """1 year (365 days) → 7 days advance."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 1
        task.frequency_metadata = {"unit": "years"}
        
        assert _get_recurrence_advance_days(task) == 7

    def test_interval_missing_metadata_returns_5(self):
        """Missing metadata defaults to days unit, so 10 days -> 5 days advance."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 10
        task.frequency_metadata = None
        
        assert _get_recurrence_advance_days(task) == 5

    def test_interval_unknown_unit_returns_5(self):
        """Unknown unit defaults to days, so 10 days -> 5 days advance."""
        task = MagicMock()
        task.frequency_type = FREQUENCY_INTERVAL
        task.frequency = 10
        task.frequency_metadata = {"unit": "unknown"}
        
        assert _get_recurrence_advance_days(task) == 5


class TestUpcomingRecurrenceFiltering:
    """Integration tests for upcoming list recurrence filtering.
    
    These tests mock the entity's time methods directly to control time.
    """

    def _create_entity_with_mocked_time(self, coordinator, config_entry, hass, now):
        """Create an entity with mocked time methods."""
        entity = DonetickDateFilteredTasksList(coordinator, config_entry, hass, "upcoming")
        # Mock the time methods on the entity instance
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return entity

    def test_daily_task_excluded_from_upcoming(self, mock_hass, mock_config_entry):
        """Daily recurring tasks should be excluded from upcoming list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Daily Task",
            "frequencyType": "daily",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-16T10:00:00Z",  # Tomorrow
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 0

    def test_weekly_task_hidden_beyond_3_days(self, mock_hass, mock_config_entry):
        """Weekly recurring tasks should be hidden when more than 3 days from due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Weekly Task",
            "frequencyType": "weekly",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-20T10:00:00Z",  # 5 days from now (beyond 3-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 0

    def test_interval_1_week_hidden_beyond_3_days(self, mock_hass, mock_config_entry):
        """Custom interval of 1 week should be hidden when more than 3 days from due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Weekly Interval Task",
            "frequencyType": "interval",
            "frequency": 1,
            "frequencyMetadata": {"unit": "weeks"},
            "nextDueDate": "2024-06-20T10:00:00Z",  # 5 days from now (beyond 3-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 0

    def test_weekly_task_shown_within_3_days(self, mock_hass, mock_config_entry):
        """Weekly task should be shown when within 3 days of due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Weekly Task",
            "frequencyType": "weekly",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-18T10:00:00Z",  # 3 days from now (within 3-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_interval_1_week_shown_within_3_days(self, mock_hass, mock_config_entry):
        """Custom interval of 1 week should be shown when within 3 days of due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Weekly Interval Task",
            "frequencyType": "interval",
            "frequency": 1,
            "frequencyMetadata": {"unit": "weeks"},
            "nextDueDate": "2024-06-17T10:00:00Z",  # 2 days from now (within 3-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_interval_4_days_excluded_from_upcoming(self, mock_hass, mock_config_entry):
        """Custom interval of 4 days should be excluded from upcoming list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "4-Day Interval Task",
            "frequencyType": "interval",
            "frequency": 4,
            "frequencyMetadata": {"unit": "days"},
            "nextDueDate": "2024-06-17T10:00:00Z",
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 0

    def test_interval_10_days_shown_within_5_days_advance(self, mock_hass, mock_config_entry):
        """10-day interval task should be shown when within 5 days of due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "10-Day Interval Task",
            "frequencyType": "interval",
            "frequency": 10,
            "frequencyMetadata": {"unit": "days"},
            "nextDueDate": "2024-06-19T10:00:00Z",  # 4 days from now (within 5-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1
        assert filtered[0].id == 1

    def test_interval_10_days_hidden_beyond_5_days_advance(self, mock_hass, mock_config_entry):
        """10-day interval task should be hidden when more than 5 days from due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "10-Day Interval Task",
            "frequencyType": "interval",
            "frequency": 10,
            "frequencyMetadata": {"unit": "days"},
            "nextDueDate": "2024-06-26T10:00:00Z",  # 11 days from now (beyond 5-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 0

    def test_interval_2_weeks_shown_within_7_days(self, mock_hass, mock_config_entry):
        """2-week interval task should be shown when within 7 days of due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "2-Week Interval Task",
            "frequencyType": "interval",
            "frequency": 2,
            "frequencyMetadata": {"unit": "weeks"},
            "nextDueDate": "2024-06-21T10:00:00Z",  # 6 days from now (within 7-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_interval_2_weeks_hidden_beyond_7_days(self, mock_hass, mock_config_entry):
        """2-week interval task should be hidden when more than 7 days from due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "2-Week Interval Task",
            "frequencyType": "interval",
            "frequency": 2,
            "frequencyMetadata": {"unit": "weeks"},
            "nextDueDate": "2024-06-30T10:00:00Z",  # 15 days from now (beyond 7-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 0

    def test_monthly_task_shown_within_7_days(self, mock_hass, mock_config_entry):
        """Monthly interval task should be shown when within 7 days of due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Monthly Task",
            "frequencyType": "interval",
            "frequency": 1,
            "frequencyMetadata": {"unit": "months"},
            "nextDueDate": "2024-06-20T10:00:00Z",  # 5 days from now
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_yearly_task_shown_within_7_days(self, mock_hass, mock_config_entry):
        """Yearly interval task should be shown when within 7 days of due date."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "Yearly Task",
            "frequencyType": "interval",
            "frequency": 1,
            "frequencyMetadata": {"unit": "years"},
            "nextDueDate": "2024-06-18T10:00:00Z",  # 3 days from now
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_once_task_always_shown_in_upcoming(self, mock_hass, mock_config_entry):
        """One-time tasks should always be shown in upcoming (no advance limit)."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "One-Time Task",
            "frequencyType": "once",
            "frequency": None,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-20T10:00:00Z",  # 5 days from now (within 7-day upcoming window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_no_repeat_task_always_shown_in_upcoming(self, mock_hass, mock_config_entry):
        """No-repeat tasks should always be shown in upcoming (no advance limit)."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "No-Repeat Task",
            "frequencyType": "no_repeat",
            "frequency": None,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-21T10:00:00Z",  # 6 days from now (within 7-day upcoming window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_interval_5_days_shown_within_advance_window(self, mock_hass, mock_config_entry):
        """5-day interval (boundary case) should be shown with 2-day advance window."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz)
        
        task_data = {
            "id": 1,
            "name": "5-Day Interval Task",
            "frequencyType": "interval",
            "frequency": 5,
            "frequencyMetadata": {"unit": "days"},
            "nextDueDate": "2024-06-17T10:00:00Z",  # 2 days from now (within 2-day window)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        
        coordinator = MagicMock()
        coordinator.data = {1: task}
        coordinator.data_version = 1
        coordinator.tasks_list = [task]
        
        entity = self._create_entity_with_mocked_time(coordinator, mock_config_entry, mock_hass, now)
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1


class TestDonetickTimeOfDayTasksList:
    """Tests for DonetickTimeOfDayTasksList entity."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config.time_zone = "America/New_York"
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry with time-of-day cutoffs."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {
            CONF_MORNING_CUTOFF: "12:00",  # Morning ends at noon
            CONF_AFTERNOON_CUTOFF: "17:00",  # Afternoon ends at 5 PM
        }
        entry.options = {}
        return entry

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = {}
        coordinator.data_version = 1
        coordinator.tasks_list = []
        return coordinator

    def _create_entity_with_mocked_time(self, coordinator, config_entry, hass, now, list_type="morning"):
        """Create entity with mocked time."""
        entity = DonetickTimeOfDayTasksList(coordinator, config_entry, hass, list_type, member=None)
        entity._get_local_now = lambda: now
        entity._get_local_today_start = lambda: now.replace(hour=0, minute=0, second=0, microsecond=0)
        entity._get_local_today_end = lambda: now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return entity

    def test_morning_task_in_morning_list(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task due at 9 AM should appear in morning list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)  # 8 AM
        
        # Task due at 9 AM local time
        task_data = {
            "id": 1,
            "name": "Morning Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T13:00:00Z",  # 9 AM EDT (UTC-4)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "morning")
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1
        assert filtered[0].id == 1

    def test_afternoon_task_in_afternoon_list(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task due at 2 PM should appear in afternoon list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        
        # Task due at 2 PM local time (14:00 EDT = 18:00 UTC)
        task_data = {
            "id": 1,
            "name": "Afternoon Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T18:00:00Z",  # 2 PM EDT
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "afternoon")
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_evening_task_in_evening_list(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task due at 7 PM should appear in evening list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        
        # Task due at 7 PM local time (19:00 EDT = 23:00 UTC)
        task_data = {
            "id": 1,
            "name": "Evening Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T23:00:00Z",  # 7 PM EDT
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "evening")
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_all_day_task_in_all_day_list(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task due at 23:59:00 (date-only) should appear in all-day list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        
        # All-day task (23:59:00 local time)
        # 23:59 EDT = 03:59 next day UTC
        task_data = {
            "id": 1,
            "name": "All Day Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-16T03:59:00Z",  # 11:59 PM EDT (next day UTC)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "all_day")
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_past_due_task_in_past_due_list(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task that was due earlier today should appear in past_due list."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 14, 0, 0, tzinfo=tz)  # 2 PM
        
        # Task was due at 9 AM local time (already past)
        task_data = {
            "id": 1,
            "name": "Past Due Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T13:00:00Z",  # 9 AM EDT (UTC-4)
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "past_due")
        filtered = entity._filter_tasks([task])
        
        assert len(filtered) == 1

    def test_past_due_task_also_in_morning_list(self, mock_hass, mock_config_entry, mock_coordinator):
        """Past due morning task SHOULD appear in morning list (and past due list)."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 14, 0, 0, tzinfo=tz)  # 2 PM (past morning)
        
        # Task was due at 9 AM local time (past due)
        task_data = {
            "id": 1,
            "name": "Past Due Morning Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T13:00:00Z",  # 9 AM EDT
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "morning")
        filtered = entity._filter_tasks([task])
        
        # Past due task SHOULD be in morning list (appears in both)
        assert len(filtered) == 1

    def test_task_due_tomorrow_not_in_today_lists(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task due tomorrow should not appear in any time-of-day lists."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        
        # Task due tomorrow
        task_data = {
            "id": 1,
            "name": "Tomorrow Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-16T13:00:00Z",  # Tomorrow
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        for list_type in ["past_due", "morning", "afternoon", "evening", "all_day"]:
            entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, list_type)
            filtered = entity._filter_tasks([task])
            assert len(filtered) == 0, f"Task should not appear in {list_type} list"

    def test_assignee_filter(self, mock_hass, mock_config_entry, mock_coordinator):
        """Tasks should be filtered by assignee."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        
        # Task assigned to user 1
        task_data = {
            "id": 1,
            "name": "Assigned Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T13:00:00Z",  # 9 AM EDT
            "isActive": True,
            "assignedTo": 1,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        # Unassigned list should NOT see this task
        entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "morning")
        filtered = entity._filter_tasks([task])
        assert len(filtered) == 0

    def test_cutoff_boundary_at_morning(self, mock_hass, mock_config_entry, mock_coordinator):
        """Task exactly at morning cutoff (12:00) should be in afternoon, not morning."""
        tz = ZoneInfo("America/New_York")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        
        # Task due exactly at noon (12:00 EDT = 16:00 UTC)
        task_data = {
            "id": 1,
            "name": "Noon Task",
            "frequencyType": "once",
            "frequency": 1,
            "frequencyMetadata": None,
            "nextDueDate": "2024-06-15T16:00:00Z",  # 12:00 PM EDT
            "isActive": True,
            "assignedTo": None,
        }
        task = DonetickTask.from_json(task_data)
        mock_coordinator.tasks_list = [task]
        mock_coordinator.data = {1: task}
        
        # Should NOT be in morning (morning is < 12:00)
        morning_entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "morning")
        assert len(morning_entity._filter_tasks([task])) == 0
        
        # Should be in afternoon (12:00 <= time < 17:00)
        afternoon_entity = self._create_entity_with_mocked_time(mock_coordinator, mock_config_entry, mock_hass, now, "afternoon")
        assert len(afternoon_entity._filter_tasks([task])) == 1


class TestGetCompletionUserId:
    """Tests for _get_completion_user_id method."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        return entry

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = {}
        coordinator.data_version = 1
        coordinator.tasks_list = []
        return coordinator

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    @pytest.fixture
    def sample_member(self, sample_circle_member_json):
        """Create sample member."""
        return DonetickMember.from_json(sample_circle_member_json)

    @pytest.fixture
    def sample_todo_item(self):
        """Create sample TodoItem for testing."""
        return TodoItem(
            uid="123--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )

    @pytest.mark.asyncio
    async def test_member_specific_list_returns_member_user_id(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_member, sample_todo_item
    ):
        """Test that member-specific list returns the member's user_id."""
        entity = DonetickAssigneeTasksList(mock_coordinator, mock_config_entry, sample_member, mock_hass)
        
        result = await entity._get_completion_user_id(mock_client, sample_todo_item)
        
        assert result == sample_member.user_id

    @pytest.mark.asyncio
    async def test_unassigned_list_with_member_none_falls_through(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that unassigned list (_member=None) looks up task's original assignee."""
        # Create a task with an assigned user
        task = DonetickTask.from_json(sample_chore_json)
        mock_coordinator.data = {task.id: task}
        
        # Create unassigned date-filtered list (member=None)
        entity = DonetickDateFilteredTasksList(
            mock_coordinator, mock_config_entry, mock_hass, "past_due", member=None
        )
        
        # Create item with matching task ID
        item = TodoItem(
            uid=f"{task.id}--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        # Should return the task's original assignee
        assert result == task.assigned_to

    @pytest.mark.asyncio
    async def test_unassigned_list_task_without_assignee_returns_none(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that unassigned list with unassigned task returns None."""
        # Create a task without an assigned user
        task_data = sample_chore_json.copy()
        task_data["assignedTo"] = None
        task = DonetickTask.from_json(task_data)
        mock_coordinator.data = {task.id: task}
        
        # Create unassigned date-filtered list (member=None)
        entity = DonetickDateFilteredTasksList(
            mock_coordinator, mock_config_entry, mock_hass, "past_due", member=None
        )
        
        # Create item with matching task ID
        item = TodoItem(
            uid=f"{task.id}--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        # Should return None since task has no assignee
        assert result is None

    @pytest.mark.asyncio
    async def test_all_tasks_list_finds_task_original_assignee(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that All Tasks list looks up task's original assignee."""
        task = DonetickTask.from_json(sample_chore_json)
        mock_coordinator.data = {task.id: task}
        
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        item = TodoItem(
            uid=f"{task.id}--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result == task.assigned_to

    @pytest.mark.asyncio
    async def test_all_tasks_list_unassigned_task_returns_none(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that All Tasks list returns None for unassigned tasks."""
        task_data = sample_chore_json.copy()
        task_data["assignedTo"] = None
        task = DonetickTask.from_json(task_data)
        mock_coordinator.data = {task.id: task}
        
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        item = TodoItem(
            uid=f"{task.id}--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_task_not_found_in_coordinator_data_returns_none(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client
    ):
        """Test that when task is not found in coordinator data, returns None."""
        mock_coordinator.data = {}  # Empty data
        
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        item = TodoItem(
            uid="999--2024-01-15T18:00:00Z",  # Non-existent task ID
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_coordinator_data_none_returns_none(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client
    ):
        """Test that when coordinator.data is None, returns None."""
        mock_coordinator.data = None
        
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        item = TodoItem(
            uid="123--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_time_of_day_list_with_member_returns_member_id(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_member
    ):
        """Test that time-of-day list with member returns member's user_id."""
        mock_config_entry.data[CONF_MORNING_CUTOFF] = DEFAULT_MORNING_CUTOFF
        mock_config_entry.data[CONF_AFTERNOON_CUTOFF] = DEFAULT_AFTERNOON_CUTOFF
        
        entity = DonetickTimeOfDayTasksList(
            mock_coordinator, mock_config_entry, mock_hass, "morning", member=sample_member
        )
        
        item = TodoItem(
            uid="123--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result == sample_member.user_id

    @pytest.mark.asyncio
    async def test_time_of_day_list_without_member_looks_up_task(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that time-of-day list without member looks up task's assignee."""
        mock_config_entry.data[CONF_MORNING_CUTOFF] = DEFAULT_MORNING_CUTOFF
        mock_config_entry.data[CONF_AFTERNOON_CUTOFF] = DEFAULT_AFTERNOON_CUTOFF
        
        task = DonetickTask.from_json(sample_chore_json)
        mock_coordinator.data = {task.id: task}
        
        entity = DonetickTimeOfDayTasksList(
            mock_coordinator, mock_config_entry, mock_hass, "morning", member=None
        )
        
        item = TodoItem(
            uid=f"{task.id}--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result == task.assigned_to

    @pytest.mark.asyncio
    async def test_time_of_day_with_unassigned_list_includes_unassigned_tasks(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_member, sample_chore_json
    ):
        """Test that TimeOfDayWithUnassigned list with member uses member's ID for completion."""
        mock_config_entry.data[CONF_MORNING_CUTOFF] = DEFAULT_MORNING_CUTOFF
        mock_config_entry.data[CONF_AFTERNOON_CUTOFF] = DEFAULT_AFTERNOON_CUTOFF
        
        # Even though task might be unassigned, completing from a member's "with unassigned" 
        # list should use that member's ID
        entity = DonetickTimeOfDayWithUnassignedList(
            mock_coordinator, mock_config_entry, mock_hass, "morning", member=sample_member
        )
        
        item = TodoItem(
            uid="123--2024-01-15T18:00:00Z",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result == sample_member.user_id

    @pytest.mark.asyncio
    async def test_multiple_tasks_finds_correct_one(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that correct task is found when multiple tasks exist."""
        # Create multiple tasks with different IDs and assignees
        task1_data = sample_chore_json.copy()
        task1_data["id"] = 100
        task1_data["assignedTo"] = 42
        task1 = DonetickTask.from_json(task1_data)
        
        task2_data = sample_chore_json.copy()
        task2_data["id"] = 200
        task2_data["assignedTo"] = 99
        task2 = DonetickTask.from_json(task2_data)
        
        task3_data = sample_chore_json.copy()
        task3_data["id"] = 300
        task3_data["assignedTo"] = None
        task3 = DonetickTask.from_json(task3_data)
        
        mock_coordinator.data = {task1.id: task1, task2.id: task2, task3.id: task3}
        
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        # Test finding task2
        item = TodoItem(
            uid="200--2024-01-15T18:00:00Z",
            summary="Task 2",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result == 99  # task2's assignee

    @pytest.mark.asyncio
    async def test_uid_parsing_with_complex_date(
        self, mock_coordinator, mock_config_entry, mock_hass, mock_client, sample_chore_json
    ):
        """Test that task ID is correctly parsed from UID with complex date."""
        task = DonetickTask.from_json(sample_chore_json)
        mock_coordinator.data = {task.id: task}
        
        entity = DonetickAllTasksList(mock_coordinator, mock_config_entry, mock_hass)
        
        # UID with timezone offset
        item = TodoItem(
            uid=f"{task.id}--2024-01-15T18:00:00+05:30",
            summary="Test Task",
            status=TodoItemStatus.COMPLETED,
        )
        
        result = await entity._get_completion_user_id(mock_client, item)
        
        assert result == task.assigned_to