[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://hacs.xyz/docs/faq/custom_repositories)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)

# Nuki Lock (nuki_ng) – secure and reliable Nuki Bridge integration for Home Assistant

Controls Nuki Smart Locks and the Nuki Opener through the **local Nuki Bridge HTTP API**
(push + polling) and optionally the **Nuki Web API**.

This project focuses on one thing: **when Home Assistant says the door was opened, it was opened –
and it never opens twice or late.**

> **Credits & license:** This project is based on [kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng)
> by Konstantin, published under the MIT license. The original copyright and license notice are kept
> in [LICENSE](LICENSE). Since 0.7.0 the code base has been reworked substantially and is maintained
> independently here. It is a drop-in replacement: same domain (`nuki_ng`), same entity IDs.

---

## Highlights

### Security
* **Encrypted token authentication** (xsalsa20poly1305, Bridge API 1.13) – the token never travels
  over the network. Automatic mode falls back to the hashed token (deprecated by Nuki) only if the
  bridge firmware is too old; the plain token is a last resort and must be chosen explicitly.
* **Hardened push endpoint:**
  * random, secret webhook ID (not derived from the config entry),
  * accepted only from the IP address of the bridge (others get HTTP 403),
  * only known fields with valid types and ranges are taken over, the device type must match,
  * a callback can never inject a timestamp or fake an "always newer" state.
* **No opening by accident:**
  * actions that open a door (`open`, `lock_n_go_open`, `electric_strike_actuation`) are refused when
    the target is the whole bridge or several devices,
  * the *Continuous Mode* switch (opens the building door for everyone) and the keypad/fob
    *authorization* switches are disabled by default – a scene, group or voice assistant can't
    switch them on unless you enable them deliberately,
  * bridge maintenance actions (`bridge_reboot`, `bridge_fwupdate`, `bridge_delete_callback`)
    are admin only.
* **No secrets in logs, attributes or diagnostics:** tokens, hashes, encrypted tokens and nonces are
  redacted everywhere, the callback URL (which contains the webhook ID) is no longer an entity
  attribute, token fields are password fields.

### Reliable actions
* **Verified execution:** `/lockAction` is synchronous – only `{"success": true}` counts as done.
  Failures raise an error instead of being reported as "ok".
* **Safe retries:**
  * HTTP 503 ("bridge busy, nothing executed") is retried transparently,
  * lock/unlock are retried after the bridge is back (it reboots itself after BLE failures);
    before repeating, the current state is checked, so a command is not sent again when it already
    took effect,
  * **opening actions are only repeated when they certainly did not reach the device** and never
    later than 35 seconds after the request – a lost answer (timeout) is reported as `unknown`
    instead of buzzing the door a second time minutes later.
* **Result per device** from `nuki_ng.execute_action`: `success`, `attempts`, `outcome`
  (`success`, `not_executed`, `unknown`), `verified`, `error` – usable in scripts.

### Stability
* All bridge requests are serialized and spaced (the bridge handles one request at a time).
* Short outages (bridge reboot, Wi-Fi hiccup) keep the last known state for up to 3 polls instead
  of making every entity unavailable; polling speeds up meanwhile.
* After a reboot the bridge reports its stored, outdated state. That state is not allowed to
  override a newer pushed state. Only bridge timestamps are compared, so a clock difference between
  bridge and Home Assistant can't hide a real change.
* Unknown or new state values (e.g. opener "boot run") are shown as *unknown* instead of crashing
  entities.
* Callback management never touches callbacks of other systems; a repair issue tells you when all
  3 callback slots are taken or the callback URL is not reachable.
* Pending actions stop when the integration is reloaded; timers are cleaned up on unload.
* Fully async (aiohttp), no blocking calls in the event loop.

### Features
* Bridge-only, Web-API-only (Smart Lock 3.0 Pro and newer, polling) or hybrid mode.
* Locks and openers as `lock` entities (incl. *open* / *opening* state while unlatching or buzzing).
* **Doorbell event** (`event` entity) for the Opener, *Ring Action* binary sensor.
* Bridge health: *Connected*, *Callback Set*, *Callback Reachable* (with last push time and rejected
  pushes), *Last Restart*, *Failed Actions*, firmware versions, **Reboot** and **Firmware Update**
  buttons.
* Battery, keypad battery, door sensor, RSSI, firmware sensors; Web API: activity log, last unlock
  user, auto lock, button, ring suppression and authorization entities.
* Options: callback URL (empty = automatic), polling interval, retries, authentication mode.
* Re-authentication flow when the bridge token changes; each bridge can only be added once.
* Diagnostics download with all secrets redacted.
* English and German translations.

---

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kewe87&repository=hass_nuki_ng&category=integration)

