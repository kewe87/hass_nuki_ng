"""Newest firmware from the release notes of the Nuki help center."""

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("firmware", Path(__file__).parent.parent / "custom_components/nuki_ng/firmware.py")
firmware = importlib.util.module_from_spec(spec)
sys.modules["firmware"] = firmware
spec.loader.exec_module(firmware)

URL = "https://help.nuki.io/hc/en-001/articles/1-Release-Notes"


def article(title, body="<ul><li>Faster&nbsp;unlocking</li><li>Bug fixes</li></ul>", created="2026-11-03T08:00:00Z"):
    return {"title": title, "html_url": URL, "body": body, "created_at": created}


ARTICLES = [
    article("Release Notes: Nuki Smart Lock 4.11.4"),
    article("Release Notes: Nuki Smart Lock 4.10.5"),
    article("Release Notes: Nuki Smart Lock 5.10.6"),
    article("Release Notes: Nuki Smart Lock 2.15.3"),
    article("Release Notes: Nuki Opener 1.12.4"),
    article("Release Notes: Smart Door 1.5.0"),
    article("Release Notes: Nuki Keypad 2.6.4"),
    article("Release Notes: Nuki iOS App 2026.11.1"),
    article("How do I update the firmware?"),
]


def test_parse_only_device_firmware():
    releases = firmware.parse_articles(ARTICLES)
    assert {(r.product, r.version) for r in releases} == {
        ("Smart Lock", "4.11.4"), ("Smart Lock", "4.10.5"), ("Smart Lock", "5.10.6"),
        ("Smart Lock", "2.15.3"), ("Opener", "1.12.4"), ("Smart Door", "1.5.0"),
    }
    assert releases[0].notes == "- Faster unlocking\n- Bug fixes"
    assert releases[0].summary == "Faster unlocking Bug fixes"


def test_newest_matches_generation_by_major_version():
    releases = firmware.parse_articles(ARTICLES)
    assert firmware.newest(releases, 4, "4.10.5").version == "4.11.4"  # 4th generation
    assert firmware.newest(releases, 0, "2.15.3").version == "2.15.3"  # Smart Lock 2.0 up to date
    assert firmware.newest(releases, 2, "1.12.4").version == "1.12.4"
    assert firmware.newest(releases, 3, "1.4.2").version == "1.5.0"  # Smart Door, not Smart Lock 1.x
    assert firmware.newest(releases, 5, "3.10.7") is None  # no 3.x release in the list
    assert firmware.newest(releases, 0, None) is None
    assert firmware.newest(releases, 99, "1.0.0") is None
    assert firmware.version_key("1.12.10") > firmware.version_key("1.12.9")


def test_summary_is_short_and_url_localized():
    long = firmware.parse_articles([article("Release Notes: Nuki Opener 1.13.0", "<p>" + "word " * 100 + "</p>")])[0]
    assert len(long.summary) <= firmware.SUMMARY_LENGTH and long.summary.endswith("…")
    assert firmware.localized_url(URL, "de") == "https://help.nuki.io/hc/de/articles/1-Release-Notes"
    assert firmware.localized_url(URL, "en") == URL
