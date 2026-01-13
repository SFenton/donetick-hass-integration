"""Unit tests for custom_components.donetick.config_flow module."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp
import voluptuous as vol

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.config_flow import (
    DonetickConfigFlow,
    DonetickOptionsFlowHandler,
    _seconds_to_time_config,
    _config_to_seconds,
    _normalize_cutoff_times,
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
    CONF_CREATE_UNIFIED_LIST,
    CONF_CREATE_ASSIGNEE_LISTS,
    CONF_CREATE_DATE_FILTERED_LISTS,
    CONF_REFRESH_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
)


class TestTimeConversionHelpers:
    """Tests for time conversion helper functions."""

    def test_seconds_to_time_config_basic(self):
        """Test converting seconds to time config dict."""
        result = _seconds_to_time_config(3661)  # 1 hour, 1 minute, 1 second
        
        assert result["hours"] == 1
        assert result["minutes"] == 1
        assert result["seconds"] == 1

    def test_seconds_to_time_config_minutes_only(self):
        """Test converting minutes only."""
        result = _seconds_to_time_config(300)  # 5 minutes
        
        assert result["hours"] == 0


class TestNormalizeCutoffTimes:
    """Tests for _normalize_cutoff_times function."""

    def test_no_swap_needed_when_afternoon_after_morning(self):
        """Test that times are unchanged when afternoon > morning."""
        morning, afternoon = _normalize_cutoff_times("12:00", "17:00")
        
        assert morning == "12:00"
        assert afternoon == "17:00"

    def test_swap_when_afternoon_before_morning(self):
        """Test that times are swapped when afternoon < morning."""
        morning, afternoon = _normalize_cutoff_times("17:00", "12:00")
        
        assert morning == "12:00"
        assert afternoon == "17:00"

    def test_swap_when_times_equal(self):
        """Test that times are swapped when equal (degenerate case)."""
        morning, afternoon = _normalize_cutoff_times("12:00", "12:00")
        
        # When equal, they get swapped (which effectively keeps them the same)
        assert morning == "12:00"
        assert afternoon == "12:00"

    def test_handles_different_formats(self):
        """Test that various time formats are handled."""
        # With seconds
        morning, afternoon = _normalize_cutoff_times("12:00:00", "17:00:00")
        assert morning == "12:00:00"
        assert afternoon == "17:00:00"
        
        # Hour only (minutes default to 0)
        morning, afternoon = _normalize_cutoff_times("12", "17")
        assert morning == "12"
        assert afternoon == "17"

    def test_swap_with_minutes_difference(self):
        """Test swap when times differ only by minutes."""
        morning, afternoon = _normalize_cutoff_times("12:30", "12:00")
        
        assert morning == "12:00"
        assert afternoon == "12:30"

    def test_seconds_to_time_config_hours_only(self):
        """Test converting hours only."""
        result = _seconds_to_time_config(7200)  # 2 hours
        
        assert result["hours"] == 2
        assert result["minutes"] == 0
        assert result["seconds"] == 0

    def test_seconds_to_time_config_zero(self):
        """Test converting zero seconds."""
        result = _seconds_to_time_config(0)
        
        assert result["hours"] == 0
        assert result["minutes"] == 0
        assert result["seconds"] == 0

    def test_config_to_seconds_basic(self):
        """Test converting time config to seconds."""
        result = _config_to_seconds({"hours": 1, "minutes": 1, "seconds": 1})
        
        assert result == 3661

    def test_config_to_seconds_minutes_only(self):
        """Test converting minutes only."""
        result = _config_to_seconds({"hours": 0, "minutes": 5, "seconds": 0})
        
        assert result == 300

    def test_config_to_seconds_default(self):
        """Test converting default refresh interval (15 minutes)."""
        result = _config_to_seconds({"hours": 0, "minutes": 15, "seconds": 0})
        
        assert result == DEFAULT_REFRESH_INTERVAL  # 900 seconds

    def test_roundtrip_conversion(self):
        """Test that conversion is reversible."""
        original = 12345
        config = _seconds_to_time_config(original)
        result = _config_to_seconds(config)
        
        assert result == original


class TestDonetickConfigFlowUserStep:
    """Tests for the initial user step in config flow."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.fixture
    def config_flow(self, mock_hass):
        """Create config flow instance."""
        flow = DonetickConfigFlow()
        flow.hass = mock_hass
        return flow

    @pytest.mark.asyncio
    async def test_user_step_shows_form(self, config_flow):
        """Test that user step shows form when no input."""
        result = await config_flow.async_step_user(None)
        
        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert CONF_URL in str(result["data_schema"])

    @pytest.mark.asyncio
    async def test_user_step_jwt_auth_proceeds(self, config_flow):
        """Test that selecting JWT auth proceeds to jwt_auth step."""
        with patch.object(config_flow, 'async_step_jwt_auth', new_callable=AsyncMock) as mock_jwt:
            mock_jwt.return_value = {"type": "form", "step_id": "jwt_auth"}
            
            result = await config_flow.async_step_user({
                CONF_URL: "https://donetick.example.com",
                CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            })
            
            mock_jwt.assert_called_once()
            assert config_flow._server_data[CONF_URL] == "https://donetick.example.com"
            assert config_flow._server_data[CONF_AUTH_TYPE] == AUTH_TYPE_JWT

    @pytest.mark.asyncio
    async def test_user_step_api_key_auth_proceeds(self, config_flow):
        """Test that selecting API key auth proceeds to api_key_auth step."""
        with patch.object(config_flow, 'async_step_api_key_auth', new_callable=AsyncMock) as mock_api:
            mock_api.return_value = {"type": "form", "step_id": "api_key_auth"}
            
            result = await config_flow.async_step_user({
                CONF_URL: "https://donetick.example.com",
                CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
            })
            
            mock_api.assert_called_once()
            assert config_flow._server_data[CONF_AUTH_TYPE] == AUTH_TYPE_API_KEY


