"""Unit tests for custom_components.donetick.webhook module."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.webhook import (
    generate_webhook_id,
    get_webhook_url,
    async_register_webhook,
    async_unregister_webhook,
    handle_webhook,
    _trigger_coordinator_refresh,
    _refresh_entry_coordinator,
    _trigger_thing_refresh,
    EVENT_TYPE_MAP,
    REFRESH_TRIGGER_EVENTS,
)
from custom_components.donetick.const import (
    DOMAIN,
    WEBHOOK_EVENT_TASK_COMPLETED,
    WEBHOOK_EVENT_TASK_SKIPPED,
    WEBHOOK_EVENT_TASK_REMINDER,
    WEBHOOK_EVENT_SUBTASK_COMPLETED,
    WEBHOOK_EVENT_THING_CHANGED,
    EVENT_DONETICK_TASK_COMPLETED,
    EVENT_DONETICK_TASK_SKIPPED,
    EVENT_DONETICK_TASK_REMINDER,
    EVENT_DONETICK_SUBTASK_COMPLETED,
    EVENT_DONETICK_THING_CHANGED,
)


class TestEventTypeMap:
    """Tests for EVENT_TYPE_MAP constant."""

    def test_event_type_map_contains_all_events(self):
        """Test that all webhook events are mapped."""
        assert WEBHOOK_EVENT_TASK_COMPLETED in EVENT_TYPE_MAP
        assert WEBHOOK_EVENT_TASK_SKIPPED in EVENT_TYPE_MAP
        assert WEBHOOK_EVENT_TASK_REMINDER in EVENT_TYPE_MAP
        assert WEBHOOK_EVENT_SUBTASK_COMPLETED in EVENT_TYPE_MAP
        assert WEBHOOK_EVENT_THING_CHANGED in EVENT_TYPE_MAP

    def test_event_type_map_correct_mappings(self):
        """Test that events are correctly mapped."""
        assert EVENT_TYPE_MAP[WEBHOOK_EVENT_TASK_COMPLETED] == EVENT_DONETICK_TASK_COMPLETED
        assert EVENT_TYPE_MAP[WEBHOOK_EVENT_TASK_SKIPPED] == EVENT_DONETICK_TASK_SKIPPED
        assert EVENT_TYPE_MAP[WEBHOOK_EVENT_TASK_REMINDER] == EVENT_DONETICK_TASK_REMINDER
        assert EVENT_TYPE_MAP[WEBHOOK_EVENT_SUBTASK_COMPLETED] == EVENT_DONETICK_SUBTASK_COMPLETED
        assert EVENT_TYPE_MAP[WEBHOOK_EVENT_THING_CHANGED] == EVENT_DONETICK_THING_CHANGED


class TestRefreshTriggerEvents:
    """Tests for REFRESH_TRIGGER_EVENTS constant."""

    def test_refresh_trigger_events_contains_expected(self):
        """Test that correct events trigger refresh."""
        assert WEBHOOK_EVENT_TASK_COMPLETED in REFRESH_TRIGGER_EVENTS
        assert WEBHOOK_EVENT_TASK_SKIPPED in REFRESH_TRIGGER_EVENTS
        assert WEBHOOK_EVENT_SUBTASK_COMPLETED in REFRESH_TRIGGER_EVENTS

    def test_refresh_trigger_events_excludes_non_refresh(self):
        """Test that non-refresh events are excluded."""
        assert WEBHOOK_EVENT_TASK_REMINDER not in REFRESH_TRIGGER_EVENTS
        assert WEBHOOK_EVENT_THING_CHANGED not in REFRESH_TRIGGER_EVENTS


class TestGenerateWebhookId:
    """Tests for generate_webhook_id function."""

    def test_generate_webhook_id(self):
        """Test that webhook ID is generated."""
        with patch('custom_components.donetick.webhook.async_generate_id', return_value="test_webhook_123"):
            webhook_id = generate_webhook_id()
        
        assert webhook_id == "test_webhook_123"

    def test_generate_webhook_id_unique(self):
        """Test that generated IDs are unique."""
        ids = []
        with patch('custom_components.donetick.webhook.async_generate_id', side_effect=["id1", "id2", "id3"]):
            for _ in range(3):
                ids.append(generate_webhook_id())
        
        assert len(ids) == len(set(ids))  # All unique


class TestGetWebhookUrl:
    """Tests for get_webhook_url function."""

    def test_get_webhook_url_success(self):
        """Test getting webhook URL."""
        mock_hass = MagicMock()
        
        with patch('custom_components.donetick.webhook.get_url', return_value="http://localhost:8123"):
            url = get_webhook_url(mock_hass, "test_webhook_id")
        
        assert url == "http://localhost:8123/api/webhook/test_webhook_id"

    def test_get_webhook_url_external_preferred(self):
        """Test that external URL is preferred."""
        mock_hass = MagicMock()
        
        with patch('custom_components.donetick.webhook.get_url', return_value="https://example.com"):
            url = get_webhook_url(mock_hass, "test_webhook_id")
        
        assert url == "https://example.com/api/webhook/test_webhook_id"

    def test_get_webhook_url_fallback_on_error(self):
        """Test fallback URL on error."""
        mock_hass = MagicMock()
        
        # First call raises exception, second also raises
        with patch('custom_components.donetick.webhook.get_url', side_effect=[Exception("No external URL"), Exception("No internal URL")]):
            url = get_webhook_url(mock_hass, "test_webhook_id")
        
        assert "your-home-assistant" in url
        assert "test_webhook_id" in url


class TestAsyncRegisterWebhook:
    """Tests for async_register_webhook function."""

    @pytest.mark.asyncio
    async def test_register_webhook(self):
        """Test registering webhook."""
        mock_hass = MagicMock()
        
        with patch('custom_components.donetick.webhook.async_register') as mock_register:
            await async_register_webhook(mock_hass, "test_webhook_id", "test_entry_id")
        
        mock_register.assert_called_once()
        call_args = mock_register.call_args
        assert call_args[0][0] == mock_hass
        assert call_args[0][1] == DOMAIN
        assert "test_webhook_id" == call_args[0][3]
        assert call_args[1]["allowed_methods"] == ["POST"]


class TestAsyncUnregisterWebhook:
    """Tests for async_unregister_webhook function."""

    @pytest.mark.asyncio
    async def test_unregister_webhook(self):
        """Test unregistering webhook."""
        mock_hass = MagicMock()
        
        with patch('custom_components.donetick.webhook.async_unregister') as mock_unregister:
            await async_unregister_webhook(mock_hass, "test_webhook_id")
        
        mock_unregister.assert_called_once_with(mock_hass, "test_webhook_id")


class TestHandleWebhook:
    """Tests for handle_webhook function."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {
            "test_entry_id": {
                "webhook_id": "test_webhook_id",
                "coordinator": MagicMock(),
            }
        }}
        hass.bus = MagicMock()
        hass.bus.async_fire = MagicMock()
        return hass

    @pytest.fixture
    def mock_request_factory(self):
        """Factory for creating mock requests."""
        def _create_request(json_data):
            request = MagicMock()
            request.json = AsyncMock(return_value=json_data)
            return request
        return _create_request

    @pytest.mark.asyncio
    async def test_handle_webhook_task_completed(self, mock_hass, mock_request_factory, sample_webhook_payload_task_completed):
        """Test handling task.completed webhook event."""
        request = mock_request_factory(sample_webhook_payload_task_completed)
        
        with patch('custom_components.donetick.webhook._trigger_coordinator_refresh', new_callable=AsyncMock) as mock_refresh:
            response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        assert response.status == 200
        mock_hass.bus.async_fire.assert_called_once()
        mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_webhook_task_skipped(self, mock_hass, mock_request_factory, sample_webhook_payload_task_skipped):
        """Test handling task.skipped webhook event."""
        request = mock_request_factory(sample_webhook_payload_task_skipped)
        
        with patch('custom_components.donetick.webhook._trigger_coordinator_refresh', new_callable=AsyncMock) as mock_refresh:
            response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        assert response.status == 200
        mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_webhook_thing_changed(self, mock_hass, mock_request_factory, sample_webhook_payload_thing_changed):
        """Test handling thing.changed webhook event."""
        request = mock_request_factory(sample_webhook_payload_thing_changed)
        
        with patch('custom_components.donetick.webhook._trigger_thing_refresh', new_callable=AsyncMock) as mock_refresh:
            response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        assert response.status == 200
        mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_webhook_fires_event(self, mock_hass, mock_request_factory, sample_webhook_payload_task_completed):
        """Test that webhook fires Home Assistant event."""
        request = mock_request_factory(sample_webhook_payload_task_completed)
        
        with patch('custom_components.donetick.webhook._trigger_coordinator_refresh', new_callable=AsyncMock):
            await handle_webhook(mock_hass, "test_webhook_id", request)
        
        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert call_args[0][0] == EVENT_DONETICK_TASK_COMPLETED

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_json(self, mock_hass):
        """Test handling invalid JSON in webhook."""
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
        
        response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        assert response.status == 400
        assert "Invalid JSON" in response.text

    @pytest.mark.asyncio
    async def test_handle_webhook_missing_event_type(self, mock_hass, mock_request_factory):
        """Test handling webhook without event type."""
        request = mock_request_factory({"data": {}})
        
        response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        assert response.status == 400
        assert "Missing event type" in response.text

    @pytest.mark.asyncio
    async def test_handle_webhook_unknown_event_type(self, mock_hass, mock_request_factory):
        """Test handling unknown event type."""
        request = mock_request_factory({
            "type": "unknown.event",
            "data": {},
        })
        
        response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        # Should still return 200, just not fire HA event
        assert response.status == 200
        mock_hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webhook_subtask_completed(self, mock_hass, mock_request_factory, sample_webhook_payload_subtask_completed):
        """Test handling subtask.completed webhook event."""
        request = mock_request_factory(sample_webhook_payload_subtask_completed)
        
        with patch('custom_components.donetick.webhook._trigger_coordinator_refresh', new_callable=AsyncMock) as mock_refresh:
            response = await handle_webhook(mock_hass, "test_webhook_id", request)
        
        assert response.status == 200
        # subtask.completed should trigger refresh
        mock_refresh.assert_called_once()


