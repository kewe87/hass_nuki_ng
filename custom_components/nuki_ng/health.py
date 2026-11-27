"""Health of the bridge and its devices: bridge log, action statistics, check-up rules.

No Home Assistant imports, so the rules can be tested on their own.

The bridge keeps a short log (GET /log) with Bluetooth and system events. It is
read in small portions: asking for many entries at once makes the bridge stop
answering HTTP for a while. Only entries newer than the last one seen count.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DAY = 24 * 3600

# Kinds of problems found in the bridge log
DROP = "ble_drop"  # the Bluetooth connection broke off early, the bridge usually retries
BLE_FAILURE = "ble_failure"  # the bridge gave up on Bluetooth (and restarts itself)
UART = "uart"  # broken data between the WiFi and the Bluetooth chip, often a weak power supply
RESTART = "restart"  # the bridge started

KINDS = (DROP, BLE_FAILURE, UART, RESTART)

OK = "ok"
WARNING = "warning"
PROBLEM = "problem"
_RANK = {OK: 0, WARNING: 1, PROBLEM: 2}

# Thresholds of the check-up
RSSI_WEAK = -70  # dBm, at or below: weak
RSSI_BAD = -85
DROPS_WARN = 1  # per 24 h
DROPS_BAD = 10
UART_WARN = 3
RESTARTS_WARN = 1
CLOCK_WARN_SEC = 30
RESPONSE_WARN_SEC = 2.0


def classify(entry_type: str) -> str | None:
    """Return the kind of problem of a bridge log entry, None for normal entries."""
    if "EarlyDisconnect" in entry_type:
        return DROP
    if "RebootBLEFailed" in entry_type or entry_type in ("BLE-CloseError", "BLE-ConnectError", "BLE-InitFailed"):
        return BLE_FAILURE
    if "Uart" in entry_type and ("Error" in entry_type or "CRC" in entry_type):
        return UART
    if entry_type in ("System-Startup", "Startup"):
        return RESTART
    return None


def device_hex(nuki_id: int | str | None) -> str | None:
    """The bridge log names devices in hex ("30E6840A"), the HTTP API in decimal."""
    if nuki_id is None:
        return None
    if isinstance(nuki_id, int):
        return f"{nuki_id:X}"
    return str(nuki_id).upper()


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Problem:
    time: float
    kind: str
    device: str | None  # hex id, None for the bridge itself
    type: str


class BridgeLog:
    """Remembers the problems of the last 24 h from the bridge log."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.last_time: float | None = data.get("last_time")
        self.last_keys: set[str] = set(data.get("last_keys", []))
        self.events: deque[Problem] = deque(Problem(**e) for e in data.get("events", []))
        self.totals: dict[str, int] = {k: int(v) for k, v in (data.get("totals") or {}).items()}
        self.gap = False  # the last read did not reach back to the one before: entries were missed
        self.read_at: float | None = data.get("read_at")

    @staticmethod
    def _key(entry: dict) -> str:
        return f"{entry.get('timestamp')}|{entry.get('type')}|{entry.get('nukiId')}|{entry.get('bleHandle')}"

    def feed(self, entries: list[dict], asked: int, now: float) -> list[Problem]:
        """Take one read of the log (newest first) and return the new problems."""
        self.read_at = now
        timed = [(t, e) for e in entries if isinstance(e, dict) and (t := _ts(e.get("timestamp"))) is not None]
        if not timed:
            return []
        first_read = self.last_time is None
        oldest = min(t for t, _ in timed)
        self.gap = not first_read and len(entries) >= asked and oldest > self.last_time
        new: list[Problem] = []
        newest = max(t for t, _ in timed)
        keys_at_newest: set[str] = set()
        for t, entry in sorted(timed, key=lambda x: x[0]):
            key = self._key(entry)
            if t == newest:
                keys_at_newest.add(key)
            if self.last_time is not None and (t < self.last_time or (t == self.last_time and key in self.last_keys)):
                continue
            kind = classify(str(entry.get("type", "")))
            if kind is None:
                continue
            problem = Problem(t, kind, device_hex(entry.get("nukiId")), str(entry.get("type")))
            if first_read and now - t > DAY:
                continue
            self.events.append(problem)
            self.totals[kind] = self.totals.get(kind, 0) + 1
            new.append(problem)
        if self.last_time is None or newest > self.last_time:
            self.last_time, self.last_keys = newest, keys_at_newest
        elif newest == self.last_time:
            self.last_keys |= keys_at_newest
        self.prune(now)
        return new

    def prune(self, now: float) -> None:
        while self.events and now - self.events[0].time > DAY:
            self.events.popleft()

    def count(self, kind: str, device: str | None = None, now: float | None = None, any_device: bool = False) -> int:
        """Problems of one kind in the last 24 h, for one device or the bridge."""
        if now is not None:
            self.prune(now)
        return sum(1 for e in self.events if e.kind == kind and (any_device or e.device == device))

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_time": self.last_time,
            "last_keys": sorted(self.last_keys),
            "events": [e.__dict__ for e in self.events],
            "totals": self.totals,
            "read_at": self.read_at,
        }


