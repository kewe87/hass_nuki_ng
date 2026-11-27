"""Logbook texts for commands and Bluetooth problems."""

from homeassistant.core import callback

from .constants import DOMAIN, EVENT_ACTION, EVENT_PROBLEM

_KINDS = {
    "ble_drop": "{count} Bluetooth connection drop(s), the bridge retried",
    "ble_failure": "the bridge gave up on Bluetooth {count} time(s) and restarted",
    "uart": "{count} data error(s) inside the bridge (power supply?)",
    "restart": "the bridge started",
}


@callback
def async_describe_events(hass, async_describe_event):
    @callback
    def describe_action(event):
        data = event.data
        tries = data.get("attempts", 1)
        if data.get("success"):
            text = f"{data.get('action')}: done in {data.get('seconds')} s"
            if tries > 1:
                text += f" after {tries} attempts"
        else:
            text = f"{data.get('action')}: failed after {tries} attempt(s): {data.get('error')}"
        return {"name": f"Nuki {data.get('device')}", "message": text}

    @callback
    def describe_problem(event):
        data = event.data
        text = _KINDS.get(data.get("kind"), str(data.get("kind"))).format(count=data.get("count", 1))
        name = data.get("device") or "Bridge"
        return {"name": name if name.startswith("Nuki") else f"Nuki {name}", "message": text}

    async_describe_event(DOMAIN, EVENT_ACTION, describe_action)
    async_describe_event(DOMAIN, EVENT_PROBLEM, describe_problem)
