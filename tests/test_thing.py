"""Unit tests for custom_components.donetick.thing module."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.thing import (
    DonetickThingBase,
    DonetickThingSensor,
    DonetickThingSwitch,
    DonetickThingNumber,
    DonetickThingText,
    async_setup_entry,
)
from custom_components.donetick.model import DonetickThing
from custom_components.donetick.const import (
    DOMAIN,
    CONF_URL,
    CONF_AUTH_TYPE,
    AUTH_TYPE_JWT,
    AUTH_TYPE_API_KEY,
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


class TestDonetickThingBase:
    """Tests for DonetickThingBase."""

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    @pytest.fixture
    def boolean_thing(self, sample_thing_json):
        """Create boolean thing."""
        return DonetickThing.from_json(sample_thing_json)

    def test_unique_id(self, mock_client, boolean_thing):
        """Test unique_id is correct."""
        entity = DonetickThingBase(mock_client, boolean_thing)
        
        assert entity.unique_id == f"donetick_thing_{boolean_thing.id}"

    def test_name(self, mock_client, boolean_thing):
        """Test name is correct."""
        entity = DonetickThingBase(mock_client, boolean_thing)
        
        assert entity.name == boolean_thing.name

    def test_device_info(self, mock_client, boolean_thing):
        """Test device_info is correct."""
        entity = DonetickThingBase(mock_client, boolean_thing)
        
        device_info = entity.device_info
        
        assert "identifiers" in device_info
        assert "name" in device_info
        assert device_info["name"] == "Donetick Things"
        assert device_info["manufacturer"] == "Donetick"

    @pytest.mark.asyncio
    async def test_async_update_success(self, mock_client, boolean_thing):
        """Test successful state update."""
        mock_client.async_get_thing_state = AsyncMock(return_value="new_state")
        
        entity = DonetickThingBase(mock_client, boolean_thing)
        
        await entity.async_update()
        
        assert entity._thing.state == "new_state"
        mock_client.async_get_thing_state.assert_called_once_with(boolean_thing.id)

    @pytest.mark.asyncio
    async def test_async_update_none_state(self, mock_client, boolean_thing):
        """Test update when state returns None."""
        original_state = boolean_thing.state
        mock_client.async_get_thing_state = AsyncMock(return_value=None)
        
        entity = DonetickThingBase(mock_client, boolean_thing)
        
        await entity.async_update()
        
        # State should remain unchanged
        assert entity._thing.state == original_state

    @pytest.mark.asyncio
    async def test_async_update_error(self, mock_client, boolean_thing):
        """Test update handles errors gracefully."""
        mock_client.async_get_thing_state = AsyncMock(side_effect=Exception("API error"))
        
        entity = DonetickThingBase(mock_client, boolean_thing)
        
        # Should not raise
        await entity.async_update()


class TestDonetickThingSensor:
    """Tests for DonetickThingSensor."""

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    def test_native_value(self, mock_client):
        """Test native_value returns thing state."""
        thing = DonetickThing.from_json(make_thing_json(1, "Test Sensor", "text", "sensor_value"))
        
        entity = DonetickThingSensor(mock_client, thing)
        
        assert entity.native_value == "sensor_value"


class TestDonetickThingSwitch:
    """Tests for DonetickThingSwitch."""

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    @pytest.fixture
    def switch_thing(self):
        """Create switch thing."""
        return DonetickThing.from_json(make_thing_json(1, "Test Switch", "boolean", "off"))

    def test_is_on_true_states(self, mock_client, switch_thing):
        """Test is_on returns True for 'on', 'true', '1'."""
        entity = DonetickThingSwitch(mock_client, switch_thing)
        
        # Test 'on'
        entity._thing.state = "on"
        assert entity.is_on is True
        
        # Test 'true'
        entity._thing.state = "true"
        assert entity.is_on is True
        
        # Test '1'
        entity._thing.state = "1"
        assert entity.is_on is True
        
        # Test case insensitivity
        entity._thing.state = "ON"
        assert entity.is_on is True
        
        entity._thing.state = "True"
        assert entity.is_on is True

    def test_is_on_false_states(self, mock_client, switch_thing):
        """Test is_on returns False for other states."""
        entity = DonetickThingSwitch(mock_client, switch_thing)
        
        entity._thing.state = "off"
        assert entity.is_on is False
        
        entity._thing.state = "false"
        assert entity.is_on is False
        
        entity._thing.state = "0"
        assert entity.is_on is False
        
        entity._thing.state = ""
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on(self, mock_client, switch_thing):
        """Test turning switch on."""
        mock_client.async_set_thing_state = AsyncMock(return_value=True)
        
        entity = DonetickThingSwitch(mock_client, switch_thing)
        entity.async_write_ha_state = MagicMock()
        
        await entity.async_turn_on()
        
        mock_client.async_set_thing_state.assert_called_once_with(switch_thing.id, "true")
        assert entity._thing.state == "true"
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_failure(self, mock_client, switch_thing):
        """Test turning switch on when API fails."""
        mock_client.async_set_thing_state = AsyncMock(return_value=False)
        
        entity = DonetickThingSwitch(mock_client, switch_thing)
        entity.async_write_ha_state = MagicMock()
        original_state = switch_thing.state
        
        await entity.async_turn_on()
        
        # State should not change on failure
        assert entity._thing.state == original_state
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off(self, mock_client, switch_thing):
        """Test turning switch off."""
        switch_thing.state = "on"
        mock_client.async_set_thing_state = AsyncMock(return_value=True)
        
        entity = DonetickThingSwitch(mock_client, switch_thing)
        entity.async_write_ha_state = MagicMock()
        
        await entity.async_turn_off()
        
        mock_client.async_set_thing_state.assert_called_once_with(switch_thing.id, "false")
        assert entity._thing.state == "false"
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_error(self, mock_client, switch_thing):
        """Test error handling when turning on."""
        mock_client.async_set_thing_state = AsyncMock(side_effect=Exception("API error"))
        
        entity = DonetickThingSwitch(mock_client, switch_thing)
        original_state = switch_thing.state
        
        # Should not raise
        await entity.async_turn_on()
        
        # State should remain unchanged
        assert entity._thing.state == original_state


class TestDonetickThingNumber:
    """Tests for DonetickThingNumber."""

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    @pytest.fixture
    def number_thing(self):
        """Create number thing."""
        return DonetickThing.from_json(make_thing_json(2, "Room Temperature", "number", "72"))

    def test_native_value(self, mock_client, number_thing):
        """Test native_value returns float."""
        entity = DonetickThingNumber(mock_client, number_thing)
        
        assert entity.native_value == 72.0

    def test_native_value_with_decimal(self, mock_client):
        """Test native_value with decimal value."""
        thing = DonetickThing.from_json(make_thing_json(2, "Temperature", "number", "72.5"))
        
        entity = DonetickThingNumber(mock_client, thing)
        
        assert entity.native_value == 72.5

    def test_native_value_invalid(self, mock_client):
        """Test native_value returns 0 for invalid state."""
        thing = DonetickThing.from_json(make_thing_json(2, "Temperature", "number", "not_a_number"))
        
        entity = DonetickThingNumber(mock_client, thing)
        
        assert entity.native_value == 0.0

    def test_native_value_empty(self, mock_client):
        """Test native_value returns 0 for empty state."""
        thing = DonetickThing.from_json(make_thing_json(2, "Temperature", "number", ""))
        
        entity = DonetickThingNumber(mock_client, thing)
        
        assert entity.native_value == 0.0

    @pytest.mark.asyncio
    async def test_async_set_native_value(self, mock_client, number_thing):
        """Test setting number value."""
        mock_client.async_set_thing_state = AsyncMock(return_value=True)
        
        entity = DonetickThingNumber(mock_client, number_thing)
        entity.async_write_ha_state = MagicMock()
        
        await entity.async_set_native_value(75.5)
        
        # Note: implementation calls str(int(value)), so 75.5 becomes "75"
        mock_client.async_set_thing_state.assert_called_once_with(number_thing.id, "75")
        # But state is set using str(value) = "75.5"
        assert entity._thing.state == "75.5"

    @pytest.mark.asyncio
    async def test_async_set_native_value_error(self, mock_client, number_thing):
        """Test error handling when setting value."""
        mock_client.async_set_thing_state = AsyncMock(side_effect=Exception("API error"))
        
        entity = DonetickThingNumber(mock_client, number_thing)
        original_state = number_thing.state
        
        # Should not raise
        await entity.async_set_native_value(75.5)
        
        # State should remain unchanged
        assert entity._thing.state == original_state


class TestDonetickThingText:
    """Tests for DonetickThingText."""

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        return AsyncMock()

    @pytest.fixture
    def text_thing(self):
        """Create text thing."""
        return DonetickThing.from_json(make_thing_json(3, "Status Message", "text", "Hello World"))

    def test_native_value(self, mock_client, text_thing):
        """Test native_value returns thing state."""
        entity = DonetickThingText(mock_client, text_thing)
        
        assert entity.native_value == "Hello World"

    def test_native_value_empty(self, mock_client):
        """Test native_value with empty state."""
        thing = DonetickThing.from_json(make_thing_json(3, "Message", "text", ""))
        
        entity = DonetickThingText(mock_client, thing)
        
        assert entity.native_value == ""

    @pytest.mark.asyncio
    async def test_async_set_value(self, mock_client, text_thing):
        """Test setting text value."""
        mock_client.async_set_thing_state = AsyncMock(return_value=True)
        
        entity = DonetickThingText(mock_client, text_thing)
        entity.async_write_ha_state = MagicMock()
        
        await entity.async_set_value("New Message")
        
        mock_client.async_set_thing_state.assert_called_once_with(text_thing.id, "New Message")
        assert entity._thing.state == "New Message"

    @pytest.mark.asyncio
    async def test_async_set_value_empty(self, mock_client, text_thing):
        """Test setting empty text value."""
        mock_client.async_set_thing_state = AsyncMock(return_value=True)
        
        entity = DonetickThingText(mock_client, text_thing)
        entity.async_write_ha_state = MagicMock()
        
        await entity.async_set_value("")
        
        mock_client.async_set_thing_state.assert_called_once_with(text_thing.id, "")

    @pytest.mark.asyncio
    async def test_async_set_value_error(self, mock_client, text_thing):
        """Test error handling when setting value."""
        mock_client.async_set_thing_state = AsyncMock(side_effect=Exception("API error"))
        
        entity = DonetickThingText(mock_client, text_thing)
        original_state = text_thing.state
        
        # Should not raise
        await entity.async_set_value("New Message")
        
        # State should remain unchanged
        assert entity._thing.state == original_state


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {}}
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

    @pytest.mark.asyncio
    async def test_setup_switch_platform(self, mock_hass, mock_config_entry):
        """Test setup creates switch entities for boolean things."""
        things = [
            DonetickThing.from_json(make_thing_json(1, "Light", "boolean", "on")),
            DonetickThing.from_json(make_thing_json(2, "Temp", "number", "72")),
            DonetickThing.from_json(make_thing_json(3, "Msg", "text", "Hello")),
        ]
        
        mock_client = AsyncMock()
        mock_client.async_get_things = AsyncMock(return_value=things)
        
        # Set up hass.data with the expected structure for _create_api_client
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        
        async_add_entities = MagicMock()
        
        # Mock _create_api_client to return our mock client
        with patch("custom_components.donetick.thing._create_api_client", return_value=mock_client):
            from custom_components.donetick import switch
            await switch.async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
        
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Should only include boolean things
        assert len(entities) == 1
        assert isinstance(entities[0], DonetickThingSwitch)

    @pytest.mark.asyncio
    async def test_setup_number_platform(self, mock_hass, mock_config_entry):
        """Test setup creates number entities for number things."""
        things = [
            DonetickThing.from_json(make_thing_json(1, "Light", "boolean", "on")),
            DonetickThing.from_json(make_thing_json(2, "Temp", "number", "72")),
            DonetickThing.from_json(make_thing_json(3, "Msg", "text", "Hello")),
        ]
        
        mock_client = AsyncMock()
        mock_client.async_get_things = AsyncMock(return_value=things)
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        
        async_add_entities = MagicMock()
        
        with patch("custom_components.donetick.thing._create_api_client", return_value=mock_client):
            from custom_components.donetick import number
            await number.async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
        
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], DonetickThingNumber)

    @pytest.mark.asyncio
    async def test_setup_text_platform(self, mock_hass, mock_config_entry):
        """Test setup creates text entities for text things."""
        things = [
            DonetickThing.from_json(make_thing_json(1, "Light", "boolean", "on")),
            DonetickThing.from_json(make_thing_json(2, "Temp", "number", "72")),
            DonetickThing.from_json(make_thing_json(3, "Msg", "text", "Hello")),
        ]
        
        mock_client = AsyncMock()
        mock_client.async_get_things = AsyncMock(return_value=things)
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        
        async_add_entities = MagicMock()
        
        with patch("custom_components.donetick.thing._create_api_client", return_value=mock_client):
            from custom_components.donetick import text
            await text.async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
        
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], DonetickThingText)

    @pytest.mark.asyncio
    async def test_setup_no_matching_things(self, mock_hass, mock_config_entry):
        """Test setup handles no matching things gracefully."""
        things = [
            DonetickThing.from_json(make_thing_json(2, "Temp", "number", "72")),
        ]
        
        mock_client = AsyncMock()
        mock_client.async_get_things = AsyncMock(return_value=things)
        
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            CONF_URL: "https://donetick.example.com",
            CONF_AUTH_TYPE: AUTH_TYPE_JWT,
        }
        
        async_add_entities = MagicMock()
        
        with patch("custom_components.donetick.thing._create_api_client", return_value=mock_client):
            from custom_components.donetick import switch
            await switch.async_setup_entry(mock_hass, mock_config_entry, async_add_entities)
        
        # When no matching things, async_add_entities is not called (implementation only calls it if entities exist)
        async_add_entities.assert_not_called()
