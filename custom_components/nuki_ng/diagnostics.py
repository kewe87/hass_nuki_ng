from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {"token", "web_token", "web_auth", "hass_url", "callback_url", "url", "callbacks_list", "serverId", "hardwareId"}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "callback": {
            "reachable": coordinator.callback_reachable,
            "uses_configured_url": coordinator.hass_url == coordinator.configured_url,
        },
        "health": {
            "failed_actions": coordinator.failed_actions,
            "last_action_error": coordinator.last_action_error,
            "bridge_last_restart": str(coordinator.bridge_last_restart),
            "action_retries": coordinator.action_retries,
        },
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
    }
