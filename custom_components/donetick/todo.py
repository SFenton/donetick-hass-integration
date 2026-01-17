"""Todo for Donetick integration."""
import logging
import asyncio
from datetime import datetime, timedelta, date
from typing import Any, Callable, Optional
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store

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
    CONF_CREATE_TIME_OF_DAY_LISTS,
    CONF_MORNING_CUTOFF,
    CONF_AFTERNOON_CUTOFF,
    DEFAULT_MORNING_CUTOFF,
    DEFAULT_AFTERNOON_CUTOFF,
    CONF_REFRESH_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    CONF_NOTIFY_ON_PAST_DUE,
    CONF_ASSIGNEE_NOTIFICATIONS,
    NOTIFICATION_REMINDER_INTERVAL,
    NOTIFICATION_STORAGE_KEY,
    NOTIFICATION_STORAGE_VERSION,
    PRIORITY_P1,
    PRIORITY_P2,
    CONF_UPCOMING_DAYS,
    DEFAULT_UPCOMING_DAYS,
    CONF_INCLUDE_UNASSIGNED,
    FREQUENCY_DAILY,
    FREQUENCY_WEEKLY,
    FREQUENCY_INTERVAL,
    FREQUENCY_ONCE,
    FREQUENCY_NO_REPEAT,
    FREQUENCY_MONTHLY,
    FREQUENCY_YEARLY,
    FREQUENCY_DAYS_OF_WEEK,
    FREQUENCY_DAY_OF_MONTH,
    CONF_AUTO_COMPLETE_PAST_DUE,
)
from .api import DonetickApiClient
from .model import DonetickTask, DonetickMember

_LOGGER = logging.getLogger(__name__)

# Track recently completed task IDs to prevent double-completion across list entities
# Key: task_id (int), Value: timestamp when completed
_recently_completed_task_ids: dict[int, datetime] = {}
_completion_lock = asyncio.Lock()


def _is_frequent_recurrence(task: DonetickTask) -> bool:
    """Check if a task recurs too frequently to show in Upcoming.
    
    Returns True if the task should be excluded from Upcoming because it recurs:
    - Daily (frequency_type="daily")
    - Custom/interval days with recurrence <= 4
    
    Weekly and longer recurrences are allowed but shown with limited advance notice.
    """
    freq_type = task.frequency_type
    freq = task.frequency
    
    # Non-recurring tasks are never "frequent recurrence"
    if freq_type in (FREQUENCY_ONCE, FREQUENCY_NO_REPEAT):
        return False
    
    # Daily recurrence - always exclude
    if freq_type == FREQUENCY_DAILY:
        return True
    
    # Weekly recurrence - NOT excluded, will be shown with advance window
    # (handled by _get_recurrence_advance_days)
    
    # Custom/interval recurrence based on unit type
    if freq_type == FREQUENCY_INTERVAL:
        metadata = task.frequency_metadata or {}
        unit = metadata.get("unit", "days")
        
        # Custom days with recurrence <= 4 - exclude
        if unit == "days" and freq <= 4:
            return True
        
        # Custom weeks, months, years - NOT excluded (shown with advance window)
    
    return False


def _get_recurrence_advance_days(task: DonetickTask) -> Optional[int]:
    """Get the number of days in advance to show a recurring task in Upcoming.
    
    Returns min(half of recurrence period rounded down, 7).
    For non-recurring tasks, returns None (always show).
    
    Examples:
    - 20 days recurrence -> 7 days (min of 10 and 7)
    - 3 weeks recurrence -> 7 days (min of 10.5 and 7)
    - 2 weeks recurrence -> 7 days (min of 7 and 7)
    - 10 days recurrence -> 5 days
    - 8 days recurrence -> 4 days
    - 9 days recurrence -> 4 days (floor of 4.5)
    """
    freq_type = task.frequency_type
    freq = task.frequency
    
    # Non-recurring tasks - always show in upcoming (no limit)
    if freq_type in (FREQUENCY_ONCE, FREQUENCY_NO_REPEAT):
        return None
    
    # Calculate recurrence period in days
    recurrence_days = 0
    
    if freq_type == FREQUENCY_DAILY:
        recurrence_days = freq if freq else 1
    elif freq_type == FREQUENCY_WEEKLY:
        recurrence_days = (freq if freq else 1) * 7
    elif freq_type == FREQUENCY_INTERVAL:
        metadata = task.frequency_metadata or {}
        unit = metadata.get("unit", "days")
        freq_val = freq if freq else 1
        
        if unit == "weeks":
            recurrence_days = freq_val * 7
        elif unit == "months":
            recurrence_days = freq_val * 30  # Approximate
        elif unit == "years":
            recurrence_days = freq_val * 365  # Approximate
        else:  # days or unknown
            recurrence_days = freq_val
    else:
        # Other types - default to 7 days
        return 7
    
    # Ensure we don't divide by zero
    if recurrence_days <= 0:
        return 7
    
    # min(half of recurrence rounded down, 7)
    half_recurrence = recurrence_days // 2
    return min(half_recurrence, 7)


def _is_recurrent_task(task: DonetickTask) -> bool:
    """Check if a task is recurrent (has a repeat schedule).
    
    Returns True if the task will generate a new occurrence when completed.
    """
    return task.frequency_type not in (FREQUENCY_ONCE, FREQUENCY_NO_REPEAT, None, "")


def _calculate_next_recurrence_date(task: DonetickTask, local_tz: ZoneInfo) -> Optional[datetime]:
    """Calculate when the next recurrence of a past-due task would occur.
    
    This calculates the theoretical next recurrence date based on the task's
    frequency settings. For past-due tasks, this tells us when to auto-complete
    so the next occurrence can be generated.
    
    Args:
        task: The DonetickTask to calculate recurrence for.
        local_tz: The local timezone for date calculations.
        
    Returns:
        The datetime of the next recurrence, or None if:
        - Task is not recurrent
        - Task has no due date
        - Unable to calculate next recurrence
    """
    if not _is_recurrent_task(task):
        return None
    
    if task.next_due_date is None:
        return None
    
    # Get the original due date in local timezone
    due_date = task.next_due_date
    if due_date.tzinfo is None:
        due_date = due_date.replace(tzinfo=ZoneInfo("UTC"))
    due_date_local = due_date.astimezone(local_tz)
    
    freq_type = task.frequency_type
    freq = task.frequency if task.frequency else 1
    metadata = task.frequency_metadata or {}
    
    # Calculate based on frequency type
    if freq_type == FREQUENCY_DAILY:
        # Daily: add freq days
        next_date = due_date_local + timedelta(days=freq)
        
    elif freq_type == FREQUENCY_WEEKLY:
        # Weekly: add freq weeks
        next_date = due_date_local + timedelta(weeks=freq)
        
    elif freq_type == FREQUENCY_MONTHLY:
        # Monthly: add freq months (approximate)
        # Try to keep same day of month
        year = due_date_local.year
        month = due_date_local.month + freq
        day = due_date_local.day
        
        # Handle year rollover
        while month > 12:
            month -= 12
            year += 1
        
        # Handle days that don't exist in target month (e.g., Jan 31 -> Feb 28)
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(day, max_day)
        
        next_date = due_date_local.replace(year=year, month=month, day=day)
        
    elif freq_type == FREQUENCY_YEARLY:
        # Yearly: add freq years
        next_date = due_date_local.replace(year=due_date_local.year + freq)
        
    elif freq_type == FREQUENCY_INTERVAL:
        # Custom interval based on unit
        unit = metadata.get("unit", "days")
        
        if unit == "days":
            next_date = due_date_local + timedelta(days=freq)
        elif unit == "weeks":
            next_date = due_date_local + timedelta(weeks=freq)
        elif unit == "months":
            # Same logic as monthly
            year = due_date_local.year
            month = due_date_local.month + freq
            day = due_date_local.day
            
            while month > 12:
                month -= 12
                year += 1
            
            import calendar
            max_day = calendar.monthrange(year, month)[1]
            day = min(day, max_day)
            
            next_date = due_date_local.replace(year=year, month=month, day=day)
        elif unit == "years":
            next_date = due_date_local.replace(year=due_date_local.year + freq)
        else:
            # Unknown unit, default to days
            next_date = due_date_local + timedelta(days=freq)
            
    elif freq_type == FREQUENCY_DAYS_OF_WEEK:
        # Days of week recurrence - find next matching day
        # metadata should contain which days (e.g., {"days": [0, 2, 4]} for Mon, Wed, Fri)
        days = metadata.get("days", [])
        if not days:
            return None
        
        # Find the next day in the list after today
        next_date = due_date_local + timedelta(days=1)
        for _ in range(8):  # Max 7 days to find next occurrence
            if next_date.weekday() in days:
                break
            next_date += timedelta(days=1)
        else:
            # Shouldn't happen, but fallback to 1 week
            next_date = due_date_local + timedelta(weeks=1)
            
    elif freq_type == FREQUENCY_DAY_OF_MONTH:
        # Day of month recurrence - find next matching day
        # metadata should contain which day (e.g., {"day": 15})
        target_day = metadata.get("day", due_date_local.day)
        
        # Move to next month and set the target day
        year = due_date_local.year
        month = due_date_local.month + 1
        
        if month > 12:
            month = 1
            year += 1
        
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(target_day, max_day)
        
        next_date = due_date_local.replace(year=year, month=month, day=day)
        
    else:
        # Unknown frequency type
        return None
    
    return next_date


def _get_midnight_of_date(dt: datetime, local_tz: ZoneInfo) -> datetime:
    """Get midnight (00:00:00) of a given date in local timezone."""
    dt_local = dt.astimezone(local_tz) if dt.tzinfo else dt.replace(tzinfo=local_tz)
    return dt_local.replace(hour=0, minute=0, second=0, microsecond=0)


