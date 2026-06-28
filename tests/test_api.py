"""Unit tests for custom_components.donetick.api module."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch
import aiohttp

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.api import DonetickApiClient, AuthenticationError
from custom_components.donetick.const import AUTH_TYPE_JWT, AUTH_TYPE_API_KEY
from custom_components.donetick.model import DonetickTask, DonetickThing, DonetickMember


class TestDonetickApiClientInit:
    """Tests for DonetickApiClient initialization."""

    def test_init_jwt_auth(self, mock_aiohttp_session):
        """Test initialization with JWT auth."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        
        assert client._base_url == "https://donetick.example.com"
        assert client._username == "testuser"
        assert client._password == "testpass"
        assert client._auth_type == AUTH_TYPE_JWT
        assert client.is_jwt_auth is True

    def test_init_api_key_auth(self, mock_aiohttp_session):
        """Test initialization with API key auth."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_API_KEY,
            api_token="my_api_key",
        )
        
        assert client._api_token == "my_api_key"
        assert client._auth_type == AUTH_TYPE_API_KEY
        assert client.is_jwt_auth is False

    def test_init_strips_trailing_slash(self, mock_aiohttp_session):
        """Test that trailing slash is stripped from base_url."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com/",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        
        assert client._base_url == "https://donetick.example.com"


class TestDonetickApiClientJWTAuth:
    """Tests for JWT authentication flow."""

    @pytest.fixture
    def jwt_client(self, mock_aiohttp_session):
        """Create JWT auth client."""
        return DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )

    @pytest.mark.asyncio
    async def test_authenticate_success(self, jwt_client, jwt_login_response):
        """Test successful JWT authentication."""
        # Create a proper async context manager mock
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=jwt_login_response)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        jwt_client._session.post = MagicMock(return_value=mock_response)
        
        await jwt_client._authenticate()
        
        assert jwt_client._jwt_token == jwt_login_response["token"]
        assert jwt_client._jwt_expiry is not None

    @pytest.mark.asyncio
    async def test_authenticate_mfa_required_raises(self, jwt_client, jwt_login_mfa_response):
        """Test that MFA required response raises an exception."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=jwt_login_mfa_response)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        jwt_client._session.post = MagicMock(return_value=mock_response)
        
        with pytest.raises(AuthenticationError) as exc_info:
            await jwt_client._authenticate()
        
        assert "MFA" in str(exc_info.value) or "not supported" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_token_refresh_when_expired(self, jwt_client, jwt_refresh_response):
        """Test token refresh when token is expired."""
        # Set expired token
        jwt_client._jwt_token = "expired_token"
        jwt_client._jwt_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=jwt_refresh_response)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        jwt_client._session.get = MagicMock(return_value=mock_response)
        
        await jwt_client._refresh_token()
        
        assert jwt_client._jwt_token == jwt_refresh_response["token"]

    @pytest.mark.asyncio
    async def test_ensure_authenticated_when_no_token(self, jwt_client, jwt_login_response):
        """Test _ensure_authenticated when no token exists."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=jwt_login_response)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        jwt_client._session.post = MagicMock(return_value=mock_response)
        
        await jwt_client._ensure_authenticated()
        
        assert jwt_client._jwt_token is not None

    @pytest.mark.asyncio
    async def test_ensure_authenticated_when_token_valid(self, jwt_client):
        """Test _ensure_authenticated when token is still valid."""
        jwt_client._jwt_token = "valid_token"
        jwt_client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        
        # Should not call any auth methods
        jwt_client._session.post = MagicMock()
        jwt_client._session.get = MagicMock()
        
        await jwt_client._ensure_authenticated()
        
        jwt_client._session.post.assert_not_called()
        jwt_client._session.get.assert_not_called()


class TestDonetickApiClientAPIKeyAuth:
    """Tests for API key authentication."""

    @pytest.fixture
    def api_key_client(self, mock_aiohttp_session):
        """Create API key auth client."""
        return DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_API_KEY,
            api_token="test_api_key",
        )

    def test_get_headers_includes_secretkey(self, api_key_client):
        """Test that API key is included in headers."""
        headers = api_key_client._get_headers()
        
        assert "secretkey" in headers
        assert headers["secretkey"] == "test_api_key"