1. HACS → three dots → *Custom repositories* → `https://github.com/kewe87/hass_nuki_ng`, category *Integration*.
2. Install **Nuki Lock** and restart Home Assistant.

Switching from kvj/hass_nuki_ng: remove the old repository from HACS (keep the integration),
install this one, restart. Domain and entity IDs stay the same.

Requires Home Assistant 2024.12 or newer.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=nuki_ng)

Search for **Nuki Lock** (not *Nuki* – that is the built-in integration). The bridge address is
discovered automatically. Enter the Bridge API token and/or a Web API token.

**Bridge API token:** Nuki app → *Manage my devices* → *Bridge* → enable the HTTP API and set a
token (6–20 characters).

**Web API token (optional):** [web.nuki.io](https://web.nuki.io/) → *API* → *Generate API token*.

### Options

| Option | Default | Meaning |
|---|---|---|
| Home Assistant URL for bridge callbacks | automatic | HTTP URL the bridge calls for push updates. The bridge can't use HTTPS. |
| State polling interval | 30 s | Fallback polling; states normally arrive by push. |
| Retries for failed lock actions | 3 | See *Safe retries* above. |
| Bridge authentication | Automatic | Encrypted token (firmware ≥ 2.14.0 / 1.22.1), fallback hashed. *Plain* sends the token unencrypted. |

## Actions

### `lock.lock` / `lock.unlock` / `lock.open`
| | Smart Lock | Opener |
|---|---|---|
| `lock.lock` | lock | deactivate ring to open (and continuous mode) |
| `lock.unlock` | unlock | activate ring to open |
| `lock.open` | unlatch | buzz the door (electric strike) |

### `nuki_ng.execute_action`
Runs any bridge action on **one** targeted lock or opener and returns the result.
Actions: `unlock`, `lock`, `open`, `lock_n_go`, `lock_n_go_open`, `activate_rto`, `deactivate_rto`,
`electric_strike_actuation`, `activate_continuous_mode`, `deactivate_continuous_mode`.

```yaml
- action: nuki_ng.execute_action
  target:
    entity_id: lock.nuki_front_door_lock
  data:
    action: electric_strike_actuation
  response_variable: result
  continue_on_error: true
- if: "{{ not result.success and result.results[0].outcome == 'unknown' }}"
  then:
    # The bridge did not answer - the door may have been opened. Don't repeat blindly.
    - action: notify.notify
      data:
        message: "Not sure whether the door was opened."
```

Response:
```yaml
success: true
results:
  - device: Front door
    nuki_id: 123456789
    success: true
    attempts: 1
    outcome: success      # success | not_executed | unknown
    verified: true        # false for the Web API (command only accepted)
    error: null
```

### Bridge maintenance (admin only)
`nuki_ng.bridge_reboot`, `nuki_ng.bridge_fwupdate`, `nuki_ng.bridge_delete_callback` (`command`: URL).

## Entities

Entity IDs depend on the device names in the Nuki app (`front_door` below).

| Entity | Description |
|---|---|
| `lock.nuki_front_door_lock` | Lock / opener control |
| `event.nuki_front_door_doorbell` | Opener: fires `ring` on every doorbell ring |
| `binary_sensor.nuki_front_door_ring_action` | Opener: on for 5 s after a ring |
| `binary_sensor.nuki_front_door_locked` | Lock state as binary sensor |
| `binary_sensor.nuki_front_door_door_open`, `sensor.…_door_state`, `sensor.…_door_security_state` | Door sensor |
| `sensor.…_battery`, `binary_sensor.…_battery_critical`, `…_battery_charging`, `…_keypad_battery_critical` | Batteries |
| `sensor.…_state`, `sensor.…_rssi`, `sensor.…_firmware_version` | Diagnostics |
| `switch.…_continuous_mode` | Opener continuous mode (**disabled by default**) |
| `switch.…_authorization_*` | Web API authorizations (**disabled by default**) |
| `binary_sensor.nuki_bridge_connected`, `…_bridge_callback_set`, `…_callback_reachable` | Bridge connectivity and push |
| `sensor.nuki_bridge_last_restart`, `sensor.nuki_bridge_failed_actions` | Bridge health |
| `button.nuki_bridge_reboot`, `button.nuki_bridge_firmware_update` | Bridge maintenance |

### Tip: open/unlatch from the dashboard
```yaml
type: button
entity: lock.nuki_front_door_lock
name: Open
tap_action:
  action: perform-action
  perform_action: lock.open
  target:
    entity_id: lock.nuki_front_door_lock
  confirmation:
    text: Open the door?
```

## Notes
* *Callback Reachable* means Home Assistant serves the callback URL. Whether the bridge can reach it
  (firewall, VLAN) shows in the `last_push` attribute after the next state change.
* The bridge keeps max. 3 callbacks. This integration only manages its own entry.
* Don't use very short polling intervals: every request occupies the single-threaded bridge.

---

## Comparison

| | Home Assistant core `nuki` | [kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng) 0.5.5 | **this project** |
|---|---|---|---|
| Bridge API (local) | ✅ | ✅ | ✅ |
| Nuki Web API / hybrid | ❌ | ✅ | ✅ |
| Encrypted token | ✅ optional | ❌ (plain; hashed option ignored) | ✅ **default**, automatic fallback |
| Action result checked (`success`) | ❌ only connection errors | ❌ | ✅ |
| Retries on bridge failure / self-reboot | ❌ | ❌ | ✅ |
| Protection against double/late opening | – | – | ✅ open actions only retried when certainly not executed, 35 s limit |
| Action result for scripts | ❌ | ❌ | ✅ `success` / `outcome` / `attempts` |
| Bridge requests serialized (single threaded bridge, HTTP 503) | ❌ | ❌ | ✅ incl. 503 retry |
| Survives a bridge reboot without going unavailable | ❌ | ❌ | ✅ |
| Outdated state after bridge reboot filtered | ❌ | ❌ | ✅ |
| Webhook ID independent of the config entry ID | ❌ | ❌ | ✅ random secret |
| Push accepted only from the bridge IP | ❌ | ❌ | ✅ |
| Push payload validated | ❌ | ❌ | ✅ |
| Open action on all devices at once prevented | – | ❌ (`execute_action` hit every device) | ✅ |
| Continuous mode / authorization switches | service only | enabled by default | disabled by default |
| Maintenance services admin-only | – | ❌ | ✅ |
| Foreign bridge callbacks left untouched | ✅ | ❌ (re-adds max. 3, drops one) | ✅ |
| Callback URL check + repair issue | HTTPS issue only | ❌ | ✅ |
| Doorbell event entity | ❌ | ❌ | ✅ |
| Bridge reboot / firmware update buttons | ❌ | ✅ | ✅ |
| Bridge health sensors (restart, failed actions, push) | ❌ | ❌ | ✅ |
| RSSI, keypad battery | ❌ | ✅ | ✅ |
| Re-authentication flow | ✅ | ❌ | ✅ |
| Duplicate bridge prevented | ✅ | ❌ | ✅ |
| DHCP discovery | ✅ | ❌ (cloud discovery) | ❌ (cloud discovery) |
| Async I/O | ❌ (blocking library in executor) | ❌ (blocking requests in executor) | ✅ aiohttp |
| Diagnostics download (secrets redacted) | ❌ | ❌ | ✅ |

Compared with Home Assistant core 2026.11 (pynuki 1.6.3) and kvj/hass_nuki_ng 0.5.5.

## License

MIT – see [LICENSE](LICENSE). Original work © 2021 Konstantin ([kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng)).
