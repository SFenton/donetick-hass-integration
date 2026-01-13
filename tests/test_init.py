"""Unit tests for custom_components.donetick.__init__ module."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import voluptuous as vol
from datetime import datetime
import zoneinfo

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick import (
    async_setup_entry,
    async_unload_entry,
    async_complete_task_service,
    async_create_task_service,
    async_update_task_service,
    async_delete_task_service,
    async_create_task_form_service,
    normalize_datetime_string,
    is_time_only_string,
    calculate_next_occurrence_for_time,
    _get_api_client,
    _get_config_entry,
    COMPLETE_TASK_SCHEMA,
    CREATE_TASK_SCHEMA,
    UPDATE_TASK_SCHEMA,
    DELETE_TASK_SCHEMA,
    SERVICE_COMPLETE_TASK,
    SERVICE_CREATE_TASK,
    SERVICE_UPDATE_TASK,
    SERVICE_DELETE_TASK,
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
    CONF_WEBHOOK_ID,
)
from custom_components.donetick.model import DonetickTask


class TestServiceSchemas:
    """Tests for service schemas."""

    def test_complete_task_schema_valid(self):
        """Test valid complete_task schema."""
        data = {"task_id": 1}
        validated = COMPLETE_TASK_SCHEMA(data)
        assert validated["task_id"] == 1

    def test_complete_task_schema_with_optional(self):
        """Test complete_task schema with optional fields."""
        data = {
            "task_id": 1,
            "completed_by": 42,
            "config_entry_id": "test_entry",
        }
        validated = COMPLETE_TASK_SCHEMA(data)
        assert validated["task_id"] == 1
        assert validated["completed_by"] == 42
        assert validated["config_entry_id"] == "test_entry"

    def test_complete_task_schema_missing_required(self):
        """Test complete_task schema without required field."""
        data = {"completed_by": 42}
        with pytest.raises(vol.MultipleInvalid):
            COMPLETE_TASK_SCHEMA(data)

    def test_create_task_schema_valid(self):
        """Test valid create_task schema."""
        data = {"name": "Test Task"}
        validated = CREATE_TASK_SCHEMA(data)
        assert validated["name"] == "Test Task"

    def test_create_task_schema_with_all_fields(self):
        """Test create_task schema with all optional fields."""
        data = {
            "name": "Test Task",
            "description": "Task description",
            "due_date": "2025-01-20T18:00:00Z",
            "created_by": 42,
            "priority": 2,
            "frequency_type": "weekly",
            "frequency": 1,
            "assignees": "42,43",
            "assign_strategy": "round_robin",
            "points": 10,
            "notification": True,
            "require_approval": False,
            "is_private": False,
            "config_entry_id": "test_entry",
        }
        validated = CREATE_TASK_SCHEMA(data)
        assert validated["name"] == "Test Task"
        assert validated["priority"] == 2
        assert validated["frequency_type"] == "weekly"

    def test_create_task_schema_invalid_priority(self):
        """Test create_task schema with invalid priority."""
        data = {"name": "Test", "priority": 5}  # Priority must be 0-3
        with pytest.raises(vol.MultipleInvalid):
            CREATE_TASK_SCHEMA(data)

    def test_create_task_schema_invalid_frequency_type(self):
        """Test create_task schema with invalid frequency type."""
        data = {"name": "Test", "frequency_type": "invalid_type"}
        with pytest.raises(vol.MultipleInvalid):
            CREATE_TASK_SCHEMA(data)

    def test_create_task_schema_invalid_assign_strategy(self):
        """Test create_task schema with invalid assign strategy."""
        data = {"name": "Test", "assign_strategy": "invalid_strategy"}
        with pytest.raises(vol.MultipleInvalid):
            CREATE_TASK_SCHEMA(data)

    def test_create_task_schema_all_frequency_types(self):
        """Test all valid frequency types."""
        freq_types = [
            "once", "daily", "weekly", "monthly", "yearly",
            "interval", "days_of_the_week", "day_of_the_month", "no_repeat"
        ]
        for freq_type in freq_types:
            data = {"name": "Test", "frequency_type": freq_type}
            validated = CREATE_TASK_SCHEMA(data)
            assert validated["frequency_type"] == freq_type

    def test_create_task_schema_all_assign_strategies(self):
        """Test all valid assign strategies."""
        strategies = [
            "random", "least_assigned", "least_completed",
            "keep_last_assigned", "random_except_last_assigned", "round_robin", "no_assignee"
        ]
        for strategy in strategies:
            data = {"name": "Test", "assign_strategy": strategy}
            validated = CREATE_TASK_SCHEMA(data)
            assert validated["assign_strategy"] == strategy

    def test_update_task_schema_valid(self):
        """Test valid update_task schema."""
        data = {"task_id": 1}
        validated = UPDATE_TASK_SCHEMA(data)
        assert validated["task_id"] == 1

    def test_update_task_schema_with_all_fields(self):
        """Test update_task schema with all fields."""
        data = {
            "task_id": 1,
            "name": "Updated Task",
            "description": "Updated description",
            "due_date": "2025-01-25T18:00:00Z",
            "priority": 3,
            "frequency_type": "daily",
            "frequency": 2,
            "assignees": "42,43,44",
            "assign_strategy": "least_assigned",
            "points": 20,
            "notification": False,
            "require_approval": True,
            "is_private": True,
        }
        validated = UPDATE_TASK_SCHEMA(data)
        assert validated["task_id"] == 1
        assert validated["name"] == "Updated Task"

    def test_delete_task_schema_valid(self):
        """Test valid delete_task schema."""
        data = {"task_id": 1}
        validated = DELETE_TASK_SCHEMA(data)
        assert validated["task_id"] == 1


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {}
        hass.config = MagicMock()
        hass.config.api = MagicMock()
        hass.config.api.use_ssl = False
        hass.config.api.local_ip = "192.168.1.100"
        hass.config.api.port = 8123
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_USERNAME: "testuser",
            CONF_PASSWORD: "testpass",
        }
        entry.add_update_listener = MagicMock()
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_success(self, mock_hass, mock_config_entry):
        """Test successful setup entry."""
        with patch('custom_components.donetick.async_register_webhook', new_callable=AsyncMock) as mock_webhook:
            with patch('custom_components.donetick.generate_webhook_id', return_value="test_webhook_id"):
                with patch('custom_components.donetick.get_webhook_url', return_value="http://localhost/api/webhook/test"):
                    result = await async_setup_entry(mock_hass, mock_config_entry)
        
        assert result is True
        assert DOMAIN in mock_hass.data
        assert mock_config_entry.entry_id in mock_hass.data[DOMAIN]
        
        # Check services were registered
        assert mock_hass.services.async_register.call_count == 5

    @pytest.mark.asyncio
    async def test_setup_entry_generates_webhook_id(self, mock_hass, mock_config_entry):
        """Test that setup generates webhook ID if not present."""
        with patch('custom_components.donetick.async_register_webhook', new_callable=AsyncMock):
            with patch('custom_components.donetick.generate_webhook_id', return_value="new_webhook_id") as mock_gen:
                with patch('custom_components.donetick.get_webhook_url', return_value="http://localhost/api/webhook/test"):
                    await async_setup_entry(mock_hass, mock_config_entry)
        
        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_entry_stores_jwt_auth(self, mock_hass, mock_config_entry):
        """Test that JWT auth credentials are stored."""
        with patch('custom_components.donetick.async_register_webhook', new_callable=AsyncMock):
            with patch('custom_components.donetick.generate_webhook_id', return_value="test_webhook_id"):
                with patch('custom_components.donetick.get_webhook_url', return_value="http://localhost/api/webhook/test"):
                    await async_setup_entry(mock_hass, mock_config_entry)
        
        entry_data = mock_hass.data[DOMAIN][mock_config_entry.entry_id]
        assert entry_data[CONF_AUTH_TYPE] == AUTH_TYPE_JWT
        assert entry_data[CONF_USERNAME] == "testuser"
        assert entry_data[CONF_PASSWORD] == "testpass"

    @pytest.mark.asyncio
    async def test_setup_entry_stores_api_key_auth(self, mock_hass):
        """Test that API key auth credentials are stored."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
            CONF_TOKEN: "test_api_key",
        }
        entry.add_update_listener = MagicMock()
        
        with patch('custom_components.donetick.async_register_webhook', new_callable=AsyncMock):
            with patch('custom_components.donetick.generate_webhook_id', return_value="test_webhook_id"):
                with patch('custom_components.donetick.get_webhook_url', return_value="http://localhost/api/webhook/test"):
                    await async_setup_entry(mock_hass, entry)
        
        entry_data = mock_hass.data[DOMAIN][entry.entry_id]
        assert entry_data[CONF_AUTH_TYPE] == AUTH_TYPE_API_KEY
        assert entry_data[CONF_TOKEN] == "test_api_key"


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {"test_entry_id": {}}}
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)
        hass.services.async_remove = MagicMock()
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_WEBHOOK_ID: "test_webhook_id",
        }
        return entry

    @pytest.mark.asyncio
    async def test_unload_entry_success(self, mock_hass, mock_config_entry):
        """Test successful unload entry."""
        with patch('custom_components.donetick.async_unregister_webhook', new_callable=AsyncMock) as mock_unreg:
            result = await async_unload_entry(mock_hass, mock_config_entry)
        
        assert result is True
        mock_unreg.assert_called_once_with(mock_hass, "test_webhook_id")
        assert mock_config_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_unload_entry_removes_services_when_last(self, mock_hass, mock_config_entry):
        """Test that services are removed when last entry is unloaded."""
        with patch('custom_components.donetick.async_unregister_webhook', new_callable=AsyncMock):
            await async_unload_entry(mock_hass, mock_config_entry)
        
        # Check services were removed (5 services)
        assert mock_hass.services.async_remove.call_count == 5