class TestDonetickApiClientRequests:
    """Tests for general request handling."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_request_adds_auth_header(self, authenticated_client):
        """Test that requests include authorization header."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"res": []})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        await authenticated_client._request("GET", "/api/v1/test")
        
        authenticated_client._session.request.assert_called_once()
        call_kwargs = authenticated_client._session.request.call_args[1]
        assert "Authorization" in call_kwargs["headers"]
        assert "Bearer" in call_kwargs["headers"]["Authorization"]


class TestDonetickApiClientGetTasks:
    """Tests for async_get_tasks method."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_get_tasks_internal_api(self, authenticated_client, sample_chores_list_internal_api):
        """Test fetching tasks from internal API."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=sample_chores_list_internal_api)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        tasks = await authenticated_client.async_get_tasks()
        
        assert len(tasks) == 2
        assert all(isinstance(t, DonetickTask) for t in tasks)
        assert tasks[0].name == "Clean Kitchen"

    @pytest.mark.asyncio
    async def test_get_tasks_empty_list(self, authenticated_client):
        """Test fetching empty task list."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"res": []})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        authenticated_client._session.request = MagicMock(return_value=mock_response)

        tasks = await authenticated_client.async_get_tasks()

        assert tasks == []


class TestDonetickApiClientGetActiveTasksByName:
    """Tests for async_get_active_tasks_by_name helper."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_get_active_tasks_by_name_filters_active_and_assigned(self, authenticated_client, sample_chore_json):
        """Should return only active tasks with matching name that are assigned to someone."""
        # Matching active + assigned
        t1 = DonetickTask.from_json({
            **sample_chore_json,
            "id": 1,
            "name": "Test Task",
            "isActive": True,
            "assignedTo": 10,
            "assignees": [{"userId": 10}],
        })
        # Matching but not assigned
        t2 = DonetickTask.from_json({
            **sample_chore_json,
            "id": 2,
            "name": "Test Task",
            "isActive": True,
            "assignedTo": 0,
            "assignees": [],
        })
        # Matching but inactive
        t3 = DonetickTask.from_json({
            **sample_chore_json,
            "id": 3,
            "name": "Test Task",
            "isActive": False,
            "assignedTo": 11,
            "assignees": [{"userId": 11}],
        })
        # Different name
        t4 = DonetickTask.from_json({
            **sample_chore_json,
            "id": 4,
            "name": "Other Task",
            "isActive": True,
            "assignedTo": 12,
            "assignees": [{"userId": 12}],
        })

        authenticated_client.async_get_tasks = AsyncMock(return_value=[t1, t2, t3, t4])

        matches = await authenticated_client.async_get_active_tasks_by_name("  test   task ")
        assert [t.id for t in matches] == [1]

    @pytest.mark.asyncio
    async def test_get_active_tasks_by_name_can_include_unassigned(self, authenticated_client, sample_chore_json):
        """When require_assigned is False, include matching active tasks even if unassigned."""
        t1 = DonetickTask.from_json({
            **sample_chore_json,
            "id": 1,
            "name": "Test Task",
            "isActive": True,
            "assignedTo": 0,
            "assignees": [],
        })
        authenticated_client.async_get_tasks = AsyncMock(return_value=[t1])

        matches = await authenticated_client.async_get_active_tasks_by_name("test task", require_assigned=False)
        assert [t.id for t in matches] == [1]


class TestDonetickApiClientGetMembers:
    """Tests for async_get_circle_members method."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_get_circle_members(self, authenticated_client, sample_circle_members_list):
        """Test fetching circle members."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"res": sample_circle_members_list})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        members = await authenticated_client.async_get_circle_members()
        
        assert len(members) == 3
        assert all(isinstance(m, DonetickMember) for m in members)
        assert members[0].username == "johndoe"


class TestDonetickApiClientGetThings:
    """Tests for async_get_things method."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_get_things(self, authenticated_client, sample_things_list):
        """Test fetching things."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"res": sample_things_list})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        things = await authenticated_client.async_get_things()
        
        assert len(things) == 3
        assert all(isinstance(t, DonetickThing) for t in things)
        assert things[0].name == "Kitchen Light"


