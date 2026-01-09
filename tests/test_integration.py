"""Integration tests for Donetick Home Assistant integration.

These tests verify component behavior with different configuration options
and test interactions between multiple components.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.api import DonetickApiClient, AuthenticationError
from custom_components.donetick.model import DonetickTask, DonetickThing, DonetickMember
from custom_components.donetick.todo import DonetickTaskCoordinator
from custom_components.donetick.const import (
    DOMAIN,
    CONF_URL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_AUTH_TYPE,
    AUTH_TYPE_JWT,
    AUTH_TYPE_API_KEY,
    CONF_SHOW_DUE_IN,
    CONF_REFRESH_INTERVAL,
    CONF_CREATE_UNIFIED_LIST,
    CONF_CREATE_ASSIGNEE_LISTS,
    CONF_CREATE_DATE_FILTERED_LISTS,
    DEFAULT_REFRESH_INTERVAL,
)


def make_thing_json(id: int, name: str, type: str, state: str) -> dict:
    """Helper to create valid thing JSON with required fields."""
    return {
        "id": id,
        "userID": 42,
        "circleId": 100,
        "name": name,
        "state": state,
        "type": type,
    }


class TestAuthenticationFlows:
    """Integration tests for authentication flows."""

    @pytest.fixture
    def mock_session(self):
        """Create mock aiohttp session."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_jwt_auth_flow_complete(self, mock_session, jwt_login_response, sample_chores_list_internal_api):
        """Test complete JWT authentication flow: login -> fetch tasks."""
        # Login response
        mock_login_response = AsyncMock()
        mock_login_response.status = 200
        mock_login_response.json = AsyncMock(return_value=jwt_login_response)
        mock_login_response.raise_for_status = MagicMock()
        mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
        mock_login_response.__aexit__ = AsyncMock(return_value=None)
        
        # Tasks response
        mock_tasks_response = AsyncMock()
        mock_tasks_response.status = 200
        mock_tasks_response.json = AsyncMock(return_value=sample_chores_list_internal_api)
        mock_tasks_response.raise_for_status = MagicMock()
        mock_tasks_response.__aenter__ = AsyncMock(return_value=mock_tasks_response)
        mock_tasks_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session.post = MagicMock(return_value=mock_login_response)
        mock_session.request = MagicMock(return_value=mock_tasks_response)
        
        # Create client and fetch tasks
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_session,
            username="testuser",
            password="testpass",
            auth_type=AUTH_TYPE_JWT,
        )
        
        tasks = await client.async_get_tasks()
        
        # Verify tasks were fetched
        assert len(tasks) == 2
        assert tasks[0].name == "Clean Kitchen"


class TestCoordinatorWithDifferentConfigs:
    """Integration tests for coordinator behavior with different configs."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {}}
        return hass

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_coordinator_refresh_interval_default(self, mock_hass, mock_client):
        """Test coordinator uses default refresh interval."""
        coordinator = DonetickTaskCoordinator(
            mock_hass,
            mock_client,
            update_interval=timedelta(seconds=DEFAULT_REFRESH_INTERVAL),
        )
        
        assert coordinator.update_interval == timedelta(seconds=DEFAULT_REFRESH_INTERVAL)

    @pytest.mark.asyncio
    async def test_coordinator_refresh_interval_custom(self, mock_hass, mock_client):
        """Test coordinator uses custom refresh interval."""
        custom_interval = 60  # 1 minute
        coordinator = DonetickTaskCoordinator(
            mock_hass,
            mock_client,
            update_interval=timedelta(seconds=custom_interval),
        )
        
        assert coordinator.update_interval == timedelta(seconds=custom_interval)

    @pytest.mark.asyncio
    async def test_coordinator_handles_empty_response(self, mock_hass, mock_client):
        """Test coordinator handles empty task list."""
        mock_client.async_get_tasks = AsyncMock(return_value=[])
        
        coordinator = DonetickTaskCoordinator(
            mock_hass,
            mock_client,
            update_interval=timedelta(seconds=300),
        )
        
        result = await coordinator._async_update_data()
        
        assert result == {}

    @pytest.mark.asyncio
    async def test_coordinator_handles_api_error(self, mock_hass, mock_client):
        """Test coordinator handles API errors gracefully."""
        mock_client.async_get_tasks = AsyncMock(side_effect=Exception("API Error"))
        
        coordinator = DonetickTaskCoordinator(
            mock_hass,
            mock_client,
            update_interval=timedelta(seconds=300),
        )
        
        with pytest.raises(Exception):
            await coordinator._async_update_data()


class TestTaskFilteringBehavior:
    """Integration tests for task filtering with different configurations."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        return hass

    @pytest.fixture
    def sample_tasks(self, sample_chore_json):
        """Create sample tasks with different due dates."""
        tasks = []
        
        # Task due today
        today_json = sample_chore_json.copy()
        today_json["id"] = 1
        today_json["name"] = "Due Today"
        today_json["nextDueDate"] = datetime.now(timezone.utc).isoformat()
        tasks.append(DonetickTask.from_json(today_json))
        
        # Task due in 3 days
        soon_json = sample_chore_json.copy()
        soon_json["id"] = 2
        soon_json["name"] = "Due Soon"
        soon_json["nextDueDate"] = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        tasks.append(DonetickTask.from_json(soon_json))
        
        # Task due in 10 days
        later_json = sample_chore_json.copy()
        later_json["id"] = 3
        later_json["name"] = "Due Later"
        later_json["nextDueDate"] = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        tasks.append(DonetickTask.from_json(later_json))
        
        return tasks


