"""Nuki Bridge / Nuki Web API client and the data update coordinator.

Design notes (Bridge HTTP API v1.13.x):

* The bridge handles exactly one request at a time and answers HTTP 503
  when it is busy. All bridge requests are therefore serialized, spaced and
  a 503 (= "not executed") is retried.
* /lockAction is synchronous: {"success": true} means the device executed
  the command. Everything else is a failure - but a *timeout* or a dropped
  connection is ambiguous: the command may have been executed and only the
  answer got lost. Actions that open a door are never repeated in that case.
* Authentication defaults to the encrypted token (xsalsa20poly1305, bridge
  firmware >= 2.14.0 / 1.22.1). The hashed token is deprecated by Nuki, the
  plain token sends the secret in clear text and is only a last resort.
"""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from nacl.secret import SecretBox

from homeassistant.components import webhook
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .constants import (
    AUTH_AUTO,
    AUTH_ENCRYPTED,
    AUTH_HASHED,
    AUTH_PLAIN,
    CONF_ACTION_RETRIES,
    CONF_AUTH_MODE,
    CONF_WEBHOOK_ID,
    DEFAULT_ACTION_RETRIES,
    DOMAIN,
    OPEN_ACTIONS,
)

_LOGGER = logging.getLogger(__name__)

BRIDGE_DISCOVERY_API = "https://api.nuki.io/discover/bridges"
LEGACY_HOOK_PREFIX = "nuki_ng_bridge_hook_"

# Plain requests (info, list, callbacks) are answered immediately.
BRIDGE_TIMEOUT = 10
# /lockAction only answers after the BLE command was executed (or failed).
BRIDGE_ACTION_TIMEOUT = 45
# The bridge is single threaded: keep a gap between two requests.
BRIDGE_MIN_GAP = 0.5
# HTTP 503 = "another request is running", nothing was executed.
BRIDGE_BUSY_RETRIES = 3
BRIDGE_BUSY_WAIT = 1.5
WEB_TIMEOUT = 20

# After a failed action: wait for the bridge to answer again (it reboots
# itself after BLE failures, which takes ~15-20 s).
ACTION_RETRY_MIN_WAIT = 5
ACTION_RETRY_FAST_WAIT = 1
ACTION_RETRY_MAX_WAIT = 60
# Actions that open a door must not happen late: no new attempt is started
# after this many seconds. Lock/unlock may take longer.
OPEN_ACTION_DEADLINE = 35
ACTION_DEADLINE = 120

# Transient outages: keep the last known state for this many failed polls
# and poll faster meanwhile, instead of turning everything unavailable.
MAX_POLL_FAILURES = 3
FAILURE_POLL_SECONDS = 20

# How often the callback URL is checked for reachability.
CALLBACK_CHECK_SECONDS = 900
MAX_BRIDGE_CALLBACKS = 3
MAX_CALLBACK_BODY = 4096

LOCK_DEVICE_TYPES = (0, 3, 4, 5)
OPENER_DEVICE_TYPES = (2,)
WEB_DEVICE_TYPES = (0, 2, 4, 5)  # Type 3 (Smart Door) is bridge-only

DEVICE_MODELS = {
    0: "Smart Lock 1.0/2.0",
    2: "Opener",
    3: "Smart Door",
    4: "Smart Lock 3.0 (Pro)",
    5: "Smart Lock",
}

BRIDGE_ACTIONS = {
    "unlock": 1,
    "lock": 2,
    "open": 3,
    "lock_n_go": 4,
    "lock_n_go_open": 5,
    "activate_rto": 1,
    "deactivate_rto": 2,
    "electric_strike_actuation": 3,
    "activate_continuous_mode": 4,
    "deactivate_continuous_mode": 5,
}
WEB_ACTIONS = {
    **BRIDGE_ACTIONS,
    "activate_continuous_mode": 6,
    "deactivate_continuous_mode": 7,
}
ALL_ACTIONS = tuple(BRIDGE_ACTIONS)

# States that prove a door was opened (lock: unlatched/unlatching,
# opener: open/opening).
OPENED_STATES = (5, 7)

# Fields accepted from a bridge callback and their types/ranges.
_INT = (int,)
CALLBACK_FIELDS = {
    "mode": (_INT, 0, 255),
    "state": (_INT, 0, 255),
    "batteryChargeState": (_INT, 0, 100),
    "doorsensorState": (_INT, 0, 255),
    "batteryCritical": ((bool,), None, None),
    "batteryCharging": ((bool,), None, None),
    "keypadBatteryCritical": ((bool,), None, None),
    "ringactionState": ((bool,), None, None),
    "stateName": ((str,), None, 64),
    "doorsensorStateName": ((str,), None, 64),
    "ringactionTimestamp": ((str,), None, 40),
}


class NukiError(HomeAssistantError):
    """Communication with the bridge or the web API failed."""


class NukiNotExecuted(NukiError):
    """The request certainly did not reach the device (safe to repeat)."""


class NukiBusy(NukiNotExecuted):
    """HTTP 503: the bridge was busy with another request."""


class NukiAuthError(NukiError):
    """HTTP 401: the token was rejected."""


class NukiActionFailed(NukiError):
    """A lock action failed.

    outcome is "not_executed" when every attempt certainly failed before
    reaching the device, "unknown" when it may have been executed.
    """

    def __init__(self, message: str, outcome: str = "unknown", attempts: int = 1):
        super().__init__(message)
        self.outcome = outcome
        self.attempts = attempts


@dataclass
class ActionResult:
    success: bool
    attempts: int
    outcome: str  # success | not_executed | unknown
    verified: bool
    error: str | None = None