@dataclass
class ActionRecord:
    action: str
    success: bool
    attempts: int
    seconds: float
    confirmed_by: str | None
    at: float
    error: str | None = None


@dataclass
class ActionStats:
    """What the commands to one device did, kept across restarts."""

    last: ActionRecord | None = None
    total: int = 0
    failed: int = 0
    retried: int = 0
    recent: deque = field(default_factory=deque)  # (time, success, attempts) of the last 24 h

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ActionStats":
        data = data or {}
        last = data.get("last")
        return cls(
            last=ActionRecord(**last) if last else None,
            total=int(data.get("total", 0)),
            failed=int(data.get("failed", 0)),
            retried=int(data.get("retried", 0)),
            recent=deque(tuple(r) for r in data.get("recent", [])),
        )

    def record(self, record: ActionRecord) -> None:
        self.last = record
        self.total += 1
        if not record.success:
            self.failed += 1
        if record.attempts > 1:
            self.retried += 1
        self.recent.append((record.at, record.success, record.attempts))
        self.prune(record.at)

    def prune(self, now: float) -> None:
        while self.recent and now - self.recent[0][0] > DAY:
            self.recent.popleft()

    def day(self, now: float) -> dict[str, int]:
        self.prune(now)
        return {
            "actions": len(self.recent),
            "failed": sum(1 for _, ok, _ in self.recent if not ok),
            "retried": sum(1 for _, _, n in self.recent if n > 1),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "last": self.last.__dict__ if self.last else None,
            "total": self.total,
            "failed": self.failed,
            "retried": self.retried,
            "recent": [list(r) for r in self.recent],
        }


@dataclass
class Check:
    id: str
    status: str
    title: str
    detail: str
    hint: str | None = None
    device: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def worst(checks: list[Check]) -> str:
    return max((c.status for c in checks), key=_RANK.get, default=OK)


def check_rssi(name: str, rssi: int | None) -> Check:
    if rssi is None:
        return Check("rssi", WARNING, f"{name}: signal", "The bridge reports no signal strength.", "The device may be out of reach of the bridge.", name)
    if rssi <= RSSI_BAD:
        return Check("rssi", PROBLEM, f"{name}: signal", f"{rssi} dBm, very weak.", "Move the bridge closer to the device (ideally within 1-3 m, no metal in between).", name)
    if rssi <= RSSI_WEAK:
        return Check("rssi", WARNING, f"{name}: signal", f"{rssi} dBm, weak.", "Connection drops are likely. A place closer to the device helps.", name)
    return Check("rssi", OK, f"{name}: signal", f"{rssi} dBm, good.", None, name)


def check_drops(name: str, drops: int, watching: bool) -> Check:
    if not watching:
        return Check("ble_drops", OK, f"{name}: Bluetooth drops", "Not watched (bridge log reading is off).", None, name)
    if drops >= DROPS_BAD:
        return Check("ble_drops", PROBLEM, f"{name}: Bluetooth drops", f"{drops} in the last 24 h.", "Frequent drops: check the distance to the bridge, its power supply and the device batteries.", name)
    if drops >= DROPS_WARN:
        return Check("ble_drops", WARNING, f"{name}: Bluetooth drops", f"{drops} in the last 24 h, the bridge retried.", "Occasional drops are normal at a weak signal. If commands get slow, move the bridge closer.", name)
    return Check("ble_drops", OK, f"{name}: Bluetooth drops", "None in the last 24 h.", None, name)


def check_actions(name: str, day: dict[str, int]) -> Check:
    if not day["actions"]:
        return Check("actions", OK, f"{name}: commands", "No commands in the last 24 h.", None, name)
    text = f"{day['actions']} in the last 24 h, {day['retried']} needed a retry, {day['failed']} failed."
    if day["failed"]:
        return Check("actions", PROBLEM, f"{name}: commands", text, "See the Home Assistant log and the logbook for the reason.", name)
    if day["retried"]:
        return Check("actions", WARNING, f"{name}: commands", text, "Retries hide Bluetooth problems: look at the signal and the drops.", name)
    return Check("actions", OK, f"{name}: commands", text, None, name)


