"""Rules of the diagnostics, without Home Assistant."""

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("health", Path(__file__).parent.parent / "custom_components/nuki_ng/health.py")
health = importlib.util.module_from_spec(spec)
sys.modules["health"] = health
spec.loader.exec_module(health)

NOW = 1_800_000_000.0


def entry(seconds_ago, type_, dev="30E6840A", handle=None):
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    item = {"timestamp": ts, "type": type_}
    if dev:
        item["nukiId"] = dev
    if handle:
        item["bleHandle"] = handle
    return item


def test_problems_are_recognised():
    assert health.classify("BLE-EarlyDisconnect") == health.DROP
    assert health.classify("BLE-RebootBLEFailed") == health.BLE_FAILURE
    assert health.classify("UartCRCError") == health.UART
    assert health.classify("System-Startup") == health.RESTART
    for normal in ("BLE-Connect", "BLE-CommandComplete", "HTTP-List", "BLE-ConnectionTimeout", "BLE-Retry"):
        assert health.classify(normal) is None


def test_device_ids_match_the_log():
    assert health.device_hex(820413450) == "30E6840A"
    assert health.device_hex("1c9f0125") == "1C9F0125"


def test_each_entry_counts_once():
    log = health.BridgeLog()
    first = [entry(60, "BLE-EarlyDisconnect"), entry(120, "HTTP-List", None), entry(300, "UartCRCError", None)]
    assert len(log.feed(first, 100, NOW)) == 2
    again = [entry(10, "BLE-EarlyDisconnect", "1C9F0125"), *first]
    new = log.feed(again, 100, NOW + 30)
    assert [(p.kind, p.device) for p in new] == [(health.DROP, "1C9F0125")]
    assert log.count(health.DROP, "30E6840A") == 1
    assert log.count(health.UART, any_device=True) == 1
    assert not log.gap


def test_equal_timestamps_are_not_lost():
    log = health.BridgeLog()
    log.feed([entry(5, "BLE-EarlyDisconnect", handle="0001")], 100, NOW)
    new = log.feed([entry(5, "BLE-EarlyDisconnect", handle="0002"), entry(5, "BLE-EarlyDisconnect", handle="0001")], 100, NOW + 1)
    assert len(new) == 1


def test_old_problems_leave_after_a_day_and_survive_a_restart():
    log = health.BridgeLog()
    log.feed([entry(3600, "BLE-EarlyDisconnect")], 100, NOW)
    restored = health.BridgeLog(log.as_dict())
    assert restored.count(health.DROP, "30E6840A", NOW) == 1
    assert restored.count(health.DROP, "30E6840A", NOW + health.DAY) == 0
    assert restored.totals[health.DROP] == 1


def test_a_burst_of_retries_counts_as_one_drop():
    log = health.BridgeLog()
    burst = [entry(600 + i, "BLE-EarlyDisconnect", "1C9F0125") for i in range(7)]
    later = [entry(100, "BLE-EarlyDisconnect", "1C9F0125"), entry(101, "BLE-EarlyDisconnect", "30E6840A")]
    log.feed(later + burst, 100, NOW)
    assert log.count(health.DROP, "1C9F0125", NOW) == 2  # the burst and the later one
    assert log.count(health.DROP, "30E6840A", NOW) == 1
    assert log.totals[health.DROP] == 9  # raw entries stay in the totals


def test_a_full_read_that_does_not_reach_back_is_a_gap():
    log = health.BridgeLog()
    log.feed([entry(3000, "HTTP-List", None)], 2, NOW)
    log.feed([entry(10, "HTTP-List", None), entry(20, "HTTP-Info", None)], 2, NOW + 10)
    assert log.gap


def test_action_statistics():
    stats = health.ActionStats()
    stats.record(health.ActionRecord("open", True, 1, 1.2, "bridge", NOW))
    stats.record(health.ActionRecord("open", True, 2, 7.5, "push", NOW + 60))
    stats.record(health.ActionRecord("unlock", False, 3, 40.0, None, NOW + 120, "timeout"))
    assert stats.day(NOW + 130) == {"actions": 3, "failed": 1, "retried": 2}
    restored = health.ActionStats.from_dict(stats.as_dict())
    assert restored.last.error == "timeout" and restored.total == 3
    assert restored.day(NOW + health.DAY + 61) == {"actions": 1, "failed": 1, "retried": 1}


def test_checkup_rules():
    assert health.check_rssi("Haustür", -42).status == health.OK
    assert health.check_rssi("Wohnungstür", -70).status == health.OK  # works fine in practice
    assert health.check_rssi("Flur", -82).status == health.WARNING
    assert health.check_rssi("Keller", -92).status == health.PROBLEM
    assert health.check_drops("x", 0, True).status == health.OK
    assert health.check_drops("x", 2, True).status == health.OK  # the bridge retried, normal
    assert health.check_drops("x", 6, True).status == health.WARNING
    assert health.check_drops("x", 25, True).status == health.PROBLEM
    assert health.check_drops("x", 25, False).status == health.OK
    assert health.check_restarts(0, 1, True).status == health.PROBLEM
    assert health.check_uart(16, True).status == health.WARNING
    assert health.check_actions("x", {"actions": 4, "failed": 0, "retried": 1}).status == health.OK
    assert health.check_actions("x", {"actions": 9, "failed": 0, "retried": 3}).status == health.WARNING
    assert health.check_actions("x", {"actions": 4, "failed": 1, "retried": 0}).status == health.PROBLEM
    checks = [health.check_rssi("a", -42), health.check_uart(16, True)]
    assert health.worst(checks) == health.WARNING
    text = health.report_markdown(checks)
    assert text.index("data errors") < text.index("a: signal")  # problems first


def test_checkup_texts_follow_the_language():
    assert health.language("de-CH") == "de" and health.language("fr") == "en" and health.language(None) == "en"
    check = health.check_drops("Wohnungstür", 2, True, "de")
    assert check.title == "Wohnungstür: Bluetooth-Abbrüche" and "normal" in check.detail
    assert "Warnung" in health.report_markdown([health.check_uart(16, True, "de")], "de")
    assert set(health.TEXTS["de"]) == set(health.TEXTS["en"])
    for lang in health.TEXTS:  # every text renders with the values the checks pass
        health.check_battery("x", True, 5, lang)
        health.check_callback(True, True, 120, lang)
        health.check_clock(40, lang)
        health.check_response(0.4, None, lang)
        health.check_auth("hashed", lang)
        health.check_web("boom", lang)


def test_every_entity_name_is_translated():
    import json
    import re
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "nuki_ng"
    for lang in ("en", "de"):
        entity = json.loads((base / "translations" / f"{lang}.json").read_text())["entity"]
        for path in base.glob("*.py"):
            for name in re.findall(r'set_name\("([^"]+)"\)', path.read_text()):
                key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                assert key in entity.get(path.stem, {}), (lang, path.stem, key)
