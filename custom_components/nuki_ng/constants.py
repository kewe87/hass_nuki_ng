DOMAIN = "nuki_ng"
PLATFORMS = ["binary_sensor", "sensor", "lock", "switch", "button", "select", "event"]

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
