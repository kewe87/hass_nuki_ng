**English** | [Deutsch](troubleshooting.de.md)

# Troubleshooting guide

The door opens only after a few seconds, a command does not go through, or the bridge keeps restarting? nuki_ng helps you find out why. This guide explains what the integration watches, how to use the check-up and what usually lies behind each finding.

- [Quick start: check-up](#quick-start-check-up)
- [What is checked](#what-is-checked)
- [Typical causes](#typical-causes)
- [Sensors for graphs and automations](#sensors-for-graphs-and-automations)
- [Log, logbook and repair notice](#log-logbook-and-repair-notice)
- [Settings](#settings)
- [Reporting a bug](#reporting-a-bug)

## Quick start: check-up

**Settings → Devices & services → Nuki Lock → device *Nuki Bridge* → press *Check-up*.**

A notification "Checking …" appears right away. After a few seconds it is replaced by the report, problems first, in the language of Home Assistant (German or English):

```
⚠️ Nuki check-up: warning
- ⚠️ Front door: Bluetooth drops: 8 in the last 24 h.
  → More drops than usual. If commands get slow, move the bridge closer or replace the batteries.
- ✅ Bridge: reachable: Answers in 0.41 s.
- ✅ Opener: signal: -43 dBm, good.
…
```

The same as an action, e.g. in a script, with the report as response:

```yaml
- action: nuki_ng.checkup
  data:
    notify: false
  response_variable: report
- if: "{{ report.status != 'ok' }}"
  then:
    - action: notify.notify
      data:
        message: "Nuki: {{ report.bridges[0].checks | selectattr('status', 'ne', 'ok') | map(attribute='title') | join(', ') }}"
```

`report.status` is `ok`, `warning` or `problem`. `report.bridges[].checks[]` holds `id`, `status`, `title`, `detail` and, where useful, `hint` for every check.

Without a target the action checks every bridge that is set up, with `device_id: <bridge>` only that one.

## What is checked

| Check | Good | Warning | Problem |
|---|---|---|---|
| **Bridge reachable** | answers | answers slower than 2 s | no answer |
| **Clock** | less than 30 s off | more than 30 s | – |
| **Token** | encrypted | hashed or plain | – |
| **Push updates** | callback set and reachable | no callback, or the bridge cannot reach Home Assistant | – |
| **Signal** (per device) | above −80 dBm | −80 to −90 dBm | below −90 dBm |
| **Bluetooth drops** (per device, 24 h) | up to 5 | 6–19 | 20 or more |
| **Data errors** (bridge, 24 h) | 0–2 | 3 or more | – |
| **Restarts** (bridge, 24 h) | none | restart | restart after a Bluetooth failure |
| **Commands** (per device, 24 h) | at most 2 needed a retry | 3 or more needed a retry | failed |
| **Battery** | – | – | critical |
| **Web API** (if set up) | answers | – | fails |

The limits are set so that only what you notice is reported. A few drops a day that the bridge retries by itself are normal and stay *good*.

The bridge *Health* sensor always shows the worst finding of what is already known. The check-up also asks the bridge itself (response time, clock, fresh log) and the Web API.

### Where the Bluetooth numbers come from

The bridge keeps a short log of its own (`/log`). Every 10 minutes nuki_ng reads the newest 100 entries and remembers what is new:

- **Bluetooth drop** (`BLE-EarlyDisconnect`): the connection to the device broke off during a command. The bridge usually retries by itself. That costs seconds but otherwise stays invisible.
- **Bluetooth failure** (`BLE-RebootBLEFailed` and others): the bridge gives up on Bluetooth and restarts. No command works for 15–20 s.
- **Data error** (`UartCRCError`): broken data between the WiFi and the Bluetooth chip of the bridge. Almost always the power supply.
- **Restart** (`System-Startup`).

Larger reads make the bridge hang, so nuki_ng reads little and rarely on purpose. If the bridge log runs over between two reads, single entries are missed (noted in the debug log). The numbers are then rather too low than too high.

## Typical causes

| Finding | Common cause | What helps |
|---|---|---|
| Weak signal, Bluetooth drops | bridge too far away, metal or a door frame in between | move the bridge closer, ideally 1–3 m with line of sight |
| Data errors, restarts | weak or failing power supply, thin USB cable | another power supply (at least 1.5 A) and cable |
| Drops at one device only | weak batteries | replace the batteries, even if not "critical" yet |
| Commands with retries, everything else fine | occasional radio interference | keep an eye on it; the integration retries safely |
| Bridge answers slowly | polling interval too short, other systems ask the same bridge | polling interval not below 30 s, fewer other requests |
| No push updates | firewall/VLAN, HTTPS URL | enter an HTTP URL of Home Assistant in the local network in the options |
| Clock is off | bridge without internet, wrong Home Assistant clock | check the time of Home Assistant; the bridge needs internet for its time |
| Hashed or plain token | old bridge firmware | update the bridge firmware (button *Firmware Update*) |

**Important for door openers:** commands that open a door are only repeated when the bridge certainly did not execute them. A timeout means "maybe open" and is not repeated. Many such cases show up in the check-up as failed commands.

## Sensors for graphs and automations

| Entity | Content |
|---|---|
| `sensor.nuki_bridge_health` | `ok`, `warning` or `problem`; attribute `problems` lists the findings, `last_checkup` the last check-up |
| `sensor.nuki_bridge_data_errors_24h` | data errors in the last 24 h; attributes `restarts_24h`, `bluetooth_failures_24h` |
| `sensor.nuki_<device>_bluetooth_drops_24h` | Bluetooth drops of this device in 24 h |
| `sensor.nuki_<device>_last_action_duration` | duration of the last command in s; attributes `action`, `success`, `attempts`, `confirmed_by`, `error`, `at` and counters `actions_24h`, `retried_24h`, `failed_24h` |
| `sensor.nuki_bridge_failed_actions` | failed attempts in total (kept across restarts) |

Example: a notification when a device has noticeably many drops.

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.nuki_front_door_bluetooth_drops_24h
    above: 5
actions:
  - action: notify.notify
    data:
      message: "Front door: {{ trigger.to_state.state }} Bluetooth drops in 24 h. Move the bridge closer?"
```

Example: how fast does the door open? Put the `…_last_action_duration` sensor on a history card. If the duration rises and `attempts` is often 2 or more, retries are hiding a radio problem.

## Log, logbook and repair notice

**Home Assistant log:** new Bluetooth problems, retried commands and check-up findings are written at the chosen *log level for problems*:

- **Warning** (default): shows up in the log without further settings.
- **Info** or **Debug**: only shows up when that level is active for the integration. To troubleshoot with every detail: *Settings → Devices & services → Nuki Lock → ⋮ → Enable debug logging*, reproduce the problem, then disable it again. Home Assistant then offers the log file for download.

Failed commands are always reported by Home Assistant as an error of the action, independent of this.

**Logbook:** every command appears with its duration and attempts ("open: done in 1.2 s"), every new Bluetooth problem with a count. The events are called `nuki_ng_action` and `nuki_ng_bluetooth_problem` and can be used as automation triggers too.

**Repair notice:** *Settings → System → Repairs* shows "Nuki bridge is unstable" while there are many Bluetooth drops, Bluetooth failures with restarts or frequent data errors. It disappears by itself when things calm down.

## Settings

*Settings → Devices & services → Nuki Lock → Configure → section Diagnostics*

| Option | Default | Effect |
|---|---|---|
| Watch the bridge log | on | reads the bridge log. Off: no Bluetooth counters, no *Bluetooth Drops* / *Data Errors* sensors, no `/log` requests |
| Log level for problems | Warning | see above |
| Logbook entries | on | commands and Bluetooth problems in the logbook |
| Repair notice when the bridge is unstable | on | see above |

The check-up and the command statistics are always available. The check-up asks the bridge only when you start it.

## Reporting a bug

If none of this helps, please open an [issue](https://github.com/kewe87/hass_nuki_ng/issues) and attach:

1. the check-up report (text of the notification),
2. the diagnostics: *Settings → Devices & services → Nuki Lock → ⋮ → Download diagnostics*. Tokens and addresses are removed. It contains the Bluetooth history of the last 24 h and the command statistics.
3. a debug log if needed (see above).
