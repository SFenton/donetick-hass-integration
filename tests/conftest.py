"""Fixtures for Donetick integration tests."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientSession

# Add custom_components to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.model import (
    DonetickTask,
    DonetickThing,
    DonetickMember,
    DonetickLabel,
    DonetickSubTask,
    DonetickAssignee,
)
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


# ==================== API Response Fixtures ====================
# Based on Donetick API structure from https://github.com/donetick/donetick

@pytest.fixture
def jwt_login_response():
    """JWT login response from /api/v1/auth/login."""
    expire = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    return {
        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test_token",
        "expire": expire,
    }


@pytest.fixture
def jwt_login_mfa_response():
    """JWT login response when MFA is required."""
    return {
        "mfaRequired": True,
        "sessionToken": "mfa_session_token_123",
    }


@pytest.fixture
def jwt_refresh_response():
    """JWT refresh response from /api/v1/auth/refresh."""
    expire = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    return {
        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.refreshed_token",
        "expire": expire,
    }


@pytest.fixture
def sample_chore_json():
    """Sample chore/task JSON from Donetick API (internal API format).
    
    Based on internal/chore/model/model.go Chore struct.
    """
    return {
        "id": 1,
        "name": "Clean Kitchen",
        "description": "Wipe down counters and clean dishes",
        "frequencyType": "weekly",
        "frequency": 1,
        "frequencyMetadata": {"days": ["monday", "friday"]},
        "nextDueDate": "2025-01-15T18:00:00Z",
        "assignedTo": 42,
        "assignees": [{"userId": 42}, {"userId": 43}],
        "assignStrategy": "round_robin",
        "isActive": True,
        "isRolling": False,
        "notification": True,
        "notificationMetadata": {"predue": True, "templates": []},
        "labels": "cleaning,kitchen",
        "labelsV2": [{"id": 1, "name": "Cleaning", "color": "#ff0000"}],
        "circleId": 100,
        "createdBy": 42,
        "createdAt": "2024-01-01T12:00:00Z",
        "updatedAt": "2025-01-10T08:00:00Z",
        "updatedBy": 42,
        "status": 0,
        "priority": 2,
        "points": 10,
        "completionWindow": 3600,
        "requireApproval": False,
        "isPrivate": False,
        "subTasks": [
            {
                "id": 101,
                "name": "Wipe counters",
                "orderId": 1,
                "completedAt": None,
                "completedBy": 0,
                "parentId": 1,
            },
            {
                "id": 102,
                "name": "Clean dishes",
                "orderId": 2,
                "completedAt": "2025-01-10T09:00:00Z",
                "completedBy": 42,
                "parentId": 1,
            },
        ],
    }


@pytest.fixture
def sample_chore_lite_json():
    """Sample chore JSON from eAPI (ChoreLiteReq format).
    
    Based on internal/chore/model/model.go ChoreLiteReq struct.
    eAPI returns the full Chore model but accepts ChoreLiteReq for create/update.
    """
    return {
        "id": 2,
        "name": "Take out trash",
        "description": "Weekly trash day",
        "frequencyType": "once",
        "frequency": 0,
        "nextDueDate": "2025-01-16T08:00:00Z",
        "assignedTo": 42,
        "isActive": True,
        "status": 0,
        "priority": 1,
    }


@pytest.fixture
def sample_chores_list(sample_chore_json, sample_chore_lite_json):
    """List of chores from API."""
    return [sample_chore_json, sample_chore_lite_json]


@pytest.fixture
def sample_chores_list_internal_api(sample_chores_list):
    """Chores list wrapped in 'res' for internal API format."""
    return {"res": sample_chores_list}


@pytest.fixture
def sample_circle_member_json():
    """Sample circle member JSON from /api/v1/circles/members.
    
    Based on internal/circle/model/model.go UserCircleDetail struct.
    """
    return {
        "id": 1,
        "userId": 42,
        "circleId": 100,
        "role": "admin",
        "isActive": True,
        "username": "johndoe",
        "displayName": "John Doe",
        "image": "https://example.com/avatar.jpg",
        "points": 150,
        "pointsRedeemed": 50,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2025-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_circle_members_list(sample_circle_member_json):
    """List of circle members."""
    return [
        sample_circle_member_json,
        {
            "id": 2,
            "userId": 43,
            "circleId": 100,
            "role": "member",
            "isActive": True,
            "username": "janedoe",
            "displayName": "Jane Doe",
            "image": None,
            "points": 100,
            "pointsRedeemed": 25,
            "createdAt": "2024-02-01T00:00:00Z",
            "updatedAt": "2025-01-05T00:00:00Z",
        },
        {
            "id": 3,
            "userId": 44,
            "circleId": 100,
            "role": "member",
            "isActive": False,  # Inactive member
            "username": "inactive_user",
            "displayName": "Inactive User",
            "image": None,
            "points": 0,
            "pointsRedeemed": 0,
            "createdAt": "2024-03-01T00:00:00Z",
            "updatedAt": "2024-06-01T00:00:00Z",
        },
    ]


@pytest.fixture
def sample_thing_json():
    """Sample thing JSON from Donetick API.
    
    Based on internal/thing/model/model.go Thing struct.
    """
    return {
        "id": 1,
        "userID": 42,
        "circleId": 100,
        "name": "Kitchen Light",
        "state": "on",
        "type": "boolean",
        "thingChores": [],
        "updatedAt": "2025-01-10T08:00:00Z",
        "createdAt": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_things_list(sample_thing_json):
    """List of things from API."""
    return [
        sample_thing_json,
        {
            "id": 2,
            "userID": 42,
            "circleId": 100,
            "name": "Room Temperature",
            "state": "72",
            "type": "number",
            "thingChores": [],
            "updatedAt": "2025-01-10T08:00:00Z",
            "createdAt": "2024-01-01T00:00:00Z",
        },
        {
            "id": 3,
            "userID": 42,
            "circleId": 100,
            "name": "Note",
            "state": "Remember to buy milk",
            "type": "text",
            "thingChores": [],
            "updatedAt": "2025-01-10T08:00:00Z",
            "createdAt": "2024-01-01T00:00:00Z",
        },
    ]


@pytest.fixture
def sample_webhook_payload_task_completed():
    """Webhook payload for task.completed event.
    
    Based on internal/events/producer.go Event and ChoreData structs.
    """
    return {
        "type": "task.completed",
        "timestamp": "2025-01-10T10:00:00Z",
        "data": {
            "chore": {
                "id": 1,
                "name": "Clean Kitchen",
                "nextDueDate": "2025-01-17T18:00:00Z",
            },
            "username": "johndoe",
            "display_name": "John Doe",
        },
    }


@pytest.fixture
def sample_webhook_payload_task_skipped():
    """Webhook payload for task.skipped event."""
    return {
        "type": "task.skipped",
        "timestamp": "2025-01-10T10:00:00Z",
        "data": {
            "chore": {
                "id": 1,
                "name": "Clean Kitchen",
                "nextDueDate": "2025-01-22T18:00:00Z",
            },
            "username": "johndoe",
            "display_name": "John Doe",
        },
    }


@pytest.fixture
def sample_webhook_payload_thing_changed():
    """Webhook payload for thing.changed event."""
    return {
        "type": "thing.changed",
        "timestamp": "2025-01-10T10:00:00Z",
        "data": {
            "id": 1,
            "name": "Kitchen Light",
            "type": "boolean",
            "from_state": "off",
            "to_state": "on",
        },
    }


@pytest.fixture
def sample_webhook_payload_subtask_completed():
    """Webhook payload for subtask.completed event."""
    return {
        "type": "subtask.completed",
        "timestamp": "2025-01-10T10:00:00Z",
        "data": {
            "id": 101,
            "choreId": 1,
            "completedAt": "2025-01-10T10:00:00Z",
            "completedBy": 42,
        },
    }


# ==================== Model Fixtures ====================

@pytest.fixture
def donetick_task(sample_chore_json):
    """DonetickTask model instance."""
    return DonetickTask.from_json(sample_chore_json)


@pytest.fixture
def donetick_member(sample_circle_member_json):
    """DonetickMember model instance."""
    return DonetickMember.from_json(sample_circle_member_json)


@pytest.fixture
def donetick_thing(sample_thing_json):
    """DonetickThing model instance."""
    return DonetickThing.from_json(sample_thing_json)


# ==================== Config Entry Fixtures ====================

@pytest.fixture
def config_entry_data_jwt():
    """Config entry data for JWT authentication."""
    return {
        CONF_URL: "https://donetick.example.com",
        CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
        CONF_SHOW_DUE_IN: 7,
        CONF_REFRESH_INTERVAL: DEFAULT_REFRESH_INTERVAL,  # 900 seconds (15 minutes)
        CONF_CREATE_UNIFIED_LIST: True,
        CONF_CREATE_ASSIGNEE_LISTS: False,
        CONF_CREATE_DATE_FILTERED_LISTS: False,
    }


@pytest.fixture
def config_entry_data_api_key():
    """Config entry data for API key authentication."""
    return {
        CONF_URL: "https://donetick.example.com",
        CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
        CONF_TOKEN: "test_api_token_12345",
        CONF_SHOW_DUE_IN: 7,
        CONF_REFRESH_INTERVAL: DEFAULT_REFRESH_INTERVAL,
        CONF_CREATE_UNIFIED_LIST: True,
        CONF_CREATE_ASSIGNEE_LISTS: False,
        CONF_CREATE_DATE_FILTERED_LISTS: False,
    }


@pytest.fixture
def config_entry_data_all_lists():
    """Config entry data with all list types enabled."""
    return {
        CONF_URL: "https://donetick.example.com",
        CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
        CONF_SHOW_DUE_IN: 7,
        CONF_REFRESH_INTERVAL: 300,  # 5 minutes
        CONF_CREATE_UNIFIED_LIST: True,
        CONF_CREATE_ASSIGNEE_LISTS: True,
        CONF_CREATE_DATE_FILTERED_LISTS: True,
    }


@pytest.fixture
def mock_config_entry(config_entry_data_jwt):
    """Mock ConfigEntry for tests."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_12345"
    entry.data = config_entry_data_jwt
    entry.options = {}
    entry.title = "Donetick"
    return entry


@pytest.fixture
def mock_config_entry_api_key(config_entry_data_api_key):
    """Mock ConfigEntry for API key auth tests."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_api_key"
    entry.data = config_entry_data_api_key
    entry.options = {}
    entry.title = "Donetick"
    return entry


# ==================== Mock Session Fixtures ====================

@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp ClientSession."""
    session = AsyncMock(spec=ClientSession)
    return session


@pytest.fixture
def mock_response_factory():
    """Factory for creating mock aiohttp responses."""
    def _create_response(status=200, json_data=None, text=None):
        response = AsyncMock()
        response.status = status
        response.json = AsyncMock(return_value=json_data or {})
        response.text = AsyncMock(return_value=text or "")
        response.raise_for_status = MagicMock()
        if status >= 400:
            from aiohttp import ClientResponseError
            response.raise_for_status.side_effect = ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=status,
            )
        return response
    return _create_response


# ==================== Home Assistant Fixtures ====================

@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    hass.config = MagicMock()
    hass.config.time_zone = "America/New_York"
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.config_entries = MagicMock()
    hass.states = MagicMock()
    hass.helpers = MagicMock()
    hass.services = MagicMock()
    return hass
