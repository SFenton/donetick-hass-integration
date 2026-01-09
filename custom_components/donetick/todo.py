"""Todo for Donetick integration."""
import logging
from datetime import datetime, timedelta, date
from typing import Any, Optional
from zoneinfo import ZoneInfo
import hashlib
import json

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature, 
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
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
from .api import DonetickApiClient
from .model import DonetickTask, DonetickMember

_LOGGER = logging.getLogger(__name__)


class DonetickTaskCoordinator(DataUpdateCoordinator):
    """Coordinator that manages task data with incremental updates.
    
    This coordinator stores tasks by ID and tracks changes via hashing
    to minimize unnecessary UI updates.
    """
    
    def __init__(
        self,
        hass: HomeAssistant,
        client: DonetickApiClient,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="donetick_todo",
            update_interval=update_interval,
        )
        self._client = client
        self._tasks_by_id: dict[int, DonetickTask] = {}
        self._task_hashes: dict[int, str] = {}
        self._data_version: int = 0  # Increments only when data changes
    
    def _hash_task(self, task: DonetickTask) -> str:
        """Generate a hash of task data to detect changes."""
        # Include all fields that matter for display
        task_data = {
            "id": task.id,
            "name": task.name,
            "description": task.description,
            "next_due_date": task.next_due_date.isoformat() if task.next_due_date else None,
            "is_active": task.is_active,
            "assigned_to": task.assigned_to,
            "priority": task.priority,
            "frequency_type": task.frequency_type,
        }
        return hashlib.md5(json.dumps(task_data, sort_keys=True).encode()).hexdigest()
    
    async def _async_update_data(self) -> dict[int, DonetickTask]:
        """Fetch tasks and detect changes."""
        new_tasks = await self._client.async_get_tasks()
        
        # Convert to dict by ID
        new_tasks_by_id = {task.id: task for task in new_tasks}
        new_task_ids = set(new_tasks_by_id.keys())
        old_task_ids = set(self._tasks_by_id.keys())
        
        # Detect changes
        added_ids = new_task_ids - old_task_ids
        removed_ids = old_task_ids - new_task_ids
        common_ids = new_task_ids & old_task_ids
        
        # Check for updates in common tasks
        updated_ids = set()
        new_hashes = {}
        for task_id in new_task_ids:
            new_hashes[task_id] = self._hash_task(new_tasks_by_id[task_id])
            if task_id in common_ids:
                if new_hashes[task_id] != self._task_hashes.get(task_id, ""):
                    updated_ids.add(task_id)
        
        has_changes = bool(added_ids or removed_ids or updated_ids)
        
        if has_changes:
            _LOGGER.debug(
                "Task changes detected - added: %d, removed: %d, updated: %d",
                len(added_ids), len(removed_ids), len(updated_ids)
            )
            # Update our cache
            self._tasks_by_id = new_tasks_by_id
            self._task_hashes = new_hashes
            self._data_version += 1
        else:
            _LOGGER.debug("No task changes detected")
        
        return self._tasks_by_id
    
    @property
    def data_version(self) -> int:
        """Return the current data version (increments on changes)."""
        return self._data_version
    
    @property
    def tasks_list(self) -> list[DonetickTask]:
        """Get tasks as a list for compatibility."""
        if self.data is None:
            return []
        return list(self.data.values())
    
    def get_task(self, task_id: int) -> DonetickTask | None:
        """Get a specific task by ID."""
        if self.data is None:
            return None
        return self.data.get(task_id)


