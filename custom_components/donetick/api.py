"""API client for Donetick with JWT authentication support."""
import logging
from datetime import datetime, timedelta, timezone
import json
import asyncio
from typing import List, Optional, Any, Dict
import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_TIMEOUT,
    AUTH_TYPE_JWT,
    AUTH_TYPE_API_KEY,
    JWT_REFRESH_BUFFER,
    FREQUENCY_ONCE,
    ASSIGN_RANDOM,
)
from .model import DonetickTask, DonetickThing, DonetickMember

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Exception raised for authentication failures."""
    pass


class DonetickApiClient:
    """API client for Donetick with JWT authentication."""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
        username: str = None,
        password: str = None,
        api_token: str = None,
        auth_type: str = AUTH_TYPE_JWT,
    ) -> None:
        """Initialize the API client.
        
        Args:
            base_url: The Donetick server URL
            session: aiohttp client session
            username: Username for JWT auth
            password: Password for JWT auth
            api_token: Legacy API token for eAPI auth
            auth_type: "jwt" or "api_key"
        """
        self._base_url = base_url.rstrip('/')
        self._session = session
        self._username = username
        self._password = password
        self._api_token = api_token
        self._auth_type = auth_type
        
        # JWT state
        self._jwt_token: Optional[str] = None
        self._jwt_expiry: Optional[datetime] = None
        self._auth_lock = asyncio.Lock()

    @property
    def is_jwt_auth(self) -> bool:
        """Check if using JWT authentication."""
        return self._auth_type == AUTH_TYPE_JWT

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid JWT token, refreshing if needed."""
        if not self.is_jwt_auth:
            return
        
        async with self._auth_lock:
            now = datetime.now(timezone.utc)
            
            # Check if token exists and is still valid (with buffer)
            if self._jwt_token and self._jwt_expiry:
                buffer = timedelta(seconds=JWT_REFRESH_BUFFER)
                if now < (self._jwt_expiry - buffer):
                    return  # Token is still valid
            
            # Need to authenticate or refresh
            if self._jwt_token:
                # Try to refresh first
                try:
                    await self._refresh_token()
                    return
                except Exception as e:
                    _LOGGER.debug("Token refresh failed, will re-authenticate: %s", e)
            
            # Authenticate with username/password
            await self._authenticate()

    async def _authenticate(self) -> None:
        """Authenticate with username and password to get JWT token."""
        if not self._username or not self._password:
            raise AuthenticationError("Username and password required for JWT authentication")
        
        try:
            async with self._session.post(
                f"{self._base_url}/api/v1/auth/login",
                json={"username": self._username, "password": self._password},
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise AuthenticationError("Invalid username or password")
                response.raise_for_status()
                data = await response.json()
                
                # Check for MFA required
                if data.get("mfaRequired"):
                    raise AuthenticationError("MFA is enabled on this account. Please disable MFA or use API key authentication.")
                
                self._jwt_token = data.get("token")
                expire_str = data.get("expire")
                
                if not self._jwt_token:
                    raise AuthenticationError("No token received from server")
                
                # Parse expiry time
                if expire_str:
                    try:
                        self._jwt_expiry = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
                    except ValueError:
                        # Default to 24 hours if parsing fails
                        self._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
                else:
                    self._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
                
                _LOGGER.debug("JWT authentication successful, token expires at %s", self._jwt_expiry)
                
        except aiohttp.ClientError as err:
            _LOGGER.error("Authentication request failed: %s", err)
            raise AuthenticationError(f"Authentication failed: {err}")

    async def _refresh_token(self) -> None:
        """Refresh the JWT token."""
        if not self._jwt_token:
            raise AuthenticationError("No token to refresh")
        
        try:
            headers = {"Authorization": f"Bearer {self._jwt_token}"}
            async with self._session.get(
                f"{self._base_url}/api/v1/auth/refresh",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise AuthenticationError("Token refresh failed - re-authentication required")
                response.raise_for_status()
                data = await response.json()
                
                self._jwt_token = data.get("token")
                expire_str = data.get("expire")
                
                if not self._jwt_token:
                    raise AuthenticationError("No token received from refresh")
                
                if expire_str:
                    try:
                        self._jwt_expiry = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
                    except ValueError:
                        self._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
                else:
                    self._jwt_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
                
                _LOGGER.debug("JWT token refreshed, new expiry: %s", self._jwt_expiry)
                
        except aiohttp.ClientError as err:
            raise AuthenticationError(f"Token refresh failed: {err}")

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers based on auth type."""
        headers = {"Content-Type": "application/json"}
        
        if self.is_jwt_auth and self._jwt_token:
            headers["Authorization"] = f"Bearer {self._jwt_token}"
        elif self._api_token:
            headers["secretkey"] = self._api_token
        
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: dict = None,
        params: dict = None,
        retry_on_401: bool = True,
    ) -> Any:
        """Make an authenticated API request with auto-retry on 401.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (will be prefixed with base_url)
            json_data: JSON body for the request
            params: Query parameters
            retry_on_401: Whether to retry after re-authentication on 401
            
        Returns:
            The JSON response data
        """
        await self._ensure_authenticated()
        headers = self._get_headers()
        
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{endpoint}",
                headers=headers,
                json=json_data,
                params=params,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                # Handle 401 with retry
                if response.status == 401 and retry_on_401 and self.is_jwt_auth:
                    _LOGGER.debug("Got 401, attempting re-authentication")
                    self._jwt_token = None  # Force re-auth
                    self._jwt_expiry = None
                    await self._ensure_authenticated()
                    return await self._request(method, endpoint, json_data, params, retry_on_401=False)
                
                # Log detailed error info for 4xx errors before raising
                if 400 <= response.status < 500:
                    try:
                        error_body = await response.text()
                        _LOGGER.error(
                            "API request failed: %s %s - Status %s, Response: %s",
                            method, endpoint, response.status, error_body
                        )
                    except Exception:
                        _LOGGER.error(
                            "API request failed: %s %s - Status %s",
                            method, endpoint, response.status
                        )
                    if json_data:
                        _LOGGER.error("Request payload was: %s", json.dumps(json_data, default=str))
                
                response.raise_for_status()
                
                # Handle empty responses
                if response.status == 204:
                    return None
                
                return await response.json()
                
        except aiohttp.ClientResponseError as err:
            # Already logged above for 4xx, just re-raise
            raise
        except aiohttp.ClientError as err:
            _LOGGER.error("API request failed: %s %s - %s", method, endpoint, err)
            if json_data:
                _LOGGER.debug("Request payload: %s", json.dumps(json_data, default=str))
            raise

    # ==================== Task/Chore Methods ====================

    async def async_get_tasks(self) -> List[DonetickTask]:
        """Get all tasks from Donetick."""
        if self.is_jwt_auth:
            endpoint = "/api/v1/chores/"
        else:
            endpoint = "/eapi/v1/chore"
        
        try:
            data = await self._request("GET", endpoint)
            
            # Internal API wraps in "res" object
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            
            if not isinstance(data, list):
                _LOGGER.error("Unexpected response format from Donetick API")
                return []
            
            return [DonetickTask.from_json(task) for task in data]
            
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing Donetick response: %s", err)
            return []

    async def async_get_circle_members(self) -> List[DonetickMember]:
        """Get circle members from Donetick."""
        if self.is_jwt_auth:
            endpoint = "/api/v1/circles/members"
        else:
            endpoint = "/eapi/v1/circle/members"
        
        try:
            data = await self._request("GET", endpoint)
            
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            
            if not isinstance(data, list):
                _LOGGER.error("Unexpected response format from Donetick circle members API")
                return []
            
            return [DonetickMember.from_json(member) for member in data]
            
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing Donetick circle members response: %s", err)
            return []

    async def async_get_things(self) -> List[DonetickThing]:
        """Get things from Donetick."""
        if self.is_jwt_auth:
            endpoint = "/api/v1/things"
        else:
            endpoint = "/eapi/v1/things"
        
        try:
            data = await self._request("GET", endpoint)
            
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            
            if not isinstance(data, list):
                _LOGGER.error("Unexpected response format from Donetick things API")
                return []
            
            return [DonetickThing.from_json(thing) for thing in data]
            
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing Donetick things response: %s", err)
            return []

    async def async_get_thing_state(self, thing_id: int) -> Optional[str]:
        """Get the current state of a thing."""
        if self.is_jwt_auth:
            endpoint = f"/api/v1/things/{thing_id}"
        else:
            endpoint = f"/eapi/v1/things/{thing_id}/state"
        
        try:
            data = await self._request("GET", endpoint)
            
            if self.is_jwt_auth and isinstance(data, dict):
                return str(data.get("state", ""))
            return data.get("state")
            
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing Donetick thing state response: %s", err)
            return None

    async def async_set_thing_state(self, thing_id: int, state: str) -> bool:
        """Set the state of a thing."""
        if self.is_jwt_auth:
            endpoint = f"/api/v1/things/{thing_id}/state"
            try:
                await self._request("PUT", endpoint, json_data={"state": state})
                return True
            except Exception as err:
                _LOGGER.error("Error setting thing state: %s", err)
                return False
        else:
            # eAPI uses GET with query param
            endpoint = f"/eapi/v1/things/{thing_id}/state"
            try:
                await self._request("GET", endpoint, params={"state": state})
                return True
            except Exception as err:
                _LOGGER.error("Error setting thing state: %s", err)
                return False

    async def async_change_thing_state(self, thing_id: int, new_state: str = None, increment: int = None) -> Optional[str]:
        """Change the state of a thing."""
        if self.is_jwt_auth:
            endpoint = f"/api/v1/things/{thing_id}/state"
            payload = {}
            if new_state is not None:
                payload["state"] = new_state
            if increment is not None:
                payload["increment"] = increment
            
            try:
                data = await self._request("PUT", endpoint, json_data=payload)
                return str(data.get("state", "")) if data else None
            except Exception as err:
                _LOGGER.error("Error changing thing state: %s", err)
                return None
        else:
            # eAPI change endpoint
            endpoint = f"/eapi/v1/things/{thing_id}/state/change"
            params = {}
            if new_state is not None:
                params["set"] = new_state
            if increment is not None:
                params["op"] = increment
            
            try:
                data = await self._request("GET", endpoint, params=params)
                return data.get("state")
            except Exception as err:
                _LOGGER.error("Error changing thing state: %s", err)
                return None

    async def async_complete_task(self, chore_id: int, completed_by: int = None) -> DonetickTask:
        """Complete a task."""
        if self.is_jwt_auth:
            endpoint = f"/api/v1/chores/{chore_id}/do"
            json_data = {}
            if completed_by:
                json_data["completedBy"] = completed_by
            
            data = await self._request("POST", endpoint, json_data=json_data if json_data else None)
        else:
            endpoint = f"/eapi/v1/chore/{chore_id}/complete"
            params = {"completedBy": completed_by} if completed_by else None
            data = await self._request("POST", endpoint, params=params)
        
        # Handle wrapped response (API may return {"res": {...}})
        if isinstance(data, dict) and "res" in data:
            data = data["res"]
        
        return DonetickTask.from_json(data)

    async def async_create_task(
        self,
        name: str,
        description: str = None,
        due_date: str = None,
        created_by: int = None,
        # Extended properties (JWT only)
        frequency_type: str = None,
        frequency: int = None,
        frequency_metadata: dict = None,
        assignees: List[int] = None,
        assigned_to: int = None,
        assign_strategy: str = None,
        priority: int = None,
        points: int = None,
        labels: List[int] = None,
        notification: bool = None,
        notification_metadata: dict = None,
        is_rolling: bool = None,
        require_approval: bool = None,
        is_private: bool = None,
        completion_window: int = None,
    ) -> DonetickTask:
        """Create a new task.
        
        With JWT auth, supports full ChoreReq properties.
        With API key auth, only supports name, description, due_date, created_by.
        """
        if self.is_jwt_auth:
            # If no assignees provided, use no_assignee strategy to avoid completion issues
            effective_strategy = assign_strategy or ASSIGN_RANDOM
            if not assignees:
                effective_strategy = "no_assignee"
            
            # Build full ChoreReq payload
            payload = {
                "name": name,
                "frequencyType": frequency_type or FREQUENCY_ONCE,
                "assignStrategy": effective_strategy,
                "assignees": [],  # Always include empty array to avoid nil issues
            }
            
            if description:
                payload["description"] = description
            if due_date:
                payload["dueDate"] = due_date
            if frequency is not None:
                payload["frequency"] = frequency
            if frequency_metadata:
                payload["frequencyMetadata"] = frequency_metadata
            if assignees:
                payload["assignees"] = [{"userId": uid} for uid in assignees]
                # Also set assignedTo to the first assignee if not explicitly set
                if assigned_to is None:
                    payload["assignedTo"] = assignees[0]
            if assigned_to is not None:
                payload["assignedTo"] = assigned_to
            if priority is not None:
                payload["priority"] = priority
            if points is not None:
                payload["points"] = points
            if labels:
                payload["labelsV2"] = [{"id": lid} for lid in labels]
            if notification is not None:
                payload["notification"] = notification
                # Always include notificationMetadata when notification is set
                # to avoid nil pointer crash in Donetick's notification planner
                payload["notificationMetadata"] = notification_metadata or {}
            elif notification_metadata:
                payload["notificationMetadata"] = notification_metadata
            if is_rolling is not None:
                payload["isRolling"] = is_rolling
            if require_approval is not None:
                payload["requireApproval"] = require_approval
            if is_private is not None:
                payload["isPrivate"] = is_private
            if completion_window is not None:
                payload["completionWindow"] = completion_window
            
            _LOGGER.debug("Create task payload: %s", payload)
            endpoint = "/api/v1/chores/"
        else:
            # eAPI ChoreLiteReq - limited fields
            payload = {"name": name}
            if description:
                payload["description"] = description
            if due_date:
                payload["dueDate"] = due_date
            if created_by:
                payload["createdBy"] = created_by
            
            endpoint = "/eapi/v1/chore"
        
        data = await self._request("POST", endpoint, json_data=payload)
        
        _LOGGER.debug("Create task raw response type: %s, value: %s", type(data).__name__, data)
        
        # Handle wrapped response (API may return {"res": {...}})
        if isinstance(data, dict) and "res" in data:
            data = data["res"]
            _LOGGER.debug("Unwrapped 'res': %s", data)
        
        # Handle case where API returns just the ID
        if isinstance(data, int):
            _LOGGER.debug("API returned task ID only: %d, fetching full task", data)
            # Fetch the full task data
            tasks = await self.async_get_tasks()
            for task in tasks:
                if task.id == data:
                    return task
            # If we can't find it, create a minimal task object
            return DonetickTask(id=data, name=name, next_due_date=None, status=0, priority=0)
        
        if not isinstance(data, dict):
            _LOGGER.error("Unexpected create task response type: %s, value: %s", type(data).__name__, data)
            raise ValueError(f"Unexpected API response: {data}")
        
        return DonetickTask.from_json(data)

    async def async_update_task(
        self,
        task_id: int,
        name: str = None,
        description: str = None,
        due_date: str = None,
        next_due_date: str = None,
        # Extended properties (JWT only)
        frequency_type: str = None,
        frequency: int = None,
        frequency_metadata: dict = None,
        assignees: List[int] = None,
        assigned_to: int = None,
        assign_strategy: str = None,
        priority: int = None,
        points: int = None,
        labels: List[int] = None,
        notification: bool = None,
        notification_metadata: dict = None,
        is_rolling: bool = None,
        is_active: bool = None,
        require_approval: bool = None,
        is_private: bool = None,
        completion_window: int = None,
    ) -> DonetickTask:
        """Update an existing task.
        
        With JWT auth, supports full ChoreReq properties.
        With API key auth, only supports name, description, due_date, next_due_date.
        
        Args:
            due_date: The task's base/original due date definition
            next_due_date: The next occurrence date (for snoozing/rescheduling)
        """
        if self.is_jwt_auth:
            # First get the current task to merge with updates
            # The internal API requires the full ChoreReq for PUT
            payload = {"id": task_id}
            
            if name:
                payload["name"] = name
            if description is not None:
                payload["description"] = description
            if due_date:
                payload["dueDate"] = due_date
            if next_due_date:
                payload["nextDueDate"] = next_due_date
            if frequency_type:
                payload["frequencyType"] = frequency_type
            if frequency is not None:
                payload["frequency"] = frequency
            if frequency_metadata:
                payload["frequencyMetadata"] = frequency_metadata
            if assignees:
                payload["assignees"] = [{"userId": uid} for uid in assignees]
            if assigned_to is not None:
                payload["assignedTo"] = assigned_to
            if assign_strategy:
                payload["assignStrategy"] = assign_strategy
            if priority is not None:
                payload["priority"] = priority
            if points is not None:
                payload["points"] = points
            if labels is not None:
                payload["labelsV2"] = [{"id": lid} for lid in labels]
            if notification is not None:
                payload["notification"] = notification
            if notification_metadata:
                payload["notificationMetadata"] = notification_metadata
            if is_rolling is not None:
                payload["isRolling"] = is_rolling
            if is_active is not None:
                payload["isActive"] = is_active
            if require_approval is not None:
                payload["requireApproval"] = require_approval
            if is_private is not None:
                payload["isPrivate"] = is_private
            if completion_window is not None:
                payload["completionWindow"] = completion_window
            
            endpoint = "/api/v1/chores/"
            data = await self._request("PUT", endpoint, json_data=payload)
        else:
            # eAPI - limited fields
            payload = {}
            if name:
                payload["name"] = name
            if description is not None:
                payload["description"] = description
            if due_date:
                payload["dueDate"] = due_date
            if next_due_date:
                payload["nextDueDate"] = next_due_date
            
            if not payload:
                raise ValueError("At least one field must be provided for update")
            
            endpoint = f"/eapi/v1/chore/{task_id}"
            data = await self._request("PUT", endpoint, json_data=payload)
        
        # Handle wrapped response (API may return {"res": {...}})
        if isinstance(data, dict) and "res" in data:
            data = data["res"]
        
        _LOGGER.debug("Update task response: %s", data)
        return DonetickTask.from_json(data)

    async def async_delete_task(self, task_id: int) -> bool:
        """Delete a task."""
        if self.is_jwt_auth:
            endpoint = f"/api/v1/chores/{task_id}"
        else:
            endpoint = f"/eapi/v1/chore/{task_id}"
        
        try:
            await self._request("DELETE", endpoint)
            return True
        except Exception as err:
            _LOGGER.error("Error deleting task: %s", err)
            return False

    async def async_skip_task(self, chore_id: int) -> DonetickTask:
        """Skip a task (JWT only)."""
        if not self.is_jwt_auth:
            raise NotImplementedError("Skip task is only available with JWT authentication")
        
        endpoint = f"/api/v1/chores/{chore_id}/skip"
        data = await self._request("POST", endpoint)
        
        # Handle wrapped response (API may return {"res": {...}})
        if isinstance(data, dict) and "res" in data:
            data = data["res"]
        
        return DonetickTask.from_json(data)

    async def async_update_priority(self, chore_id: int, priority: int) -> bool:
        """Update task priority (JWT only)."""
        if not self.is_jwt_auth:
            raise NotImplementedError("Update priority is only available with JWT authentication")
        
        endpoint = f"/api/v1/chores/{chore_id}/priority"
        try:
            await self._request("PUT", endpoint, json_data={"priority": priority})
            return True
        except Exception as err:
            _LOGGER.error("Error updating priority: %s", err)
            return False

    async def async_update_due_date(self, chore_id: int, due_date: str) -> bool:
        """Update task due date (JWT only)."""
        if not self.is_jwt_auth:
            raise NotImplementedError("Update due date is only available with JWT authentication")
        
        endpoint = f"/api/v1/chores/{chore_id}/dueDate"
        try:
            await self._request("PUT", endpoint, json_data={"dueDate": due_date})
            return True
        except Exception as err:
            _LOGGER.error("Error updating due date: %s", err)
            return False

    async def async_archive_task(self, chore_id: int) -> bool:
        """Archive a task (JWT only)."""
        if not self.is_jwt_auth:
            raise NotImplementedError("Archive task is only available with JWT authentication")
        
        endpoint = f"/api/v1/chores/{chore_id}/archive"
        try:
            await self._request("PUT", endpoint)
            return True
        except Exception as err:
            _LOGGER.error("Error archiving task: %s", err)
            return False

    # ==================== User/Circle Methods ====================

    async def async_get_user_profile(self) -> Optional[Dict[str, Any]]:
        """Get current user profile (JWT only)."""
        if not self.is_jwt_auth:
            return None
        
        try:
            data = await self._request("GET", "/api/v1/users/profile")
            return data
        except Exception as err:
            _LOGGER.error("Error getting user profile: %s", err)
            return None

    async def async_get_labels(self) -> List[Dict[str, Any]]:
        """Get all labels (JWT only)."""
        if not self.is_jwt_auth:
            return []
        
        try:
            data = await self._request("GET", "/api/v1/labels")
            if isinstance(data, list):
                return data
            return []
        except Exception as err:
            _LOGGER.error("Error getting labels: %s", err)
            return []

    # ==================== Test Connection ====================

    async def async_test_connection(self) -> bool:
        """Test the API connection."""
        try:
            await self.async_get_tasks()
            return True
        except Exception as err:
            _LOGGER.error("Connection test failed: %s", err)
            return False
