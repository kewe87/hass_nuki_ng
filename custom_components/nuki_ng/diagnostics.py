from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {
    "token", "web_token", "webhook_id", "web_auth", "hass_url", "callback_url", "url",
    "callbacks_list", "serverId", "hardwareId", "address", "last_log", "last_unlock_log",
}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "callback": {
            "reachable": coordinator.callback_reachable,
            "uses_configured_url": coordinator.hass_url == coordinator.configured_url,
            "last_push": str(coordinator.last_push),
            "rejected_pushes": coordinator.rejected_pushes,
            "auth_mode": coordinator.api.active_auth,
        },
        "health": {
            "failed_actions": coordinator.failed_actions,
            "last_action_error": coordinator.last_action_error,
            "bridge_last_restart": str(coordinator.bridge_last_restart),
            "action_retries": coordinator.action_retries,
            "status": coordinator.health_status(),
            "checks": [c.as_dict() for c in coordinator.passive_checks()],
            "last_checkup": coordinator.last_checkup,
            "bridge_log": {
                "watching": coordinator.watch_log,
                "read_at": coordinator.bridge_log.read_at,
                "problems_24h": [e.__dict__ for e in coordinator.bridge_log.events],
                "totals": coordinator.bridge_log.totals,
            },
            "actions": {k: v.as_dict() for k, v in coordinator.action_stats.items()},
        },
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
    }