# Global tracker for auto-completion schedules to prevent duplicates across entities
# Key: task_id, Value: (cancel_callback, scheduled_time)
_auto_completion_schedules: dict[int, tuple[Callable, datetime]] = {}


class AutoCompletionManager:
    """Manages automatic completion of past-due recurrent tasks.
    
    When a recurrent task becomes past due, this manager schedules an
    auto-completion at midnight of the day when the next recurrence
    would occur. This allows Donetick to generate the next occurrence
    of the task, keeping the schedule moving forward.
    """
    
    def __init__(
        self, 
        hass: HomeAssistant, 
        config_entry: ConfigEntry,
        client: DonetickApiClient
    ) -> None:
        """Initialize the auto-completion manager."""
        self._hass = hass
        self._config_entry = config_entry
        self._client = client
        self._scheduled_tasks: dict[int, Callable] = {}  # task_id -> cancel_callback
        self._local_tz = ZoneInfo(hass.config.time_zone)
    
    def is_enabled(self) -> bool:
        """Check if auto-completion is enabled."""
        return self._config_entry.data.get(CONF_AUTO_COMPLETE_PAST_DUE, False)
    
    async def process_tasks(self, tasks: list[DonetickTask]) -> None:
        """Process a list of tasks and schedule auto-completions as needed.
        
        This should be called after each coordinator update with the current
        list of all tasks.
        """
        if not self.is_enabled():
            return
        
        local_now = datetime.now(self._local_tz)
        current_task_ids = set()
        
        for task in tasks:
            # Skip inactive tasks
            if not task.is_active:
                continue
                
            # Skip non-recurrent tasks
            if not _is_recurrent_task(task):
                continue
            
            # Skip tasks without due dates
            if task.next_due_date is None:
                continue
            
            # Check if task is past due
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            task_due_local = task_due.astimezone(self._local_tz)
            
            if task_due_local >= local_now:
                # Not past due - cancel any existing schedule
                self._cancel_schedule(task.id)
                continue
            
            current_task_ids.add(task.id)
            
            # Calculate next recurrence date
            next_recurrence = _calculate_next_recurrence_date(task, self._local_tz)
            if next_recurrence is None:
                continue
            
            # Get midnight of the next recurrence day
            midnight = _get_midnight_of_date(next_recurrence, self._local_tz)
            
            # If midnight is in the past, schedule for next available time (now + small delay)
            if midnight <= local_now:
                # The next recurrence day has already started, auto-complete soon
                midnight = local_now + timedelta(seconds=5)
            
            # Schedule auto-completion if not already scheduled for this time
            self._schedule_auto_completion(task, midnight)
        
        # Cancel schedules for tasks that are no longer past due
        scheduled_task_ids = set(self._scheduled_tasks.keys())
        for task_id in scheduled_task_ids - current_task_ids:
            self._cancel_schedule(task_id)
    
    def _schedule_auto_completion(self, task: DonetickTask, when: datetime) -> None:
        """Schedule auto-completion for a task at a specific time."""
        global _auto_completion_schedules
        
        task_id = task.id
        
        # Check if already scheduled for the same time
        if task_id in _auto_completion_schedules:
            _, scheduled_time = _auto_completion_schedules[task_id]
            if abs((scheduled_time - when).total_seconds()) < 60:
                # Already scheduled for approximately the same time
                return
            # Cancel existing schedule
            self._cancel_schedule(task_id)
        
        _LOGGER.debug(
            "Scheduling auto-completion for task %d (%s) at %s",
            task_id, task.name, when.isoformat()
        )
        
        @callback
        def auto_complete_callback(now: datetime) -> None:
            """Callback to auto-complete the task."""
            self._hass.async_create_task(
                self._execute_auto_completion(task_id, task.name)
            )
        
        # Schedule the callback
        cancel = async_track_point_in_time(
            self._hass,
            auto_complete_callback,
            when
        )
        
        self._scheduled_tasks[task_id] = cancel
        _auto_completion_schedules[task_id] = (cancel, when)
    
    async def _execute_auto_completion(self, task_id: int, task_name: str) -> None:
        """Execute the auto-completion of a task."""
        global _auto_completion_schedules
        
        _LOGGER.info(
            "Auto-completing past-due recurrent task %d (%s) to generate next occurrence",
            task_id, task_name
        )
        
        try:
            await self._client.async_complete_task(task_id)
            _LOGGER.info("Successfully auto-completed task %d", task_id)
            
            # Clean up tracking
            if task_id in self._scheduled_tasks:
                del self._scheduled_tasks[task_id]
            if task_id in _auto_completion_schedules:
                del _auto_completion_schedules[task_id]
            
            # Trigger a coordinator refresh to get the new task occurrence
            coordinator = self._hass.data[DOMAIN].get(
                self._config_entry.entry_id, {}
            ).get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
                
        except Exception as e:
            _LOGGER.error("Failed to auto-complete task %d: %s", task_id, e)
    
    def _cancel_schedule(self, task_id: int) -> None:
        """Cancel a scheduled auto-completion."""
        global _auto_completion_schedules
        
        if task_id in self._scheduled_tasks:
            cancel = self._scheduled_tasks.pop(task_id)
            cancel()
            _LOGGER.debug("Cancelled auto-completion schedule for task %d", task_id)
        
        if task_id in _auto_completion_schedules:
            del _auto_completion_schedules[task_id]
    
    def cancel_all(self) -> None:
        """Cancel all scheduled auto-completions."""
        for task_id in list(self._scheduled_tasks.keys()):
            self._cancel_schedule(task_id)


# Global tracker for notification reminders to prevent duplicates across entities
# Key: task_id, Value: (cancel_callback, scheduled_time)
_notification_reminders: dict[int, tuple[Callable, datetime]] = {}


