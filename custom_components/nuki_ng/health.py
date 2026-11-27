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
# Set so that only what users notice is reported: a few drops a day that the
# bridge retries by itself are normal and stay "ok".
RSSI_WEAK = -80  # dBm, at or below: weak
RSSI_BAD = -90
DROPS_WARN = 6  # per 24 h
DROPS_BAD = 20
RETRIES_WARN = 3  # commands that needed a retry, per 24 h
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


TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "t_signal": "{name}: signal",
        "t_drops": "{name}: Bluetooth drops",
        "t_actions": "{name}: commands",
        "t_battery": "{name}: battery",
        "t_uart": "Bridge: data errors",
        "t_restarts": "Bridge: restarts",
        "t_clock": "Bridge: clock",
        "t_bridge": "Bridge: reachable",
        "t_auth": "Bridge: token",
        "t_callback": "Push updates",
        "t_web": "Nuki Web API",
        "not_watched": "Not watched (bridge log reading is off).",
        "rssi_none": "The bridge reports no signal strength.",
        "rssi_none_h": "The device may be out of reach of the bridge.",
        "rssi_bad": "{rssi} dBm, very weak.",
        "rssi_bad_h": "Move the bridge closer to the device (ideally within 1-3 m, no metal in between).",
        "rssi_weak": "{rssi} dBm, weak.",
        "rssi_weak_h": "Connection drops are likely. A place closer to the device helps.",
        "rssi_ok": "{rssi} dBm, good.",
        "drops_bad": "{n} in the last 24 h.",
        "drops_bad_h": "Frequent drops: check the distance to the bridge, its power supply and the device batteries.",
        "drops_warn": "{n} in the last 24 h.",
        "drops_warn_h": "More drops than usual. If commands get slow, move the bridge closer or replace the batteries.",
        "drops_few": "{n} in the last 24 h, the bridge retried by itself. That is normal.",
        "drops_none": "None in the last 24 h.",
        "actions_none": "No commands in the last 24 h.",
        "actions": "{actions} in the last 24 h, {retried} needed a retry, {failed} failed.",
        "actions_failed_h": "See the Home Assistant log and the logbook for the reason.",
        "actions_retried_h": "Retries hide Bluetooth problems: look at the signal and the drops.",
        "uart_warn": "{n} data errors in the last 24 h.",
        "uart_warn_h": "Often a weak or failing power supply of the bridge. Try another USB power supply (at least 1.5 A) and cable.",
        "uart_ok": "{n} in the last 24 h.",
        "restarts_bad": "Gave up on Bluetooth {n} time(s) in the last 24 h and restarted.",
        "restarts_bad_h": "Commands fail during the restart. Check the power supply and the distance to the devices.",
        "restarts_warn": "{n} restart(s) in the last 24 h.",
        "restarts_warn_h": "Fine after a firmware update or a power cut, otherwise check the power supply.",
        "restarts_none": "None in the last 24 h.",
        "clock_unknown": "Not known.",
        "clock_warn": "Differs by {sec:.0f} s from Home Assistant.",
        "clock_warn_h": "The encrypted token depends on the time. Check the time of Home Assistant and the internet access of the bridge.",
        "clock_ok": "In sync ({sec:.0f} s).",
        "bridge_err": "No answer: {error}",
        "bridge_err_h": "Check that the bridge is powered, in the WiFi and has the HTTP API enabled.",
        "bridge_slow": "Answers slowly ({sec:.1f} s).",
        "bridge_slow_h": "A busy bridge or a weak WiFi. The bridge handles only one request at a time.",
        "bridge_ok": "Answers in {sec:.2f} s.",
        "bridge_ok_plain": "Answers.",
        "auth_encrypted": "Encrypted token.",
        "auth_plain": "Plain token, the secret travels unencrypted.",
        "auth_plain_h": "Update the bridge firmware (encrypted token needs 2.14.0 / 1.22.1) and check the clock.",
        "auth_old": "{mode} token (deprecated by Nuki).",
        "auth_old_h": "Update the bridge firmware so the encrypted token can be used.",
        "cb_none": "No callback, states come by polling only.",
        "cb_none_h": "Set the Home Assistant URL in the options.",
        "cb_unreachable": "The bridge cannot reach Home Assistant, states come by polling only.",
        "cb_unreachable_h": "Use an HTTP URL of Home Assistant in the local network (the bridge does not support HTTPS).",
        "cb_waiting": "Registered, no push received since the start yet.",
        "cb_ok": "Registered, last push {min:.0f} min ago.",
        "battery_critical": "Critical{percent}.",
        "battery_critical_h": "Replace the batteries soon: weak batteries also cause Bluetooth drops.",
        "battery_ok": "{percent} %.",
        "web_ok": "Answers.",
        "web_err": "Fails: {error}",
        "web_err_h": "Check the Web API token in the Nuki Web portal.",
        "report": "Nuki check-up",
        "s_ok": "everything fine",
        "s_warning": "warning",
        "s_problem": "problem",
        "running": "Checking the bridge and the devices, this takes a few seconds …",
    },
    "de": {
        "t_signal": "{name}: Signal",
        "t_drops": "{name}: Bluetooth-Abbrüche",
        "t_actions": "{name}: Befehle",
        "t_battery": "{name}: Batterie",
        "t_uart": "Bridge: Datenfehler",
        "t_restarts": "Bridge: Neustarts",
        "t_clock": "Bridge: Uhrzeit",
        "t_bridge": "Bridge: erreichbar",
        "t_auth": "Bridge: Token",
        "t_callback": "Push-Updates",
        "t_web": "Nuki Web API",
        "not_watched": "Nicht überwacht (Bridge-Log lesen ist aus).",
        "rssi_none": "Die Bridge meldet keine Signalstärke.",
        "rssi_none_h": "Das Gerät ist vielleicht außer Reichweite der Bridge.",
        "rssi_bad": "{rssi} dBm, sehr schwach.",
        "rssi_bad_h": "Die Bridge näher ans Gerät stellen (ideal 1–3 m, kein Metall dazwischen).",
        "rssi_weak": "{rssi} dBm, schwach.",
        "rssi_weak_h": "Verbindungsabbrüche sind wahrscheinlich. Ein Platz näher am Gerät hilft.",
        "rssi_ok": "{rssi} dBm, gut.",
        "drops_bad": "{n} in den letzten 24 h.",
        "drops_bad_h": "Häufige Abbrüche: Abstand zur Bridge, ihr Netzteil und die Batterien des Geräts prüfen.",
        "drops_warn": "{n} in den letzten 24 h.",
        "drops_warn_h": "Mehr Abbrüche als üblich. Werden Befehle langsam, die Bridge näher stellen oder die Batterien tauschen.",
        "drops_few": "{n} in den letzten 24 h, die Bridge hat selbst wiederholt. Das ist normal.",
        "drops_none": "Keine in den letzten 24 h.",
        "actions_none": "Keine Befehle in den letzten 24 h.",
        "actions": "{actions} in den letzten 24 h, {retried} mit Wiederholung, {failed} fehlgeschlagen.",
        "actions_failed_h": "Den Grund zeigen das Home-Assistant-Log und das Logbuch.",
        "actions_retried_h": "Wiederholungen verdecken Bluetooth-Probleme: Signal und Abbrüche ansehen.",
        "uart_warn": "{n} Datenfehler in den letzten 24 h.",
        "uart_warn_h": "Meist ein schwaches oder defektes Netzteil der Bridge. Ein anderes USB-Netzteil (mind. 1,5 A) und Kabel probieren.",
        "uart_ok": "{n} in den letzten 24 h.",
        "restarts_bad": "Hat Bluetooth in den letzten 24 h {n}-mal aufgegeben und neu gestartet.",
        "restarts_bad_h": "Während des Neustarts gehen keine Befehle. Netzteil und Abstand zu den Geräten prüfen.",
        "restarts_warn": "{n} Neustart(s) in den letzten 24 h.",
        "restarts_warn_h": "Nach einem Firmware-Update oder Stromausfall normal, sonst das Netzteil prüfen.",
        "restarts_none": "Keine in den letzten 24 h.",
        "clock_unknown": "Nicht bekannt.",
        "clock_warn": "Weicht {sec:.0f} s von Home Assistant ab.",
        "clock_warn_h": "Das verschlüsselte Token hängt an der Uhrzeit. Zeit von Home Assistant und Internetzugang der Bridge prüfen.",
        "clock_ok": "Stimmt ({sec:.0f} s).",
        "bridge_err": "Keine Antwort: {error}",
        "bridge_err_h": "Prüfen, ob die Bridge Strom hat, im WLAN ist und die HTTP-API aktiv ist.",
        "bridge_slow": "Antwortet langsam ({sec:.1f} s).",
        "bridge_slow_h": "Bridge ausgelastet oder schwaches WLAN. Die Bridge bearbeitet nur eine Anfrage gleichzeitig.",
        "bridge_ok": "Antwortet in {sec:.2f} s.",
        "bridge_ok_plain": "Antwortet.",
        "auth_encrypted": "Verschlüsseltes Token.",
        "auth_plain": "Klartext-Token, das Geheimnis geht unverschlüsselt übers Netz.",
        "auth_plain_h": "Bridge-Firmware aktualisieren (verschlüsseltes Token ab 2.14.0 / 1.22.1) und Uhrzeit prüfen.",
        "auth_old": "{mode}-Token (von Nuki abgekündigt).",
        "auth_old_h": "Bridge-Firmware aktualisieren, damit das verschlüsselte Token geht.",
        "cb_none": "Kein Callback, Zustände kommen nur per Abfrage.",
        "cb_none_h": "Die Home-Assistant-URL in den Optionen eintragen.",
        "cb_unreachable": "Die Bridge erreicht Home Assistant nicht, Zustände kommen nur per Abfrage.",
        "cb_unreachable_h": "Eine HTTP-URL von Home Assistant im lokalen Netz verwenden (die Bridge kann kein HTTPS).",
        "cb_waiting": "Eingetragen, seit dem Start noch kein Push empfangen.",
        "cb_ok": "Eingetragen, letzter Push vor {min:.0f} min.",
        "battery_critical": "Kritisch{percent}.",
        "battery_critical_h": "Batterien bald tauschen: schwache Batterien verursachen auch Bluetooth-Abbrüche.",
        "battery_ok": "{percent} %.",
        "web_ok": "Antwortet.",
        "web_err": "Fehler: {error}",
        "web_err_h": "Das Web-API-Token im Nuki-Web-Portal prüfen.",
        "report": "Nuki Check-up",
        "s_ok": "alles in Ordnung",
        "s_warning": "Warnung",
        "s_problem": "Problem",
        "running": "Bridge und Geräte werden geprüft, das dauert ein paar Sekunden …",
    },
}