class TestGetApiClient:
    """Tests for _get_api_client helper."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        return hass

    def test_get_api_client_jwt(self, mock_hass):
        """Test getting API client with JWT auth."""
        mock_hass.data = {DOMAIN: {"test_entry": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_USERNAME: "testuser",
            CONF_PASSWORD: "testpass",
        }}}
        
        with patch('custom_components.donetick.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.DonetickApiClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client
                
                client = _get_api_client(mock_hass, "test_entry")
                
                mock_client_class.assert_called_once()
                call_kwargs = mock_client_class.call_args[1]
                assert call_kwargs["auth_type"] == AUTH_TYPE_JWT
                assert call_kwargs["username"] == "testuser"

    def test_get_api_client_api_key(self, mock_hass):
        """Test getting API client with API key auth."""
        mock_hass.data = {DOMAIN: {"test_entry": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
            CONF_TOKEN: "test_api_key",
        }}}
        
        with patch('custom_components.donetick.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.DonetickApiClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client
                
                client = _get_api_client(mock_hass, "test_entry")
                
                mock_client_class.assert_called_once()
                call_kwargs = mock_client_class.call_args[1]
                assert call_kwargs["auth_type"] == AUTH_TYPE_API_KEY
                assert call_kwargs["api_token"] == "test_api_key"


class TestGetConfigEntry:
    """Tests for _get_config_entry helper."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config_entries = MagicMock()
        return hass

    @pytest.mark.asyncio
    async def test_get_config_entry_by_id(self, mock_hass):
        """Test getting config entry by ID."""
        mock_entry = MagicMock()
        mock_hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
        
        result = await _get_config_entry(mock_hass, "test_entry_id")
        
        assert result is mock_entry
        mock_hass.config_entries.async_get_entry.assert_called_once_with("test_entry_id")

    @pytest.mark.asyncio
    async def test_get_config_entry_by_entity_id(self, mock_hass):
        """Test getting config entry by entity ID."""
        mock_hass.config_entries.async_get_entry = MagicMock(return_value=None)
        
        mock_entity_entry = MagicMock()
        mock_entity_entry.config_entry_id = "actual_entry_id"
        
        mock_registry = MagicMock()
        mock_registry.async_get = MagicMock(return_value=mock_entity_entry)
        mock_hass.helpers = MagicMock()
        mock_hass.helpers.entity_registry.async_get = MagicMock(return_value=mock_registry)
        
        mock_entry = MagicMock()
        # Second call should return the entry
        mock_hass.config_entries.async_get_entry = MagicMock(side_effect=[None, mock_entry])
        
        result = await _get_config_entry(mock_hass, "todo.dt_test")
        
        assert result is mock_entry

    @pytest.mark.asyncio
    async def test_get_config_entry_first_entry(self, mock_hass):
        """Test getting first config entry when no ID provided."""
        mock_entry = MagicMock()
        mock_hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])
        
        result = await _get_config_entry(mock_hass, None)
        
        assert result is mock_entry

    @pytest.mark.asyncio
    async def test_get_config_entry_not_found(self, mock_hass):
        """Test getting config entry when not found."""
        mock_hass.config_entries.async_get_entry = MagicMock(return_value=None)
        
        mock_registry = MagicMock()
        mock_registry.async_get = MagicMock(return_value=None)
        mock_hass.helpers = MagicMock()
        mock_hass.helpers.entity_registry.async_get = MagicMock(return_value=mock_registry)
        
        result = await _get_config_entry(mock_hass, "nonexistent")
        
        assert result is None


