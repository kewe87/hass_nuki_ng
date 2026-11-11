from __future__ import annotations

import logging
from homeassistant.components import webhook
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .nuki import NukiCoordinator
from .constants import DOMAIN, PLATFORMS

TO_REDACT = {"token", "web_token"}

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

OPENER_TYPE = 1
LOCK_TYPE = 0

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------
#  Home Assistant Setup
# ------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    data = {**entry.data, **entry.options}
    _LOGGER.debug(f"async_setup_entry: {async_redact_data(data, TO_REDACT)}")

    coordinator = NukiCoordinator(hass, entry, data)
    entry.runtime_data = coordinator

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        webhook.async_unregister(hass, coordinator.hook_id)
        raise
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry):
    coordinator = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await coordinator.unload()
        entry.runtime_data = None
    return unloaded


def _coordinators(hass: HomeAssistant):
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _resolve_targets(hass: HomeAssistant, call: ServiceCall):
    """Map the service target to (coordinator, nuki device id) pairs.

    Only the targeted devices are used. Targeting the bridge device (or an
    entity of it) selects all devices connected to that bridge.
    """
    device_ids = set(cv.ensure_list(call.data.get("device_id", [])))
    ent_reg = er.async_get(hass)
    for entity_id in cv.ensure_list(call.data.get("entity_id", [])):
        if (entry := ent_reg.async_get(entity_id)) and entry.device_id:
            device_ids.add(entry.device_id)
    dev_reg = dr.async_get(hass)
    result = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if not device:
            continue
        idents = [str(ident[1]) for ident in device.identifiers if ident[0] == DOMAIN]
        # HA >= 2026.x: a device belongs to a single config entry.
        entry_id = getattr(device, "config_entry_id", None)
        entry_ids = {entry_id} if entry_id else set(device.config_entries)
        for coordinator in _coordinators(hass):
            if coordinator.entry.entry_id not in entry_ids:
                continue
            devices = coordinator.data.get("devices", {}) if coordinator.data else {}
            matched = [dev_id for dev_id in devices if str(dev_id) in idents]
            bridge_id = coordinator.info_data().get("ids", {}).get("hardwareId")
            if not matched and bridge_id is not None and str(bridge_id) in idents:
                matched = list(devices)
            for dev_id in matched:
                if (coordinator, dev_id) not in result:
                    result.append((coordinator, dev_id))
    return result


def _bridge_coordinators(hass: HomeAssistant, call: ServiceCall):
    result = []
    for coordinator, _ in _resolve_targets(hass, call):
        if coordinator not in result:
            result.append(coordinator)
    if not result:
        raise ServiceValidationError("No Nuki bridge found for the selected target")
    return result


async def async_setup(hass: HomeAssistant, config) -> bool:
    async def _bridge_reboot(call: ServiceCall):
        for coordinator in _bridge_coordinators(hass, call):
            await coordinator.do_reboot()

    async def _bridge_fwupdate(call: ServiceCall):
        for coordinator in _bridge_coordinators(hass, call):
            await coordinator.do_fwupdate()

    async def _bridge_delete_callback(call: ServiceCall):
        for coordinator in _bridge_coordinators(hass, call):
            await coordinator.do_delete_callback(call.data.get("command") or call.data.get("callback"))

    async def _execute_action(call: ServiceCall) -> ServiceResponse:
        targets = _resolve_targets(hass, call)
        if not targets:
            raise ServiceValidationError("No Nuki device found for the selected target")
        action = call.data.get("action")
        results = []
        for coordinator, dev_id in targets:
            name = coordinator.device_data(dev_id).get("name", dev_id)
            try:
                outcome = await coordinator.action(dev_id, action)
                results.append(dict(device=name, nuki_id=dev_id, success=True, attempts=outcome.get("attempts", 1), error=None))
            except HomeAssistantError as err:
                if not call.return_response:
                    raise
                results.append(dict(device=name, nuki_id=dev_id, success=False, attempts=coordinator.action_retries + 1, error=str(err)))
        if call.return_response:
            return dict(success=all(item["success"] for item in results), results=results)
        return None

    hass.services.async_register(DOMAIN, "bridge_reboot", _bridge_reboot)
    hass.services.async_register(DOMAIN, "bridge_fwupdate", _bridge_fwupdate)
    hass.services.async_register(DOMAIN, "bridge_delete_callback", _bridge_delete_callback)
    hass.services.async_register(
        DOMAIN, "execute_action", _execute_action, supports_response=SupportsResponse.OPTIONAL
    )

    return True


