**English** · [Deutsch](README.de.md)

# Nuki Lock (nuki_ng)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

The secure and reliable Nuki integration for Home Assistant.
Smart Locks and Opener, local push via the Nuki Bridge, optionally the Nuki Web API.

> **If Home Assistant says the door was opened, it was opened.
> And it never opens twice or late.**

## Why nuki_ng?

Compared with the **Core** integration built into Home Assistant and with **kvj**, the project nuki_ng is based on:

| Security | Core | kvj | **nuki_ng** |
|---|:-:|:-:|:-:|
| Encrypted token | ☑️ ¹ | ❌ | ✅ |
| Push only from bridge | ❌ | ❌ | ✅ |
| Push data validated | ❌ | ❌ | ✅ |
| Secret webhook ID | ❌ | ❌ | ✅ |
| Blocks "open all" | ➖ | ❌ | ✅ |
| Admin-only maintenance | ➖ | ❌ | ✅ |

| Reliability | Core | kvj | **nuki_ng** |
|---|:-:|:-:|:-:|
| Checks action result | ❌ | ❌ | ✅ |
| Retries on bridge failure | ❌ | ❌ | ✅ |
| Never opens twice/late | ➖ ³ | ➖ ³ | ✅ |
| Result for scripts | ❌ | ❌ | ✅ |
| Handles busy bridge | ❌ | ❌ | ✅ |
| Survives bridge reboot | ❌ | ❌ | ✅ |
| Ignores stale states | ❌ | ❌ | ✅ |
| Keeps other callbacks | ✅ | ❌ | ✅ |
| Fully async | ❌ | ❌ | ✅ |

| Features | Core | kvj | **nuki_ng** |
|---|:-:|:-:|:-:|
| Web API / hybrid | ❌ | ✅ | ✅ |
| Doorbell event | ❌ | ❌ | ✅ |
| Bridge health sensors | ❌ | ❌ | ✅ |
| Reboot / FW update | ❌ | ✅ | ✅ |
| RSSI, keypad battery | ❌ | ✅ | ✅ |
| Re-authentication | ✅ | ❌ | ✅ |
| Diagnostics download | ❌ | ❌ | ✅ |
| DHCP discovery | ✅ | ❌ | ❌ ² |

✅ yes · ☑️ partly · ❌ no · ➖ not applicable

<sub>¹ optional, off by default · ² discovery via Nuki server instead · ³ no retries at all<br>
Core = Home Assistant 2026.11 (pynuki 1.6.3), kvj = kvj/hass_nuki_ng 0.5.5, checked against their source code.</sub>

**Switching from kvj is painless:** same domain, same entity IDs.

> **Credits:** Based on [kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng)
> by Konstantin (MIT). Original copyright and license notice are kept in [LICENSE](LICENSE).
> Reworked substantially and maintained independently since 0.7.0.

## What makes the difference

### 🔒 Security

- **Encrypted token** (Bridge API 1.13): the token never travels over the network, every request is only valid for 60 s. Older firmware falls back to the hashed token. The plain token must be chosen explicitly.
- **Hardened push:** secret random webhook ID, only the bridge IP is accepted, only known and valid fields are used.
- **No opening by accident:** "open" is refused for the whole bridge or several devices. Continuous mode (opens the building door for everyone) and authorization switches are off by default.
- **No secrets** in logs, attributes or diagnostics.

### 🚪 Reliable door actions

- **Verified:** only a confirmed success from the bridge counts. Failures are reported, not swallowed.
- **Safe retries:** after a bridge reboot, lock/unlock are repeated – but only after checking that the command didn't already work.
- **Never twice, never late:** "open" is only repeated if it certainly didn't reach the door, and never after 35 s. A lost answer is reported as `unknown` instead of buzzing again minutes later.

### 🛡️ Stability

- One bridge request at a time – the bridge can't handle more.
- Short outages don't make entities unavailable.
- Outdated states after a bridge reboot can't overwrite newer ones.
- Unknown values show as *unknown* instead of breaking entities.

## Installation

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kewe87&repository=hass_nuki_ng&category=integration)

1. HACS → ⋮ → *Custom repositories*
2. Add `https://github.com/kewe87/hass_nuki_ng`, type *Integration*
3. Install **Nuki Lock**, restart Home Assistant

**Coming from kvj:** remove the old repository from HACS (keep the integration), install this one, restart.

Requires Home Assistant 2024.12 or newer.

## Setup

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=nuki_ng)

Search for **Nuki Lock** (not *Nuki* – that's the built-in one). The bridge is found automatically.

- **Bridge token:** Nuki app → *Manage my devices* → *Bridge* → enable HTTP API, set a token.
- **Web API token** (optional): [web.nuki.io](https://web.nuki.io/) → *API* → *Generate API token*.

Bridge only, Web API only (Smart Lock 3.0 Pro and newer) or both.

### Options

- **Callback URL** – automatic by default. Must be HTTP, the bridge can't do HTTPS.
- **Polling interval** – 30 s. States normally arrive by push.
- **Retries** – 3, see *Reliable door actions*.
- **Authentication** – automatic: encrypted token (firmware ≥ 2.14.0 / 1.22.1), else hashed. *Plain* sends the token unencrypted.

## Actions

### Lock entity

- `lock.lock` – Smart Lock: lock · Opener: ring to open off
- `lock.unlock` – Smart Lock: unlock · Opener: ring to open on
- `lock.open` – Smart Lock: unlatch · Opener: buzz the door

### `nuki_ng.execute_action`

Runs a bridge action on **one** lock or opener and returns the result.

Actions: `unlock`, `lock`, `open`, `lock_n_go`, `lock_n_go_open`, `activate_rto`, `deactivate_rto`, `electric_strike_actuation`, `activate_continuous_mode`, `deactivate_continuous_mode`

```yaml
- action: nuki_ng.execute_action
  target:
    entity_id: lock.nuki_front_door_lock
  data:
    action: electric_strike_actuation
  response_variable: result
  continue_on_error: true
- if: >
    {{ not result.success and
       result.results[0].outcome == 'unknown' }}
  then:
    - action: notify.notify
      data:
        message: Door may have opened.
```

Result per device:

- `success` – true / false
- `outcome` – `success`, `not_executed` or `unknown`
- `attempts` – number of tries
- `verified` – false for the Web API (only accepted)
- `error` – error text or null

### Bridge maintenance (admin only)

`nuki_ng.bridge_reboot`, `nuki_ng.bridge_fwupdate`, `nuki_ng.bridge_delete_callback`

## Entities

Names follow the devices in the Nuki app.

**Per lock / opener**
- Lock with *open* / *opening* state
- Doorbell event and ring action (Opener)
- Door sensor: open, state, security state
- Battery, battery critical, charging, keypad battery
- State, RSSI, firmware
- Continuous mode (Opener, **off by default**)

**Bridge**
- Connected, callback set, callback reachable
- Last restart, failed actions
- Reboot and firmware update buttons

**Web API**
- Activity log, last unlock user
- Auto lock, button, ring suppression
- Authorizations (**off by default**)

### Tip: open button with confirmation

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

## Good to know

- *Callback reachable* means Home Assistant serves the URL. Whether the bridge gets through (firewall, VLAN) shows in `last_push` after the next change.
- The bridge holds max. 3 callbacks. nuki_ng only touches its own.
- Avoid very short polling intervals – every request blocks the bridge.

## Changelog

[CHANGELOG.md](CHANGELOG.md) · [Releases](https://github.com/kewe87/hass_nuki_ng/releases)

## License

MIT – see [LICENSE](LICENSE).
Original work © 2021 Konstantin ([kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng)), changes © 2026 kewe87.

*Nuki* is a trademark of Nuki Home Solutions GmbH. This is an independent project, not affiliated with or supported by Nuki. Provided "as is", without warranty.
