"""The Donetick integration."""
import logging
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
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
    CONF_WEBHOOK_ID,
)
from .api import DonetickApiClient
from .webhook import (
    generate_webhook_id,
    get_webhook_url,
    async_register_webhook,
    async_unregister_webhook,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.TODO, Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.TEXT]


SERVICE_COMPLETE_TASK = "complete_task"
SERVICE_CREATE_TASK = "create_task"
SERVICE_UPDATE_TASK = "update_task"
SERVICE_DELETE_TASK = "delete_task"
SERVICE_CREATE_TASK_FORM = "create_task_form"

COMPLETE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): cv.positive_int,
    vol.Optional("completed_by"): cv.positive_int,
    vol.Optional("config_entry_id"): cv.string,
})

CREATE_TASK_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,
    vol.Optional("created_by"): cv.positive_int,
    vol.Optional("priority"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
    vol.Optional("frequency_type"): vol.In([
        "once", "daily", "weekly", "monthly", "yearly",
        "interval", "days_of_the_week", "day_of_the_month", "no_repeat"
    ]),
    vol.Optional("frequency"): cv.positive_int,
    vol.Optional("assignees"): cv.string,  # Comma-separated user IDs
    vol.Optional("assign_strategy"): vol.In([
        "random", "least_assigned", "least_completed", 
        "keep_last_assigned", "random_except_last_assigned", "round_robin", "no_assignee"
    ]),
    vol.Optional("points"): vol.Coerce(int),
    vol.Optional("notification"): cv.boolean,
    vol.Optional("require_approval"): cv.boolean,
    vol.Optional("is_private"): cv.boolean,
    vol.Optional("config_entry_id"): cv.string,
})

UPDATE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): cv.positive_int,
    vol.Optional("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,
    vol.Optional("priority"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
    vol.Optional("frequency_type"): vol.In([
        "once", "daily", "weekly", "monthly", "yearly",
        "interval", "days_of_the_week", "day_of_the_month", "no_repeat"
    ]),
    vol.Optional("frequency"): cv.positive_int,
    vol.Optional("assignees"): cv.string,  # Comma-separated user IDs
    vol.Optional("assign_strategy"): vol.In([
        "random", "least_assigned", "least_completed", 
        "keep_last_assigned", "random_except_last_assigned", "round_robin", "no_assignee"
    ]),
    vol.Optional("points"): vol.Coerce(int),
    vol.Optional("notification"): cv.boolean,
    vol.Optional("require_approval"): cv.boolean,
    vol.Optional("is_private"): cv.boolean,
    vol.Optional("config_entry_id"): cv.string,
})

DELETE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): cv.positive_int,
    vol.Optional("config_entry_id"): cv.string,
})

