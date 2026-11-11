from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory

import logging

from . import NukiEntity, NukiBridge
from .states import DoorSensorStates, LockStates, LockModes

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    entities = []
    coordinator = entry.runtime_data

    for dev_id in coordinator.data.get("devices", {}):
        entities.append(BatteryLow(coordinator, dev_id))
        if coordinator.device_supports(dev_id, "batteryCharging"):
            entities.append(BatteryCharging(coordinator, dev_id))
        entities.append(LockState(coordinator, dev_id))
        if coordinator.device_supports(dev_id, "keypadBatteryCritical"):
            entities.append(KeypadBatteryLow(coordinator, dev_id))
        if coordinator.is_opener(dev_id):
            entities.append(RingAction(coordinator, dev_id))
        if coordinator.device_supports(dev_id, "doorsensorStateName"):
            entities.append(DoorState(coordinator, dev_id))
    if coordinator.api.can_bridge():
        entities.append(BridgeServerConnection(coordinator))
        entities.append(BridgeCallbackSet(coordinator))
        entities.append(BridgeCallbackReachable(coordinator))
    async_add_entities(entities)
    return True


class BatteryLow(NukiEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("binary_sensor", "battery_low")
        self.set_name("Battery Critical")

    @property
    def is_on(self) -> bool:
        return self.last_state.get("batteryCritical") is True

    @property
    def device_class(self) -> str:
        return "battery"

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class BatteryCharging(NukiEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("binary_sensor", "battery_charging")
        self.set_name("Battery Charging")
        self._attr_device_class = "battery_charging"

    @property
    def is_on(self) -> bool:
        return self.last_state.get("batteryCharging") is True

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class KeypadBatteryLow(NukiEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("binary_sensor", "keypad_battery_low")
        self.set_name("Keypad Battery Critical")

    @property
    def is_on(self) -> bool:
        return self.last_state.get("keypadBatteryCritical") is True

    @property
    def device_class(self) -> str:
        return "battery"

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class RingAction(NukiEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("binary_sensor", "ring_action")
        self.set_name("Ring Action")

    @property
    def is_on(self) -> bool:
        return self.last_state.get("ringactionState") is True

    @property
    def extra_state_attributes(self):
        return {
            "timestamp": self.last_state.get("ringactionTimestamp")
        }

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class LockState(NukiEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("binary_sensor", "state")
        self.set_name("Locked")
        self._attr_device_class = "lock"

    @property
    def is_on(self) -> bool | None:
        current_state = LockStates.parse(self.last_state.get("state"))
        if current_state is None or current_state == LockStates.UNDEFINED:
            return None
        current_mode = LockModes.parse(self.last_state.get("mode", LockModes.DOOR_MODE.value))
        return current_state != LockStates.LOCKED or current_mode == LockModes.CONTINUOUS_MODE

    @property
    def extra_state_attributes(self):
        return {
            "timestamp": self.last_state.get("timestamp")
        }


class DoorState(NukiEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("binary_sensor", "door_state")
        self.set_name("Door Open")
        self._attr_device_class = "door"

    @property
    def is_on(self) -> bool | None:
        current = DoorSensorStates.parse(self.last_state.get("doorsensorState"))
        if current == DoorSensorStates.DOOR_CLOSED:
            return False
        if current == DoorSensorStates.DOOR_OPENED:
            return True
        return None


class BridgeServerConnection(NukiBridge, BinarySensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("connected")
        self.set_name("Connected")
        self._attr_device_class = "connectivity"

    @property
    def is_on(self) -> bool:
        return self.data.get("serverConnected", False)

    @property
    def extra_state_attributes(self):
        versions = self.data.get("versions", {})
        return {
            "wifiFirmwareVersion": versions.get("wifiFirmwareVersion")
        }

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class BridgeCallbackSet(NukiBridge, BinarySensorEntity):

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("callback_set")
        self.set_name("Bridge Callback Set")

    @property
    def is_on(self) -> bool:
        callbacks = self.data.get("callbacks_list")
        if callbacks is None:
            return False
        url = self.data.get("callback_url")
        return any(item.get("url") == url for item in callbacks)

    @property
    def extra_state_attributes(self):
        # The URLs contain the secret webhook id and are not exposed.
        callbacks = self.data.get("callbacks_list") or []
        return {
            "callbacks_registered": len(callbacks),
            "foreign_callbacks": sum(
                1 for item in callbacks if not self.coordinator._is_own_callback(item.get("url", ""))
            ),
        }

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class BridgeCallbackReachable(NukiBridge, BinarySensorEntity):
    """Whether the callback URL given to the bridge is reachable."""

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("callback_reachable")
        self.set_name("Callback Reachable")
        self._attr_device_class = "connectivity"

    @property
    def is_on(self) -> bool:
        return self.data.get("callback_reachable") is True

    @property
    def extra_state_attributes(self):
        last_push = self.coordinator.last_push
        return {
            "last_push": last_push.isoformat() if last_push else None,
            "rejected_pushes": self.coordinator.rejected_pushes,
            "auth_mode": self.data.get("auth_mode"),
        }

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC
