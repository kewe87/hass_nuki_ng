**English** | [Deutsch](CHANGELOG.de.md)

# Changelog

## 0.7.5 – unreleased

- **Diagnostics** for everyone who wonders why a door is slow or does not open:
  - *Check-up* button and `nuki_ng.checkup` action: checks bridge, clock, token, push updates, signal, Bluetooth drops, data errors, restarts, commands and batteries, with a hint for every finding.
  - Reads the bridge log (small portions every 10 minutes) and counts Bluetooth drops, Bluetooth failures, data errors and restarts.
  - New sensors: health of the bridge, data errors (24 h), Bluetooth drops (24 h) and duration of the last command per device.
  - Commands and Bluetooth problems in the logbook, a repair notice while the bridge is unstable, problem messages at an adjustable log level.
  - Everything can be switched off in the options (section *Diagnostics*). Failed actions are now kept across restarts.
- [Troubleshooting guide](docs/troubleshooting.md).
- **Entity names in your language**: names follow the language of Home Assistant (English and German included), e.g. *Nuki Front door Battery* or *Nuki Haustür Batterie*. Entity IDs stay the same.
- The check-up report speaks German too and shows a notification right away while it runs.
- **Firmware updates in the update overview**: locks and openers show new firmware under *Settings → Updates*, with release notes from the Nuki help center (option, on by default).
  The bridge button is now called *Update bridge firmware*: the bridge has no release notes, so it stays the way to update the bridge itself.
- Leftover devices of older versions (a second, empty device per lock) are removed automatically. Devices the bridge no longer reports can be deleted in the UI.

## 0.7.4 – 2026-11-17

- Shows the Nuki logo again (light and dark), as the original nuki_ng did, so the integration is easy to recognise.
- README: clear legal notice – free, unofficial hobby project; Nuki name and logo belong to Nuki Home Solutions GmbH.

## 0.7.3 – 2026-11-17

- Brand icon (`brand/icon.png`, `brand/icon@2x.png`), so the integration shows an icon in Home Assistant.

## 0.7.2 – 2026-11-12

- Faster retry when the bridge reports a failed action (usually a dropped Bluetooth connection to the device):
  the next attempt starts after 1 s instead of 5 s, so the door opens sooner after a hiccup.

## 0.7.1 – 2026-11-11

- README reworked: comparison first, readable on phones; German README and changelog.
- Validation with hassfest and the HACS action on every push.
- Bridge maintenance actions select the bridge with a device field instead of a target
  (YAML with `target: device_id` keeps working).
- Manifest keys sorted, `hacs.json` cleaned up.

## 0.7.0 – 2026-11-11

Independent project from this version on (based on kvj/hass_nuki_ng, see [LICENSE](LICENSE)).

### Security
- Encrypted token authentication (xsalsa20poly1305, Bridge API 1.13) by default, automatic fallback
  to the deprecated hashed token; plain token only when chosen explicitly.
- Random secret webhook ID instead of one derived from the config entry.
- Push callbacks accepted only from the bridge IP (HTTP 403 otherwise), body size limited,
  payload whitelisted and type checked, device type must match, no timestamps taken from callbacks.
- Opening actions (`open`, `lock_n_go_open`, `electric_strike_actuation`) refused for the whole
  bridge or several devices.
- Bridge maintenance actions (`bridge_reboot`, `bridge_fwupdate`, `bridge_delete_callback`) admin only.
- Continuous mode and authorization switches disabled by default.
- Tokens, hashes, nonces and webhook ID redacted in logs, attributes and diagnostics; token fields are
  password fields; user input is no longer logged.

### Reliability
- HTTP 503 (bridge busy, nothing executed) retried transparently.
- Opening actions repeated only when they certainly did not reach the device, never later than 35 s;
  a lost answer is reported as `unknown`.
- Lock/unlock check the current state before repeating, so a command is not sent twice.
- `execute_action` returns `outcome` (`success` / `not_executed` / `unknown`) and `verified`, and
  validates the action.
- HTTP 401 starts the re-authentication flow.
- Polled state merge compares bridge timestamps only (no clock skew issues).
- Pending actions stop and timers are cancelled on unload.
- Callbacks of other systems are never removed; repair issue when all 3 slots are taken.
- Web API config writes read the current config first and update the cache only after success.

### Entities
- Unknown state values (e.g. opener boot run) become *unknown* instead of errors.
- Lock entity reports *open* / *opening* while unlatching or buzzing.
- Missing battery level is *unknown* instead of 0 %.
- Callback URLs removed from attributes; last push, rejected pushes and auth mode exposed instead.
- Device model derived from the device type.

### Configuration
- Each bridge can only be added once (hardware ID).
- Re-authentication flow when the token changes.
- New option: bridge authentication mode.
- English and German translations for flows, repair issues and actions.

### Upgrade notes
- After the update the bridge callback is re-registered with a new secret URL automatically.
- If you used `switch.…_continuous_mode` or the authorization switches, enable them again under
  *Settings → Entities*.
- Scripts using the response of `nuki_ng.execute_action` can now read `outcome` per device.

## 0.6.0 – 2026-11-11

First release of this fork.

- Lock actions verified (`success` from the bridge), bridge requests serialized, short outages tolerated.
- `execute_action` runs only on the targeted devices and returns results.
- Options flow: callback URL, polling interval, retries.
- Doorbell event entity, bridge health entities, diagnostics download.

## 0.5.5 and earlier

See the original project [kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng).
