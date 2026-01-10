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
)
from custom_components.donetick.model import DonetickTask, DonetickMember
from custom_components.donetick.const import (
    DOMAIN,
    CONF_URL,
    CONF_AUTH_TYPE,
    AUTH_TYPE_JWT,
    CONF_SHOW_DUE_IN,
    CONF_REFRESH_INTERVAL,
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
        # Create a task due in 1 hour
        now = datetime.now(ZoneInfo("America/New_York"))
        due_soon = now + timedelta(hours=1)
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
        
        # Create due_today entity
        entity = DonetickDateFilteredTasksList(coordinator, mock_config_entry, mock_hass, "due_today")
        
        # Initially, task should be in due_today
        items = entity.todo_items
        assert len(items) == 1
        
        # Simulate time passing - task now past due
        past = now - timedelta(hours=1)
        task_data["nextDueDate"] = past.isoformat()
        task = DonetickTask.from_json(task_data)
        coordinator.tasks_list = [task]
        coordinator.data = {1: task}
        
        # Now check due_today - task should have migrated out
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