class TestDonetickApiClientTaskOperations:
    """Tests for task CRUD operations."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_complete_task(self, authenticated_client, sample_chore_json):
        """Test completing a task."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=sample_chore_json)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        # Note: method uses chore_id, not task_id
        result = await authenticated_client.async_complete_task(chore_id=1, completed_by=42)
        
        authenticated_client._session.request.assert_called_once()
        assert isinstance(result, DonetickTask)

    @pytest.mark.asyncio
    async def test_complete_task_without_completed_by(self, authenticated_client, sample_chore_json):
        """Test completing a task without specifying who completed it."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=sample_chore_json)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        result = await authenticated_client.async_complete_task(chore_id=1)
        
        assert isinstance(result, DonetickTask)

    @pytest.mark.asyncio
    async def test_set_task_notifications_updates_each_task(self, authenticated_client, sample_chore_json):
        """Test bulk notification updates."""
        updated_task = DonetickTask.from_json(sample_chore_json)
        authenticated_client.async_update_task = AsyncMock(return_value=updated_task)

        result = await authenticated_client.async_set_task_notifications([1, 2, 3], False)

        assert result == [updated_task, updated_task, updated_task]
        assert authenticated_client.async_update_task.call_args_list == [
            call(task_id=1, notification=False),
            call(task_id=2, notification=False),
            call(task_id=3, notification=False),
        ]

    @pytest.mark.asyncio
    async def test_set_task_notifications_requires_jwt(self, mock_aiohttp_session):
        """Bulk notification updates require JWT auth."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_API_KEY,
            api_token="test_api_key",
        )

        with pytest.raises(NotImplementedError):
            await client.async_set_task_notifications([1], False)

    @pytest.mark.asyncio
    async def test_update_task_merges_with_current_task_for_jwt_put(self, authenticated_client, sample_chore_json):
        """JWT updates should send a full task payload, not only changed fields."""
        existing_task = DonetickTask.from_json(sample_chore_json)
        authenticated_client.async_get_tasks = AsyncMock(return_value=[existing_task])
        authenticated_client._request = AsyncMock(return_value=sample_chore_json)

        await authenticated_client.async_update_task(task_id=existing_task.id, notification=False)

        authenticated_client._request.assert_called_once()
        method, endpoint = authenticated_client._request.call_args.args[:2]
        payload = authenticated_client._request.call_args.kwargs["json_data"]
        assert method == "PUT"
        assert endpoint == "/api/v1/chores/"
        assert payload["id"] == existing_task.id
        assert payload["name"] == existing_task.name
        assert payload["frequencyType"] == existing_task.frequency_type
        assert payload["assignStrategy"] == existing_task.assign_strategy
        assert payload["assignees"] == [{"userId": 42}, {"userId": 43}]
        assert payload["notification"] is False
        assert payload["notificationMetadata"] == existing_task.notification_metadata


class TestDonetickApiClientThingOperations:
    """Tests for thing state operations."""

    @pytest.fixture
    def authenticated_client(self, mock_aiohttp_session):
        """Create authenticated client."""
        client = DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )
        client._jwt_token = "valid_token"
        client._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return client

    @pytest.mark.asyncio
    async def test_set_thing_state_boolean(self, authenticated_client):
        """Test setting boolean thing state."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        result = await authenticated_client.async_set_thing_state(thing_id=1, state="on")
        
        authenticated_client._session.request.assert_called_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_set_thing_state_number(self, authenticated_client):
        """Test setting number thing state."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        result = await authenticated_client.async_set_thing_state(thing_id=2, state="72")
        
        assert result is True

    @pytest.mark.asyncio
    async def test_set_thing_state_text(self, authenticated_client):
        """Test setting text thing state."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        authenticated_client._session.request = MagicMock(return_value=mock_response)
        
        result = await authenticated_client.async_set_thing_state(thing_id=3, state="Hello World")
        
        assert result is True


class TestDonetickApiClientTokenExpiry:
    """Tests for token expiry parsing."""

    @pytest.fixture
    def jwt_client(self, mock_aiohttp_session):
        """Create JWT client."""
        return DonetickApiClient(
            base_url="https://donetick.example.com",
            session=mock_aiohttp_session,
            auth_type=AUTH_TYPE_JWT,
            username="testuser",
            password="testpass",
        )

    @pytest.mark.asyncio
    async def test_parse_expire_iso_format(self, jwt_client):
        """Test parsing ISO format expiry time."""
        expire = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"token": "test_token", "expire": expire})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        jwt_client._session.post = MagicMock(return_value=mock_response)
        
        await jwt_client._authenticate()
        
        assert jwt_client._jwt_expiry is not None
        assert jwt_client._jwt_expiry > datetime.now(timezone.utc)
