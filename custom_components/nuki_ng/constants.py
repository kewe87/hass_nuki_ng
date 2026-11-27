DOMAIN = "nuki_ng"
PLATFORMS = ["binary_sensor", "sensor", "lock", "switch", "button", "select", "event", "update"]

CONF_ACTION_RETRIES = "action_retries"
DEFAULT_ACTION_RETRIES = 3

CONF_AUTH_MODE = "auth_mode"
AUTH_AUTO = "auto"
AUTH_ENCRYPTED = "encrypted"
AUTH_HASHED = "hashed"
AUTH_PLAIN = "plain"
AUTH_MODES = [AUTH_AUTO, AUTH_ENCRYPTED, AUTH_HASHED, AUTH_PLAIN]

CONF_WEBHOOK_ID = "webhook_id"

# Actions that open a door: never repeated when they may have been executed.
OPEN_ACTIONS = frozenset({"open", "lock_n_go_open", "electric_strike_actuation"})

# Diagnostics (options section "diagnostics"); every part can be switched off
CONF_WATCH_LOG = "watch_bridge_log"  # read the bridge log for Bluetooth and power problems
CONF_PROBLEM_LOG_LEVEL = "problem_log_level"  # level of problem messages in the Home Assistant log
CONF_LOGBOOK = "logbook_entries"  # commands and Bluetooth problems in the logbook
CONF_REPAIRS = "health_repairs"  # a repair issue while the bridge is clearly unstable
DEFAULT_WATCH_LOG = True
DEFAULT_PROBLEM_LOG_LEVEL = "warning"
DEFAULT_LOGBOOK = True
DEFAULT_REPAIRS = True
PROBLEM_LOG_LEVELS = ["warning", "info", "debug"]

# Firmware updates from the release notes of the Nuki help center
CONF_FIRMWARE_CHECK = "firmware_check"
DEFAULT_FIRMWARE_CHECK = True

EVENT_ACTION = "nuki_ng_action"
EVENT_PROBLEM = "nuki_ng_bluetooth_problem"