def _create_api_client(hass: HomeAssistant, config_entry: ConfigEntry) -> DonetickApiClient:
    """Create an API client for the config entry."""
    session = async_get_clientsession(hass)
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    auth_type = entry_data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
    
    if auth_type == AUTH_TYPE_JWT:
        return DonetickApiClient(
            entry_data[CONF_URL],
            session,
            username=entry_data.get(CONF_USERNAME),
            password=entry_data.get(CONF_PASSWORD),
            auth_type=AUTH_TYPE_JWT,
        )
    else:
        return DonetickApiClient(
            entry_data[CONF_URL],
            session,
            api_token=entry_data.get(CONF_TOKEN),
            auth_type=AUTH_TYPE_API_KEY,
        )

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Donetick todo platform."""
    client = _create_api_client(hass, config_entry)

    refresh_interval_seconds = config_entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
    coordinator = DonetickTaskCoordinator(
        hass,
        client,
        update_interval=timedelta(seconds=refresh_interval_seconds),
    )

    await coordinator.async_config_entry_first_refresh()
    
    # Store the coordinator in hass.data so the webhook can trigger refreshes
    hass.data[DOMAIN][config_entry.entry_id]["coordinator"] = coordinator

    entities = []
    
    # Create unified list if enabled (check options first, then data)
    create_unified = config_entry.options.get(CONF_CREATE_UNIFIED_LIST, config_entry.data.get(CONF_CREATE_UNIFIED_LIST, True))
    if create_unified:
        entity = DonetickAllTasksList(coordinator, config_entry, hass)
        entity._circle_members = []  # Will be set after we get members
        entities.append(entity)
    
    # Get circle members for all entities (useful for custom cards)
    circle_members = []
    try:
        circle_members = await client.async_get_circle_members()
        _LOGGER.debug("Found %d circle members", len(circle_members))
        
        # Set circle members on unified entity if it exists
        if entities and hasattr(entities[0], '_circle_members'):
            entities[0]._circle_members = circle_members
            
    except Exception as e:
        _LOGGER.error("Failed to get circle members: %s", e)
    
    # Create per-assignee lists if enabled (check options first, then data)
    create_assignee_lists = config_entry.options.get(CONF_CREATE_ASSIGNEE_LISTS, config_entry.data.get(CONF_CREATE_ASSIGNEE_LISTS, False))
    if create_assignee_lists:
        _LOGGER.debug("Assignee lists enabled in config")
        for member in circle_members:
            if member.is_active:
                _LOGGER.debug("Creating entity for member: %s (ID: %d)", member.display_name, member.user_id)
                entity = DonetickAssigneeTasksList(coordinator, config_entry, member, hass)
                entity._circle_members = circle_members
                entities.append(entity)
    else:
        _LOGGER.debug("Assignee lists not enabled in config")
    
    # Create date-filtered sub-lists if enabled
    create_date_filtered = config_entry.options.get(CONF_CREATE_DATE_FILTERED_LISTS, config_entry.data.get(CONF_CREATE_DATE_FILTERED_LISTS, False))
    if create_date_filtered:
        _LOGGER.debug("Date-filtered lists enabled in config")
        
        # Create global (unassigned) date-filtered lists
        _LOGGER.debug("Creating global date-filtered lists for unassigned tasks")
        for list_type in ["past_due", "due_today", "upcoming"]:
            entity = DonetickDateFilteredTasksList(coordinator, config_entry, hass, list_type, member=None)
            entity._circle_members = circle_members
            entities.append(entity)
        
        # Create date-filtered lists for each member
        for member in circle_members:
            if member.is_active:
                _LOGGER.debug("Creating date-filtered lists for member: %s (ID: %d)", member.display_name, member.user_id)
                for list_type in ["past_due", "due_today", "upcoming"]:
                    entity = DonetickDateFilteredTasksList(coordinator, config_entry, hass, list_type, member=member)
                    entity._circle_members = circle_members
                    entities.append(entity)
    else:
        _LOGGER.debug("Date-filtered lists not enabled in config")
    
    _LOGGER.debug("Creating %d total entities", len(entities))
    async_add_entities(entities)

# Remove old assignee detection function since we now use circle members

class DonetickTodoListBase(CoordinatorEntity, TodoListEntity):
    """Base class for Donetick Todo List entities."""
    
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM | 
        TodoListEntityFeature.UPDATE_TODO_ITEM |
        TodoListEntityFeature.DELETE_TODO_ITEM |
        TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM |
        TodoListEntityFeature.SET_DUE_DATE_ON_ITEM |
        TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    )

    def __init__(self, coordinator: DonetickTaskCoordinator, config_entry: ConfigEntry, hass: HomeAssistant = None) -> None:
        """Initialize the Todo List."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._hass = hass
        self._cached_todo_items: list[TodoItem] | None = None
        self._cached_data_version: int = -1

    def _get_local_now(self) -> datetime:
        """Get the current time in the Home Assistant configured timezone."""
        if self._hass and self._hass.config.time_zone:
            tz = ZoneInfo(self._hass.config.time_zone)
            return datetime.now(tz)
        return datetime.now()
    
    def _get_local_today_start(self) -> datetime:
        """Get the start of today in the Home Assistant configured timezone."""
        local_now = self._get_local_now()
        return local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    def _get_local_today_end(self) -> datetime:
        """Get the end of today in the Home Assistant configured timezone."""
        local_now = self._get_local_now()
        return local_now.replace(hour=23, minute=59, second=59, microsecond=999999)

    def _filter_tasks(self, tasks: list[DonetickTask]) -> list[DonetickTask]:
        """Filter tasks based on entity type. Override in subclasses."""
        return tasks
    
    def _build_todo_items(self) -> list[TodoItem]:
        """Build the list of todo items from filtered tasks."""
        all_tasks = self.coordinator.tasks_list
        filtered_tasks = self._filter_tasks(all_tasks)
        return [
            TodoItem(
                summary=task.name,
                uid="%s--%s" % (task.id, task.next_due_date),
                status=self.get_status(task.next_due_date, task.is_active),
                due=task.next_due_date,
                description=task.description or ""
            ) for task in filtered_tasks if task.is_active
        ]

    @property
    def todo_items(self) -> list[TodoItem] | None: 
        """Return a list of todo items, using cache when data hasn't changed."""
        if self.coordinator.data is None:
            return None
        
        # Check if we need to rebuild the cache
        current_version = self.coordinator.data_version
        if self._cached_data_version != current_version:
            self._cached_todo_items = self._build_todo_items()
            self._cached_data_version = current_version
            _LOGGER.debug(
                "%s: Rebuilt todo items cache (version %d, %d items)",
                self.entity_id or self.name,
                current_version,
                len(self._cached_todo_items)
            )
        
        return self._cached_todo_items

    def get_status(self, due_date: datetime, is_active: bool) -> TodoItemStatus:
        """Return the status of the task."""
        if not is_active:
            return TodoItemStatus.COMPLETED
        return TodoItemStatus.NEEDS_ACTION
    
    @property
    def extra_state_attributes(self):
        """Return additional state attributes for custom cards."""
        attributes = {
            "config_entry_id": self._config_entry.entry_id,
            "donetick_url": self._config_entry.data[CONF_URL],
        }
        
        # Add circle members data for custom card user selection
        if hasattr(self, '_circle_members'):
            attributes["circle_members"] = [
                {
                    "user_id": member.user_id,
                    "display_name": member.display_name,
                    "username": member.username,
                }
                for member in self._circle_members
            ]
        
        return attributes

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a todo item."""
        client = _create_api_client(self.hass, self._config_entry)
        
        try:
            # Determine the created_by user for assignee lists
            created_by = None
            if hasattr(self, '_member'):
                created_by = self._member.user_id
            
            # Convert due date to RFC3339 format if provided
            due_date = None
            if item.due:
                due_date = item.due.isoformat()
            
            result = await client.async_create_task(
                name=item.summary,
                description=item.description,
                due_date=due_date,
                created_by=created_by
            )
            _LOGGER.info("Created task '%s' with ID %d", item.summary, result.id)
            
        except Exception as e:
            _LOGGER.error("Failed to create task '%s': %s", item.summary, e)
            raise
        
        await self.coordinator.async_refresh()

    async def async_update_todo_item(self, item: TodoItem, context = None) -> None:
        """Update a todo item."""
        _LOGGER.debug("Update todo item: %s %s", item.uid, item.status)
        if not self.coordinator.data:
            return None
        
        client = _create_api_client(self.hass, self._config_entry)
        
        task_id = int(item.uid.split("--")[0])
        
        try:
            if item.status == TodoItemStatus.COMPLETED:
                # Complete the task
                _LOGGER.debug("Completing task %s", item.uid)
                # Determine who should complete this task using smart logic
                completed_by = await self._get_completion_user_id(client, item, context)
                
                res = await client.async_complete_task(task_id, completed_by)
                if res.frequency_type != "once":
                    _LOGGER.debug("Task %s is recurring, updating next due date", res.name)
                    item.status = TodoItemStatus.NEEDS_ACTION
                    item.due = res.next_due_date
                    self.async_update_todo_item(item)
            else:
                # Update task properties (summary, description, due date)
                _LOGGER.debug("Updating task %d properties", task_id)
                
                # Convert due date to RFC3339 format if provided
                due_date = None
                if item.due:
                    due_date = item.due.isoformat()
                
                await client.async_update_task(
                    task_id=task_id,
                    name=item.summary,
                    description=item.description,
                    due_date=due_date
                )
                _LOGGER.info("Updated task %d", task_id)
                
        except Exception as e:
            _LOGGER.error("Error updating task %d: %s", task_id, e)
            raise
        
        await self.coordinator.async_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete todo items."""
        client = _create_api_client(self.hass, self._config_entry)
        
        for uid in uids:
            try:
                task_id = int(uid.split("--")[0])
                success = await client.async_delete_task(task_id)
                if success:
                    _LOGGER.info("Deleted task %d", task_id)
                else:
                    _LOGGER.error("Failed to delete task %d", task_id)
                    
            except Exception as e:
                _LOGGER.error("Error deleting task %s: %s", uid, e)
                raise
        
        await self.coordinator.async_refresh()
    
    async def _get_completion_user_id(self, client, item, context=None) -> int | None:
        """Determine who should complete this task using smart logic."""
        
        # Option 1: Context-based completion
        # If this is an assignee-specific list, use that assignee
        if hasattr(self, '_member'):
            _LOGGER.debug("Using assignee from specific list: %s (ID: %d)", self._member.display_name, self._member.user_id)
            return self._member.user_id
        
        # If completing from "All Tasks", find the task's original assignee
        task_id = int(item.uid.split("--")[0])
        if self.coordinator.data:
            for task in self.coordinator.data:
                if task.id == task_id and task.assigned_to:
                    _LOGGER.debug("Using task's original assignee: %d", task.assigned_to)
                    return task.assigned_to
        
        # No default user - rely on context-based or task assignee
        
        _LOGGER.debug("No completion user determined, using default")
        return None

