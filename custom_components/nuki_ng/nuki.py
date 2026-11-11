from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from random import randint
from urllib.parse import urlencode

import aiohttp

from homeassistant.components import webhook
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
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
    CONF_ACTION_RETRIES,
    DEFAULT_ACTION_RETRIES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

BRIDGE_DISCOVERY_API = "https://api.nuki.io/discover/bridges"
BRIDGE_HOOK = "nuki_ng_bridge_hook"

# Plain requests (info, list, callbacks) are answered immediately.
BRIDGE_TIMEOUT = 10
# /lockAction is synchronous: the bridge answers only after the BLE command
# has been executed (or has failed), which can take a while.
BRIDGE_ACTION_TIMEOUT = 45
# The bridge is single threaded and drops or hangs on back-to-back requests.
BRIDGE_MIN_GAP = 0.5
WEB_TIMEOUT = 20

# After a failed action: how long to wait for the bridge to answer again
# (it reboots itself after BLE failures, which takes ~15-20 s).
ACTION_RETRY_MIN_WAIT = 8
ACTION_RETRY_MAX_WAIT = 60

# Transient outages: keep the last known state for this many failed polls
# and poll faster meanwhile, instead of turning everything unavailable.
MAX_POLL_FAILURES = 3
FAILURE_POLL_SECONDS = 20

# How often the callback URL is checked for reachability.
CALLBACK_CHECK_SECONDS = 900

LOCK_DEVICE_TYPES = (0, 3, 4, 5)
OPENER_DEVICE_TYPES = (2,)
WEB_DEVICE_TYPES = (0, 2, 4, 5)  # Type 3 (Smart Door) is bridge-only


class NukiError(HomeAssistantError):
    """Communication with the bridge or the web API failed."""


class NukiActionFailed(NukiError):
    """A lock action was not executed."""


def _redact(url: str) -> str:
    return re.sub(r"(token|hash|rnr|ts)=[^&]+", r"\1=***", url)