class TestThingEntitiesWithDifferentTypes:
    """Integration tests for thing entities with different types."""

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    def test_boolean_thing_type_detection(self, mock_client):
        """Test boolean thing creates switch entity."""
        from custom_components.donetick.thing import DonetickThingSwitch
        
        thing = DonetickThing.from_json(make_thing_json(1, "Light", "boolean", "on"))
        
        entity = DonetickThingSwitch(mock_client, thing)
        
        assert entity.is_on is True

    def test_number_thing_type_detection(self, mock_client):
        """Test number thing creates number entity."""
        from custom_components.donetick.thing import DonetickThingNumber
        
        thing = DonetickThing.from_json(make_thing_json(2, "Temperature", "number", "72"))
        
        entity = DonetickThingNumber(mock_client, thing)
        
        assert entity.native_value == 72.0

    def test_text_thing_type_detection(self, mock_client):
        """Test text thing creates text entity."""
        from custom_components.donetick.thing import DonetickThingText
        
        thing = DonetickThing.from_json(make_thing_json(3, "Message", "text", "Hello"))
        
        entity = DonetickThingText(mock_client, thing)
        
        assert entity.native_value == "Hello"


class TestModelParsing:
    """Integration tests for model parsing with different data formats."""

    def test_task_parsing_with_all_fields(self, sample_chore_json):
        """Test parsing task with all fields populated."""
        task = DonetickTask.from_json(sample_chore_json)
        
        assert task.id == 1
        assert task.name == "Clean Kitchen"
        assert task.assignees is not None
        assert len(task.assignees) == 2
        assert task.labels_v2 is not None
        assert len(task.labels_v2) == 1
        assert task.sub_tasks is not None
        assert len(task.sub_tasks) == 2

    def test_task_parsing_minimal(self):
        """Test parsing task with minimal fields."""
        minimal_json = {
            "id": 999,
            "name": "Minimal Task",
        }
        task = DonetickTask.from_json(minimal_json)
        
        assert task.id == 999
        assert task.name == "Minimal Task"
        assert task.next_due_date is None
        assert task.assignees is None

    def test_member_parsing_complete(self, sample_circle_member_json):
        """Test parsing member with all fields."""
        member = DonetickMember.from_json(sample_circle_member_json)
        
        assert member.id == 1
        assert member.user_id == 42
        assert member.username == "johndoe"
        assert member.display_name == "John Doe"

    def test_thing_parsing_all_types(self):
        """Test parsing things of all types."""
        types_and_states = [
            ("boolean", "on"),
            ("boolean", "off"),
            ("number", "42"),
            ("number", "3.14"),
            ("text", "Hello World"),
            ("action", "triggered"),
        ]
        
        for thing_type, state in types_and_states:
            thing = DonetickThing.from_json(make_thing_json(1, "Test", thing_type, state))
            assert thing.type == thing_type
            assert thing.state == state