def language(code: str | None) -> str:
    """Pick the text language from the Home Assistant language ("de-CH" -> "de")."""
    base = (code or "en").split("-")[0].lower()
    return base if base in TEXTS else "en"


def text(lang: str, key: str, **values: Any) -> str:
    return TEXTS.get(lang, TEXTS["en"]).get(key, TEXTS["en"][key]).format(**values)


def _check(lang, check_id, status, title, detail, hint=None, device=None, **values) -> Check:
    return Check(
        check_id, status, text(lang, title, **values), text(lang, detail, **values),
        text(lang, hint, **values) if hint else None, device,
    )


def check_rssi(name: str, rssi: int | None, lang: str = "en") -> Check:
    args = dict(name=name, rssi=rssi, device=name)
    if rssi is None:
        return _check(lang, "rssi", WARNING, "t_signal", "rssi_none", "rssi_none_h", **args)
    if rssi <= RSSI_BAD:
        return _check(lang, "rssi", PROBLEM, "t_signal", "rssi_bad", "rssi_bad_h", **args)
    if rssi <= RSSI_WEAK:
        return _check(lang, "rssi", WARNING, "t_signal", "rssi_weak", "rssi_weak_h", **args)
    return _check(lang, "rssi", OK, "t_signal", "rssi_ok", **args)