class TestCompleteTaskService:
    """Tests for async_complete_task_service."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {"test_entry_id": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }}}
        return hass

    @pytest.fixture
    def mock_call(self):
        """Create mock service call."""
        call = MagicMock()
        call.data = {
            "task_id": 1,
            "completed_by": 42,
        }
        return call

    @pytest.mark.asyncio
    async def test_complete_task_success(self, mock_hass, mock_call, sample_chore_json):
        """Test successful task completion."""
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])
        mock_hass.config_entries.async_get_entry = MagicMock(return_value=None)
        mock_hass.states = MagicMock()
        mock_hass.states.async_entity_ids = MagicMock(return_value=[])
        
        completed_task = DonetickTask.from_json(sample_chore_json)
        
        with patch('custom_components.donetick._get_api_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_client.async_complete_task = AsyncMock(return_value=completed_task)
            mock_get_client.return_value = mock_client
            
            await async_complete_task_service(mock_hass, mock_call)
            
            mock_client.async_complete_task.assert_called_once_with(1, 42)


class TestCreateTaskService:
    """Tests for async_create_task_service."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {"test_entry_id": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }}}
        return hass

    @pytest.fixture
    def mock_call(self):
        """Create mock service call."""
        call = MagicMock()
        call.data = {
            "name": "Test Task",
            "description": "Test description",
            "due_date": "2025-01-20T18:00:00Z",
        }
        return call

    @pytest.mark.asyncio
    async def test_create_task_success(self, mock_hass, mock_call, sample_chore_json):
        """Test successful task creation."""
        created_task = DonetickTask.from_json(sample_chore_json)
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_client.async_create_task = AsyncMock(return_value=created_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_service(mock_hass, mock_call)
                
                mock_client.async_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_task_parses_assignees(self, mock_hass, sample_chore_json):
        """Test that assignees string is parsed correctly."""
        call = MagicMock()
        call.data = {
            "name": "Test Task",
            "assignees": "42, 43, 44",
        }
        
        created_task = DonetickTask.from_json(sample_chore_json)
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_client.async_create_task = AsyncMock(return_value=created_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_service(mock_hass, call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                assert call_kwargs["assignees"] == [42, 43, 44]


class TestUpdateTaskService:
    """Tests for async_update_task_service."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {"test_entry_id": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }}}
        return hass

    @pytest.fixture
    def mock_call(self):
        """Create mock service call."""
        call = MagicMock()
        call.data = {
            "task_id": 1,
            "name": "Updated Task",
        }
        return call

    @pytest.mark.asyncio
    async def test_update_task_success(self, mock_hass, mock_call, sample_chore_json):
        """Test successful task update."""
        updated_task = DonetickTask.from_json(sample_chore_json)
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_client.async_update_task = AsyncMock(return_value=updated_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_update_task_service(mock_hass, mock_call)
                
                mock_client.async_update_task.assert_called_once()


class TestDeleteTaskService:
    """Tests for async_delete_task_service."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {"test_entry_id": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }}}
        return hass

    @pytest.fixture
    def mock_call(self):
        """Create mock service call."""
        call = MagicMock()
        call.data = {
            "task_id": 1,
        }
        return call

    @pytest.mark.asyncio
    async def test_delete_task_success(self, mock_hass, mock_call):
        """Test successful task deletion."""
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_client.async_delete_task = AsyncMock(return_value=True)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_delete_task_service(mock_hass, mock_call)
                
                mock_client.async_delete_task.assert_called_once_with(1)


class TestNormalizeDatetimeString:
    """Tests for normalize_datetime_string helper function."""

    @pytest.fixture
    def local_tz(self):
        """Create a local timezone for testing."""
        return zoneinfo.ZoneInfo("America/New_York")

    def test_empty_string_returns_empty(self, local_tz):
        """Test that empty string is returned as-is."""
        assert normalize_datetime_string("", local_tz) == ""
        assert normalize_datetime_string(None, local_tz) is None

    def test_string_with_z_suffix_unchanged(self, local_tz):
        """Test that strings with Z suffix are returned unchanged."""
        dt_str = "2025-01-11T14:30:00Z"
        assert normalize_datetime_string(dt_str, local_tz) == dt_str

    def test_string_with_timezone_offset_unchanged(self, local_tz):
        """Test that strings with timezone offset are returned unchanged."""
        dt_str = "2025-01-11T14:30:00+05:00"
        assert normalize_datetime_string(dt_str, local_tz) == dt_str
        
        dt_str2 = "2025-01-11T14:30:00-08:00"
        assert normalize_datetime_string(dt_str2, local_tz) == dt_str2

    def test_date_only_gets_2359(self, local_tz):
        """Test that date-only strings get 23:59:00 time appended."""
        result = normalize_datetime_string("2025-01-11", local_tz)
        assert result == "2025-01-11T23:59:00"

    def test_hour_only_defaults_minute_to_zero(self, local_tz):
        """Test that hour-only time defaults minute to 0."""
        result = normalize_datetime_string("2025-01-11T14", local_tz)
        assert result == "2025-01-11T14:00:00"

    def test_hour_only_single_digit(self, local_tz):
        """Test that single-digit hour is zero-padded."""
        result = normalize_datetime_string("2025-01-11T9", local_tz)
        assert result == "2025-01-11T09:00:00"

    def test_hour_and_minute_without_seconds(self, local_tz):
        """Test that hour:minute format gets seconds appended."""
        result = normalize_datetime_string("2025-01-11T14:30", local_tz)
        assert result == "2025-01-11T14:30:00"

    def test_complete_datetime_unchanged(self, local_tz):
        """Test that complete datetime strings are unchanged."""
        dt_str = "2025-01-11T14:30:45"
        assert normalize_datetime_string(dt_str, local_tz) == dt_str

    def test_empty_hour_uses_current_hour(self, local_tz):
        """Test that empty hour uses current local hour."""
        # This is an edge case - if someone passes "2025-01-11T" with no hour
        result = normalize_datetime_string("2025-01-11T", local_tz)
        now_hour = datetime.now(local_tz).hour
        assert result == f"2025-01-11T{now_hour:02d}:00:00"

    def test_hour_with_empty_minute(self, local_tz):
        """Test that hour with empty minute defaults minute to 0."""
        result = normalize_datetime_string("2025-01-11T14:", local_tz)
        assert result == "2025-01-11T14:00:00"

    def test_whitespace_stripped(self, local_tz):
        """Test that leading/trailing whitespace is stripped."""
        result = normalize_datetime_string("  2025-01-11T14  ", local_tz)
        assert result == "2025-01-11T14:00:00"

    def test_seconds_only_uses_2359(self, local_tz):
        """Test that seconds-only time uses 23:59:ss."""
        result = normalize_datetime_string("2025-01-11T::30", local_tz)
        assert result == "2025-01-11T23:59:30"

    def test_seconds_only_single_digit(self, local_tz):
        """Test that single-digit seconds-only is zero-padded."""
        result = normalize_datetime_string("2025-01-11T::5", local_tz)
        assert result == "2025-01-11T23:59:05"

    def test_hour_and_seconds_only(self, local_tz):
        """Test that hour and seconds without minute uses minute 00."""
        result = normalize_datetime_string("2025-01-11T14::30", local_tz)
        assert result == "2025-01-11T14:00:30"


class TestCreateTaskFormServiceDueDateHandling:
    """Tests for async_create_task_form_service due date handling."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {"test_entry_id": {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_USERNAME: "testuser",
            CONF_PASSWORD: "testpass",
        }}}
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        return hass

    @pytest.fixture
    def mock_call_with_due_date(self):
        """Create a factory for mock service calls with custom due_date."""
        def _create_call(due_date):
            call = MagicMock()
            call.data = {
                "name": "Test Task",
                "due_date": due_date,
            }
            return call
        return _create_call

    @pytest.mark.asyncio
    async def test_hour_only_defaults_minute_to_zero(self, mock_hass, mock_call_with_due_date):
        """Test that providing only hour defaults minute to 0."""
        mock_call = mock_call_with_due_date("2025-01-11T14")
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                # Verify the API was called with a properly formatted due_date
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                # Should be in UTC format with minute = 0
                assert due_date is not None
                assert due_date.endswith("Z")
                # Parse it back and verify minute is 0 when converted to local
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_tz = zoneinfo.ZoneInfo("America/New_York")
                local_dt = parsed.astimezone(local_tz)
                assert local_dt.hour == 14
                assert local_dt.minute == 0

    @pytest.mark.asyncio
    async def test_date_only_sets_time_to_2359(self, mock_hass, mock_call_with_due_date):
        """Test that date-only sets time to 11:59 PM local time."""
        mock_call = mock_call_with_due_date("2025-01-11")
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                assert due_date.endswith("Z")
                # Parse it back and verify time is 23:59 local
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_tz = zoneinfo.ZoneInfo("America/New_York")
                local_dt = parsed.astimezone(local_tz)
                assert local_dt.hour == 23
                assert local_dt.minute == 59

    @pytest.mark.asyncio
    async def test_complete_datetime_preserved(self, mock_hass, mock_call_with_due_date):
        """Test that complete datetime is preserved correctly."""
        mock_call = mock_call_with_due_date("2025-01-11T14:30:00")
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                assert due_date.endswith("Z")
                # Parse it back and verify time is preserved
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_tz = zoneinfo.ZoneInfo("America/New_York")
                local_dt = parsed.astimezone(local_tz)
                assert local_dt.hour == 14
                assert local_dt.minute == 30

    @pytest.mark.asyncio
    async def test_datetime_with_timezone_preserved(self, mock_hass, mock_call_with_due_date):
        """Test that datetime with timezone info is preserved."""
        mock_call = mock_call_with_due_date("2025-01-11T14:30:00Z")
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                # Should be passed through unchanged since it already has timezone
                assert due_date == "2025-01-11T14:30:00Z"

    @pytest.mark.asyncio
    async def test_no_due_date_passes_none(self, mock_hass):
        """Test that no due_date passes None to API."""
        mock_call = MagicMock()
        mock_call.data = {"name": "Test Task"}  # No due_date
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                assert call_kwargs["due_date"] is None

    @pytest.mark.asyncio
    async def test_datetime_object_with_noon_preserved(self, mock_hass):
        """Test that datetime object with noon time is preserved (not converted to 23:59).
        
        We can't distinguish between "user picked date only" (HA defaults to noon) and
        "user intentionally picked noon", so we must preserve the time as-is.
        """
        mock_call = MagicMock()
        mock_call.data = {
            "name": "Test Task",
            "due_date": datetime(2025, 1, 11, 12, 0, 0),  # Noon
        }
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                assert due_date.endswith("Z")
                # Noon should be preserved
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_tz = zoneinfo.ZoneInfo("America/New_York")
                local_dt = parsed.astimezone(local_tz)
                assert local_dt.hour == 12
                assert local_dt.minute == 0

    @pytest.mark.asyncio
    async def test_datetime_object_with_midnight_preserved(self, mock_hass):
        """Test that datetime object with midnight time is preserved."""
        mock_call = MagicMock()
        mock_call.data = {
            "name": "Test Task",
            "due_date": datetime(2025, 1, 11, 0, 0, 0),  # Midnight
        }
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                # Midnight should be preserved
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_tz = zoneinfo.ZoneInfo("America/New_York")
                local_dt = parsed.astimezone(local_tz)
                assert local_dt.hour == 0
                assert local_dt.minute == 0

    @pytest.mark.asyncio
    async def test_datetime_object_with_specific_time_preserved(self, mock_hass):
        """Test that datetime object with specific time is preserved."""
        mock_call = MagicMock()
        mock_call.data = {
            "name": "Test Task",
            "due_date": datetime(2025, 1, 11, 15, 30, 0),  # 3:30 PM
        }
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                # Time should be preserved
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_tz = zoneinfo.ZoneInfo("America/New_York")
                local_dt = parsed.astimezone(local_tz)
                assert local_dt.hour == 15
                assert local_dt.minute == 30


class TestIsTimeOnlyString:
    """Tests for is_time_only_string function."""

    def test_valid_hh_mm(self):
        """Test valid HH:MM format."""
        assert is_time_only_string("14:30") is True
        assert is_time_only_string("00:00") is True
        assert is_time_only_string("23:59") is True
        assert is_time_only_string("9:30") is True

    def test_valid_hh_mm_ss(self):
        """Test valid HH:MM:SS format."""
        assert is_time_only_string("14:30:00") is True
        assert is_time_only_string("00:00:00") is True
        assert is_time_only_string("23:59:59") is True
        assert is_time_only_string("9:30:45") is True

    def test_invalid_datetime(self):
        """Test datetime strings are not time-only."""
        assert is_time_only_string("2025-01-11T14:30") is False
        assert is_time_only_string("2025-01-11T14:30:00") is False
        assert is_time_only_string("2025-01-11T14:30:00Z") is False

    def test_invalid_date(self):
        """Test date strings are not time-only."""
        assert is_time_only_string("2025-01-11") is False
        assert is_time_only_string("2025-1-11") is False

    def test_valid_hour_only(self):
        """Test hour-only input is valid (interpreted as HH:00)."""
        assert is_time_only_string("14") is True  # 2:00 PM
        assert is_time_only_string("0") is True   # Midnight
        assert is_time_only_string("23") is True  # 11:00 PM
        assert is_time_only_string("9") is True   # 9:00 AM

    def test_invalid_hour_only_out_of_range(self):
        """Test hour-only values out of range."""
        assert is_time_only_string("24") is False
        assert is_time_only_string("25") is False
        assert is_time_only_string("-1") is False

    def test_invalid_out_of_range(self):
        """Test out of range values."""
        assert is_time_only_string("24:00") is False
        assert is_time_only_string("14:60") is False
        assert is_time_only_string("14:30:60") is False
        assert is_time_only_string("-1:30") is False

    def test_invalid_empty_and_none(self):
        """Test empty string and None."""
        assert is_time_only_string("") is False
        assert is_time_only_string(None) is False

    def test_invalid_non_numeric(self):
        """Test non-numeric values."""
        assert is_time_only_string("ab:cd") is False
        assert is_time_only_string("14:ab") is False

    def test_with_whitespace(self):
        """Test time strings with whitespace."""
        assert is_time_only_string(" 14:30 ") is True
        assert is_time_only_string("  17:00  ") is True


class TestCalculateNextOccurrenceForTime:
    """Tests for calculate_next_occurrence_for_time function."""

    def test_future_time_today(self):
        """Test that a future time today returns today's date."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        # Current time: 12:00 PM
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=local_tz)
        
        # 5:00 PM is in the future today
        result = calculate_next_occurrence_for_time("17:00", local_tz, now=now)
        
        # Parse result and check
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.month == 1
        assert local_result.day == 15  # Today
        assert local_result.hour == 17
        assert local_result.minute == 0

    def test_equal_time_is_tomorrow(self):
        """Test that equal time (now) returns tomorrow's date."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        # Current time: exactly 5:00 PM
        now = datetime(2025, 1, 15, 17, 0, 0, tzinfo=local_tz)
        
        # 5:00 PM is equal to now
        result = calculate_next_occurrence_for_time("17:00", local_tz, now=now)
        
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.month == 1
        assert local_result.day == 16  # Tomorrow
        assert local_result.hour == 17
        assert local_result.minute == 0

    def test_past_time_is_tomorrow(self):
        """Test that a past time returns tomorrow's date."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        # Current time: 9:00 PM
        now = datetime(2025, 1, 15, 21, 0, 0, tzinfo=local_tz)
        
        # 5:00 PM is in the past today
        result = calculate_next_occurrence_for_time("17:00", local_tz, now=now)
        
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.month == 1
        assert local_result.day == 16  # Tomorrow
        assert local_result.hour == 17
        assert local_result.minute == 0

    def test_with_seconds(self):
        """Test time with seconds component."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=local_tz)
        
        result = calculate_next_occurrence_for_time("17:30:45", local_tz, now=now)
        
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.day == 15  # Today
        assert local_result.hour == 17
        assert local_result.minute == 30
        assert local_result.second == 45

    def test_hour_only_future(self):
        """Test hour-only input for future time today."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=local_tz)
        
        # "17" means 5:00 PM
        result = calculate_next_occurrence_for_time("17", local_tz, now=now)
        
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.day == 15  # Today
        assert local_result.hour == 17
        assert local_result.minute == 0
        assert local_result.second == 0

    def test_hour_only_past(self):
        """Test hour-only input for past time goes to tomorrow."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime(2025, 1, 15, 14, 0, 0, tzinfo=local_tz)  # 2:00 PM
        
        # "9" means 9:00 AM, which is in the past
        result = calculate_next_occurrence_for_time("9", local_tz, now=now)
        
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.day == 16  # Tomorrow
        assert local_result.hour == 9
        assert local_result.minute == 0

    def test_midnight_tomorrow(self):
        """Test midnight time when current time is afternoon."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime(2025, 1, 15, 14, 0, 0, tzinfo=local_tz)
        
        result = calculate_next_occurrence_for_time("00:00", local_tz, now=now)
        
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.day == 16  # Tomorrow (midnight is in the past today)
        assert local_result.hour == 0
        assert local_result.minute == 0

    def test_different_timezone(self):
        """Test with a different timezone."""
        local_tz = zoneinfo.ZoneInfo("Europe/London")
        now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=local_tz)
        
        result = calculate_next_occurrence_for_time("15:00", local_tz, now=now)
        
        # Result should be in UTC
        assert result.endswith("Z")
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        local_result = parsed.astimezone(local_tz)
        
        assert local_result.day == 15  # Today
        assert local_result.hour == 15

    def test_utc_format_output(self):
        """Test that output is always in RFC3339 UTC format."""
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=local_tz)
        
        result = calculate_next_occurrence_for_time("17:00", local_tz, now=now)
        
        assert result.endswith("Z")
        # Should be parseable
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