CREATE_TASK_FORM_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,  # Will accept datetime from UI
    vol.Optional("priority", default="none"): vol.In(["none", "low", "medium", "high"]),
    vol.Optional("recurrence", default="no_repeat"): vol.In([
        "no_repeat", "daily", "weekly", "monthly", "yearly", "interval"
    ]),
    vol.Optional("recurrence_interval", default=1): cv.positive_int,
    vol.Optional("assignees"): cv.string,  # Comma-separated user IDs
    vol.Optional("assign_strategy", default="random"): vol.In([
        "random", "least_assigned", "least_completed", 
        "keep_last_assigned", "random_except_last_assigned", "round_robin"
    ]),
    vol.Optional("points", default=0): vol.Coerce(int),
    vol.Optional("notification", default=True): cv.boolean,
    vol.Optional("require_approval", default=False): cv.boolean,
    vol.Optional("is_private", default=False): cv.boolean,
    vol.Optional("config_entry_id"): cv.string,
})

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Donetick from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    # Generate or retrieve webhook ID
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        webhook_id = generate_webhook_id()
        # Update the config entry with the webhook ID
        new_data = {**entry.data, CONF_WEBHOOK_ID: webhook_id}
        hass.config_entries.async_update_entry(entry, data=new_data)
    
    # Register the webhook
    await async_register_webhook(hass, webhook_id, entry.entry_id)
    
    # Get webhook URL for display
    webhook_url = get_webhook_url(hass, webhook_id)
    _LOGGER.info("Donetick webhook URL: %s", webhook_url)
    
    # Store auth configuration
    auth_type = entry.data.get(CONF_AUTH_TYPE, AUTH_TYPE_API_KEY)
    
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_URL: entry.data[CONF_URL],
        CONF_AUTH_TYPE: auth_type,
        CONF_SHOW_DUE_IN: entry.data.get(CONF_SHOW_DUE_IN, 7),
        "webhook_id": webhook_id,
        "webhook_url": webhook_url,
    }
    
    # Store auth credentials based on type
    if auth_type == AUTH_TYPE_JWT:
        hass.data[DOMAIN][entry.entry_id][CONF_USERNAME] = entry.data.get(CONF_USERNAME)
        hass.data[DOMAIN][entry.entry_id][CONF_PASSWORD] = entry.data.get(CONF_PASSWORD)
    else:
        hass.data[DOMAIN][entry.entry_id][CONF_TOKEN] = entry.data.get(CONF_TOKEN)
    
    # Register services before setting up platforms
    async def complete_task_handler(call: ServiceCall) -> None:
        await async_complete_task_service(hass, call)
    
    async def create_task_handler(call: ServiceCall) -> None:
        await async_create_task_service(hass, call)
    
    async def update_task_handler(call: ServiceCall) -> None:
        await async_update_task_service(hass, call)
    
    async def delete_task_handler(call: ServiceCall) -> None:
        await async_delete_task_service(hass, call)
    
    async def create_task_form_handler(call: ServiceCall) -> None:
        await async_create_task_form_service(hass, call)
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPLETE_TASK,
        complete_task_handler,
        schema=COMPLETE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TASK,
        create_task_handler,
        schema=CREATE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_TASK,
        update_task_handler,
        schema=UPDATE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_TASK,
        delete_task_handler,
        schema=DELETE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TASK_FORM,
        create_task_form_handler,
        schema=CREATE_TASK_FORM_SCHEMA,
    )
    _LOGGER.debug("Registered services: %s.%s, %s.%s, %s.%s, %s.%s, %s.%s", 
                  DOMAIN, SERVICE_COMPLETE_TASK, DOMAIN, SERVICE_CREATE_TASK, 
                  DOMAIN, SERVICE_UPDATE_TASK, DOMAIN, SERVICE_DELETE_TASK,
                  DOMAIN, SERVICE_CREATE_TASK_FORM)
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.add_update_listener(async_reload_entry)
    
    return True

async def async_complete_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the complete_task service call."""
    task_id = call.data["task_id"]
    completed_by = call.data.get("completed_by")
    config_entry_id = call.data.get("config_entry_id")
    
    # Find the config entry to use
    entry = None
    if config_entry_id:
        # Check if it's a config entry ID
        entry = hass.config_entries.async_get_entry(config_entry_id)
        
        # If not found, check if it's an entity ID and extract config entry from it
        if not entry and config_entry_id.startswith("todo."):
            entity_registry = hass.helpers.entity_registry.async_get()
            entity_entry = entity_registry.async_get(config_entry_id)
            if entity_entry:
                entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        
        if not entry:
            _LOGGER.error("Config entry not found for: %s", config_entry_id)
            return
    else:
        # Use the first Donetick integration if no specific entry provided
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)]
        if not entries:
            _LOGGER.error("No Donetick integration found")
            return
        entry = entries[0]
    
    # Get API client
    client = _get_api_client(hass, entry.entry_id)
    
    try:
        result = await client.async_complete_task(task_id, completed_by)
        _LOGGER.info("Task %d completed successfully by user %s", task_id, completed_by or "default")
        
        # Trigger coordinator refresh for all todo entities
        entity_registry = hass.helpers.entity_registry.async_get()
        for entity_id in hass.states.async_entity_ids("todo"):
            if entity_id.startswith("todo.dt_"):
                entity_entry = entity_registry.async_get(entity_id)
                if entity_entry and entity_entry.config_entry_id == entry.entry_id:
                    # Trigger update - this will refresh the coordinator
                    await hass.helpers.entity_component.async_update_entity(entity_id)
                    
    except Exception as e:
        _LOGGER.error("Failed to complete task %d: %s", task_id, e)

async def async_create_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the create_task service call."""
    name = call.data["name"]
    description = call.data.get("description")
    due_date = call.data.get("due_date")
    created_by = call.data.get("created_by")
    config_entry_id = call.data.get("config_entry_id")
    
    # Enhanced fields (only work with JWT auth)
    priority = call.data.get("priority")
    frequency_type = call.data.get("frequency_type")
    frequency = call.data.get("frequency")
    assignees_str = call.data.get("assignees")
    assign_strategy = call.data.get("assign_strategy")
    points = call.data.get("points")
    notification = call.data.get("notification")
    require_approval = call.data.get("require_approval")
    is_private = call.data.get("is_private")
    
    # Parse assignees from comma-separated string to list of ints
    assignees = None
    if assignees_str:
        try:
            assignees = [int(x.strip()) for x in assignees_str.split(",") if x.strip()]
        except ValueError:
            _LOGGER.error("Invalid assignees format. Expected comma-separated user IDs.")
            return
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return
    
    # Get API client
    client = _get_api_client(hass, entry.entry_id)
    
    try:
        result = await client.async_create_task(
            name=name,
            description=description,
            due_date=due_date,
            created_by=created_by,
            priority=priority,
            frequency_type=frequency_type,
            frequency=frequency,
            assignees=assignees,
            assign_strategy=assign_strategy,
            points=points,
            notification=notification,
            require_approval=require_approval,
            is_private=is_private,
        )
        _LOGGER.info("Task '%s' created successfully with ID %d", name, result.id)
        
        # Trigger coordinator refresh for all todo entities
        await _refresh_todo_entities(hass, entry.entry_id)
                    
    except Exception as e:
        _LOGGER.error("Failed to create task '%s': %s", name, e)