def check_uart(count: int, watching: bool) -> Check:
    if not watching:
        return Check("uart", OK, "Bridge: data errors", "Not watched (bridge log reading is off).")
    if count >= UART_WARN:
        return Check("uart", WARNING, "Bridge: data errors", f"{count} UART errors in the last 24 h.", "Often a weak or failing power supply of the bridge. Try another USB power supply (at least 1.5 A) and cable.")
    return Check("uart", OK, "Bridge: data errors", f"{count} in the last 24 h.")


def check_restarts(restarts: int, failures: int, watching: bool) -> Check:
    if not watching:
        return Check("restarts", OK, "Bridge: restarts", "Not watched (bridge log reading is off).")
    if failures:
        return Check("restarts", PROBLEM, "Bridge: restarts", f"Gave up on Bluetooth {failures} time(s) in the last 24 h and restarted.", "Commands fail during the restart. Check the power supply and the distance to the devices.")
    if restarts >= RESTARTS_WARN:
        return Check("restarts", WARNING, "Bridge: restarts", f"{restarts} restart(s) in the last 24 h.", "Fine after a firmware update or a power cut, otherwise check the power supply.")
    return Check("restarts", OK, "Bridge: restarts", "None in the last 24 h.")


def check_clock(skew: float | None) -> Check:
    if skew is None:
        return Check("clock", OK, "Bridge: clock", "Not known.")
    if abs(skew) > CLOCK_WARN_SEC:
        return Check("clock", WARNING, "Bridge: clock", f"Differs by {abs(skew):.0f} s from Home Assistant.", "The encrypted token depends on the time. Check the time of Home Assistant and the internet access of the bridge.")
    return Check("clock", OK, "Bridge: clock", f"In sync ({abs(skew):.0f} s).")


def check_response(seconds: float | None, error: str | None) -> Check:
    if error:
        return Check("bridge", PROBLEM, "Bridge: reachable", f"No answer: {error}", "Check that the bridge is powered, in the WiFi and has HTTP API enabled.")
    if seconds is not None and seconds > RESPONSE_WARN_SEC:
        return Check("bridge", WARNING, "Bridge: reachable", f"Answers slowly ({seconds:.1f} s).", "A busy bridge or a weak WiFi. The bridge handles only one request at a time.")
    return Check("bridge", OK, "Bridge: reachable", f"Answers in {seconds:.2f} s." if seconds is not None else "Answers.")


def check_auth(mode: str | None) -> Check:
    if mode == "encrypted":
        return Check("auth", OK, "Bridge: token", "Encrypted token.")
    if mode == "plain":
        return Check("auth", WARNING, "Bridge: token", "Plain token, the secret travels unencrypted.", "Update the bridge firmware (encrypted token needs 2.14.0 / 1.22.1) and check the clock.")
    return Check("auth", WARNING, "Bridge: token", f"{mode or 'unknown'} token (deprecated by Nuki).", "Update the bridge firmware so the encrypted token can be used.")


def check_callback(configured: bool, reachable: bool | None, last_push_age: float | None) -> Check:
    if not configured:
        return Check("callback", WARNING, "Push updates", "No callback, states come by polling only.", "Set the Home Assistant URL in the options.")
    if reachable is False:
        return Check("callback", WARNING, "Push updates", "The bridge cannot reach Home Assistant, states come by polling only.", "Use an HTTP URL of Home Assistant in the local network (the bridge does not support HTTPS).")
    if last_push_age is None:
        return Check("callback", OK, "Push updates", "Registered, no push received since the start yet.")
    return Check("callback", OK, "Push updates", f"Registered, last push {last_push_age / 60:.0f} min ago.")


def check_battery(name: str, critical: bool | None, percent: int | None) -> Check | None:
    if critical:
        return Check("battery", PROBLEM, f"{name}: battery", f"Critical{f' ({percent} %)' if percent is not None else ''}.", "Replace the batteries soon: weak batteries also cause Bluetooth drops.", name)
    if percent is None:
        return None
    return Check("battery", OK, f"{name}: battery", f"{percent} %.", None, name)


def report_markdown(checks: list[Check], title: str = "Nuki check-up") -> str:
    icon = {OK: "✅", WARNING: "⚠️", PROBLEM: "❌"}
    lines = [f"**{icon[worst(checks)]} {title}: {worst(checks)}**", ""]
    for check in sorted(checks, key=lambda c: -_RANK[c.status]):
        line = f"- {icon[check.status]} **{check.title}**: {check.detail}"
        if check.hint and check.status != OK:
            line += f"  \n  → {check.hint}"
        lines.append(line)
    return "\n".join(lines)