class TestTriggerCoordinatorRefresh:
    """Tests for _trigger_coordinator_refresh function."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_request_refresh = AsyncMock()
        
        hass = MagicMock()
        hass.data = {DOMAIN: {
            "test_entry_id": {
                "coordinator": mock_coordinator,
            }
        }}
        return hass

    @pytest.mark.asyncio
    async def test_trigger_refresh_with_entry_id(self, mock_hass):
        """Test triggering refresh with specific entry ID."""
        await _trigger_coordinator_refresh(mock_hass, "test_entry_id")
        
        mock_hass.data[DOMAIN]["test_entry_id"]["coordinator"].async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_refresh_all_entries(self, mock_hass):
        """Test triggering refresh for all entries when no ID provided."""
        mock_coordinator2 = MagicMock()
        mock_coordinator2.async_request_refresh = AsyncMock()
        mock_hass.data[DOMAIN]["another_entry"] = {"coordinator": mock_coordinator2}
        
        await _trigger_coordinator_refresh(mock_hass, None)
        
        # Both coordinators should be refreshed
        mock_hass.data[DOMAIN]["test_entry_id"]["coordinator"].async_request_refresh.assert_called_once()
        mock_coordinator2.async_request_refresh.assert_called_once()


class TestRefreshEntryCoordinator:
    """Tests for _refresh_entry_coordinator function."""

    @pytest.mark.asyncio
    async def test_refresh_with_coordinator(self):
        """Test refresh when coordinator exists."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_request_refresh = AsyncMock()
        
        mock_hass = MagicMock()
        mock_hass.data = {DOMAIN: {
            "test_entry": {
                "coordinator": mock_coordinator,
            }
        }}
        
        await _refresh_entry_coordinator(mock_hass, "test_entry")
        
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_without_coordinator(self):
        """Test refresh when no coordinator exists."""
        mock_hass = MagicMock()
        mock_hass.data = {DOMAIN: {
            "test_entry": {}
        }}
        
        # Should not raise
        await _refresh_entry_coordinator(mock_hass, "test_entry")

    @pytest.mark.asyncio
    async def test_refresh_missing_entry(self):
        """Test refresh with missing entry."""
        mock_hass = MagicMock()
        mock_hass.data = {DOMAIN: {}}
        
        # Should not raise
        await _refresh_entry_coordinator(mock_hass, "nonexistent_entry")