_REDACT_RE = re.compile(r"(token|ctoken|nonce|hash|rnr|ts)=[^&\s]+")


def redact(text) -> str:
    return _REDACT_RE.sub(r"\1=***", str(text))


def parse_bridge_address(address: str) -> tuple[str, int]:
    address = (address or "").strip()
    address = re.sub(r"^https?://", "", address).rstrip("/")
    match = re.match(r"^(.+):(\d+)$", address)
    if match and not address.endswith("]"):
        return match.group(1), int(match.group(2))
    return address, 8080


class NukiInterface:
    def __init__(
        self,
        hass,
        *,
        bridge: str | None = None,
        token: str | None = None,
        web_token: str | None = None,
        auth_mode: str = AUTH_AUTO,
    ):
        self.hass = hass
        self.bridge = bridge
        self.host, self.port = parse_bridge_address(bridge or "")
        self.token = token
        self.web_token = web_token
        self.auth_mode = auth_mode or AUTH_AUTO
        # The mode actually used (resolved from "auto" on first contact).
        self.active_auth = AUTH_ENCRYPTED if self.auth_mode == AUTH_AUTO else self.auth_mode
        self._box = SecretBox(sha256(token.encode("utf-8")).digest()) if token else None
        self._bridge_lock = asyncio.Lock()
        self._last_bridge_request = 0.0

    # ---- HTTP ---------------------------------------------------------

    async def _http(self, method: str, url: str, *, timeout: float, headers=None, json_body=None):
        session = async_get_clientsession(self.hass)
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                text = await response.text()
                if response.status == 401:
                    raise NukiAuthError(f"HTTP 401 (token rejected) from {redact(url)}")
                if response.status == 503:
                    raise NukiBusy(f"HTTP 503 (busy) from {redact(url)}")
                if response.status >= 400:
                    raise NukiError(f"HTTP {response.status} from {redact(url)}: {redact(text[:200])}")
                if response.status != 200 or not text.strip():
                    return dict()
                try:
                    return json.loads(text)
                except ValueError as err:
                    raise NukiError(f"Invalid JSON from {redact(url)}: {redact(text[:200])}") from err
        except (asyncio.TimeoutError, TimeoutError) as err:
            raise NukiError(f"Timeout after {timeout} s: {redact(url)}") from err
        except aiohttp.ClientConnectorError as err:
            # The TCP connection was never established: nothing was sent.
            raise NukiNotExecuted(f"Cannot connect to {redact(url)}: {redact(err)}") from err
        except aiohttp.ClientError as err:
            raise NukiError(f"Connection error for {redact(url)}: {redact(err)}") from err

    async def discover_bridge(self) -> str:
        try:
            response = await self._http("GET", BRIDGE_DISCOVERY_API, timeout=WEB_TIMEOUT)
            bridges = response.get("bridges", []) if isinstance(response, dict) else []
            for bridge in bridges:
                if bridge.get("ip") and bridge.get("ip") != "0.0.0.0":
                    port = bridge.get("port") or 8080
                    return bridge["ip"] if port == 8080 else f"{bridge['ip']}:{port}"
        except NukiError as err:
            _LOGGER.warning("Failed to discover bridge: %s", err)
        return ""

    # ---- Bridge -------------------------------------------------------

    def _auth_params(self, mode: str) -> dict:
        if mode == AUTH_PLAIN:
            return {"token": self.token}
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rnr = secrets.randbelow(65536)
        if mode == AUTH_HASHED:
            hashed = sha256(f"{ts},{rnr},{self.token}".encode("utf-8")).hexdigest()
            return {"ts": ts, "rnr": rnr, "hash": hashed}
        nonce = secrets.token_bytes(SecretBox.NONCE_SIZE)
        ctoken = self._box.encrypt(f"{ts},{rnr}".encode("utf-8"), nonce).ciphertext
        return {"ctoken": ctoken.hex(), "nonce": nonce.hex()}

    def bridge_url(self, path: str, extra=None, mode: str | None = None) -> str:
        params = dict(extra or {})
        params.update(self._auth_params(mode or self.active_auth))
        return f"http://{self.host}:{self.port}{path}?{urlencode(params)}"

    async def bridge_request(
        self, path: str, extra=None, timeout: float = BRIDGE_TIMEOUT, mode: str | None = None
    ):
        """Serialized bridge request; HTTP 503 (busy) is retried."""
        async with self._bridge_lock:
            for attempt in range(BRIDGE_BUSY_RETRIES + 1):
                wait = BRIDGE_MIN_GAP - (time.monotonic() - self._last_bridge_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    # Built inside the lock, so time based tokens are fresh.
                    url = self.bridge_url(path, extra, mode)
                    return await self._http("GET", url, timeout=timeout)
                except NukiBusy:
                    if attempt >= BRIDGE_BUSY_RETRIES:
                        raise
                    _LOGGER.debug("Bridge busy on %s, retrying", path)
                    await asyncio.sleep(BRIDGE_BUSY_WAIT * (attempt + 1))
                finally:
                    self._last_bridge_request = time.monotonic()

    async def resolve_auth(self) -> dict:
        """Find a working authentication mode and return /info.

        "auto" prefers the encrypted token and falls back to the deprecated
        hashed token, then to the plain token (old firmware or clock skew).
        """
        if self.auth_mode != AUTH_AUTO:
            return await self.bridge_request("/info", mode=self.auth_mode)
        last_err = None
        for mode in (AUTH_ENCRYPTED, AUTH_HASHED, AUTH_PLAIN):
            try:
                info = await self.bridge_request("/info", mode=mode)
            except NukiAuthError as err:
                last_err = err
                continue
            if mode != self.active_auth:
                _LOGGER.warning(
                    "Bridge rejected the %s token, using the %s token instead. "
                    "Check the bridge firmware (encrypted token needs >= 2.14.0) "
                    "and the clock of Home Assistant.",
                    self.active_auth,
                    mode,
                )
            self.active_auth = mode
            return info
        raise last_err

    async def bridge_list(self) -> dict:
        data = await self.bridge_request("/list")
        if not isinstance(data, list):
            raise NukiError(f"Unexpected /list response: {redact(data)}")
        return {
            item["nukiId"]: item
            for item in data
            if isinstance(item, dict) and isinstance(item.get("nukiId"), int)
        }

    async def bridge_info(self, timeout: float = BRIDGE_TIMEOUT):
        data = await self.bridge_request("/info", timeout=timeout)
        if not isinstance(data, dict):
            raise NukiError(f"Unexpected /info response: {redact(data)}")
        return data

    async def bridge_reboot(self):
        return await self.bridge_request("/reboot")

    async def bridge_fwupdate(self):
        return await self.bridge_request("/fwupdate")

    async def bridge_lock_action(self, dev_id: int, action: str, device_type):
        if action not in BRIDGE_ACTIONS:
            raise NukiError(f"Unknown action: {action}")
        result = await self.bridge_request(
            "/lockAction",
            dict(action=BRIDGE_ACTIONS[action], nukiId=dev_id, deviceType=device_type or 0),
            timeout=BRIDGE_ACTION_TIMEOUT,
        )
        # {"success": true} confirms the execution, anything else is a failure.
        if not isinstance(result, dict) or result.get("success") is not True:
            raise NukiActionFailed(f"Bridge reported failure for '{action}': {redact(result)}")
        return result

    async def bridge_callbacks(self) -> list:
        callbacks = await self.bridge_request("/callback/list")
        items = callbacks.get("callbacks", []) if isinstance(callbacks, dict) else []
        return [item for item in items if isinstance(item, dict) and "url" in item]

    async def _bridge_remove_callback_id(self, callback_id):
        result = await self.bridge_request("/callback/remove", {"id": callback_id})
        if isinstance(result, dict) and result.get("success") is False:
            raise NukiError(f"Removing callback failed: {result.get('message')}")

    async def bridge_remove_callback(self, callback_url: str) -> bool:
        for item in await self.bridge_callbacks():
            if item["url"] == callback_url:
                await self._bridge_remove_callback_id(item["id"])
                return True
        return False

    async def bridge_ensure_callback(self, callback_url: str, is_ours) -> list:
        """Register our callback once; remove only our own stale entries.

        Callbacks of other systems are never touched. Returns the list.
        """
        callbacks = await self.bridge_callbacks()
        changed = False
        for item in callbacks:
            if item["url"] != callback_url and is_ours(item["url"]):
                _LOGGER.info("Removing stale callback %s", redact(item["url"]))
                await self._bridge_remove_callback_id(item["id"])
                changed = True
        if changed:
            callbacks = await self.bridge_callbacks()
        if any(item["url"] == callback_url for item in callbacks):
            return callbacks
        if len(callbacks) >= MAX_BRIDGE_CALLBACKS:
            raise CallbackSlotsFull(f"All {MAX_BRIDGE_CALLBACKS} callback slots of the bridge are used")
        result = await self.bridge_request("/callback/add", {"url": callback_url})
        if isinstance(result, dict) and result.get("success") is False:
            raise NukiError(f"Adding callback failed: {result.get('message')}")
        _LOGGER.info("Bridge callback registered")
        return await self.bridge_callbacks()

    # ---- Web API ------------------------------------------------------

    def web_url(self, path):
        return f"https://api.nuki.io{path}"

    async def web_request(self, method: str, path: str, json_body=None):
        return await self._http(
            method,
            self.web_url(path),
            timeout=WEB_TIMEOUT,
            headers={"authorization": f"Bearer {self.web_token}"},
            json_body=json_body,
        )

    def can_web(self):
        return bool(self.web_token)

    def can_bridge(self):
        return bool(self.token and self.host)

    async def web_lock_action(self, dev_id, action: str):
        if action not in WEB_ACTIONS:
            raise NukiError(f"Unknown action: {action}")
        await self.web_request("POST", f"/smartlock/{dev_id}/action", dict(action=WEB_ACTIONS[action]))

    async def _web_log(self, dev_id) -> list:
        response = await self.web_request("GET", f"/smartlock/{dev_id}/log?limit=20")
        return response if isinstance(response, list) else []

    async def web_get_logs(self, dev_id) -> tuple[dict, dict]:
        """Last log entry and last unlock entry from a single request."""
        lock_actions = {1: "unlock", 2: "lock", 3: "unlatch", 4: "lock_n_go", 5: "lock_n_go_unlatch"}
        opener_actions = {
            1: "activate_rto",
            2: "deactivate_rto",
            3: "electric_strike_actuation",
            6: "activate_continuous_mode",
            7: "deactivate_continuous_mode",
        }
        unlock_actions = {1: "unlock", 3: "unlatch", 5: "lock_n_go_unlatch"}
        last_log, last_unlock = {}, {}
        for item in await self._web_log(dev_id):
            if not isinstance(item, dict) or not item.get("date"):
                continue
            actions = opener_actions if item.get("deviceType") in OPENER_DEVICE_TYPES else lock_actions
            entry = {
                "name": item.get("name"),
                "timestamp": str(item["date"]).replace("Z", "+00:00"),
            }
            if not last_log and item.get("action") in actions:
                last_log = {**entry, "action": actions[item["action"]]}
            if (
                not last_unlock
                and item.get("deviceType") not in OPENER_DEVICE_TYPES
                and item.get("action") in unlock_actions
            ):
                last_unlock = {**entry, "action": unlock_actions[item["action"]]}
            if last_log and last_unlock:
                break
        return last_log, last_unlock

    async def web_list_all_auths(self, dev_id):
        response = await self.web_request("GET", f"/smartlock/{dev_id}/auth")
        if not isinstance(response, list):
            return {}
        return {item["id"]: item for item in response if isinstance(item, dict) and "id" in item}

    async def web_list(self):
        door_state_map = {
            1: "deactivated",
            2: "door closed",
            3: "door opened",
            4: "door state unknown",
            5: "calibrating",
            16: "uncalibrated",
            240: "removed",
            255: "unknown",
        }
        lock_state_map = {
            0: "uncalibrated",
            1: "locked",
            2: "unlocking",
            3: "unlocked",
            4: "locking",
            5: "unlatched",
            6: "unlocked (lock 'n' go)",
            7: "unlatching",
            254: "motor blocked",
            255: "undefined",
        }
        opener_state_map = {
            0: "untrained",
            1: "online",
            3: "ring to open active",
            5: "open",
            7: "opening",
            253: "boot run",
            255: "undefined",
        }
        resp = await self.web_request("GET", "/smartlock")
        result = dict()
        for item in resp if isinstance(resp, list) else []:
            if not isinstance(item, dict) or item.get("type") not in WEB_DEVICE_TYPES:
                continue
            state = item.get("state") or {}
            states = opener_state_map if item.get("type") in OPENER_DEVICE_TYPES else lock_state_map
            result[item.get("smartlockId")] = {
                "deviceType": item.get("type"),
                "nukiId": item.get("smartlockId"),
                "web": True,
                "name": item.get("name"),
                "firmwareVersion": str(item.get("firmwareVersion")),
                "config": item.get("config"),
                "advancedConfig": item.get("advancedConfig"),
                "openerAdvancedConfig": item.get("openerAdvancedConfig"),
                "lastKnownState": {
                    "mode": state.get("mode"),
                    "state": state.get("state"),
                    "stateName": states.get(state.get("state")),
                    "batteryCritical": state.get("batteryCritical"),
                    "batteryCharging": state.get("batteryCharging"),
                    "batteryChargeState": state.get("batteryCharge"),
                    "keypadBatteryCritical": state.get("keypadBatteryCritical"),
                    "doorsensorState": state.get("doorState"),
                    "doorsensorStateName": door_state_map.get(state.get("doorState")),
                    "timestamp": item.get("updateDate"),
                },
            }
        return result

    async def web_update_auth(self, dev_id, auth_id: str, changes: dict):
        await self.web_request("POST", f"/smartlock/{dev_id}/auth/{auth_id}", changes)

    async def web_update_config(self, dev_id, name: str, changes: dict) -> dict:
        """Read the current config, apply the changes and write it back.

        Reading first avoids writing back stale values that were changed in
        the Nuki app since the last poll. Returns the written config.
        """
        mapping = {
            "config": "/config",
            "advancedConfig": "/advanced/config",
            "openerAdvancedConfig": "/advanced/openerconfig",
        }
        if name not in mapping:
            raise NukiError(f"Unknown config section: {name}")
        current = await self.web_request("GET", f"/smartlock/{dev_id}")
        section = current.get(name) if isinstance(current, dict) else None
        if not isinstance(section, dict):
            raise NukiError(f"Config section {name} not available")
        updated = copy.deepcopy(section)
        updated.update(changes)
        await self.web_request("POST", f"/smartlock/{dev_id}" + mapping[name], updated)
        return updated


class CallbackSlotsFull(NukiError):
    """All callback slots of the bridge are used by other systems."""


def _clean_callback(body: dict) -> dict:
    """Keep only known callback fields with valid types and ranges."""
    clean = {}
    for key, (types, low, high) in CALLBACK_FIELDS.items():
        if key not in body:
            continue
        value = body[key]
        if types == _INT:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if not low <= value <= high:
                continue
        elif not isinstance(value, types):
            continue
        elif isinstance(value, str):
            if len(value) > high:
                continue
            if key == "ringactionTimestamp" and dt_util.parse_datetime(value) is None:
                continue
        clean[key] = value
    return clean


class NukiCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, config: dict):
        self.entry = entry
        self.api = NukiInterface(
            hass,
            bridge=config.get("address"),
            token=config.get("token"),
            web_token=config.get("web_token"),
            auth_mode=config.get(CONF_AUTH_MODE, AUTH_AUTO),
        )
        self._update_seconds = int(config.get("update_seconds", 30))
        self.action_retries = int(config.get(CONF_ACTION_RETRIES, DEFAULT_ACTION_RETRIES))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_method=self._update,
            update_interval=timedelta(seconds=self._update_seconds),
        )

        self.hook_id = config[CONF_WEBHOOK_ID]
        self.hook_path = webhook.async_generate_path(self.hook_id)
        self._legacy_hook_path = webhook.async_generate_path(f"{LEGACY_HOOK_PREFIX}{entry.entry_id}")
        # An empty hass_url means: detect it automatically.
        self.configured_url = (config.get("hass_url") or "").strip().rstrip("/")
        self.hass_url = self.configured_url or self._auto_url()
        self.callback_reachable = None
        self._callback_checked = 0.0
        self.last_push = None
        self.rejected_pushes = 0
        self._bridge_ips: set[str] = set()
        self._ips_resolved = 0.0

        self._auth_resolved = False
        self._poll_failures = 0
        self.failed_actions = 0
        self.last_action_error = None
        self.bridge_last_restart = None
        self._ring_listeners = {}
        self._ring_reset = {}
        self._last_ring = {}
        self._poll_ts = {}
        self._pushed = {}
        self._opened_at = {}
        self._action_locks = {}
        self._closing = False

    # ---- Lifecycle ----------------------------------------------------

    async def async_start(self):
        await self._resolve_bridge_ips()
        webhook.async_unregister(self.hass, self.hook_id)
        webhook.async_register(
            self.hass,
            DOMAIN,
            "Nuki bridge",
            self.hook_id,
            handler=self._hook_handler,
            local_only=True,
            allowed_methods=["POST", "HEAD"],
        )

    async def unload(self):
        self._closing = True
        webhook.async_unregister(self.hass, self.hook_id)
        for cancel in list(self._ring_reset.values()):
            cancel()
        self._ring_reset.clear()
        try:
            if self.api.can_bridge() and self.hass_url:
                await self.api.bridge_remove_callback(self.bridge_hook)
        except Exception as err:  # noqa: BLE001 - best effort on unload
            _LOGGER.warning("Failed to remove callback: %s", redact(err))

    # ---- Callback URL -------------------------------------------------

    @property
    def bridge_hook(self) -> str:
        return f"{self.hass_url}{self.hook_path}"

    def _is_own_callback(self, url: str) -> bool:
        return url.endswith(self.hook_path) or url.endswith(self._legacy_hook_path)

    def _auto_url(self) -> str:
        try:
            url = get_url(
                self.hass,
                allow_external=False,
                allow_cloud=False,
                allow_ip=True,
                prefer_external=False,
            ).rstrip("/")
        except NoURLAvailableError:
            return ""
        # The bridge can't call HTTPS URLs.
        return url if url.lower().startswith("http://") else ""

    async def _probe_url(self, base: str) -> bool:
        """Check that the webhook answers on this URL.

        This proves that Home Assistant serves the URL, not that the bridge
        can reach it (firewall/VLAN) - see last_push for that.
        """
        session = async_get_clientsession(self.hass, verify_ssl=False)
        try:
            async with session.head(
                f"{base}{self.hook_path}", timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return response.status < 400
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError):
            return False

    async def _check_callback_url(self):
        """Pick a reachable base URL for the bridge callback."""
        if (
            self.callback_reachable
            and time.monotonic() - self._callback_checked < CALLBACK_CHECK_SECONDS
        ):
            return
        self._callback_checked = time.monotonic()
        candidates = []
        for url in (self.configured_url, self._auto_url()):
            if url and url not in candidates:
                candidates.append(url)
        issue_id = f"callback_unreachable_{self.entry.entry_id}"
        for base in candidates:
            if await self._probe_url(base):
                if base != self.hass_url:
                    _LOGGER.warning(
                        "Callback URL %s is not reachable, using %s instead. "
                        "Update the Home Assistant URL in the integration options.",
                        self.hass_url or "(none)",
                        base,
                    )
                    self.hass_url = base
                self.callback_reachable = True
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                return
        self.callback_reachable = False
        _LOGGER.warning("No reachable callback URL (tried %s), state updates rely on polling only", candidates)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="callback_unreachable",
            translation_placeholders={"url": self.hass_url or "-"},
        )

    async def _resolve_bridge_ips(self):
        """IP addresses the bridge callbacks are accepted from."""
        self._ips_resolved = time.monotonic()
        host = self.api.host
        if not host:
            self._bridge_ips = set()
            return
        try:
            self._bridge_ips = {str(ipaddress.ip_address(host.strip("[]")))}
            return
        except ValueError:
            pass
        try:
            infos = await self.hass.loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            self._bridge_ips = {info[4][0] for info in infos}
        except OSError as err:
            _LOGGER.warning("Cannot resolve bridge address %s: %s", host, err)

    # ---- Callback (push) handling -------------------------------------

    async def _hook_handler(self, hass, hook_id, request):
        if request.method != "POST":
            return None
        remote = request.remote
        if self._bridge_ips and remote not in self._bridge_ips:
            if time.monotonic() - self._ips_resolved > 60 and not self._is_ip_config():
                # The bridge may have a new address (DHCP + hostname config).
                await self._resolve_bridge_ips()
            if remote not in self._bridge_ips:
                self.rejected_pushes += 1
                _LOGGER.warning("Rejected Nuki callback from %s (not the bridge)", remote)
                return web.Response(status=403)
        if request.content_length is not None and request.content_length > MAX_CALLBACK_BODY:
            self.rejected_pushes += 1
            return web.Response(status=413)
        try:
            body = json.loads(await request.text())
        except ValueError:
            self.rejected_pushes += 1
            _LOGGER.warning("Bridge callback with invalid JSON body ignored")
            return web.Response(status=400)
        if not isinstance(body, dict):
            self.rejected_pushes += 1
            return web.Response(status=400)
        _LOGGER.debug("Bridge callback: %s", body)
        self._apply_callback(body)
        return None

    def _is_ip_config(self) -> bool:
        try:
            ipaddress.ip_address(self.api.host.strip("[]"))
            return True
        except ValueError:
            return False

    @callback
    def add_ring_listener(self, dev_id, listener) -> CALLBACK_TYPE:
        listeners = self._ring_listeners.setdefault(dev_id, [])
        listeners.append(listener)

        def _remove():
            if listener in listeners:
                listeners.remove(listener)

        return _remove

    @callback
    def _apply_callback(self, body: dict):
        dev_id = body.get("nukiId")
        if not self.data or isinstance(dev_id, bool) or not isinstance(dev_id, int):
            return
        device = self.data.get("devices", {}).get(dev_id)
        if not device:
            _LOGGER.debug("Callback for unknown device %s ignored", dev_id)
            return
        if body.get("deviceType") != device.get("deviceType"):
            _LOGGER.warning("Callback for %s with wrong device type ignored", dev_id)
            self.rejected_pushes += 1
            return
        update = _clean_callback(body)
        if not update:
            return
        self.last_push = dt_util.utcnow()
        last_state = device.setdefault("lastKnownState", {})
        last_state.update(update)
        self._pushed[dev_id] = True
        if update.get("state") in OPENED_STATES:
            self._opened_at[dev_id] = time.monotonic()
        self.async_update_listeners()

        if update.get("ringactionState") is True and self.is_opener(dev_id):
            ring_ts = update.get("ringactionTimestamp")
            if ring_ts is None or ring_ts != self._last_ring.get(dev_id):
                self._last_ring[dev_id] = ring_ts
                for listener in list(self._ring_listeners.get(dev_id, [])):
                    listener(update)
            self._schedule_ring_reset(dev_id)

    @callback
    def _schedule_ring_reset(self, dev_id):
        """Turn off the ring_action binary sensor after 5 seconds."""
        if cancel := self._ring_reset.pop(dev_id, None):
            cancel()

        @callback
        def _reset(_now):
            self._ring_reset.pop(dev_id, None)
            state = self.device_data(dev_id).get("lastKnownState")
            if state is not None and state.get("ringactionState"):
                state["ringactionState"] = False
                self.async_update_listeners()

        self._ring_reset[dev_id] = async_call_later(self.hass, 5, _reset)

    # ---- Polling ------------------------------------------------------

    def _track_bridge_restart(self, bridge_info: dict):
        uptime = bridge_info.get("uptime")
        current = dt_util.parse_datetime(str(bridge_info.get("currentTime") or ""))
        if not isinstance(uptime, int) or current is None:
            return
        restart = current - timedelta(seconds=uptime)
        previous = self.bridge_last_restart
        # currentTime and uptime have a resolution of 1 s, ignore jitter.
        if previous is None or abs((restart - previous).total_seconds()) > 90:
            if previous is not None:
                _LOGGER.info("Nuki bridge has restarted at %s", restart.isoformat())
                self._callback_checked = 0.0
            self.bridge_last_restart = restart

    def _merge_polled_state(self, dev_id, item: dict):
        """Merge a polled state with what we got via push.

        After a reboot the bridge reports the state it had stored before,
        with its old timestamp. A polled state whose (bridge) timestamp did
        not advance since the last poll must not override a pushed state.
        Only bridge timestamps are compared - never with Home Assistant's
        clock, so clock skew can't hide a real change.
        """
        previous = self.device_data(dev_id).get("lastKnownState")
        current = item.get("lastKnownState")
        if not isinstance(current, dict):
            item["lastKnownState"] = previous or {}
            return
        new_ts = dt_util.parse_datetime(str(current.get("timestamp") or ""))
        last_ts = self._poll_ts.get(dev_id)
        if previous:
            for key in ("ringactionTimestamp",):
                if key not in current and key in previous:
                    current[key] = previous[key]
            if (
                self._pushed.get(dev_id)
                and last_ts is not None
                and new_ts is not None
                and new_ts <= last_ts
                and current.get("state") != previous.get("state")
            ):
                _LOGGER.info(
                    "%s: ignoring outdated polled state '%s' (bridge timestamp %s did not "
                    "advance), keeping pushed state '%s'",
                    item.get("name", dev_id),
                    current.get("stateName"),
                    new_ts.isoformat(),
                    previous.get("stateName"),
                )
                kept = dict(current)
                for key in ("state", "stateName", "mode"):
                    if key in previous:
                        kept[key] = previous[key]
                item["lastKnownState"] = kept
                return
        if new_ts is not None and (last_ts is None or new_ts >= last_ts):
            if last_ts is None or new_ts > last_ts:
                self._pushed[dev_id] = False
            self._poll_ts[dev_id] = new_ts

    async def _update(self):
        try:
            result = await self._fetch()
        except NukiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - any failure counts as outage
            self._poll_failures += 1
            self.update_interval = timedelta(seconds=min(FAILURE_POLL_SECONDS, self._update_seconds))
            if self.data and self._poll_failures < MAX_POLL_FAILURES:
                _LOGGER.warning(
                    "Update failed (%s/%s), keeping last known state: %s",
                    self._poll_failures,
                    MAX_POLL_FAILURES,
                    redact(err),
                )
                return self.data
            raise UpdateFailed(redact(err)) from err
        if self._poll_failures:
            _LOGGER.info("Update succeeded again after %s failure(s)", self._poll_failures)
        self._poll_failures = 0
        self.update_interval = timedelta(seconds=self._update_seconds)
        return result

    async def _fetch(self):
        callbacks_list = None
        bridge_info = None
        info_mapping = dict()
        device_list = None
        web_list = None

        def web_id_for_item(item):
            if item.get("web"):
                return item["nukiId"]  # Already in web mode
            as_hex = "{0:x}".format(item["nukiId"])
            device_type = item.get("deviceType", 0)
            if device_type != 0:
                as_hex = f"{device_type}{as_hex}"
            return int(as_hex, 16)

        if self.api.can_bridge():
            if not self._auth_resolved:
                bridge_info = await self.api.resolve_auth()
                self._auth_resolved = True
            else:
                bridge_info = await self.api.bridge_info()
            self._track_bridge_restart(bridge_info)
            for item in bridge_info.get("scanResults", []) or []:
                if isinstance(item, dict):
                    info_mapping[item.get("nukiId")] = item
            issue_id = f"callback_slots_full_{self.entry.entry_id}"
            try:
                await self._check_callback_url()
                if self.hass_url:
                    callbacks_list = await self.api.bridge_ensure_callback(
                        self.bridge_hook, self._is_own_callback
                    )
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            except CallbackSlotsFull as err:
                _LOGGER.warning("%s", err)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="callback_slots_full",
                )
            except NukiAuthError:
                raise
            except NukiError as err:
                _LOGGER.warning("Failed to update the bridge callback: %s", redact(err))
            bridge_info["callbacks_list"] = callbacks_list
            bridge_info["callback_url"] = self.bridge_hook if self.hass_url else None
            bridge_info["callback_reachable"] = self.callback_reachable
            bridge_info["auth_mode"] = self.api.active_auth
            device_list = await self.api.bridge_list()
        if self.api.can_web():
            try:
                web_list = await self.api.web_list()
            except HomeAssistantError as err:
                _LOGGER.warning("Despite being configured, Web API request has failed: %s", redact(err))
            if not device_list:
                device_list = web_list
        result = dict(devices={}, bridge_info=bridge_info)
        if not device_list:
            raise NukiError("No available device data")
        for item in device_list.values():
            dev_id = item["nukiId"]
            web_id = None
            if self.api.can_web() and item.get("deviceType") in WEB_DEVICE_TYPES:
                web_id = web_id_for_item(item)
                item["webId"] = web_id
                previous = self.device_data(dev_id)
                try:
                    item["web_auth"] = await self.api.web_list_all_auths(web_id)
                except HomeAssistantError as err:
                    _LOGGER.warning("Web API request for authorizations has failed: %s", redact(err))
                    item["web_auth"] = previous.get("web_auth", {})
                try:
                    item["last_log"], item["last_unlock_log"] = await self.api.web_get_logs(web_id)
                except HomeAssistantError as err:
                    _LOGGER.warning("Web API request for the activity log has failed: %s", redact(err))
                    item["last_log"] = previous.get("last_log", {})
                    item["last_unlock_log"] = previous.get("last_unlock_log", {})
            if web_list and web_id is not None:
                web_item = web_list.get(web_id, {})
                item["config"] = web_item.get("config")
                item["advancedConfig"] = web_item.get("advancedConfig")
                item["openerAdvancedConfig"] = web_item.get("openerAdvancedConfig")
            self._merge_polled_state(dev_id, item)
            item["bridge_info"] = info_mapping.get(dev_id)
            result["devices"][dev_id] = item
        return result

    # ---- Actions ------------------------------------------------------

    async def _wait_for_bridge(self, deadline: float) -> bool:
        """Wait until the bridge answers again (e.g. after a self-reboot)."""
        await asyncio.sleep(min(ACTION_RETRY_MIN_WAIT, max(0, deadline - time.monotonic())))
        end = min(deadline, time.monotonic() + ACTION_RETRY_MAX_WAIT)
        while time.monotonic() < end and not self._closing:
            try:
                await self.api.bridge_info(timeout=5)
                return True
            except NukiError:
                await asyncio.sleep(2)
        return False

    def _target_reached(self, dev_id, action: str, state: dict) -> bool:
        """Whether a (polled) state shows that an idempotent action is done."""
        lock_state, mode = state.get("state"), state.get("mode")
        if self.is_opener(dev_id):
            return {
                "activate_rto": lock_state == 3,
                "unlock": lock_state == 3,
                "deactivate_rto": lock_state == 1,
                "lock": lock_state == 1 and mode == 2,
                "activate_continuous_mode": mode == 3,
                "deactivate_continuous_mode": mode == 2,
            }.get(action, False)
        return {"lock": lock_state == 1, "unlock": lock_state == 3}.get(action, False)

    async def _state_after(self, dev_id) -> dict:
        try:
            devices = await self.api.bridge_list()
        except NukiError:
            return {}
        return (devices.get(dev_id) or {}).get("lastKnownState") or {}

    async def action(self, dev_id, action: str) -> ActionResult:
        """Execute an action, verify it and retry when it is safe.

        Opening actions are only repeated when the previous attempt certainly
        did not reach the device (or the bridge reported a failure and no
        opening was seen), and never after OPEN_ACTION_DEADLINE seconds.
        Raises NukiActionFailed when the action did not succeed.
        """
        if action not in BRIDGE_ACTIONS:
            raise NukiError(f"Unknown action: {action}")
        name = self.device_data(dev_id).get("name", dev_id)
        if not self.api.can_bridge():
            if not self.api.can_web():
                raise NukiError("Neither bridge nor web API configured")
            await self.api.web_lock_action(self.web_id(dev_id), action)
            await self.async_request_refresh()
            # The web API only accepts the command (HTTP 204): not verified.
            return ActionResult(True, 1, "success", verified=False)

        lock = self._action_locks.setdefault(dev_id, asyncio.Lock())
        async with lock:
            return await self._bridge_action(dev_id, name, action)

    async def _bridge_action(self, dev_id, name, action: str) -> ActionResult:
        opening = action in OPEN_ACTIONS
        device_type = self.device_data(dev_id).get("deviceType")
        attempts_max = 1 + max(0, self.action_retries)
        started = time.monotonic()
        deadline = started + (OPEN_ACTION_DEADLINE if opening else ACTION_DEADLINE)
        attempt = 0
        outcome = "not_executed"
        last_err = None
        while True:
            attempt += 1
            try:
                await self.api.bridge_lock_action(dev_id, action, device_type)
            except NukiAuthError as err:
                self.failed_actions += 1
                self._action_failed(action, err)
                self.entry.async_start_reauth(self.hass)
                raise NukiActionFailed(f"{name}: '{action}' failed: {err}", "not_executed", attempt) from err
            except NukiError as err:
                last_err = err
                self.failed_actions += 1
                certain = isinstance(err, NukiNotExecuted)
                reported = isinstance(err, NukiActionFailed)
                if not (certain or reported):
                    # Timeout or dropped connection: may have been executed.
                    outcome = "unknown"
                _LOGGER.warning("%s: '%s' failed (attempt %s/%s): %s", name, action, attempt, attempts_max, redact(err))
                if self._closing:
                    break
                if opening and self._opened_since(dev_id, started):
                    return self._action_done(name, action, attempt, verified=True, how="push")
                if opening and not (certain or reported):
                    # Timeout/dropped connection: the door may have opened.
                    _LOGGER.warning("%s: not repeating '%s' - it may have been executed", name, action)
                    break
                if attempt >= attempts_max or time.monotonic() >= deadline:
                    break
                if reported:
                    # The bridge answered "success: false" (typically a BLE connection drop
                    # to the device), so it is up: retry quickly instead of waiting for a reboot.
                    await asyncio.sleep(min(ACTION_RETRY_FAST_WAIT, max(0, deadline - time.monotonic())))
                else:
                    await self._wait_for_bridge(deadline)
                if self._closing or time.monotonic() >= deadline:
                    break
                state = await self._state_after(dev_id)
                if opening:
                    if self._opened_since(dev_id, started):
                        return self._action_done(name, action, attempt, verified=True, how="push")
                elif self._target_reached(dev_id, action, state):
                    return self._action_done(name, action, attempt, verified=True, how="state")
                continue
            return self._action_done(name, action, attempt, verified=True)

        self._action_failed(action, last_err)
        raise NukiActionFailed(
            f"{name}: '{action}' failed after {attempt} attempt(s): {redact(last_err)}",
            outcome,
            attempt,
        )

    def _opened_since(self, dev_id, started: float) -> bool:
        return self._opened_at.get(dev_id, 0) >= started

    def _action_done(self, name, action, attempt, verified: bool, how: str = "bridge") -> ActionResult:
        if attempt > 1 or how != "bridge":
            _LOGGER.info("%s: '%s' succeeded on attempt %s (confirmed by %s)", name, action, attempt, how)
        self.last_action_error = None
        self.hass.async_create_task(self.async_request_refresh(), eager_start=False)
        return ActionResult(True, attempt, "success", verified)

    def _action_failed(self, action, err):
        self.last_action_error = f"{action}: {redact(err)}"
        self.async_update_listeners()

    async def do_reboot(self):
        if not self.api.can_bridge():
            raise HomeAssistantError("Not supported without bridge")
        await self.api.bridge_reboot()

    async def do_fwupdate(self):
        if not self.api.can_bridge():
            raise HomeAssistantError("Not supported without bridge")
        await self.api.bridge_fwupdate()

    async def do_delete_callback(self, callback_url):
        if not self.api.can_bridge():
            raise HomeAssistantError("Not supported without bridge")
        await self.api.bridge_remove_callback(callback_url)

    # ---- Data access --------------------------------------------------

    def device_data(self, dev_id) -> dict:
        if not self.data:
            return {}
        return self.data.get("devices", {}).get(dev_id, {})

    def web_id(self, dev_id):
        return self.device_data(dev_id).get("webId", dev_id)

    def info_data(self):
        return (self.data or {}).get("bridge_info") or {}

    def is_lock(self, dev_id) -> bool:
        return self.device_data(dev_id).get("deviceType") in LOCK_DEVICE_TYPES

    def is_opener(self, dev_id) -> bool:
        return self.device_data(dev_id).get("deviceType") in OPENER_DEVICE_TYPES

    def device_model(self, dev_id) -> str:
        return "Nuki " + DEVICE_MODELS.get(self.device_data(dev_id).get("deviceType"), "Device")

    def device_supports(self, dev_id, feature: str) -> bool:
        return self.device_data(dev_id).get("lastKnownState", {}).get(feature) is not None

    def info_field(self, dev_id, default, *args):
        data = self.device_data(dev_id)
        for field in args:
            if not isinstance(data, dict):
                return default
            data = data.get(field)
            if data is None:
                return default
        return default if data is None else data

    async def update_web_auth(self, dev_id, auth: dict, changes: dict):
        if "id" not in auth:
            raise HomeAssistantError("Invalid auth entry")
        await self.api.web_update_auth(self.web_id(dev_id), auth["id"], changes)
        auth_obj = self.device_data(dev_id).get("web_auth", {}).get(auth["id"])
        if auth_obj is not None:
            auth_obj.update(changes)
        self.async_update_listeners()

    async def update_config(self, dev_id, name: str, changes: dict):
        _LOGGER.debug("Updating config %s: %s", name, changes)
        updated = await self.api.web_update_config(self.web_id(dev_id), name, changes)
        # Only touch the cache after a successful write.
        device = self.device_data(dev_id)
        if device:
            device[name] = updated
        self.async_update_listeners()