class DonetickAllTasksList(DonetickTodoListBase):
    """Donetick All Tasks List entity."""

    def __init__(self, coordinator: DataUpdateCoordinator, config_entry: ConfigEntry, hass: HomeAssistant = None) -> None:
        """Initialize the All Tasks List."""
        super().__init__(coordinator, config_entry, hass)
        self._attr_unique_id = f"dt_{config_entry.entry_id}_all_tasks"
        self._attr_name = "All Tasks"

    def _filter_tasks(self, tasks):
        """Return all active tasks."""
        return [task for task in tasks if task.is_active]

class DonetickAssigneeTasksList(DonetickTodoListBase):
    """Donetick Assignee-specific Tasks List entity."""

    def __init__(self, coordinator: DataUpdateCoordinator, config_entry: ConfigEntry, member: DonetickMember, hass: HomeAssistant = None) -> None:
        """Initialize the Assignee Tasks List."""
        super().__init__(coordinator, config_entry, hass)
        self._member = member
        self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_tasks"
        self._attr_name = f"{member.display_name}'s Tasks"

    def _filter_tasks(self, tasks):
        """Return tasks assigned to this member."""
        return [task for task in tasks if task.is_active and task.assigned_to == self._member.user_id]


class DonetickDateFilteredTasksList(DonetickTodoListBase):
    """Donetick Date-Filtered Tasks List entity (Past Due, Due Today, Upcoming)."""

    LIST_TYPE_NAMES = {
        "past_due": "Past Due",
        "due_today": "Due Today",
        "upcoming": "Upcoming",
    }

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        list_type: str,
        member: Optional[DonetickMember] = None
    ) -> None:
        """Initialize the Date-Filtered Tasks List."""
        super().__init__(coordinator, config_entry, hass)
        self._list_type = list_type
        self._member = member
        
        list_type_name = self.LIST_TYPE_NAMES.get(list_type, list_type.replace("_", " ").title())
        
        if member:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_{list_type}"
            self._attr_name = f"{member.display_name}'s {list_type_name}"
        else:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_unassigned_{list_type}"
            self._attr_name = f"Unassigned {list_type_name}"

    def _filter_tasks(self, tasks):
        """Filter tasks based on date and assignee criteria."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Filter by assignee
            if self._member:
                # For member-specific lists, only include tasks assigned to this member
                if task.assigned_to != self._member.user_id:
                    continue
            else:
                # For unassigned lists, only include tasks with no assignee
                if task.assigned_to is not None:
                    continue
            
            # Filter by date
            if task.next_due_date is None:
                # Tasks without a due date are excluded from date-filtered lists
                continue
            
            # Ensure task due date is timezone-aware for comparison
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                # If task due date is naive, assume it's in UTC and convert
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            if self._list_type == "past_due":
                # Past due: incomplete tasks with due date < now
                if task_due < local_now:
                    filtered.append(task)
            elif self._list_type == "due_today":
                # Due today: incomplete tasks due today but not yet past due
                # Due date >= start of today AND due date <= end of today AND due date >= now
                if today_start <= task_due <= today_end and task_due >= local_now:
                    filtered.append(task)
            elif self._list_type == "upcoming":
                # Upcoming: incomplete tasks with due date > end of today
                if task_due > today_end:
                    filtered.append(task)
        
        return filtered


# Keep the old class for backward compatibility
class DonetickTodoListEntity(DonetickAllTasksList):
    """Donetick Todo List entity."""
    
    """Legacy Donetick Todo List entity for backward compatibility."""
    
    def __init__(self, coordinator: DataUpdateCoordinator, config_entry: ConfigEntry, hass: HomeAssistant = None) -> None:
        """Initialize the Todo List."""
        super().__init__(coordinator, config_entry, hass)
        self._attr_unique_id = f"dt_{config_entry.entry_id}"