class TestTriggerThingRefresh:
    """Tests for _trigger_thing_refresh function."""

    @pytest.mark.asyncio
    async def test_trigger_thing_refresh_with_coordinator(self):
        """Test thing refresh when coordinator exists."""
        mock_thing_coordinator = MagicMock()
        mock_thing_coordinator.async_request_refresh = AsyncMock()
        
        mock_hass = MagicMock()
        mock_hass.data = {DOMAIN: {
            "test_entry": {
                "thing_coordinator": mock_thing_coordinator,
            }
        }}
        
        event_data = {"id": 1, "to_state": "on"}
        
        await _trigger_thing_refresh(mock_hass, "test_entry", event_data)
        
        mock_thing_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_thing_refresh_without_coordinator(self):
        """Test thing refresh when no coordinator exists."""
        mock_hass = MagicMock()
        mock_hass.data = {DOMAIN: {
            "test_entry": {}
        }}
        
        event_data = {"id": 1, "to_state": "on"}
        
        # Should not raise
        await _trigger_thing_refresh(mock_hass, "test_entry", event_data)

    @pytest.mark.asyncio
    async def test_trigger_thing_refresh_no_entry_id(self):
        """Test thing refresh without entry ID."""
        mock_hass = MagicMock()
        mock_hass.data = {DOMAIN: {}}
        
        event_data = {"id": 1, "to_state": "on"}
        
        # Should not raise
        await _trigger_thing_refresh(mock_hass, None, event_data)
