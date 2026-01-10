"""Constants for the Donetick integration."""
DOMAIN = "donetick"
TODO_STORAGE_KEY = f"{DOMAIN}_items"

CONF_URL = "url"
CONF_TOKEN = "token"  # Legacy API token for eAPI
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_JWT_TOKEN = "jwt_token"
CONF_JWT_EXPIRY = "jwt_expiry"
CONF_AUTH_TYPE = "auth_type"  # "jwt" or "api_key"
CONF_SHOW_DUE_IN = "show_due_in"
CONF_CREATE_UNIFIED_LIST = "create_unified_list"
CONF_CREATE_ASSIGNEE_LISTS = "create_assignee_lists"
CONF_CREATE_DATE_FILTERED_LISTS = "create_date_filtered_lists"
CONF_REFRESH_INTERVAL = "refresh_interval"
CONF_WEBHOOK_ID = "webhook_id"

# Auth types
AUTH_TYPE_JWT = "jwt"
AUTH_TYPE_API_KEY = "api_key"

DEFAULT_REFRESH_INTERVAL = 900  # seconds - 15 minutes
JWT_REFRESH_BUFFER = 300  # seconds - refresh token 5 minutes before expiry

API_TIMEOUT = 10  # seconds

# Frequency types for task creation
FREQUENCY_ONCE = "once"
FREQUENCY_DAILY = "daily"
FREQUENCY_WEEKLY = "weekly"
FREQUENCY_MONTHLY = "monthly"
FREQUENCY_YEARLY = "yearly"
FREQUENCY_INTERVAL = "interval"
FREQUENCY_DAYS_OF_WEEK = "days_of_the_week"
FREQUENCY_DAY_OF_MONTH = "day_of_the_month"
FREQUENCY_NO_REPEAT = "no_repeat"

# Assignment strategies
ASSIGN_RANDOM = "random"
ASSIGN_LEAST_ASSIGNED = "least_assigned"
ASSIGN_LEAST_COMPLETED = "least_completed"
ASSIGN_KEEP_LAST = "keep_last_assigned"
ASSIGN_RANDOM_EXCEPT_LAST = "random_except_last_assigned"
ASSIGN_ROUND_ROBIN = "round_robin"
ASSIGN_NO_ASSIGNEE = "no_assignee"

# Webhook event types from Donetick
WEBHOOK_EVENT_TASK_COMPLETED = "task.completed"
WEBHOOK_EVENT_TASK_SKIPPED = "task.skipped"
WEBHOOK_EVENT_TASK_REMINDER = "task.reminder"
WEBHOOK_EVENT_SUBTASK_COMPLETED = "subtask.completed"
WEBHOOK_EVENT_THING_CHANGED = "thing.changed"

# Home Assistant event types fired by this integration
EVENT_DONETICK_TASK_COMPLETED = f"{DOMAIN}_task_completed"
EVENT_DONETICK_TASK_SKIPPED = f"{DOMAIN}_task_skipped"
EVENT_DONETICK_TASK_REMINDER = f"{DOMAIN}_task_reminder"
EVENT_DONETICK_SUBTASK_COMPLETED = f"{DOMAIN}_subtask_completed"
EVENT_DONETICK_THING_CHANGED = f"{DOMAIN}_thing_changed"

# Notification configuration
CONF_NOTIFY_ON_PAST_DUE = "notify_on_past_due"
CONF_ASSIGNEE_NOTIFICATIONS = "assignee_notifications"  # Dict mapping user_id -> notify service

# Notification reminder interval (24 hours)
NOTIFICATION_REMINDER_INTERVAL = 86400  # seconds

# Priority levels for interruption mapping
PRIORITY_P1 = 1  # critical
PRIORITY_P2 = 2  # time-sensitive
PRIORITY_P3 = 3  # normal
PRIORITY_P4 = 4  # normal