class NotificationManager:
    """Manages past-due notifications for tasks."""
    
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the notification manager."""
        self._hass = hass
        self._config_entry = config_entry
    
    def is_enabled(self) -> bool:
        """Check if notifications are enabled."""
        return self._config_entry.data.get(CONF_NOTIFY_ON_PAST_DUE, False)
    
    def get_notify_service(self, assignee_id: int | None) -> str | None:
        """Get the notification service for a given assignee."""
        if assignee_id is None:
            return None
        
        mappings = self._config_entry.data.get(CONF_ASSIGNEE_NOTIFICATIONS, {})
        return mappings.get(str(assignee_id))
    
    def get_all_notify_services(self) -> list[tuple[str, str]]:
        """Get all configured notification services.
        
        Returns a list of (user_id, notify_service) tuples for all users
        who have a notification service configured.
        """
        mappings = self._config_entry.data.get(CONF_ASSIGNEE_NOTIFICATIONS, {})
        return [(user_id, service) for user_id, service in mappings.items() if service]
    
    def _get_interruption_level(self, priority: int | None) -> str:
        """Determine interruption level based on task priority."""
        if priority == PRIORITY_P1:
            return "critical"
        elif priority == PRIORITY_P2:
            return "time-sensitive"
        else:
            return "passive"
    
    async def send_past_due_notification(
        self,
        task: DonetickTask,
        is_reminder: bool = False
    ) -> bool:
        """Send a notification for a past-due task.
        
        Returns True if notification was sent successfully.
        """
        if not self.is_enabled():
            return False
        
        notify_service = self.get_notify_service(task.assigned_to)
        if not notify_service:
            _LOGGER.debug("No notify service configured for assignee %s", task.assigned_to)
            return False
        
        # Parse service name (format: "notify.service_name")
        if not notify_service.startswith("notify."):
            _LOGGER.warning("Invalid notify service format: %s", notify_service)
            return False
        
        service_name = notify_service.replace("notify.", "")
        
        # Determine interruption level based on priority
        interruption_level = self._get_interruption_level(task.priority)
        
        # Build notification title
        title = f"{task.name} · Past Due"
        if is_reminder:
            title = f"Reminder: {title}"
        
        # Build notification data with actionable buttons
        data = {
            "title": title,
            "message": "Your task is past due. Please complete it or edit its due date to stop receiving notifications.",
            "data": {
                "url": "/at-a-glance/chores",
                "clickAction": "/at-a-glance/chores",
                "tag": f"donetick_task_{task.id}",  # Allows replacing/dismissing notification
                "push": {
                    "sound": "default",
                    "interruption-level": interruption_level,
                },
                # Action buttons for iOS/Android companion app
                "actions": [
                    {
                        "action": f"DONETICK_COMPLETE_{task.id}",
                        "title": "Complete",
                    },
                    {
                        "action": f"DONETICK_SNOOZE_1H_{task.id}",
                        "title": "Snooze 1 Hour",
                    },
                    {
                        "action": f"DONETICK_SNOOZE_1D_{task.id}",
                        "title": "Snooze 1 Day",
                    },
                ],
            },
        }
        
        try:
            await self._hass.services.async_call(
                "notify",
                service_name,
                data,
                blocking=True,
            )
            _LOGGER.info(
                "Sent %s notification for task '%s' (priority: %s, level: %s)",
                "reminder" if is_reminder else "initial",
                task.name,
                task.priority,
                interruption_level
            )
            return True
        except Exception as e:
            _LOGGER.error("Failed to send notification for task '%s': %s", task.name, e)
            return False
    
    async def send_unassigned_past_due_notification(
        self,
        task: DonetickTask,
        is_reminder: bool = False
    ) -> int:
        """Send a notification for an unassigned past-due task to ALL configured users.
        
        Returns the number of notifications sent successfully.
        """
        if not self.is_enabled():
            return 0
        
        all_services = self.get_all_notify_services()
        if not all_services:
            _LOGGER.debug("No notify services configured for unassigned task notification")
            return 0
        
        # Determine interruption level based on priority
        interruption_level = self._get_interruption_level(task.priority)
        
        # Build notification title
        title = f"{task.name} · Past Due"
        if is_reminder:
            title = f"Reminder: {title}"
        
        # Build notification data with actionable buttons
        data = {
            "title": title,
            "message": "An unassigned task is past due. Please assign it or complete it.",
            "data": {
                "url": "/at-a-glance/chores",
                "clickAction": "/at-a-glance/chores",
                "tag": f"donetick_unassigned_task_{task.id}",  # Allows replacing/dismissing notification
                "push": {
                    "sound": "default",
                    "interruption-level": interruption_level,
                },
                # Action buttons for iOS/Android companion app
                "actions": [
                    {
                        "action": f"DONETICK_COMPLETE_{task.id}",
                        "title": "Complete",
                    },
                    {
                        "action": f"DONETICK_SNOOZE_1H_{task.id}",
                        "title": "Snooze 1 Hour",
                    },
                    {
                        "action": f"DONETICK_SNOOZE_1D_{task.id}",
                        "title": "Snooze 1 Day",
                    },
                ],
            },
        }
        
        sent_count = 0
        for user_id, notify_service in all_services:
            if not notify_service.startswith("notify."):
                _LOGGER.warning("Invalid notify service format: %s", notify_service)
                continue
            
            service_name = notify_service.replace("notify.", "")
            
            try:
                await self._hass.services.async_call(
                    "notify",
                    service_name,
                    data,
                    blocking=True,
                )
                _LOGGER.info(
                    "Sent %s unassigned notification for task '%s' to user %s",
                    "reminder" if is_reminder else "initial",
                    task.name,
                    user_id
                )
                sent_count += 1
            except Exception as e:
                _LOGGER.error("Failed to send unassigned notification for task '%s' to user %s: %s", task.name, user_id, e)
        
        return sent_count
    
    def schedule_reminder(
        self,
        task: DonetickTask,
        reminder_time: datetime
    ) -> Callable | None:
        """Schedule a reminder notification for a task.
        
        Returns a cancel callback if scheduled successfully.
        """
        global _notification_reminders
        
        if not self.is_enabled():
            return None
        
        # Check if reminder already scheduled
        if task.id in _notification_reminders:
            existing_cancel, existing_time = _notification_reminders[task.id]
            # If same time, don't reschedule
            if existing_time == reminder_time:
                _LOGGER.debug("Reminder already scheduled for task %d at %s", task.id, reminder_time)
                return existing_cancel
            # Cancel existing and reschedule
            existing_cancel()
            del _notification_reminders[task.id]
        
        async def reminder_callback(now: datetime) -> None:
            """Handle reminder callback."""
            global _notification_reminders
            
            # Remove from tracking
            if task.id in _notification_reminders:
                del _notification_reminders[task.id]
            
            # Check if task is still past due and exists
            coordinator = self._hass.data.get(DOMAIN, {}).get(
                self._config_entry.entry_id, {}
            ).get("coordinator")
            
            if coordinator and coordinator.data:
                current_task = coordinator.data.get(task.id)
                if current_task and current_task.is_active:
                    # Task still exists and is active - send reminder
                    await self.send_past_due_notification(current_task, is_reminder=True)
                    
                    # Schedule next reminder
                    next_reminder = now + timedelta(seconds=NOTIFICATION_REMINDER_INTERVAL)
                    self.schedule_reminder(current_task, next_reminder)
                else:
                    _LOGGER.debug("Task %d no longer active, skipping reminder", task.id)
        
        cancel = async_track_point_in_time(
            self._hass,
            reminder_callback,
            reminder_time
        )
        
        _notification_reminders[task.id] = (cancel, reminder_time)
        _LOGGER.debug(
            "Scheduled reminder for task '%s' (ID: %d) at %s",
            task.name, task.id, reminder_time.isoformat()
        )
        
        return cancel
    
    def schedule_unassigned_reminder(
        self,
        task: DonetickTask,
        reminder_time: datetime
    ) -> Callable | None:
        """Schedule a reminder notification for an unassigned task.
        
        This will notify ALL configured users when the reminder fires.
        Returns a cancel callback if scheduled successfully.
        """
        global _notification_reminders
        
        if not self.is_enabled():
            return None
        
        # Use a distinct key for unassigned task reminders
        reminder_key = f"unassigned_{task.id}"
        
        # Check if reminder already scheduled
        if reminder_key in _notification_reminders:
            existing_cancel, existing_time = _notification_reminders[reminder_key]
            if existing_time == reminder_time:
                _LOGGER.debug("Unassigned reminder already scheduled for task %d at %s", task.id, reminder_time)
                return existing_cancel
            existing_cancel()
            del _notification_reminders[reminder_key]
        
        async def reminder_callback(now: datetime) -> None:
            """Handle reminder callback."""
            global _notification_reminders
            
            if reminder_key in _notification_reminders:
                del _notification_reminders[reminder_key]
            
            coordinator = self._hass.data.get(DOMAIN, {}).get(
                self._config_entry.entry_id, {}
            ).get("coordinator")
            
            if coordinator and coordinator.data:
                current_task = coordinator.data.get(task.id)
                # Check if task is still unassigned, active, and exists
                if current_task and current_task.is_active and current_task.assigned_to is None:
                    await self.send_unassigned_past_due_notification(current_task, is_reminder=True)
                    
                    next_reminder = now + timedelta(seconds=NOTIFICATION_REMINDER_INTERVAL)
                    self.schedule_unassigned_reminder(current_task, next_reminder)
                else:
                    _LOGGER.debug("Task %d no longer unassigned or active, skipping reminder", task.id)
        
        cancel = async_track_point_in_time(
            self._hass,
            reminder_callback,
            reminder_time
        )
        
        _notification_reminders[reminder_key] = (cancel, reminder_time)
        _LOGGER.debug(
            "Scheduled unassigned reminder for task '%s' (ID: %d) at %s",
            task.name, task.id, reminder_time.isoformat()
        )
        
        return cancel
    
    @staticmethod
    def cancel_reminder(task_id: int, is_unassigned: bool = False) -> None:
        """Cancel a scheduled reminder for a task.
        
        Args:
            task_id: The task ID to cancel reminders for.
            is_unassigned: If True, cancel the unassigned reminder instead of the regular one.
        """
        global _notification_reminders
        
        reminder_key = f"unassigned_{task_id}" if is_unassigned else task_id
        
        if reminder_key in _notification_reminders:
            cancel, _ = _notification_reminders[reminder_key]
            cancel()
            del _notification_reminders[reminder_key]
            _LOGGER.debug("Cancelled %s reminder for task %d", "unassigned" if is_unassigned else "assigned", task_id)


class NotificationStore:
    """Persistent storage for notification tracking.
    
    Stores which tasks have been notified about to prevent duplicate 
    notifications after Home Assistant restarts. Tracks task_id -> due_date
    so that we can notify again if a task becomes overdue with a new due date
    (e.g., after completing a recurring task).
    """
    
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the notification store."""
        self._hass = hass
        self._entry_id = entry_id
        self._store = Store(
            hass, 
            NOTIFICATION_STORAGE_VERSION, 
            f"{NOTIFICATION_STORAGE_KEY}_{entry_id}"
        )
        self._data: dict[str, str] = {}  # task_id (str) -> due_date ISO string
        self._loaded = False
    
    async def async_load(self) -> None:
        """Load notification data from storage."""
        if self._loaded:
            return
        
        data = await self._store.async_load()
        if data is not None:
            self._data = data.get("notified_tasks", {})
            _LOGGER.debug(
                "Loaded %d notified task entries from storage",
                len(self._data)
            )
        self._loaded = True
    
    async def async_save(self) -> None:
        """Save notification data to storage."""
        await self._store.async_save({"notified_tasks": self._data})
    
    def was_notified(self, task_id: int, due_date: datetime | None) -> bool:
        """Check if we already sent a notification for this task's due date.
        
        Args:
            task_id: The task ID to check.
            due_date: The task's current due date.
            
        Returns:
            True if we already notified for this exact task+due_date combo.
        """
        task_key = str(task_id)
        if task_key not in self._data:
            return False
        
        # Compare stored due date with current due date
        stored_due = self._data[task_key]
        current_due = due_date.isoformat() if due_date else ""
        
        return stored_due == current_due
    
    def mark_notified(self, task_id: int, due_date: datetime | None) -> None:
        """Mark a task as notified for its current due date.
        
        Args:
            task_id: The task ID that was notified.
            due_date: The task's due date when the notification was sent.
        """
        task_key = str(task_id)
        self._data[task_key] = due_date.isoformat() if due_date else ""
    
    def clear_task(self, task_id: int) -> None:
        """Remove a task from the notified set.
        
        Called when a task is no longer past due (completed, rescheduled, etc.)
        """
        task_key = str(task_id)
        if task_key in self._data:
            del self._data[task_key]
    
    def prune_old_entries(self, current_task_ids: set[int]) -> int:
        """Remove entries for tasks that no longer exist or are no longer past due.
        
        Args:
            current_task_ids: Set of task IDs currently in past_due lists.
            
        Returns:
            Number of entries pruned.
        """
        current_keys = {str(tid) for tid in current_task_ids}
        old_keys = set(self._data.keys()) - current_keys
        
        for key in old_keys:
            del self._data[key]
        
        if old_keys:
            _LOGGER.debug("Pruned %d stale notification entries", len(old_keys))
        
        return len(old_keys)


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
    
    # Create and store auto-completion manager for past-due recurrent tasks
    auto_completion_manager = AutoCompletionManager(hass, config_entry, client)
    hass.data[DOMAIN][config_entry.entry_id]["auto_completion_manager"] = auto_completion_manager
    
    # Process initial tasks for auto-completion scheduling
    if coordinator.data:
        await auto_completion_manager.process_tasks(list(coordinator.data.values()))
    
    # Set up listener to process tasks on each coordinator update
    @callback
    def _handle_coordinator_update() -> None:
        """Handle coordinator updates for auto-completion scheduling."""
        if coordinator.data:
            hass.async_create_task(
                auto_completion_manager.process_tasks(list(coordinator.data.values()))
            )
    
    coordinator.async_add_listener(_handle_coordinator_update)

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
    include_unassigned = config_entry.options.get(CONF_INCLUDE_UNASSIGNED, config_entry.data.get(CONF_INCLUDE_UNASSIGNED, False))
    
    if create_date_filtered:
        _LOGGER.debug("Date-filtered lists enabled in config")
        
        # Create global (unassigned) date-filtered lists
        _LOGGER.debug("Creating global date-filtered lists for unassigned tasks")
        for list_type in ["past_due", "due_today", "upcoming", "no_due_date"]:
            entity = DonetickDateFilteredTasksList(coordinator, config_entry, hass, list_type, member=None)
            entity._circle_members = circle_members
            entities.append(entity)
        
        # Create date-filtered lists for each member
        for member in circle_members:
            if member.is_active:
                _LOGGER.debug("Creating date-filtered lists for member: %s (ID: %d)", member.display_name, member.user_id)
                for list_type in ["past_due", "due_today", "upcoming", "no_due_date"]:
                    entity = DonetickDateFilteredTasksList(coordinator, config_entry, hass, list_type, member=member)
                    entity._circle_members = circle_members
                    entities.append(entity)
                
                # If include_unassigned is enabled, also create "With Unassigned" lists
                if include_unassigned:
                    _LOGGER.debug("Creating 'With Unassigned' lists for member: %s (ID: %d)", member.display_name, member.user_id)
                    for list_type in ["past_due", "due_today", "upcoming", "no_due_date"]:
                        entity = DonetickDateFilteredWithUnassignedList(coordinator, config_entry, hass, list_type, member=member)
                        entity._circle_members = circle_members
                        entities.append(entity)
    else:
        _LOGGER.debug("Date-filtered lists not enabled in config")
    
    # Create time-of-day sub-lists if enabled
    create_time_of_day = config_entry.options.get(CONF_CREATE_TIME_OF_DAY_LISTS, config_entry.data.get(CONF_CREATE_TIME_OF_DAY_LISTS, False))
    
    if create_time_of_day:
        _LOGGER.debug("Time-of-day lists enabled in config")
        
        # Create global (unassigned) time-of-day lists
        _LOGGER.debug("Creating global time-of-day lists for unassigned tasks")
        for list_type in ["past_due", "morning", "afternoon", "evening", "all_day"]:
            entity = DonetickTimeOfDayTasksList(coordinator, config_entry, hass, list_type, member=None)
            entity._circle_members = circle_members
            entities.append(entity)
        
        # Create time-of-day lists for each member
        for member in circle_members:
            if member.is_active:
                _LOGGER.debug("Creating time-of-day lists for member: %s (ID: %d)", member.display_name, member.user_id)
                for list_type in ["past_due", "morning", "afternoon", "evening", "all_day"]:
                    entity = DonetickTimeOfDayTasksList(coordinator, config_entry, hass, list_type, member=member)
                    entity._circle_members = circle_members
                    entities.append(entity)
                
                # If include_unassigned is enabled, also create "With Unassigned" lists
                if include_unassigned:
                    _LOGGER.debug("Creating time-of-day 'With Unassigned' lists for member: %s (ID: %d)", member.display_name, member.user_id)
                    for list_type in ["past_due", "morning", "afternoon", "evening", "all_day"]:
                        entity = DonetickTimeOfDayWithUnassignedList(coordinator, config_entry, hass, list_type, member=member)
                        entity._circle_members = circle_members
                        entities.append(entity)
        
        # Create "Upcoming Today By Time" lists (tasks due today past next time boundary)
        _LOGGER.debug("Creating 'Upcoming Today By Time' lists")
        
        # Create unassigned Upcoming Today By Time lists
        entity = DonetickUpcomingTodayByTimeList(coordinator, config_entry, hass, member=None)
        entity._circle_members = circle_members
        entities.append(entity)
        
        entity = DonetickUpcomingTodayByTimeAndFutureList(coordinator, config_entry, hass, member=None)
        entity._circle_members = circle_members
        entities.append(entity)
        
        # Create per-member Upcoming Today By Time lists
        for member in circle_members:
            if member.is_active:
                _LOGGER.debug("Creating 'Upcoming Today By Time' lists for member: %s (ID: %d)", member.display_name, member.user_id)
                
                entity = DonetickUpcomingTodayByTimeList(coordinator, config_entry, hass, member=member)
                entity._circle_members = circle_members
                entities.append(entity)
                
                entity = DonetickUpcomingTodayByTimeAndFutureList(coordinator, config_entry, hass, member=member)
                entity._circle_members = circle_members
                entities.append(entity)
                
                # If include_unassigned is enabled, also create "With Unassigned" variants
                if include_unassigned:
                    _LOGGER.debug("Creating 'Upcoming Today By Time With Unassigned' lists for member: %s (ID: %d)", member.display_name, member.user_id)
                    
                    entity = DonetickUpcomingTodayByTimeWithUnassignedList(coordinator, config_entry, hass, member=member)
                    entity._circle_members = circle_members
                    entities.append(entity)
                    
                    entity = DonetickUpcomingTodayByTimeAndFutureWithUnassignedList(coordinator, config_entry, hass, member=member)
                    entity._circle_members = circle_members
                    entities.append(entity)
    else:
        _LOGGER.debug("Time-of-day lists not enabled in config")
    
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
        _LOGGER.debug(
            "async_update_todo_item called: List=%s, UID=%s, Status=%s",
            self._attr_name, item.uid, item.status
        )
        if not self.coordinator.data:
            return None
        
        client = _create_api_client(self.hass, self._config_entry)
        
        task_id = int(item.uid.split("--")[0])
        
        try:
            if item.status == TodoItemStatus.COMPLETED:
                # Guard: Check if this task was recently completed (prevents double-completion
                # when multiple list entities contain the same task, or when HA fires
                # completion events multiple times). Uses task ID since UID includes the
                # due date which changes after completion.
                global _recently_completed_task_ids
                async with _completion_lock:
                    now = datetime.now()
                    # Clean up old entries (older than 30 seconds)
                    expired = [tid for tid, ts in _recently_completed_task_ids.items() 
                               if (now - ts).total_seconds() > 30]
                    for tid in expired:
                        del _recently_completed_task_ids[tid]
                    
                    if task_id in _recently_completed_task_ids:
                        _LOGGER.debug(
                            "Ignoring duplicate completion for task %d - already completed %.1f seconds ago",
                            task_id, (now - _recently_completed_task_ids[task_id]).total_seconds()
                        )
                        return
                    
                    # Mark this task as being completed
                    _recently_completed_task_ids[task_id] = now
                
                # Complete the task
                _LOGGER.debug(
                    "Completing task %d via %s list",
                    task_id, self._attr_name
                )
                # Determine who should complete this task using smart logic
                completed_by = await self._get_completion_user_id(client, item, context)
                
                res = await client.async_complete_task(task_id, completed_by)
                if res.frequency_type != "once":
                    _LOGGER.debug(
                        "Task %s is recurring, next due date is %s. "
                        "The coordinator refresh will pick up the new occurrence.",
                        res.name, res.next_due_date
                    )
                    # NOTE: We intentionally do NOT recursively call async_update_todo_item here.
                    # The coordinator.async_refresh() at the end of this method will fetch the
                    # updated task with the new next_due_date. Recursively calling would cause
                    # a spurious update API call to Donetick with the new due date, which could
                    # lead to bugs where the next day's recurrence gets unexpectedly completed.
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
        if hasattr(self, '_member') and self._member is not None:
            _LOGGER.debug("Using assignee from specific list: %s (ID: %d)", self._member.display_name, self._member.user_id)
            return self._member.user_id
        
        # If completing from "All Tasks" or unassigned list, find the task's original assignee
        task_id = int(item.uid.split("--")[0])
        if self.coordinator.data:
            # coordinator.data is a dict mapping task_id -> task object
            task = self.coordinator.data.get(task_id)
            if task and task.assigned_to:
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
    """Donetick Date-Filtered Tasks List entity (Past Due, Due Today, Upcoming, No Due Date)."""

    LIST_TYPE_NAMES = {
        "past_due": "Past Due",
        "due_today": "Due Today",
        "upcoming": "Upcoming",
        "no_due_date": "No Due Date",
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
        self._cached_task_ids: set[int] = set()  # Track which task IDs are in this list
        self._scheduled_transition_cancel: Optional[Callable] = None  # Cancel callback for scheduled transition
        self._notification_manager: Optional[NotificationManager] = None  # Will be set when hass available
        self._notification_store: Optional[NotificationStore] = None  # Persistent notification tracking
        
        list_type_name = self.LIST_TYPE_NAMES.get(list_type, list_type.replace("_", " ").title())
        
        if member:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_{list_type}"
            self._attr_name = f"{member.display_name}'s {list_type_name}"
        else:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_unassigned_{list_type}"
            self._attr_name = f"Unassigned {list_type_name}"

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return todo items with content-based cache invalidation.
        
        Only rebuilds (triggers UI update) when:
        1. Server data version changed (task added/removed/updated)
        2. Tasks have migrated between lists due to time passing
        """
        if self.coordinator.data is None:
            return None
        
        # First check: has server data changed?
        server_changed = self._cached_data_version != self.coordinator.data_version
        
        # Get current filtered tasks
        filtered_tasks = self._filter_tasks(self.coordinator.tasks_list)
        current_task_ids = {task.id for task in filtered_tasks}
        
        # Second check: have tasks migrated in/out due to time?
        time_migration = current_task_ids != self._cached_task_ids
        
        if server_changed or time_migration:
            # Build new todo items
            self._cached_todo_items = [
                TodoItem(
                    summary=task.name,
                    uid="%s--%s" % (task.id, task.next_due_date),
                    status=self.get_status(task.next_due_date, task.is_active),
                    due=task.next_due_date,
                    description=task.description or ""
                ) for task in filtered_tasks if task.is_active
            ]
            
            # Update caches
            old_task_ids = self._cached_task_ids
            self._cached_task_ids = current_task_ids
            self._cached_data_version = self.coordinator.data_version
            
            # Log what changed
            if time_migration and not server_changed:
                added = current_task_ids - old_task_ids
                removed = old_task_ids - current_task_ids
                _LOGGER.debug(
                    "%s: Time-based migration - added %d tasks, removed %d tasks",
                    self._attr_name, len(added), len(removed)
                )
            elif server_changed:
                _LOGGER.debug(
                    "%s: Rebuilt due to server changes (version %d, %d items)",
                    self._attr_name, self.coordinator.data_version, len(self._cached_todo_items)
                )
            
            # Reschedule transitions when list content changes
            self._schedule_next_transition()
        
        return self._cached_todo_items

    async def async_added_to_hass(self) -> None:
        """Handle entity being added to Home Assistant."""
        await super().async_added_to_hass()
        
        # Initialize notification manager and store for past_due lists
        if self._list_type == "past_due":
            self._notification_manager = NotificationManager(self.hass, self._config_entry)
            self._notification_store = NotificationStore(
                self.hass, 
                f"{self._config_entry.entry_id}_{self._member.user_id if self._member else 'unassigned'}"
            )
            await self._notification_store.async_load()
            # Check for any tasks already past due and send initial notifications
            await self._check_and_notify_past_due_tasks()
        
        # Schedule the first transition check
        self._schedule_next_transition()

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal from Home Assistant."""
        await super().async_will_remove_from_hass()
        # Cancel any scheduled transition
        if self._scheduled_transition_cancel:
            self._scheduled_transition_cancel()
            self._scheduled_transition_cancel = None
        
        # Cancel any scheduled reminders for tasks tracked by this entity
        is_unassigned = self._member is None
        if self._notification_store:
            # Get all task IDs we've been tracking
            for task_key in list(self._notification_store._data.keys()):
                task_id = int(task_key)
                NotificationManager.cancel_reminder(task_id, is_unassigned=is_unassigned)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        
        # For past_due lists, also check for new past due tasks and send notifications
        if self._list_type == "past_due" and self._notification_manager:
            # Schedule the notification check to run asynchronously
            self.hass.async_create_task(self._check_and_notify_past_due_tasks())
        
        # Reschedule transition since tasks may have changed
        self._schedule_next_transition()

    async def _check_and_notify_past_due_tasks(self) -> None:
        """Check for past due tasks and send notifications for new ones."""
        if self._list_type != "past_due" or not self._notification_manager:
            return
        
        if not self._notification_manager.is_enabled():
            return
        
        if not self._notification_store:
            return
        
        # Get current filtered tasks (past due tasks for this assignee)
        filtered_tasks = self._filter_tasks(self.coordinator.tasks_list)
        current_task_ids = {task.id for task in filtered_tasks}
        
        # Determine if this is an unassigned list
        is_unassigned = self._member is None
        
        # Prune stale entries and cancel their reminders
        stale_task_keys = set(self._notification_store._data.keys()) - {str(tid) for tid in current_task_ids}
        for task_key in stale_task_keys:
            task_id = int(task_key)
            NotificationManager.cancel_reminder(task_id, is_unassigned=is_unassigned)
            self._notification_store.clear_task(task_id)
        
        # Check each past due task - only notify if we haven't already for this due date
        notifications_sent = False
        for task in filtered_tasks:
            # Check if we already notified for this specific task+due_date combo
            if self._notification_store.was_notified(task.id, task.next_due_date):
                continue
            
            # This is either a new task or the due date changed - send notification
            if is_unassigned:
                # For unassigned tasks, notify ALL configured users
                sent_count = await self._notification_manager.send_unassigned_past_due_notification(task)
                if sent_count > 0:
                    # Schedule reminder for 24 hours from now
                    local_now = self._get_local_now()
                    reminder_time = local_now + timedelta(seconds=NOTIFICATION_REMINDER_INTERVAL)
                    self._notification_manager.schedule_unassigned_reminder(task, reminder_time)
                    # Mark as notified with current due date
                    self._notification_store.mark_notified(task.id, task.next_due_date)
                    notifications_sent = True
            else:
                # For assigned tasks, notify the specific assignee
                sent = await self._notification_manager.send_past_due_notification(task)
                if sent:
                    # Schedule reminder for 24 hours from now
                    local_now = self._get_local_now()
                    reminder_time = local_now + timedelta(seconds=NOTIFICATION_REMINDER_INTERVAL)
                    self._notification_manager.schedule_reminder(task, reminder_time)
                    # Mark as notified with current due date
                    self._notification_store.mark_notified(task.id, task.next_due_date)
                    notifications_sent = True
        
        # Persist changes if any notifications were sent or tasks were pruned
        if notifications_sent or stale_task_keys:
            await self._notification_store.async_save()

    def _schedule_next_transition(self) -> None:
        """Schedule a state update at the next task transition time.
        
        This ensures tasks move between lists at exactly the right moment,
        not just when the coordinator syncs.
        """
        # Don't schedule if entity not yet added to hass
        # Use self.hass (from CoordinatorEntity) or self._hass (stored in __init__)
        hass = getattr(self, 'hass', None) or self._hass
        if hass is None:
            return
        
        # Cancel any existing scheduled transition
        if self._scheduled_transition_cancel:
            self._scheduled_transition_cancel()
            self._scheduled_transition_cancel = None
        
        next_transition = self._calculate_next_transition_time()
        if next_transition is None:
            _LOGGER.debug("%s: No upcoming transitions to schedule", self._attr_name)
            return
        
        _LOGGER.debug(
            "%s: Scheduling transition at %s",
            self._attr_name, next_transition.isoformat()
        )
        
        self._scheduled_transition_cancel = async_track_point_in_time(
            hass,
            self._handle_transition_callback,
            next_transition
        )

    async def _handle_transition_callback(self, now: datetime) -> None:
        """Handle the scheduled transition callback."""
        _LOGGER.debug("%s: Transition callback fired at %s", self._attr_name, now.isoformat())
        
        # For past_due lists, check for new past due tasks and send notifications
        if self._list_type == "past_due":
            await self._check_and_notify_past_due_tasks()
        
        # Trigger a state update - this will cause todo_items to be re-evaluated
        self.async_write_ha_state()
        
        # Schedule the next transition
        self._schedule_next_transition()

    def _calculate_next_transition_time(self) -> Optional[datetime]:
        """Calculate when the next task will transition into or out of this list.
        
        Returns the earliest time at which a task will move, or None if no transitions pending.
        
        For each list type:
        - past_due: Tasks enter when their due time passes (due_datetime)
        - due_today: Tasks enter at midnight (start of due day), exit when due time passes
        - upcoming: Tasks exit at midnight when they become due_today
        """
        if self.coordinator.data is None:
            return None
        
        # Can't calculate without hass context
        hass = getattr(self, 'hass', None) or self._hass
        if hass is None:
            return None
        
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        # Add a small buffer (1 second) to avoid edge cases
        buffer = timedelta(seconds=1)
        
        next_times: list[datetime] = []
        
        for task in self.coordinator.tasks_list:
            if not task.is_active or task.next_due_date is None:
                continue
            
            # Filter by assignee (same logic as _filter_tasks)
            if self._member:
                if task.assigned_to != self._member.user_id:
                    continue
            else:
                if task.assigned_to is not None:
                    continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            if self._list_type == "past_due":
                # Tasks enter past_due when their due time passes
                # Only schedule for tasks currently in due_today (due today and not yet past)
                if today_start <= task_due <= today_end and task_due > local_now:
                    # This task will become past_due at task_due
                    next_times.append(task_due + buffer)
                    
            elif self._list_type == "due_today":
                # Tasks enter due_today at midnight of their due date
                # Tasks exit due_today when their due time passes (move to past_due)
                
                if task_due > today_end:
                    # Task is upcoming - will enter due_today at midnight
                    # Calculate midnight of the task's due date in local time
                    task_local = task_due.astimezone(ZoneInfo(str(hass.config.time_zone)))
                    task_midnight = task_local.replace(hour=0, minute=0, second=0, microsecond=0)
                    if task_midnight > local_now:
                        next_times.append(task_midnight + buffer)
                
                elif today_start <= task_due <= today_end and task_due > local_now:
                    # Task is currently in due_today - will exit when due time passes
                    next_times.append(task_due + buffer)
                    
            elif self._list_type == "upcoming":
                # Tasks exit upcoming at midnight when they become due_today
                if task_due > today_end:
                    # Calculate midnight of the task's due date
                    task_local = task_due.astimezone(ZoneInfo(str(hass.config.time_zone)))
                    task_midnight = task_local.replace(hour=0, minute=0, second=0, microsecond=0)
                    if task_midnight > local_now:
                        next_times.append(task_midnight + buffer)
        
        if not next_times:
            return None
        
        return min(next_times)

    def _filter_tasks(self, tasks):
        """Filter tasks based on date and assignee criteria."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        # Get upcoming days limit from config
        upcoming_days = self._config_entry.data.get(CONF_UPCOMING_DAYS, DEFAULT_UPCOMING_DAYS)
        upcoming_cutoff = today_end + timedelta(days=upcoming_days)
        
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
            
            # Handle no_due_date list type specially
            if self._list_type == "no_due_date":
                if task.next_due_date is None:
                    filtered.append(task)
                continue
            
            # Filter by date - skip tasks without due date for other list types
            if task.next_due_date is None:
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
                # Upcoming: incomplete tasks with due date > end of today AND within upcoming_days
                # But exclude frequent recurrent tasks and apply advance-days logic for others
                if task_due > today_end and task_due <= upcoming_cutoff:
                    # Skip tasks that recur too frequently (daily, weekly, etc.)
                    if _is_frequent_recurrence(task):
                        continue
                    
                    # For other recurring tasks, only show within advance window
                    # Non-recurring tasks return None (always show)
                    advance_days = _get_recurrence_advance_days(task)
                    if advance_days is None:
                        # Non-recurring task - always show in upcoming
                        filtered.append(task)
                    else:
                        cutoff_date = local_now + timedelta(days=advance_days)
                        if task_due <= cutoff_date:
                            filtered.append(task)
            # no_due_date is handled separately above (doesn't need task_due)
        
        return filtered


class DonetickDateFilteredWithUnassignedList(DonetickDateFilteredTasksList):
    """Donetick Date-Filtered Tasks List that includes unassigned tasks.
    
    This creates lists like "Stephen's Upcoming With Unassigned" that show
    both the assignee's tasks and any unassigned tasks.
    
    These lists do NOT trigger notifications - notifications are handled by the
    regular assignee lists and the separate unassigned past due notifications.
    """

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        list_type: str,
        member: DonetickMember
    ) -> None:
        """Initialize the Date-Filtered With Unassigned Tasks List."""
        # Initialize parent but we'll override the unique_id and name
        super().__init__(coordinator, config_entry, hass, list_type, member)
        
        list_type_name = self.LIST_TYPE_NAMES.get(list_type, list_type.replace("_", " ").title())
        
        # Override unique_id and name to indicate "With Unassigned"
        self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_{list_type}_with_unassigned"
        self._attr_name = f"{member.display_name}'s {list_type_name} With Unassigned"
        
        # Disable notification manager for these lists - they don't trigger notifications
        self._notification_manager = None

    async def async_added_to_hass(self) -> None:
        """Handle entity being added to Home Assistant.
        
        Override to skip notification setup - these lists don't trigger notifications.
        """
        # Call grandparent's async_added_to_hass, skipping parent's notification setup
        await CoordinatorEntity.async_added_to_hass(self)
        
        # Schedule the first transition check
        self._schedule_next_transition()

    def _filter_tasks(self, tasks):
        """Filter tasks to include both assignee's tasks and unassigned tasks."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        # Get upcoming days limit from config
        upcoming_days = self._config_entry.data.get(CONF_UPCOMING_DAYS, DEFAULT_UPCOMING_DAYS)
        upcoming_cutoff = today_end + timedelta(days=upcoming_days)
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Include tasks assigned to this member OR unassigned tasks
            if task.assigned_to is not None and task.assigned_to != self._member.user_id:
                continue
            
            # Handle no_due_date list type specially
            if self._list_type == "no_due_date":
                if task.next_due_date is None:
                    filtered.append(task)
                continue
            
            # Filter by date - skip tasks without due date for other list types
            if task.next_due_date is None:
                continue
            
            # Ensure task due date is timezone-aware for comparison
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            if self._list_type == "past_due":
                if task_due < local_now:
                    filtered.append(task)
            elif self._list_type == "due_today":
                if today_start <= task_due <= today_end and task_due >= local_now:
                    filtered.append(task)
            elif self._list_type == "upcoming":
                # Upcoming: incomplete tasks with due date > end of today AND within upcoming_days
                # But exclude frequent recurrent tasks and apply advance-days logic for others
                if task_due > today_end and task_due <= upcoming_cutoff:
                    # Skip tasks that recur too frequently (daily, weekly, etc.)
                    if _is_frequent_recurrence(task):
                        continue
                    
                    # For other recurring tasks, only show within advance window
                    # Non-recurring tasks return None (always show)
                    advance_days = _get_recurrence_advance_days(task)
                    if advance_days is None:
                        # Non-recurring task - always show in upcoming
                        filtered.append(task)
                    else:
                        cutoff_date = local_now + timedelta(days=advance_days)
                        if task_due <= cutoff_date:
                            filtered.append(task)
        
        return filtered

    def _calculate_next_transition_time(self) -> Optional[datetime]:
        """Calculate when the next task will transition.
        
        Override to include both assignee's tasks and unassigned tasks.
        """
        if self.coordinator.data is None:
            return None
        
        hass = getattr(self, 'hass', None) or self._hass
        if hass is None:
            return None
        
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        buffer = timedelta(seconds=1)
        
        next_times: list[datetime] = []
        
        for task in self.coordinator.tasks_list:
            if not task.is_active or task.next_due_date is None:
                continue
            
            # Include tasks assigned to this member OR unassigned tasks
            if task.assigned_to is not None and task.assigned_to != self._member.user_id:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            if self._list_type == "past_due":
                if today_start <= task_due <= today_end and task_due > local_now:
                    next_times.append(task_due + buffer)
                    
            elif self._list_type == "due_today":
                if task_due > today_end:
                    task_local = task_due.astimezone(ZoneInfo(str(hass.config.time_zone)))
                    task_midnight = task_local.replace(hour=0, minute=0, second=0, microsecond=0)
                    if task_midnight > local_now:
                        next_times.append(task_midnight + buffer)
                
                elif today_start <= task_due <= today_end and task_due > local_now:
                    next_times.append(task_due + buffer)
                    
            elif self._list_type == "upcoming":
                if task_due > today_end:
                    task_local = task_due.astimezone(ZoneInfo(str(hass.config.time_zone)))
                    task_midnight = task_local.replace(hour=0, minute=0, second=0, microsecond=0)
                    if task_midnight > local_now:
                        next_times.append(task_midnight + buffer)
        
        if not next_times:
            return None
        
        return min(next_times)


class DonetickTimeOfDayTasksList(DonetickTodoListBase):
    """Donetick Time-of-Day Tasks List entity (Past Due, Morning, Afternoon, Evening, All Day).
    
    These lists show tasks due TODAY, sorted by time-of-day:
    - Past Due: Tasks due today where due time < now
    - Morning: Tasks due today before morning_cutoff (not yet past due)
    - Afternoon: Tasks due today between morning_cutoff and afternoon_cutoff (not yet past due)
    - Evening: Tasks due today after afternoon_cutoff (not yet past due)
    - All Day: Tasks due today with time 23:59:00 (date-only tasks, not yet past due)
    """

    LIST_TYPE_NAMES = {
        "past_due": "Today Past Due",
        "morning": "Morning",
        "afternoon": "Afternoon",
        "evening": "Evening",
        "all_day": "All Day",
    }

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        list_type: str,
        member: Optional[DonetickMember] = None
    ) -> None:
        """Initialize the Time-of-Day Tasks List."""
        super().__init__(coordinator, config_entry, hass)
        self._list_type = list_type
        self._member = member
        self._cached_task_ids: set[int] = set()
        self._scheduled_transition_cancel: Optional[Callable] = None
        
        list_type_name = self.LIST_TYPE_NAMES.get(list_type, list_type.replace("_", " ").title())
        
        if member:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_tod_{list_type}"
            self._attr_name = f"{member.display_name}'s {list_type_name}"
        else:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_unassigned_tod_{list_type}"
            self._attr_name = f"Unassigned {list_type_name}"

    def _parse_cutoff_time(self, cutoff_str: str) -> tuple[int, int]:
        """Parse a cutoff time string (HH:MM) into hours and minutes."""
        parts = cutoff_str.split(":")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)

    def _get_cutoff_times(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Get morning and afternoon cutoff times from config."""
        morning = self._config_entry.data.get(CONF_MORNING_CUTOFF, DEFAULT_MORNING_CUTOFF)
        afternoon = self._config_entry.data.get(CONF_AFTERNOON_CUTOFF, DEFAULT_AFTERNOON_CUTOFF)
        return self._parse_cutoff_time(morning), self._parse_cutoff_time(afternoon)

    def _is_all_day_task(self, task_due: datetime) -> bool:
        """Check if a task is an all-day task (due at 23:59:00)."""
        return task_due.hour == 23 and task_due.minute == 59 and task_due.second == 0

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return todo items with content-based cache invalidation."""
        if self.coordinator.data is None:
            return None
        
        server_changed = self._cached_data_version != self.coordinator.data_version
        
        filtered_tasks = self._filter_tasks(self.coordinator.tasks_list)
        current_task_ids = {task.id for task in filtered_tasks}
        
        time_migration = current_task_ids != self._cached_task_ids
        
        if server_changed or time_migration:
            self._cached_todo_items = [
                TodoItem(
                    summary=task.name,
                    uid="%s--%s" % (task.id, task.next_due_date),
                    status=self.get_status(task.next_due_date, task.is_active),
                    due=task.next_due_date,
                    description=task.description or ""
                ) for task in filtered_tasks if task.is_active
            ]
            
            old_task_ids = self._cached_task_ids
            self._cached_task_ids = current_task_ids
            self._cached_data_version = self.coordinator.data_version
            
            if time_migration and not server_changed:
                added = current_task_ids - old_task_ids
                removed = old_task_ids - current_task_ids
                _LOGGER.debug(
                    "%s: Time-based migration - added %d tasks, removed %d tasks",
                    self._attr_name, len(added), len(removed)
                )
            elif server_changed:
                _LOGGER.debug(
                    "%s: Rebuilt due to server changes (version %d, %d items)",
                    self._attr_name, self.coordinator.data_version, len(self._cached_todo_items)
                )
            
            self._schedule_next_transition()
        
        return self._cached_todo_items

    async def async_added_to_hass(self) -> None:
        """Handle entity being added to Home Assistant."""
        await super().async_added_to_hass()
        self._schedule_next_transition()

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal from Home Assistant."""
        await super().async_will_remove_from_hass()
        if self._scheduled_transition_cancel:
            self._scheduled_transition_cancel()
            self._scheduled_transition_cancel = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        self._schedule_next_transition()

    def _schedule_next_transition(self) -> None:
        """Schedule a state update at the next task transition time."""
        hass = getattr(self, 'hass', None) or self._hass
        if hass is None:
            return
        
        if self._scheduled_transition_cancel:
            self._scheduled_transition_cancel()
            self._scheduled_transition_cancel = None
        
        next_transition = self._calculate_next_transition_time()
        if next_transition is None:
            return
        
        _LOGGER.debug(
            "%s: Scheduling transition at %s",
            self._attr_name, next_transition.isoformat()
        )
        
        self._scheduled_transition_cancel = async_track_point_in_time(
            hass,
            self._handle_transition_callback,
            next_transition
        )

    async def _handle_transition_callback(self, now: datetime) -> None:
        """Handle the scheduled transition callback."""
        _LOGGER.debug("%s: Transition callback fired at %s", self._attr_name, now.isoformat())
        self.async_write_ha_state()
        self._schedule_next_transition()

    def _calculate_next_transition_time(self) -> Optional[datetime]:
        """Calculate when the next task will transition into or out of this list."""
        if self.coordinator.data is None:
            return None
        
        hass = getattr(self, 'hass', None) or self._hass
        if hass is None:
            return None
        
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        morning_cutoff, afternoon_cutoff = self._get_cutoff_times()
        
        buffer = timedelta(seconds=1)
        next_times: list[datetime] = []
        
        for task in self.coordinator.tasks_list:
            if not task.is_active or task.next_due_date is None:
                continue
            
            # Filter by assignee
            if self._member:
                if task.assigned_to != self._member.user_id:
                    continue
            else:
                if task.assigned_to is not None:
                    continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            # Only consider tasks due today
            if not (today_start <= task_due <= today_end):
                continue
            
            # Tasks transition to past_due when their due time passes
            if task_due > local_now:
                next_times.append(task_due + buffer)
        
        # Also schedule at cutoff times if we have tasks that could transition
        morning_time = today_start.replace(hour=morning_cutoff[0], minute=morning_cutoff[1])
        afternoon_time = today_start.replace(hour=afternoon_cutoff[0], minute=afternoon_cutoff[1])
        
        if morning_time > local_now:
            next_times.append(morning_time + buffer)
        if afternoon_time > local_now:
            next_times.append(afternoon_time + buffer)
        
        if not next_times:
            return None
        
        return min(next_times)

    def _filter_tasks(self, tasks):
        """Filter tasks based on time-of-day and assignee criteria."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        morning_cutoff, afternoon_cutoff = self._get_cutoff_times()
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Filter by assignee
            if self._member:
                if task.assigned_to != self._member.user_id:
                    continue
            else:
                if task.assigned_to is not None:
                    continue
            
            # Must have a due date for time-of-day lists
            if task.next_due_date is None:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            # Convert to local time for time-of-day comparison
            task_local = task_due.astimezone(ZoneInfo(str(self._hass.config.time_zone)))
            
            # Only include tasks due today
            if not (today_start <= task_due <= today_end):
                continue
            
            task_hour, task_minute = task_local.hour, task_local.minute
            task_time_minutes = task_hour * 60 + task_minute
            morning_minutes = morning_cutoff[0] * 60 + morning_cutoff[1]
            afternoon_minutes = afternoon_cutoff[0] * 60 + afternoon_cutoff[1]
            
            is_all_day = self._is_all_day_task(task_local)
            is_past_due = task_due < local_now
            
            if self._list_type == "past_due":
                # Past due: tasks due today where due time < now
                if is_past_due:
                    filtered.append(task)
            elif self._list_type == "all_day":
                # All day: tasks with 23:59:00 time (includes past due all-day tasks)
                if is_all_day:
                    filtered.append(task)
            elif self._list_type == "morning":
                # Morning: before morning_cutoff (not all-day, not past due)
                if not is_all_day and not is_past_due and task_time_minutes < morning_minutes:
                    filtered.append(task)
            elif self._list_type == "afternoon":
                # Afternoon: between morning and afternoon cutoff (not all-day, not past due)
                if not is_all_day and not is_past_due and morning_minutes <= task_time_minutes < afternoon_minutes:
                    filtered.append(task)
            elif self._list_type == "evening":
                # Evening: after afternoon_cutoff but not 23:59 (not all-day, not past due)
                if not is_all_day and not is_past_due and task_time_minutes >= afternoon_minutes:
                    filtered.append(task)
        
        return filtered


class DonetickTimeOfDayWithUnassignedList(DonetickTimeOfDayTasksList):
    """Donetick Time-of-Day Tasks List that includes unassigned tasks.
    
    Creates lists like "Stephen's Morning With Unassigned" that show
    both the assignee's tasks and any unassigned tasks.
    """

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        list_type: str,
        member: DonetickMember
    ) -> None:
        """Initialize the Time-of-Day With Unassigned Tasks List."""
        super().__init__(coordinator, config_entry, hass, list_type, member)
        
        list_type_name = self.LIST_TYPE_NAMES.get(list_type, list_type.replace("_", " ").title())
        
        self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_tod_{list_type}_with_unassigned"
        self._attr_name = f"{member.display_name}'s {list_type_name} With Unassigned"

    def _filter_tasks(self, tasks):
        """Filter tasks to include both assignee's tasks and unassigned tasks."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        morning_cutoff, afternoon_cutoff = self._get_cutoff_times()
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Include tasks assigned to this member OR unassigned tasks
            if task.assigned_to is not None and task.assigned_to != self._member.user_id:
                continue
            
            if task.next_due_date is None:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            task_local = task_due.astimezone(ZoneInfo(str(self._hass.config.time_zone)))
            
            if not (today_start <= task_due <= today_end):
                continue
            
            task_hour, task_minute = task_local.hour, task_local.minute
            task_time_minutes = task_hour * 60 + task_minute
            morning_minutes = morning_cutoff[0] * 60 + morning_cutoff[1]
            afternoon_minutes = afternoon_cutoff[0] * 60 + afternoon_cutoff[1]
            
            is_all_day = self._is_all_day_task(task_local)
            is_past_due = task_due < local_now
            
            if self._list_type == "past_due":
                if is_past_due:
                    filtered.append(task)
            elif self._list_type == "all_day":
                # All day: tasks with 23:59:00 time (includes past due all-day tasks)
                if is_all_day:
                    filtered.append(task)
            elif self._list_type == "morning":
                # Morning: before morning_cutoff (not all-day, not past due)
                if not is_all_day and not is_past_due and task_time_minutes < morning_minutes:
                    filtered.append(task)
            elif self._list_type == "afternoon":
                # Afternoon: between morning and afternoon cutoff (not all-day, not past due)
                if not is_all_day and not is_past_due and morning_minutes <= task_time_minutes < afternoon_minutes:
                    filtered.append(task)
            elif self._list_type == "evening":
                # Evening: after afternoon_cutoff but not 23:59 (not all-day, not past due)
                if not is_all_day and not is_past_due and task_time_minutes >= afternoon_minutes:
                    filtered.append(task)
        
        return filtered


class DonetickUpcomingTodayByTimeList(DonetickTimeOfDayTasksList):
    """Donetick Upcoming Today By Time List entity.
    
    Shows tasks due TODAY that are past the next time boundary from the current time.
    - If now is Morning (before morning_cutoff): shows tasks due after morning_cutoff
    - If now is Afternoon (between cutoffs): shows tasks due after afternoon_cutoff
    - If now is Evening (after afternoon_cutoff): empty (no next boundary)
    
    Excludes:
    - Past due tasks (due time < now)
    - All-day tasks (23:59:00)
    - Tasks not due today
    """

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        member: Optional[DonetickMember] = None
    ) -> None:
        """Initialize the Upcoming Today By Time List."""
        # Pass a dummy list_type to parent, we override filtering
        super().__init__(coordinator, config_entry, hass, "upcoming_today_by_time", member)
        
        if member:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_upcoming_today_by_time"
            self._attr_name = f"{member.display_name}'s Upcoming Today By Time"
        else:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_unassigned_upcoming_today_by_time"
            self._attr_name = "Unassigned Upcoming Today By Time"

    def _get_next_boundary(self, local_now: datetime) -> Optional[datetime]:
        """Get the next time boundary based on current time.
        
        Returns None if we're in Evening (no next boundary).
        """
        morning_cutoff, afternoon_cutoff = self._get_cutoff_times()
        today_start = self._get_local_today_start()
        
        now_minutes = local_now.hour * 60 + local_now.minute
        morning_minutes = morning_cutoff[0] * 60 + morning_cutoff[1]
        afternoon_minutes = afternoon_cutoff[0] * 60 + afternoon_cutoff[1]
        
        if now_minutes < morning_minutes:
            # Currently morning - next boundary is morning cutoff
            return today_start.replace(hour=morning_cutoff[0], minute=morning_cutoff[1], second=0, microsecond=0)
        elif now_minutes < afternoon_minutes:
            # Currently afternoon - next boundary is afternoon cutoff
            return today_start.replace(hour=afternoon_cutoff[0], minute=afternoon_cutoff[1], second=0, microsecond=0)
        else:
            # Currently evening - no next boundary
            return None

    def _filter_tasks(self, tasks):
        """Filter tasks to show today's tasks past the next time boundary."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        next_boundary = self._get_next_boundary(local_now)
        
        # If no next boundary (evening), return empty list
        if next_boundary is None:
            return []
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Filter by assignee
            if self._member:
                if task.assigned_to != self._member.user_id:
                    continue
            else:
                if task.assigned_to is not None:
                    continue
            
            # Must have a due date
            if task.next_due_date is None:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            # Convert to local time
            task_local = task_due.astimezone(ZoneInfo(str(self._hass.config.time_zone)))
            
            # Must be due today
            if not (today_start <= task_due <= today_end):
                continue
            
            # Exclude all-day tasks (23:59:00)
            if self._is_all_day_task(task_local):
                continue
            
            # Exclude past due tasks
            if task_due < local_now:
                continue
            
            # Must be past the next boundary
            if task_due > next_boundary:
                filtered.append(task)
        
        return filtered

    def _calculate_next_transition_time(self) -> Optional[datetime]:
        """Calculate when the next task will transition into or out of this list."""
        if self.coordinator.data is None:
            return None
        
        hass = getattr(self, 'hass', None) or self._hass
        if hass is None:
            return None
        
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        morning_cutoff, afternoon_cutoff = self._get_cutoff_times()
        
        buffer = timedelta(seconds=1)
        next_times: list[datetime] = []
        
        # Schedule at cutoff times for boundary transitions
        morning_time = today_start.replace(hour=morning_cutoff[0], minute=morning_cutoff[1])
        afternoon_time = today_start.replace(hour=afternoon_cutoff[0], minute=afternoon_cutoff[1])
        
        if morning_time > local_now:
            next_times.append(morning_time + buffer)
        if afternoon_time > local_now:
            next_times.append(afternoon_time + buffer)
        
        # Also schedule when tasks become past due (exit the list)
        next_boundary = self._get_next_boundary(local_now)
        if next_boundary is not None:
            for task in self.coordinator.tasks_list:
                if not task.is_active or task.next_due_date is None:
                    continue
                
                task_due = task.next_due_date
                if task_due.tzinfo is None:
                    task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
                
                # Tasks exit when they become past due
                if task_due > local_now and task_due > next_boundary:
                    next_times.append(task_due + buffer)
        
        if not next_times:
            return None
        
        return min(next_times)