class TestDonetickConfigFlowJWTAuth:
    """Tests for JWT authentication step."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.fixture
    def config_flow(self, mock_hass):
        """Create config flow instance with server data."""
        flow = DonetickConfigFlow()
        flow.hass = mock_hass
        flow._server_data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        return flow

    @pytest.mark.asyncio
    async def test_jwt_auth_shows_form(self, config_flow):
        """Test that jwt_auth step shows form when no input."""
        result = await config_flow.async_step_jwt_auth(None)
        
        assert result["type"] == "form"
        assert result["step_id"] == "jwt_auth"
        assert CONF_USERNAME in str(result["data_schema"])
        assert CONF_PASSWORD in str(result["data_schema"])

    @pytest.mark.asyncio
    async def test_jwt_auth_success(self, config_flow):
        """Test successful JWT authentication."""
        with patch('custom_components.donetick.config_flow.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.config_flow.DonetickApiClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.async_get_tasks = AsyncMock(return_value=[])
                mock_client_class.return_value = mock_client
                
                with patch.object(config_flow, 'async_step_options', new_callable=AsyncMock) as mock_options:
                    mock_options.return_value = {"type": "form", "step_id": "options"}
                    
                    result = await config_flow.async_step_jwt_auth({
                        CONF_USERNAME: "testuser",
                        CONF_PASSWORD: "testpass",
                    })
                    
                    mock_options.assert_called_once()
                    assert config_flow._server_data[CONF_USERNAME] == "testuser"
                    assert config_flow._server_data[CONF_PASSWORD] == "testpass"

    @pytest.mark.asyncio
    async def test_jwt_auth_invalid_credentials(self, config_flow):
        """Test JWT authentication with invalid credentials."""
        from custom_components.donetick.api import AuthenticationError
        
        with patch('custom_components.donetick.config_flow.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.config_flow.DonetickApiClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.async_get_tasks = AsyncMock(side_effect=AuthenticationError("Invalid credentials"))
                mock_client_class.return_value = mock_client
                
                result = await config_flow.async_step_jwt_auth({
                    CONF_USERNAME: "wronguser",
                    CONF_PASSWORD: "wrongpass",
                })
                
                assert result["type"] == "form"
                assert result["errors"]["base"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_jwt_auth_mfa_not_supported(self, config_flow):
        """Test JWT authentication when MFA is required."""
        from custom_components.donetick.api import AuthenticationError
        
        with patch('custom_components.donetick.config_flow.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.config_flow.DonetickApiClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.async_get_tasks = AsyncMock(side_effect=AuthenticationError("MFA is required"))
                mock_client_class.return_value = mock_client
                
                result = await config_flow.async_step_jwt_auth({
                    CONF_USERNAME: "mfauser",
                    CONF_PASSWORD: "testpass",
                })
                
                assert result["type"] == "form"
                assert result["errors"]["base"] == "mfa_not_supported"

    @pytest.mark.asyncio
    async def test_jwt_auth_connection_error(self, config_flow):
        """Test JWT authentication with connection error."""
        with patch('custom_components.donetick.config_flow.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.config_flow.DonetickApiClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.async_get_tasks = AsyncMock(side_effect=aiohttp.ClientError("Connection failed"))
                mock_client_class.return_value = mock_client
                
                result = await config_flow.async_step_jwt_auth({
                    CONF_USERNAME: "testuser",
                    CONF_PASSWORD: "testpass",
                })
                
                assert result["type"] == "form"
                assert result["errors"]["base"] == "cannot_connect"


class TestDonetickConfigFlowAPIKeyAuth:
    """Tests for API key authentication step."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.fixture
    def config_flow(self, mock_hass):
        """Create config flow instance with server data."""
        flow = DonetickConfigFlow()
        flow.hass = mock_hass
        flow._server_data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
        }
        return flow

    @pytest.mark.asyncio
    async def test_api_key_auth_shows_form(self, config_flow):
        """Test that api_key_auth step shows form when no input."""
        result = await config_flow.async_step_api_key_auth(None)
        
        assert result["type"] == "form"
        assert result["step_id"] == "api_key_auth"
        assert CONF_TOKEN in str(result["data_schema"])

    @pytest.mark.asyncio
    async def test_api_key_auth_success(self, config_flow):
        """Test successful API key authentication."""
        with patch('custom_components.donetick.config_flow.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.config_flow.DonetickApiClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.async_get_tasks = AsyncMock(return_value=[])
                mock_client_class.return_value = mock_client
                
                with patch.object(config_flow, 'async_step_options', new_callable=AsyncMock) as mock_options:
                    mock_options.return_value = {"type": "form", "step_id": "options"}
                    
                    result = await config_flow.async_step_api_key_auth({
                        CONF_TOKEN: "api_key_12345",
                    })
                    
                    mock_options.assert_called_once()
                    assert config_flow._server_data[CONF_TOKEN] == "api_key_12345"

    @pytest.mark.asyncio
    async def test_api_key_auth_connection_error(self, config_flow):
        """Test API key authentication with connection error."""
        with patch('custom_components.donetick.config_flow.async_get_clientsession') as mock_session:
            with patch('custom_components.donetick.config_flow.DonetickApiClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.async_get_tasks = AsyncMock(side_effect=aiohttp.ClientError("Connection failed"))
                mock_client_class.return_value = mock_client
                
                result = await config_flow.async_step_api_key_auth({
                    CONF_TOKEN: "invalid_api_key",
                })
                
                assert result["type"] == "form"
                assert result["errors"]["base"] == "cannot_connect"


class TestDonetickConfigFlowOptions:
    """Tests for options step in config flow."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.fixture
    def config_flow(self, mock_hass):
        """Create config flow instance with full server data."""
        flow = DonetickConfigFlow()
        flow.hass = mock_hass
        flow._server_data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_USERNAME: "testuser",
            CONF_PASSWORD: "testpass",
        }
        return flow

    @pytest.mark.asyncio
    async def test_options_step_shows_form(self, config_flow):
        """Test that options step shows form when no input."""
        result = await config_flow.async_step_options(None)
        
        assert result["type"] == "form"
        assert result["step_id"] == "options"
        assert CONF_SHOW_DUE_IN in str(result["data_schema"])
        assert CONF_CREATE_UNIFIED_LIST in str(result["data_schema"])

    @pytest.mark.asyncio
    async def test_options_step_creates_entry(self, config_flow):
        """Test that options step creates config entry."""
        with patch.object(config_flow, 'async_create_entry') as mock_create:
            mock_create.return_value = {"type": "create_entry"}
            
            result = await config_flow.async_step_options({
                CONF_SHOW_DUE_IN: 7,
                CONF_CREATE_UNIFIED_LIST: True,
                CONF_CREATE_ASSIGNEE_LISTS: False,
                CONF_CREATE_DATE_FILTERED_LISTS: False,
                CONF_REFRESH_INTERVAL: {"hours": 0, "minutes": 5, "seconds": 0},
            })
            
            mock_create.assert_called_once()
            call_args = mock_create.call_args
            assert call_args[1]["title"] == "Donetick"
            assert call_args[1]["data"][CONF_SHOW_DUE_IN] == 7
            assert call_args[1]["data"][CONF_CREATE_UNIFIED_LIST] is True
            assert call_args[1]["data"][CONF_REFRESH_INTERVAL] == 300

    @pytest.mark.asyncio
    async def test_options_step_all_lists_enabled(self, config_flow):
        """Test options with all list types enabled."""
        with patch.object(config_flow, 'async_create_entry') as mock_create:
            mock_create.return_value = {"type": "create_entry"}
            
            result = await config_flow.async_step_options({
                CONF_SHOW_DUE_IN: 14,
                CONF_CREATE_UNIFIED_LIST: True,
                CONF_CREATE_ASSIGNEE_LISTS: True,
                CONF_CREATE_DATE_FILTERED_LISTS: True,
                CONF_REFRESH_INTERVAL: {"hours": 0, "minutes": 10, "seconds": 0},
            })
            
            call_args = mock_create.call_args
            assert call_args[1]["data"][CONF_CREATE_ASSIGNEE_LISTS] is True
            assert call_args[1]["data"][CONF_CREATE_DATE_FILTERED_LISTS] is True
            assert call_args[1]["data"][CONF_REFRESH_INTERVAL] == 600


class TestDonetickOptionsFlowHandler:
    """Tests for the options flow handler."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.async_create_task = MagicMock()
        return hass

    @pytest.fixture
    def mock_entry(self):
        """Create mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
            CONF_USERNAME: "testuser",
            CONF_PASSWORD: "testpass",
            CONF_SHOW_DUE_IN: 7,
            CONF_CREATE_UNIFIED_LIST: True,
            CONF_CREATE_ASSIGNEE_LISTS: False,
            CONF_CREATE_DATE_FILTERED_LISTS: False,
            CONF_REFRESH_INTERVAL: 300,
        }
        entry.options = {}
        return entry

    @pytest.fixture
    def options_flow(self, mock_hass, mock_entry):
        """Create options flow instance."""
        flow = DonetickOptionsFlowHandler(mock_entry)
        flow.hass = mock_hass
        return flow

    @pytest.mark.asyncio
    async def test_init_step_shows_form(self, options_flow):
        """Test that init step shows form when no input."""
        result = await options_flow.async_step_init(None)
        
        assert result["type"] == "form"
        assert result["step_id"] == "init"

    @pytest.mark.asyncio
    async def test_init_step_preserves_jwt_credentials(self, options_flow):
        """Test that init step preserves JWT credentials."""
        with patch.object(options_flow, 'async_create_entry') as mock_create:
            with patch.object(options_flow, 'async_abort') as mock_abort:
                mock_create.return_value = {"type": "create_entry"}
                
                result = await options_flow.async_step_init({
                    CONF_SHOW_DUE_IN: 14,
                    CONF_CREATE_UNIFIED_LIST: True,
                    CONF_CREATE_ASSIGNEE_LISTS: True,
                    CONF_CREATE_DATE_FILTERED_LISTS: False,
                    CONF_REFRESH_INTERVAL: {"hours": 0, "minutes": 10, "seconds": 0},
                })
                
                # Check that config entry was updated
                options_flow.hass.config_entries.async_update_entry.assert_called_once()
                call_args = options_flow.hass.config_entries.async_update_entry.call_args
                updated_data = call_args[1]["data"]
                
                assert updated_data[CONF_USERNAME] == "testuser"
                assert updated_data[CONF_PASSWORD] == "testpass"
                assert updated_data[CONF_SHOW_DUE_IN] == 14

    @pytest.mark.asyncio
    async def test_init_step_preserves_api_key_credentials(self, mock_hass):
        """Test that init step preserves API key credentials."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
            CONF_TOKEN: "api_key_12345",
            CONF_SHOW_DUE_IN: 7,
            CONF_CREATE_UNIFIED_LIST: True,
            CONF_CREATE_ASSIGNEE_LISTS: False,
            CONF_CREATE_DATE_FILTERED_LISTS: False,
            CONF_REFRESH_INTERVAL: 300,
        }
        entry.options = {}
        
        flow = DonetickOptionsFlowHandler(entry)
        flow.hass = mock_hass
        
        with patch.object(flow, 'async_create_entry') as mock_create:
            with patch.object(flow, 'async_abort') as mock_abort:
                mock_create.return_value = {"type": "create_entry"}
                
                result = await flow.async_step_init({
                    CONF_SHOW_DUE_IN: 21,
                    CONF_CREATE_UNIFIED_LIST: False,
                    CONF_CREATE_ASSIGNEE_LISTS: False,
                    CONF_CREATE_DATE_FILTERED_LISTS: True,
                    CONF_REFRESH_INTERVAL: {"hours": 0, "minutes": 15, "seconds": 0},
                })
                
                call_args = flow.hass.config_entries.async_update_entry.call_args
                updated_data = call_args[1]["data"]
                
                assert updated_data[CONF_TOKEN] == "api_key_12345"
                assert CONF_USERNAME not in updated_data
                assert CONF_PASSWORD not in updated_data

    @pytest.mark.asyncio
    async def test_init_step_triggers_reload(self, options_flow):
        """Test that init step triggers config entry reload."""
        with patch.object(options_flow, 'async_create_entry') as mock_create:
            with patch.object(options_flow, 'async_abort') as mock_abort:
                mock_create.return_value = {"type": "create_entry"}
                
                result = await options_flow.async_step_init({
                    CONF_SHOW_DUE_IN: 7,
                    CONF_CREATE_UNIFIED_LIST: True,
                    CONF_CREATE_ASSIGNEE_LISTS: False,
                    CONF_CREATE_DATE_FILTERED_LISTS: False,
                    CONF_REFRESH_INTERVAL: {"hours": 0, "minutes": 5, "seconds": 0},
                })
                
                # Check that reload was scheduled
                options_flow.hass.async_create_task.assert_called_once()


class TestConfigFlowVersion:
    """Tests for config flow version."""

    def test_config_flow_version(self):
        """Test that config flow has correct version."""
        flow = DonetickConfigFlow()
        assert flow.VERSION == 2  # Should match current version for migrations
