"""Firmware of locks and openers in the update overview of Home Assistant."""

from __future__ import annotations

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)

from . import NukiEntity
from . import firmware


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    if not coordinator.firmware_check:
        return True
    async_add_entities(
        NukiFirmware(coordinator, dev_id)
        for dev_id, item in (coordinator.data.get("devices") or {}).items()
        if item.get("deviceType") in firmware.PRODUCT_BY_DEVICE_TYPE
    )
    return True


class NukiFirmware(NukiEntity, UpdateEntity):
    """Installed and newest firmware.

    Nuki devices install updates by themselves (automatic updates, at night)
    or through the Nuki app; the bridge API cannot start them. So the entity
    shows that an update exists and what it brings, without an install button.
    """

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("update", "firmware")
        self.set_name("Firmware")

    @property
    def release(self) -> firmware.Release | None:
        found = firmware.newest(
            self.coordinator.firmware_releases, self.data.get("deviceType"), self.installed_version
        )
        if found and found.key > firmware.version_key(self.installed_version):
            return found
        return None

    @property
    def installed_version(self) -> str | None:
        version = self.data.get("firmwareVersion")
        return str(version) if version not in (None, "None", "") else None

    @property
    def latest_version(self) -> str | None:
        release = self.release
        return release.version if release else self.installed_version

    @property
    def title(self) -> str:
        return self.model

    @property
    def release_summary(self) -> str | None:
        release = self.release
        return release.summary if release else None

    @property
    def release_url(self) -> str | None:
        release = self.release
        return firmware.localized_url(release.url, self.coordinator.language) if release else None

    async def async_release_notes(self) -> str | None:
        release = self.release
        if not release:
            return None
        note = {
            "de": "Nuki installiert Updates selbst (automatische Updates, nachts) oder über die Nuki-App.",
            "en": "Nuki installs updates by itself (automatic updates, at night) or through the Nuki app.",
        }[self.coordinator.language]
        return f"{release.notes}\n\n*{note}*"