def check_drops(name: str, drops: int, watching: bool, lang: str = "en") -> Check:
    args = dict(name=name, n=drops, device=name)
    if not watching:
        return _check(lang, "ble_drops", OK, "t_drops", "not_watched", **args)
    if drops >= DROPS_BAD:
        return _check(lang, "ble_drops", PROBLEM, "t_drops", "drops_bad", "drops_bad_h", **args)
    if drops >= DROPS_WARN:
        return _check(lang, "ble_drops", WARNING, "t_drops", "drops_warn", "drops_warn_h", **args)
    if drops:
        return _check(lang, "ble_drops", OK, "t_drops", "drops_few", **args)
    return _check(lang, "ble_drops", OK, "t_drops", "drops_none", **args)


def check_actions(name: str, day: dict[str, int], lang: str = "en") -> Check:
    args = dict(name=name, device=name, **day)
    if not day["actions"]:
        return _check(lang, "actions", OK, "t_actions", "actions_none", **args)
    if day["failed"]:
        return _check(lang, "actions", PROBLEM, "t_actions", "actions", "actions_failed_h", **args)
    if day["retried"] >= RETRIES_WARN:
        return _check(lang, "actions", WARNING, "t_actions", "actions", "actions_retried_h", **args)
    return _check(lang, "actions", OK, "t_actions", "actions", **args)