class NukiInterface:
    def __init__(
        self, hass, *, bridge: str = None, token: str = None, web_token: str = None, use_hashed: bool = False
    ):
        self.hass = hass
        self.bridge = bridge
        self.token = token
        self.web_token = web_token
        self.use_hashed = bool(use_hashed)
        self._bridge_lock = asyncio.Lock()
        self._last_bridge_request = 0.0

    async def _http(self, method: str, url: str, *, timeout: float, headers=None, json_body=None):
        session = async_get_clientsession(self.hass)
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise NukiError(
                        f"HTTP {response.status} from {_redact(url)}: {text[:200]}"
                    )
                if response.status != 200 or not text.strip():
                    return dict()
                try:
                    return json.loads(text)
                except ValueError as err:
                    raise NukiError(f"Invalid JSON from {_redact(url)}: {text[:200]}") from err
        except asyncio.TimeoutError as err:
            raise NukiError(f"Timeout after {timeout} s: {_redact(url)}") from err
        except aiohttp.ClientError as err:
            raise NukiError(f"Connection error for {_redact(url)}: {err}") from err

    async def discover_bridge(self) -> str:
        try:
            response = await self._http("GET", BRIDGE_DISCOVERY_API, timeout=WEB_TIMEOUT)
            bridges = response.get("bridges", [])
            if len(bridges) > 0:
                return bridges[0]["ip"]
        except Exception as err:
            _LOGGER.warning(f"Failed to discover bridge: {err}")
        return ""

    def bridge_url(self, path: str, extra=None) -> str:
        extra_str = "&%s" % (urlencode(extra)) if extra else ""
        url = f"http://{self.bridge}:8080"
        if re.match(r".+:\d+$", self.bridge):
            # Port inside
            url = f"http://{self.bridge}"
        if self.use_hashed:
            tz = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rnr = randint(0, 65535)
            to_hash = "%s,%s,%s" % (tz, rnr, self.token)
            hashed = sha256(to_hash.encode("utf-8")).hexdigest()
            return f"{url}{path}?ts={tz}&rnr={rnr}&hash={hashed}{extra_str}"
        return f"{url}{path}?token={self.token}{extra_str}"

    async def bridge_request(self, path: str, extra=None, timeout: float = BRIDGE_TIMEOUT):
        """Serialized bridge request with a minimum gap between requests."""
        async with self._bridge_lock:
            wait = BRIDGE_MIN_GAP - (time.monotonic() - self._last_bridge_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                # Build the URL inside the lock, so hashed tokens are fresh.
                return await self._http("GET", self.bridge_url(path, extra), timeout=timeout)
            finally:
                self._last_bridge_request = time.monotonic()

    async def bridge_list(self):
        data = await self.bridge_request("/list")
        if not isinstance(data, list):
            raise NukiError(f"Unexpected /list response: {data}")
        result = dict()
        for item in data:
            result[item.get("nukiId")] = item
        return result

    async def bridge_info(self, timeout: float = BRIDGE_TIMEOUT):
        return await self.bridge_request("/info", timeout=timeout)

    async def bridge_reboot(self):
        return await self.bridge_request("/reboot")

    async def bridge_fwupdate(self):
        return await self.bridge_request("/fwupdate")

    async def bridge_lock_action(self, dev_id: str, action: str, device_type):
        actions_map = {
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
        if action not in actions_map:
            raise NukiError(f"Unknown action: {action}")
        result = await self.bridge_request(
            "/lockAction",
            dict(
                action=actions_map[action],
                nukiId=dev_id,
                deviceType=device_type,
            ),
            timeout=BRIDGE_ACTION_TIMEOUT,
        )
        # The bridge confirms execution with {"success": true}. Anything else
        # means the command did not reach the device (e.g. BLE failure).
        if not isinstance(result, dict) or result.get("success") is not True:
            raise NukiActionFailed(f"Bridge reported failure for '{action}': {result}")
        return result

    async def web_lock_action(self, dev_id: str, action: str):
        actions_map = {
            "unlock": 1,
            "lock": 2,
            "open": 3,
            "lock_n_go": 4,
            "lock_n_go_open": 5,
            "activate_rto": 1,
            "deactivate_rto": 2,
            "electric_strike_actuation": 3,
            "activate_continuous_mode": 6,
            "deactivate_continuous_mode": 7,
        }
        if action not in actions_map:
            raise NukiError(f"Unknown action: {action}")
        await self.web_request(
            "POST", f"/smartlock/{dev_id}/action", dict(action=actions_map.get(action))
        )

    async def bridge_callbacks(self) -> list:
        callbacks = await self.bridge_request("/callback/list")
        return callbacks.get("callbacks", []) if isinstance(callbacks, dict) else []

    async def bridge_remove_callback(self, callback_url: str):
        for item in await self.bridge_callbacks():
            if item["url"] == callback_url:
                result = await self.bridge_request("/callback/remove", {"id": item["id"]})
                if not result.get("success", True):
                    raise NukiError(result.get("message"))
                return True
        return False

    async def bridge_check_callback(self, callback_url: str, hook_path: str):
        """Make sure our callback is registered first.

        Stale callbacks of this entry (same webhook path, but another base
        URL, e.g. after a port change) are removed instead of being kept.
        """
        callbacks_list = await self.bridge_callbacks()
        _LOGGER.debug(f"bridge_check_callback: {callbacks_list}, {callback_url}")
        own = [item for item in callbacks_list if item["url"] == callback_url]
        stale = [
            item for item in callbacks_list
            if item["url"] != callback_url and item["url"].endswith(hook_path)
        ]
        if callbacks_list and callbacks_list[0]["url"] == callback_url and not stale:
            return callbacks_list
        keep = [
            item["url"] for item in callbacks_list
            if item["url"] != callback_url and not item["url"].endswith(hook_path)
        ]
        if own or stale or keep:
            # Re-create the list with our URL first (the bridge keeps max 3).
            for item in callbacks_list:
                await self.bridge_remove_callback(item["url"])
        for url in ([callback_url] + keep)[:3]:
            result = await self.bridge_request("/callback/add", {"url": url})
            if not result.get("success", True):
                raise NukiError(f"Adding callback {url} failed: {result.get('message')}")
        _LOGGER.info(f"Bridge callback set to {callback_url}")
        return await self.bridge_callbacks()

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
        return True if self.web_token else False

    def can_bridge(self):
        return True if self.token and self.bridge else False

    async def web_get_last_log(self, dev_id: str):
        lock_actions_map = {
            1: "unlock",
            2: "lock",
            3: "unlatch",
            4: "lock_n_go",
            5: "lock_n_go_unlatch",
        }
        device_actions_map = {t: lock_actions_map for t in LOCK_DEVICE_TYPES}
        device_actions_map.update({t: {
            1: "activate_rto",
            2: "deactivate_rto",
            3: "electric_strike_actuation",
            6: "activate_continuous_mode",
            7: "deactivate_continuous_mode",
        } for t in OPENER_DEVICE_TYPES})
        response = await self.web_request("GET", f"/smartlock/{dev_id}/log")
        _LOGGER.debug(f"web_get_last_log ({dev_id}): {response}")
        for item in response:
            actions_map = device_actions_map.get(item.get("deviceType"), {})
            if item.get("action") in actions_map.keys():
                return {
                    "name": item.get("name"),
                    "action": actions_map[item["action"]],
                    "timestamp": item["date"].replace("Z", "+00:00"),
                }
        return dict()

    async def web_get_last_unlock_log(self, dev_id: str):
        actions_map = {
            1: "unlock",
            3: "unlatch",
            5: "lock_n_go_unlatch",
        }
        response = await self.web_request("GET", f"/smartlock/{dev_id}/log")
        _LOGGER.debug(f"web_get_last_unlock_log ({dev_id}): {response}")
        for item in response:
            if item.get("action") in actions_map.keys():
                # unlock, unlatch, lock'n'go with unlatch
                return {
                    "name": item.get("name"),
                    "action": actions_map[item["action"]],
                    "timestamp": item["date"].replace("Z", "+00:00"),
                }
        return dict()

    async def web_list_all_auths(self, dev_id: str):
        result = {}
        response = await self.web_request("GET", f"/smartlock/{dev_id}/auth")
        for item in response:
            result[item["id"]] = item
        return result

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
        device_state_map = {t: lock_state_map for t in LOCK_DEVICE_TYPES}
        device_state_map.update({t: {
            0: "untrained",
            1: "online",
            3: "ring to open active",
            5: "open",
            7: "opening",
            253: "boot run",
            255: "undefined",
        } for t in OPENER_DEVICE_TYPES})
        resp = await self.web_request("GET", "/smartlock")
        result = dict()
        for item in resp:
            if item.get("type") not in WEB_DEVICE_TYPES:
                continue
            state = item.get("state", {})
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
                    "stateName": device_state_map.get(item.get("type"), {}).get(
                        state.get("state")
                    ),
                    "batteryCritical": state.get("batteryCritical"),
                    "batteryCharging": state.get("batteryCharging"),
                    "batteryChargeState": state.get("batteryCharge"),
                    "keypadBatteryCritical": state.get("keypadBatteryCritical"),
                    "doorsensorState": state.get("doorState"),
                    "doorsensorStateName": door_state_map.get(
                        state.get("doorState")
                    ),
                    "timestamp": item.get("updateDate"),
                },
            }
        return result

    async def web_update_auth(self, dev_id: str, auth_id: str, changes: dict):
        await self.web_request("POST", f"/smartlock/{dev_id}/auth/{auth_id}", changes)

    async def web_update_config(self, dev_id: str, name: str, changes: dict):
        mapping = {"config": "/config", "advancedConfig": "/advanced/config", "openerAdvancedConfig": "/advanced/openerconfig"}
        await self.web_request("POST", f"/smartlock/{dev_id}" + mapping[name], changes)


class NukiCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, config: dict):
        self.entry = entry
        self.api = NukiInterface(
            hass,
            bridge=config.get("address"),
            token=config.get("token"),
            web_token=config.get("web_token"),
            use_hashed=config.get("use_hashed", False),
        )
        self._update_seconds = config.get("update_seconds", 30)
        self.action_retries = int(config.get(CONF_ACTION_RETRIES, DEFAULT_ACTION_RETRIES))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_method=self._update,
            update_interval=timedelta(seconds=self._update_seconds),
        )

        self.hook_id = "%s_%s" % (BRIDGE_HOOK, entry.entry_id)
        self.hook_path = webhook.async_generate_path(self.hook_id)
        # An empty hass_url means: detect it automatically.
        self.configured_url = (config.get("hass_url") or "").strip().rstrip("/")
        self.hass_url = self.configured_url or self._auto_url()
        self.callback_reachable = None
        self._callback_checked = 0.0

        self._poll_failures = 0
        self.failed_actions = 0
        self.last_action_error = None
        self.bridge_last_restart = None
        self._ring_listeners = {}

        webhook.async_unregister(hass, self.hook_id)
        webhook.async_register(
            hass,
            DOMAIN,
            "bridge",
            self.hook_id,
            handler=self._hook_handler,
            local_only=True,
            allowed_methods=["POST", "HEAD"],
        )

    # ---- Callback URL ------------------------------------------------

    @property
    def bridge_hook(self) -> str:
        return "{}{}".format(self.hass_url, self.hook_path)

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
        session = async_get_clientsession(self.hass, verify_ssl=False)
        try:
            async with session.head(
                f"{base}{self.hook_path}", timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return response.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
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
                        f"Callback URL {self.hass_url or '(none)'} is not reachable, using {base} instead. "
                        "Update the Home Assistant URL in the integration options."
                    )
                    self.hass_url = base
                self.callback_reachable = True
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                return
        self.callback_reachable = False
        _LOGGER.warning(f"No reachable callback URL (tried {candidates}), state updates rely on polling only")
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="callback_unreachable",
            translation_placeholders={"url": self.hass_url or "-"},
        )

    # ---- Callback (push) handling ------------------------------------

    async def _hook_handler(self, hass, hook_id, request):
        if request.method != "POST":
            return None
        try:
            body = await request.json()
        except ValueError:
            _LOGGER.warning("Bridge callback with invalid JSON body ignored")
            return None
        _LOGGER.debug(f"_hook_handler: {body}")
        if isinstance(body, dict):
            self._add_update(body.get("nukiId"), body)
        return None

    @callback
    def add_ring_listener(self, dev_id, listener):
        listeners = self._ring_listeners.setdefault(dev_id, [])
        listeners.append(listener)

        def _remove():
            if listener in listeners:
                listeners.remove(listener)

        return _remove

    def _add_update(self, dev_id, update):
        data = self.data
        if not data:
            return None
        previous = data.get("devices", {}).get(dev_id)
        if not previous:
            _LOGGER.debug(f"Callback for unknown device {dev_id} ignored")
            return None
        last_state = previous.setdefault("lastKnownState", {})
        # Merge the whole payload: callbacks carry keys (e.g. ringactionState)
        # which are not part of the /list response.
        for key, value in update.items():
            if key in ("nukiId", "deviceType"):
                continue
            last_state[key] = value
        if "timestamp" not in update:
            # Callbacks carry no timestamp; mark the state as current so an
            # older state from a later poll can't overwrite it.
            last_state["timestamp"] = dt_util.utcnow().replace(microsecond=0).isoformat()
        self.async_set_updated_data(data)

        if update.get("ringactionState") is True:
            for listener in list(self._ring_listeners.get(dev_id, [])):
                listener(update)

            # Turn off ring_action binary sensor after 5 seconds
            @callback
            def _reset_ring_action(_now):
                last_state["ringactionState"] = False
                self.async_set_updated_data(data)

            async_call_later(self.hass, 5, _reset_ring_action)

    # ---- Polling -----------------------------------------------------

    def _track_bridge_restart(self, bridge_info: dict):
        uptime = bridge_info.get("uptime")
        current = dt_util.parse_datetime(bridge_info.get("currentTime") or "")
        if uptime is None or current is None:
            return
        restart = current - timedelta(seconds=int(uptime))
        previous = self.bridge_last_restart
        # currentTime and uptime have a resolution of 1 s, ignore jitter.
        if previous is None or abs((restart - previous).total_seconds()) > 90:
            if previous is not None:
                _LOGGER.info(f"Nuki bridge has restarted at {restart.isoformat()}")
                self._callback_checked = 0.0
            self.bridge_last_restart = restart

    def _keep_newer_state(self, dev_id, item: dict):
        """Never replace a known state with an older one.

        Right after a reboot the bridge reports the state it had stored
        before, which can be older than what we already got via callback.
        """
        previous = self.device_data(dev_id).get("lastKnownState")
        current = item.get("lastKnownState")
        if not previous or not current:
            return
        prev_ts = dt_util.parse_datetime(str(previous.get("timestamp") or ""))
        new_ts = dt_util.parse_datetime(str(current.get("timestamp") or ""))
        if prev_ts and new_ts and new_ts < prev_ts:
            _LOGGER.info(
                f"{item.get('name', dev_id)}: ignoring outdated state "
                f"'{current.get('stateName')}' from {new_ts.isoformat()}, "
                f"keeping '{previous.get('stateName')}' from {prev_ts.isoformat()}"
            )
            item["lastKnownState"] = previous

    async def _update(self):
        try:
            result = await self._fetch()
        except Exception as err:
            self._poll_failures += 1
            self.update_interval = timedelta(seconds=min(FAILURE_POLL_SECONDS, self._update_seconds))
            if self.data and self._poll_failures < MAX_POLL_FAILURES:
                _LOGGER.warning(
                    f"Update failed ({self._poll_failures}/{MAX_POLL_FAILURES}), keeping last known state: {err}"
                )
                return self.data
            raise UpdateFailed(str(err)) from err
        if self._poll_failures:
            _LOGGER.info(f"Update succeeded again after {self._poll_failures} failure(s)")
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
            deviceType = item.get("deviceType", 0)
            if deviceType != 0:
                as_hex = f"{deviceType}{as_hex}"
            return int(as_hex, 16)

        if self.api.can_bridge():
            bridge_info = await self.api.bridge_info()
            self._track_bridge_restart(bridge_info)
            for item in bridge_info.get("scanResults", []):
                info_mapping[item.get("nukiId")] = item
            try:
                await self._check_callback_url()
                if self.hass_url:
                    callbacks_list = await self.api.bridge_check_callback(
                        self.bridge_hook, self.hook_path
                    )
            except Exception as err:
                _LOGGER.warning(f"Failed to update callback {self.bridge_hook}: {err}")
            bridge_info["callbacks_list"] = callbacks_list
            bridge_info["callback_url"] = self.bridge_hook if self.hass_url else None
            bridge_info["callback_reachable"] = self.callback_reachable
            device_list = await self.api.bridge_list()
        if self.api.can_web():
            try:
                web_list = await self.api.web_list()
            except HomeAssistantError as err:
                _LOGGER.warning(f"Despite being configured, Web API request has failed: {err}")
            if not device_list:
                device_list = web_list
        result = dict(devices={}, bridge_info=bridge_info)
        if not device_list:
            raise NukiError("No available device data")
        for key, item in device_list.items():
            dev_id = item["nukiId"]
            web_id = None
            if self.api.can_web():
                web_id = web_id_for_item(item)
                item["webId"] = web_id
                for field, loader in (
                    ("web_auth", self.api.web_list_all_auths),
                    ("last_unlock_log", self.api.web_get_last_unlock_log),
                    ("last_log", self.api.web_get_last_log),
                ):
                    try:
                        item[field] = await loader(web_id)
                    except HomeAssistantError as err:
                        _LOGGER.warning(f"Web API request for {field} has failed: {err}")
                        item[field] = self.device_data(dev_id).get(field, {}) if self.data else {}
            if web_list:
                item["config"] = web_list.get(web_id, {}).get("config")
                item["advancedConfig"] = web_list.get(web_id, {}).get("advancedConfig")
                item["openerAdvancedConfig"] = web_list.get(web_id, {}).get("openerAdvancedConfig")
            self._keep_newer_state(dev_id, item)
            result["devices"][dev_id] = item
            result["devices"][dev_id]["bridge_info"] = info_mapping.get(dev_id)
        _LOGGER.debug(f"_update: {json.dumps(result, default=str)}")
        return result

    async def unload(self):
        webhook.async_unregister(self.hass, self.hook_id)
        try:
            if self.api.can_bridge():
                result = await self.api.bridge_remove_callback(self.bridge_hook)
                _LOGGER.debug(f"unload: {result} {self.bridge_hook}")
        except Exception as err:
            _LOGGER.warning(f"Failed to remove callback: {err}")

    # ---- Actions -----------------------------------------------------

    async def _wait_for_bridge(self) -> bool:
        """Wait until the bridge answers again (e.g. after a self-reboot)."""
        await asyncio.sleep(ACTION_RETRY_MIN_WAIT)
        deadline = time.monotonic() + ACTION_RETRY_MAX_WAIT
        while time.monotonic() < deadline:
            try:
                await self.api.bridge_info(timeout=5)
                return True
            except NukiError:
                await asyncio.sleep(3)
        return False

    async def action_for_devices(self, action: str):
        for device_id in self.data.get("devices", {}):
            await self.action(device_id, action)

    async def action(self, dev_id, action: str) -> dict:
        """Execute an action, verify it and retry on failure.

        Raises NukiActionFailed when all attempts failed.
        """
        name = self.device_data(dev_id).get("name", dev_id)
        if self.api.can_bridge():
            device_type = self.device_data(dev_id).get("deviceType")
            attempts = 1 + max(0, self.action_retries)
            last_err = None
            for attempt in range(1, attempts + 1):
                try:
                    result = await self.api.bridge_lock_action(dev_id, action, device_type)
                except NukiError as err:
                    last_err = err
                    self.failed_actions += 1
                    _LOGGER.warning(
                        f"{name}: '{action}' failed (attempt {attempt}/{attempts}): {err}"
                    )
                    if attempt < attempts:
                        await self._wait_for_bridge()
                    continue
                _LOGGER.debug(f"bridge action result: {result}, {action}")
                if attempt > 1:
                    _LOGGER.info(f"{name}: '{action}' succeeded on attempt {attempt}")
                self.last_action_error = None
                await self.async_request_refresh()
                return dict(success=True, attempts=attempt)
            self.last_action_error = f"{action}: {last_err}"
            self.async_update_listeners()
            raise NukiActionFailed(
                f"{name}: '{action}' failed after {attempts} attempt(s): {last_err}"
            )
        elif self.api.can_web():
            await self.api.web_lock_action(self.web_id(dev_id), action)
            await self.async_request_refresh()
            _LOGGER.debug(f"web action result: {action}")
            return dict(success=True, attempts=1)
        raise NukiError("Neither bridge nor web API configured")

    async def do_reboot(self):
        if self.api.can_bridge():
            await self.api.bridge_reboot()
        else:
            raise HomeAssistantError("Not supported")

    async def do_fwupdate(self):
        if self.api.can_bridge():
            await self.api.bridge_fwupdate()
        else:
            raise HomeAssistantError("Not supported")

    async def do_delete_callback(self, callback_url):
        if self.api.can_bridge():
            await self.api.bridge_remove_callback(callback_url)
        else:
            raise HomeAssistantError("Not supported")

    # ---- Data access -------------------------------------------------

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

    def device_supports(self, dev_id, feature: str) -> bool:
        return self.device_data(dev_id).get("lastKnownState", {}).get(feature) != None

    def info_field(self, dev_id, default, *args):
        data = self.device_data(dev_id)
        for field in args:
            data = data.get(field)
            if data == None:
                return default
        return default if data == None else data

    async def update_web_auth(self, dev_id, auth: dict, changes: dict):
        if "id" not in auth:
            raise UpdateFailed("Invalid auth entry")
        await self.api.web_update_auth(self.web_id(dev_id), auth["id"], changes)
        data = self.data
        auth_obj = data["devices"].get(dev_id, {}).get("web_auth", {}).get(auth["id"], {})
        for key in changes:
            auth_obj[key] = changes[key]
        self.async_set_updated_data(data)

    async def update_config(self, dev_id, name: str, changes: dict):
        data = self.data
        obj = data["devices"].get(dev_id, {}).get(name)
        for key in changes:
            obj[key] = changes[key]
        _LOGGER.debug(f"Updating config: {name}: {changes} = {obj}")
        await self.api.web_update_config(self.web_id(dev_id), name, obj)
        self.async_set_updated_data(data)