async def async_update_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the update_task service call."""
    task_id = call.data["task_id"]
    name = call.data.get("name")
    description = call.data.get("description")
    due_date = call.data.get("due_date")
    config_entry_id = call.data.get("config_entry_id")
    
    # Enhanced fields (only work with JWT auth)
    priority = call.data.get("priority")
    frequency_type = call.data.get("frequency_type")
    frequency = call.data.get("frequency")
    assignees_str = call.data.get("assignees")
    assign_strategy = call.data.get("assign_strategy")
    points = call.data.get("points")
    notification = call.data.get("notification")
    require_approval = call.data.get("require_approval")
    is_private = call.data.get("is_private")
    
    # Parse assignees from comma-separated string to list of ints
    assignees = None
    if assignees_str:
        try:
            assignees = [int(x.strip()) for x in assignees_str.split(",") if x.strip()]
        except ValueError:
            _LOGGER.error("Invalid assignees format. Expected comma-separated user IDs.")
            return
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return
    
    # Get API client
    client = _get_api_client(hass, entry.entry_id)
    
    try:
        result = await client.async_update_task(
            task_id=task_id,
            name=name,
            description=description,
            due_date=due_date,
            priority=priority,
            frequency_type=frequency_type,
            frequency=frequency,
            assignees=assignees,
            assign_strategy=assign_strategy,
            points=points,
            notification=notification,
            require_approval=require_approval,
            is_private=is_private,
        )
        _LOGGER.info("Task %d updated successfully", task_id)
        
        # Trigger coordinator refresh for all todo entities
        await _refresh_todo_entities(hass, entry.entry_id)
                    
    except Exception as e:
        _LOGGER.error("Failed to update task %d: %s", task_id, e)

async def async_delete_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the delete_task service call."""
    task_id = call.data["task_id"]
    config_entry_id = call.data.get("config_entry_id")
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return
    
    # Get API client
    client = _get_api_client(hass, entry.entry_id)
    
    try:
        success = await client.async_delete_task(task_id)
        if success:
            _LOGGER.info("Task %d deleted successfully", task_id)
            
            # Trigger coordinator refresh for all todo entities
            await _refresh_todo_entities(hass, entry.entry_id)
        else:
            _LOGGER.error("Failed to delete task %d", task_id)
                    
    except Exception as e:
        _LOGGER.error("Failed to delete task %d: %s", task_id, e)