# ------------------------------------------------------
#  Nuki Base Entities
# ------------------------------------------------------

class NukiEntity(CoordinatorEntity):
    """Base entity for Nuki locks and openers."""

    def __init__(self, coordinator, device_id: str):
        super().__init__(coordinator)
        self.device_id = device_id

    # ---- ID + Name helpers -------------------------------------

    def set_id(self, prefix: str, suffix: str):
        self.id_prefix = prefix
        self.id_suffix = suffix

    def set_name(self, name: str):
        self._attr_name_suffix = name

    @property
    def name_suffix(self):
        return self._attr_name_suffix

    @property
    def get_name(self):
        return "Nuki %s" % (self.data.get("name", self.device_id))

    @property
    def name(self) -> str:
        return "%s %s" % (self.get_name, self.name_suffix)

    @property
    def unique_id(self) -> str:
        return "nuki-%s-%s" % (self.device_id, self.id_suffix)

    # ---- Data Access --------------------------------------------

    @property
    def available(self):
        if "nukiId" not in self.data:
            return False
        return super().available

    @property
    def data(self) -> dict:
        return self.coordinator.device_data(self.device_id)

    @property
    def last_state(self) -> dict:
        return self.data.get("lastKnownState", {})

    @property
    def model(self) -> str:
        if self.coordinator.is_lock(self.device_id):
            return "Nuki Smart Lock"
        if self.coordinator.is_opener(self.device_id):
            return "Nuki Opener"
        return "Nuki Device"

    # ---- FIXED device_info --------------------------------------

    @property
    def device_info(self):
        # No via_device: linking devices to the bridge via the deprecated
        # via_device tuple stops working in HA 2027.8.
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.get_name,
            "manufacturer": "Nuki",
            "model": self.model,
            "sw_version": self.data.get("firmwareVersion"),
        }


# ------------------------------------------------------
#  Nuki Bridge Entity
# ------------------------------------------------------

class NukiBridge(CoordinatorEntity):
    """Represents the Nuki Bridge device."""

    def set_id(self, suffix: str):
        self.id_suffix = suffix

    def set_name(self, name: str):
        self.name_suffix = name

    @property
    def name(self) -> str:
        return "Nuki Bridge %s" % (self.name_suffix)

    @property
    def unique_id(self) -> str:
        return "nuki-bridge-%s-%s" % (self.get_id, self.id_suffix)

    @property
    def data(self) -> dict:
        return self.coordinator.data.get("bridge_info", {})

    @property
    def get_id(self):
        return self.data.get("ids", {}).get("hardwareId")

    @property
    def device_info(self):
        """Correct HA-compliant Bridge info."""
        model = (
            "Hardware Bridge"
            if self.data.get("bridgeType", 1) == 1
            else "Software Bridge"
        )
        versions = self.data.get("versions", {})

        return {
            "identifiers": {(DOMAIN, self.get_id)},
            "name": "Nuki Bridge",
            "manufacturer": "Nuki",
            "model": model,
            "sw_version": versions.get("firmwareVersion"),
        }


# ------------------------------------------------------
#  Advanced Opener Entity
# ------------------------------------------------------

class NukiOpenerRingSuppressionEntity(NukiEntity):

    SUP_RING = 4
    SUP_RTO = 2
    SUP_CM = 1

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    @property
    def doorbellSuppression(self):
        return self.coordinator.info_field(
            self.device_id, 0, "openerAdvancedConfig", "doorbellSuppression"
        )

    async def update_doorbell_suppression(self, new_value):
        await self.coordinator.update_config(
            self.device_id,
            "openerAdvancedConfig",
            dict(doorbellSuppression=new_value),
        )