class DonetickUpcomingTodayByTimeWithUnassignedList(DonetickUpcomingTodayByTimeList):
    """Donetick Upcoming Today By Time List that includes unassigned tasks.
    
    Creates lists like "Stephen's Upcoming Today By Time With Unassigned".
    """

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        member: DonetickMember
    ) -> None:
        """Initialize the Upcoming Today By Time With Unassigned List."""
        super().__init__(coordinator, config_entry, hass, member)
        
        self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_upcoming_today_by_time_with_unassigned"
        self._attr_name = f"{member.display_name}'s Upcoming Today By Time With Unassigned"

    def _filter_tasks(self, tasks):
        """Filter tasks to include both assignee's tasks and unassigned tasks."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        next_boundary = self._get_next_boundary(local_now)
        
        if next_boundary is None:
            return []
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Include tasks assigned to this member OR unassigned tasks
            if task.assigned_to is not None and task.assigned_to != self._member.user_id:
                continue
            
            if task.next_due_date is None:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            task_local = task_due.astimezone(ZoneInfo(str(self._hass.config.time_zone)))
            
            if not (today_start <= task_due <= today_end):
                continue
            
            if self._is_all_day_task(task_local):
                continue
            
            if task_due < local_now:
                continue
            
            if task_due > next_boundary:
                filtered.append(task)
        
        return filtered


class DonetickUpcomingTodayByTimeAndFutureList(DonetickUpcomingTodayByTimeList):
    """Donetick Upcoming Today By Time And Future List entity.
    
    Combines:
    - Today's tasks past the next time boundary (Upcoming Today By Time logic)
    - Future tasks within upcoming_days window (existing Upcoming logic)
    """

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        member: Optional[DonetickMember] = None
    ) -> None:
        """Initialize the Upcoming Today By Time And Future List."""
        super().__init__(coordinator, config_entry, hass, member)
        
        if member:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_upcoming_today_by_time_and_future"
            self._attr_name = f"{member.display_name}'s Upcoming Today By Time And Future"
        else:
            self._attr_unique_id = f"dt_{config_entry.entry_id}_unassigned_upcoming_today_by_time_and_future"
            self._attr_name = "Unassigned Upcoming Today By Time And Future"

    def _filter_tasks(self, tasks):
        """Filter tasks combining today-by-time and future upcoming logic."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        next_boundary = self._get_next_boundary(local_now)
        
        # Get upcoming days limit from config
        upcoming_days = self._config_entry.data.get(CONF_UPCOMING_DAYS, DEFAULT_UPCOMING_DAYS)
        upcoming_cutoff = today_end + timedelta(days=upcoming_days)
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Filter by assignee
            if self._member:
                if task.assigned_to != self._member.user_id:
                    continue
            else:
                if task.assigned_to is not None:
                    continue
            
            if task.next_due_date is None:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            task_local = task_due.astimezone(ZoneInfo(str(self._hass.config.time_zone)))
            
            # Case 1: Today's tasks past next boundary (Upcoming Today By Time)
            if today_start <= task_due <= today_end:
                # Skip all-day tasks
                if self._is_all_day_task(task_local):
                    continue
                
                # Skip past due
                if task_due < local_now:
                    continue
                
                # Include if past next boundary, OR if we're in evening (no boundary)
                # and the task is still upcoming (after current time)
                if next_boundary is not None:
                    if task_due > next_boundary:
                        filtered.append(task)
                else:
                    # Evening period - include all remaining today tasks after now
                    filtered.append(task)
            
            # Case 2: Future tasks (existing Upcoming logic)
            elif task_due > today_end and task_due <= upcoming_cutoff:
                # Skip tasks that recur too frequently
                if _is_frequent_recurrence(task):
                    continue
                
                # Apply advance-days logic for recurring tasks
                advance_days = _get_recurrence_advance_days(task)
                if advance_days is None:
                    # Non-recurring task - always show
                    filtered.append(task)
                else:
                    cutoff_date = local_now + timedelta(days=advance_days)
                    if task_due <= cutoff_date:
                        filtered.append(task)
        
        return filtered

    def _calculate_next_transition_time(self) -> Optional[datetime]:
        """Calculate when the next task will transition into or out of this list."""
        # Use parent's calculation for today transitions
        parent_next = super()._calculate_next_transition_time()
        
        # Also check for midnight transition (future tasks becoming today)
        local_now = self._get_local_now()
        tomorrow_start = self._get_local_today_end() + timedelta(seconds=1)
        
        next_times = []
        if parent_next:
            next_times.append(parent_next)
        
        # Schedule at midnight for future task transitions
        if tomorrow_start > local_now:
            next_times.append(tomorrow_start)
        
        if not next_times:
            return None
        
        return min(next_times)