class TestTimeOnlyIntegration:
    """Integration tests for time-only due date handling in create_task_form_service."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.time_zone = "America/New_York"
        hass.data = {DOMAIN: {"entries": {}}}
        return hass

    @pytest.mark.asyncio
    async def test_time_only_future_today(self, mock_hass):
        """Test time-only input for future time today."""
        mock_call = MagicMock()
        mock_call.data = {
            "name": "Test Task",
            "due_date": "17:00",  # 5 PM time-only
        }
        
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        # Mock current time to 12:00 PM on Jan 15
        fixed_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=local_tz)
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    with patch('custom_components.donetick.datetime') as mock_datetime:
                        mock_datetime.now.return_value = fixed_now
                        mock_datetime.fromisoformat = datetime.fromisoformat
                        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                        await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                assert due_date.endswith("Z")
                
                # Parse and check - should be today 5 PM
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_result = parsed.astimezone(local_tz)
                assert local_result.day == 15  # Today
                assert local_result.hour == 17
                assert local_result.minute == 0

    @pytest.mark.asyncio
    async def test_time_only_past_is_tomorrow(self, mock_hass):
        """Test time-only input for past time goes to tomorrow."""
        mock_call = MagicMock()
        mock_call.data = {
            "name": "Test Task",
            "due_date": "17:00",  # 5 PM time-only
        }
        
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        # Mock current time to 9:00 PM on Jan 15 (5 PM is in the past)
        fixed_now = datetime(2025, 1, 15, 21, 0, 0, tzinfo=local_tz)
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    with patch('custom_components.donetick.datetime') as mock_datetime:
                        mock_datetime.now.return_value = fixed_now
                        mock_datetime.fromisoformat = datetime.fromisoformat
                        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                        await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_result = parsed.astimezone(local_tz)
                assert local_result.day == 16  # Tomorrow
                assert local_result.hour == 17
                assert local_result.minute == 0

    @pytest.mark.asyncio
    async def test_time_only_equal_is_tomorrow(self, mock_hass):
        """Test time-only input for equal time (now) goes to tomorrow."""
        mock_call = MagicMock()
        mock_call.data = {
            "name": "Test Task",
            "due_date": "17:00",  # 5 PM time-only
        }
        
        local_tz = zoneinfo.ZoneInfo("America/New_York")
        # Mock current time to exactly 5:00 PM on Jan 15
        fixed_now = datetime(2025, 1, 15, 17, 0, 0, tzinfo=local_tz)
        
        with patch('custom_components.donetick._get_config_entry', new_callable=AsyncMock) as mock_get_entry:
            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry_id"
            mock_get_entry.return_value = mock_entry
            
            with patch('custom_components.donetick._get_api_client') as mock_get_client:
                mock_client = AsyncMock()
                mock_task = MagicMock()
                mock_task.id = 123
                mock_client.async_create_task = AsyncMock(return_value=mock_task)
                mock_get_client.return_value = mock_client
                
                with patch('custom_components.donetick._refresh_todo_entities', new_callable=AsyncMock):
                    with patch('custom_components.donetick.datetime') as mock_datetime:
                        mock_datetime.now.return_value = fixed_now
                        mock_datetime.fromisoformat = datetime.fromisoformat
                        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                        await async_create_task_form_service(mock_hass, mock_call)
                
                call_kwargs = mock_client.async_create_task.call_args[1]
                due_date = call_kwargs["due_date"]
                assert due_date is not None
                
                parsed = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                local_result = parsed.astimezone(local_tz)
                assert local_result.day == 16  # Tomorrow
                assert local_result.hour == 17
                assert local_result.minute == 0