async def async_create_task_form_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the create_task_form service call with user-friendly field names."""
    name = call.data["name"]
    description = call.data.get("description")
    due_date_raw = call.data.get("due_date")
    config_entry_id = call.data.get("config_entry_id")
    
    # Map user-friendly priority to API priority (0-3)
    priority_map = {"none": 0, "low": 1, "medium": 2, "high": 3}
    priority_str = call.data.get("priority", "none")
    priority = priority_map.get(priority_str, 0)
    
    # Map recurrence to frequency_type
    recurrence = call.data.get("recurrence", "no_repeat")
    frequency_type = recurrence
    
    # Get interval for custom recurrence
    frequency = None
    if recurrence == "interval":
        frequency = call.data.get("recurrence_interval", 1)
    
    # Parse assignees from comma-separated string to list of ints
    assignees_str = call.data.get("assignees")
    assignees = None
    if assignees_str:
        try:
            assignees = [int(x.strip()) for x in assignees_str.split(",") if x.strip()]
        except ValueError:
            _LOGGER.error("Invalid assignees format. Expected comma-separated user IDs.")
            return
    
    # Get other options
    assign_strategy = call.data.get("assign_strategy", "random")
    points = call.data.get("points", 0)
    notification = call.data.get("notification", True)
    require_approval = call.data.get("require_approval", False)
    is_private = call.data.get("is_private", False)
    
    # Process due_date - handle datetime from UI selector
    due_date = None
    if due_date_raw:
        _LOGGER.debug("Raw due_date received: %r (type: %s)", due_date_raw, type(due_date_raw).__name__)
        if isinstance(due_date_raw, str):
            # Strip whitespace that may come from Jinja templates
            due_date = due_date_raw.strip()
            # If it's just a date or datetime without timezone, add Z suffix
            if due_date and 'T' in due_date and not (due_date.endswith('Z') or '+' in due_date or due_date.count(':') >= 2 and '-' in due_date[-6:]):
                due_date = due_date + "Z"
            _LOGGER.debug("Processed due_date string: %r", due_date)
        else:
            # Convert datetime object to RFC3339 format
            try:
                from datetime import datetime
                if hasattr(due_date_raw, 'isoformat'):
                    due_date = due_date_raw.isoformat() + "Z"
                else:
                    due_date = str(due_date_raw)
                _LOGGER.debug("Converted due_date from object: %r", due_date)
            except Exception as e:
                _LOGGER.warning("Could not parse due date: %s", e)
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return
    
    # Get API client
    client = _get_api_client(hass, entry.entry_id)
    
    _LOGGER.debug(
        "Creating task via form - name: %r, due_date: %r, priority: %r, frequency_type: %r",
        name, due_date, priority, frequency_type
    )
    
    try:
        result = await client.async_create_task(
            name=name,
            description=description,
            due_date=due_date,
            priority=priority,
            frequency_type=frequency_type,
            frequency=frequency,
            assignees=assignees,
            assign_strategy=assign_strategy if assignees else None,
            points=points,
            notification=notification,
            require_approval=require_approval,
            is_private=is_private,
        )
        _LOGGER.info("Task '%s' created successfully with ID %d (via form)", name, result.id)
        
        # Trigger coordinator refresh for all todo entities
        await _refresh_todo_entities(hass, entry.entry_id)
                    
    except Exception as e:
        _LOGGER.error("Failed to create task '%s': %s", name, e)

async def _get_config_entry(hass: HomeAssistant, config_entry_id: str = None) -> ConfigEntry:
    """Get the config entry to use for the service call."""
    entry = None
    if config_entry_id:
        # Check if it's a config entry ID
        entry = hass.config_entries.async_get_entry(config_entry_id)
        
        # If not found, check if it's an entity ID and extract config entry from it
        if not entry and config_entry_id.startswith("todo."):
            entity_registry = hass.helpers.entity_registry.async_get()
            entity_entry = entity_registry.async_get(config_entry_id)
            if entity_entry:
                entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        
        if not entry:
            _LOGGER.error("Config entry not found for: %s", config_entry_id)
            return None
    else:
        # Use the first Donetick integration if no specific entry provided
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)]
        if not entries:
            _LOGGER.error("No Donetick integration found")
            return None
        entry = entries[0]
    
    return entry

async def _refresh_todo_entities(hass: HomeAssistant, config_entry_id: str) -> None:
    """Refresh all todo entities for the given config entry."""
    entity_registry = hass.helpers.entity_registry.async_get()
    for entity_id in hass.states.async_entity_ids("todo"):
        if entity_id.startswith("todo.dt_"):
            entity_entry = entity_registry.async_get(entity_id)
            if entity_entry and entity_entry.config_entry_id == config_entry_id:
                # Trigger update - this will refresh the coordinator
                await hass.helpers.entity_component.async_update_entity(entity_id)


def _get_api_client(hass: HomeAssistant, entry_id: str) -> DonetickApiClient:
    """Create an API client for the given config entry."""
    session = async_get_clientsession(hass)
    entry_data = hass.data[DOMAIN][entry_id]
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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unregister the webhook
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id:
        await async_unregister_webhook(hass, webhook_id)
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Remove services if this is the last config entry
        if not hass.data[DOMAIN]:
            for service_name in [SERVICE_COMPLETE_TASK, SERVICE_CREATE_TASK, SERVICE_UPDATE_TASK, SERVICE_DELETE_TASK, SERVICE_CREATE_TASK_FORM]:
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
            _LOGGER.debug("Removed services: %s.%s, %s.%s, %s.%s, %s.%s, %s.%s", 
                          DOMAIN, SERVICE_COMPLETE_TASK, DOMAIN, SERVICE_CREATE_TASK, 
                          DOMAIN, SERVICE_UPDATE_TASK, DOMAIN, SERVICE_DELETE_TASK,
                          DOMAIN, SERVICE_CREATE_TASK_FORM)
    
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)