def check_uart(count: int, watching: bool, lang: str = "en") -> Check:
    if not watching:
        return _check(lang, "uart", OK, "t_uart", "not_watched")
    if count >= UART_WARN:
        return _check(lang, "uart", WARNING, "t_uart", "uart_warn", "uart_warn_h", n=count)
    return _check(lang, "uart", OK, "t_uart", "uart_ok", n=count)


def check_restarts(restarts: int, failures: int, watching: bool, lang: str = "en") -> Check:
    if not watching:
        return _check(lang, "restarts", OK, "t_restarts", "not_watched")
    if failures:
        return _check(lang, "restarts", PROBLEM, "t_restarts", "restarts_bad", "restarts_bad_h", n=failures)
    if restarts >= RESTARTS_WARN:
        return _check(lang, "restarts", WARNING, "t_restarts", "restarts_warn", "restarts_warn_h", n=restarts)
    return _check(lang, "restarts", OK, "t_restarts", "restarts_none")


def check_clock(skew: float | None, lang: str = "en") -> Check:
    if skew is None:
        return _check(lang, "clock", OK, "t_clock", "clock_unknown")
    if abs(skew) > CLOCK_WARN_SEC:
        return _check(lang, "clock", WARNING, "t_clock", "clock_warn", "clock_warn_h", sec=abs(skew))
    return _check(lang, "clock", OK, "t_clock", "clock_ok", sec=abs(skew))


def check_response(seconds: float | None, error: str | None, lang: str = "en") -> Check:
    if error:
        return _check(lang, "bridge", PROBLEM, "t_bridge", "bridge_err", "bridge_err_h", error=error)
    if seconds is not None and seconds > RESPONSE_WARN_SEC:
        return _check(lang, "bridge", WARNING, "t_bridge", "bridge_slow", "bridge_slow_h", sec=seconds)
    if seconds is None:
        return _check(lang, "bridge", OK, "t_bridge", "bridge_ok_plain")
    return _check(lang, "bridge", OK, "t_bridge", "bridge_ok", sec=seconds)


def check_auth(mode: str | None, lang: str = "en") -> Check:
    if mode == "encrypted":
        return _check(lang, "auth", OK, "t_auth", "auth_encrypted")
    if mode == "plain":
        return _check(lang, "auth", WARNING, "t_auth", "auth_plain", "auth_plain_h")
    return _check(lang, "auth", WARNING, "t_auth", "auth_old", "auth_old_h", mode=mode or "unknown")


def check_callback(configured: bool, reachable: bool | None, last_push_age: float | None, lang: str = "en") -> Check:
    if not configured:
        return _check(lang, "callback", WARNING, "t_callback", "cb_none", "cb_none_h")
    if reachable is False:
        return _check(lang, "callback", WARNING, "t_callback", "cb_unreachable", "cb_unreachable_h")
    if last_push_age is None:
        return _check(lang, "callback", OK, "t_callback", "cb_waiting")
    return _check(lang, "callback", OK, "t_callback", "cb_ok", min=last_push_age / 60)


def check_battery(name: str, critical: bool | None, percent: int | None, lang: str = "en") -> Check | None:
    if critical:
        shown = f" ({percent} %)" if percent is not None else ""
        return _check(lang, "battery", PROBLEM, "t_battery", "battery_critical", "battery_critical_h", name=name, percent=shown, device=name)
    if percent is None:
        return None
    return _check(lang, "battery", OK, "t_battery", "battery_ok", name=name, percent=percent, device=name)


def check_web(error: str | None, lang: str = "en") -> Check:
    if error:
        return _check(lang, "web", PROBLEM, "t_web", "web_err", "web_err_h", error=error)
    return _check(lang, "web", OK, "t_web", "web_ok")


def report_markdown(checks: list[Check], lang: str = "en") -> str:
    icon = {OK: "✅", WARNING: "⚠️", PROBLEM: "❌"}
    status = worst(checks)
    lines = [f"**{icon[status]} {text(lang, 'report')}: {text(lang, 's_' + status)}**", ""]
    for check in sorted(checks, key=lambda c: -_RANK[c.status]):
        line = f"- {icon[check.status]} **{check.title}**: {check.detail}"
        if check.hint and check.status != OK:
            line += f"  \n  → {check.hint}"
        lines.append(line)
    return "\n".join(lines)