class DonetickUpcomingTodayByTimeAndFutureWithUnassignedList(DonetickUpcomingTodayByTimeAndFutureList):
    """Donetick Upcoming Today By Time And Future List that includes unassigned tasks.
    
    Creates lists like "Stephen's Upcoming Today By Time And Future With Unassigned".
    """

    def __init__(
        self, 
        coordinator: DataUpdateCoordinator, 
        config_entry: ConfigEntry, 
        hass: HomeAssistant,
        member: DonetickMember
    ) -> None:
        """Initialize the Upcoming Today By Time And Future With Unassigned List."""
        super().__init__(coordinator, config_entry, hass, member)
        
        self._attr_unique_id = f"dt_{config_entry.entry_id}_{member.user_id}_upcoming_today_by_time_and_future_with_unassigned"
        self._attr_name = f"{member.display_name}'s Upcoming Today By Time And Future With Unassigned"

    def _filter_tasks(self, tasks):
        """Filter tasks combining today-by-time and future upcoming logic, including unassigned."""
        local_now = self._get_local_now()
        today_start = self._get_local_today_start()
        today_end = self._get_local_today_end()
        
        next_boundary = self._get_next_boundary(local_now)
        
        upcoming_days = self._config_entry.data.get(CONF_UPCOMING_DAYS, DEFAULT_UPCOMING_DAYS)
        upcoming_cutoff = today_end + timedelta(days=upcoming_days)
        
        filtered = []
        for task in tasks:
            if not task.is_active:
                continue
            
            # Include tasks assigned to this member OR unassigned tasks
            if task.assigned_to is not None and task.assigned_to != self._member.user_id:
                continue
            
            if task.next_due_date is None:
                continue
            
            task_due = task.next_due_date
            if task_due.tzinfo is None:
                task_due = task_due.replace(tzinfo=ZoneInfo("UTC"))
            
            task_local = task_due.astimezone(ZoneInfo(str(self._hass.config.time_zone)))
            
            # Case 1: Today's tasks past next boundary
            if today_start <= task_due <= today_end:
                if self._is_all_day_task(task_local):
                    continue
                
                if task_due < local_now:
                    continue
                
                # Include if past next boundary, OR if we're in evening (no boundary)
                # and the task is still upcoming (after current time)
                if next_boundary is not None:
                    if task_due > next_boundary:
                        filtered.append(task)
                else:
                    # Evening period - include all remaining today tasks after now
                    filtered.append(task)
            
            # Case 2: Future tasks
            elif task_due > today_end and task_due <= upcoming_cutoff:
                if _is_frequent_recurrence(task):
                    continue
                
                advance_days = _get_recurrence_advance_days(task)
                if advance_days is None:
                    filtered.append(task)
                else:
                    cutoff_date = local_now + timedelta(days=advance_days)
                    if task_due <= cutoff_date:
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

