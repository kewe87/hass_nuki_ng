from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

import logging
import time
from datetime import datetime

from . import NukiEntity, NukiBridge
from . import health
from .states import DoorSensorStates, LockStates, DoorSecurityStates

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    entities = []
    coordinator = entry.runtime_data

    if coordinator.api.can_bridge():
        entities.append(BridgeWifiVersion(coordinator))
        entities.append(BridgeVersion(coordinator))
        entities.append(BridgeLastRestart(coordinator))
        entities.append(BridgeFailedActions(coordinator))
        if coordinator.watch_log:
            entities.append(BridgeDataErrors(coordinator))
    entities.append(BridgeHealth(coordinator))
    for dev_id in coordinator.data.get("devices", {}):
        entities.append(LockState(coordinator, dev_id))
        if coordinator.api.can_bridge():
            entities.append(RSSI(coordinator, dev_id))
            if coordinator.watch_log:
                entities.append(BluetoothDrops(coordinator, dev_id))
        entities.append(LastAction(coordinator, dev_id))
        entities.append(LockVersion(coordinator, dev_id))
        if coordinator.device_supports(dev_id, "batteryChargeState"):
            entities.append(Battery(coordinator, dev_id))
        if coordinator.device_supports(dev_id, "doorsensorStateName"):
            entities.append(DoorSensorState(coordinator, dev_id))
            entities.append(DoorSecurityState(coordinator, dev_id))
        if coordinator.info_field(dev_id, None, "last_log"):
            entities.append(LastLog(coordinator, dev_id))
        if coordinator.info_field(dev_id, None, "last_unlock_log"):
            entities.append(LastUnlockUser(coordinator, dev_id))
        
    async_add_entities(entities)
    return True


class Battery(NukiEntity, SensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "battery")
        self.set_name("Battery")
        self._attr_device_class = "battery"
        self._attr_state_class = "measurement"

    @property
    def native_unit_of_measurement(self):
        return "%"

    @property
    def native_value(self):
        value = self.last_state.get("batteryChargeState")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class LockState(NukiEntity, SensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "state")
        self.set_name("State")
        self._attr_icon = "mdi:door"

    @property
    def native_value(self):
        return self.last_state.get("stateName")

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class RSSI(NukiEntity, SensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "rssi")
        self.set_name("RSSI")
        self._attr_device_class = "signal_strength"
        self._attr_state_class = "measurement"

    @property
    def native_unit_of_measurement(self):
        return "dBm"

    @property
    def native_value(self):
        return (self.data.get("bridge_info") or {}).get("rssi")

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class DoorSensorState(NukiEntity, SensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "door_state")
        self.set_name("Door State")
        self._attr_icon = "mdi:door"

    @property
    def native_value(self):
        return self.last_state.get("doorsensorStateName")

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class DoorSecurityState(NukiEntity, SensorEntity):
    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "door_security_state")
        self.set_name("Door Security State")
        self._attr_icon = "mdi:door-closed-lock"

    @property
    def icon(self):

        state = self.get_state()

        if state == DoorSecurityStates.CLOSED_AND_LOCKED:
            return "mdi:door-closed-lock"
        elif state == DoorSecurityStates.CLOSED_AND_UNLOCKED:
            return "mdi:door-closed"
        elif state is None:
            return "mdi:door"
        return "mdi:door-open"

    @property
    def native_value(self):
        state = self.get_state()
        return str(state) if state is not None else None

    def get_state(self) -> DoorSecurityStates | None:
        lock_state = LockStates.parse(self.last_state.get("state"))
        door_sensor_state = DoorSensorStates.parse(self.last_state.get("doorsensorState"))
        if door_sensor_state in (None, DoorSensorStates.DOOR_UNKNOWN, DoorSensorStates.UNKNOWN,
                                 DoorSensorStates.UNAVAILABLE, DoorSensorStates.DEACTIVATED,
                                 DoorSensorStates.CALIBRATING, DoorSensorStates.UNCALIBRATED,
                                 DoorSensorStates.REMOVED):
            return None

        if lock_state == LockStates.LOCKED and door_sensor_state == DoorSensorStates.DOOR_CLOSED:
            return DoorSecurityStates.CLOSED_AND_LOCKED
        elif door_sensor_state == DoorSensorStates.DOOR_CLOSED:
            return DoorSecurityStates.CLOSED_AND_UNLOCKED
        return DoorSecurityStates.OPEN


class BridgeWifiVersion(NukiBridge, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("wifi_version")
        self.set_name("WiFi Firmware Version")

    @property
    def native_value(self):
        versions = self.data.get("versions", {})
        return versions.get("wifiFirmwareVersion")

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class BridgeVersion(NukiBridge, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("version")
        self.set_name("Firmware Version")

    @property
    def native_value(self):
        versions = self.data.get("versions", {})
        return versions.get("firmwareVersion")

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class LockVersion(NukiEntity, SensorEntity):

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "version")
        self.set_name("Firmware Version")

    @property
    def native_value(self):
        return self.data.get("firmwareVersion")

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC

class LastLog(NukiEntity, SensorEntity):

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "last_log")
        self.set_name("Last Log")
        self._attr_icon = "mdi:history"

    @property
    def native_value(self):
        return self.coordinator.info_field(self.device_id, "Unknown", "last_log", "name")

    @property
    def extra_state_attributes(self):
        timestamp = self.coordinator.info_field(self.device_id, None, "last_log", "timestamp")
        action = self.coordinator.info_field(self.device_id, "unknown", "last_log", "action")
        return {
            "timestamp": datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else None,
            "action": action,
        }

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC

class LastUnlockUser(NukiEntity, SensorEntity):

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "last_unlock_user")
        self.set_name("Last Unlock user")
        self._attr_icon = "mdi:account-lock-open"

    @property
    def native_value(self):
        return self.coordinator.info_field(self.device_id, "Unknown", "last_unlock_log", "name")

    @property
    def extra_state_attributes(self):
        timestamp = self.coordinator.info_field(self.device_id, None, "last_unlock_log", "timestamp")
        action = self.coordinator.info_field(self.device_id, "unknown", "last_unlock_log", "action")
        return {
            "timestamp": datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else None,
            "action": action,
        }

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC

class BridgeLastRestart(NukiBridge, SensorEntity):
    """Time of the last bridge (re)start, derived from its uptime."""

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("last_restart")
        self.set_name("Last Restart")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self.coordinator.bridge_last_restart


class BridgeFailedActions(NukiBridge, SensorEntity):
    """Failed action attempts (incl. retried ones), kept across restarts."""

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("failed_actions")
        self.set_name("Failed Actions")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:alert-circle-outline"

    @property
    def native_value(self):
        return self.coordinator.failed_actions

    @property
    def extra_state_attributes(self):
        return {"last_error": self.coordinator.last_action_error}


class BridgeHealth(NukiBridge, SensorEntity):
    """ok / warning / problem from what is known; the check-up button asks the bridge too."""

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("health")
        self.set_name("Health")
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [health.OK, health.WARNING, health.PROBLEM]
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:stethoscope"

    @property
    def native_value(self):
        return self.coordinator.health_status()

    @property
    def extra_state_attributes(self):
        checks = self.coordinator.passive_checks()
        last = self.coordinator.last_checkup or {}
        return {
            "problems": [f"{c.title}: {c.detail}" for c in checks if c.status != health.OK],
            "last_checkup": last.get("at"),
            "last_checkup_status": last.get("status"),
        }


class BridgeDataErrors(NukiBridge, SensorEntity):
    """UART errors of the bridge in the last 24 h, a sign of a weak power supply."""

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.set_id("uart_errors")
        self.set_name("Data Errors 24h")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:flash-alert-outline"

    @property
    def native_value(self):
        return self.coordinator.bridge_problems(health.UART)

    @property
    def extra_state_attributes(self):
        return {
            "restarts_24h": self.coordinator.bridge_problems(health.RESTART),
            "bluetooth_failures_24h": self.coordinator.bridge_problems(health.BLE_FAILURE),
            "log_read_at": self.coordinator.bridge_log.read_at,
        }


class BluetoothDrops(NukiEntity, SensorEntity):
    """Early Bluetooth disconnects between the bridge and this device in the last 24 h."""

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "ble_drops")
        self.set_name("Bluetooth Drops 24h")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:bluetooth-off"

    @property
    def native_value(self):
        return self.coordinator.ble_drops(self.device_id)


class LastAction(NukiEntity, SensorEntity):
    """How long the last command took, with its result."""

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("sensor", "last_action")
        self.set_name("Last Action Duration")
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = "s"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:timer-outline"

    @property
    def native_value(self):
        last = self.coordinator.stats_for(self.device_id).last
        return None if last is None else last.seconds

    @property
    def extra_state_attributes(self):
        stats = self.coordinator.stats_for(self.device_id)
        last = stats.last
        attrs = {f"{k}_24h": v for k, v in stats.day(time.time()).items()}
        attrs.update(total=stats.total, total_failed=stats.failed, total_retried=stats.retried)
        if last:
            attrs.update(
                action=last.action, success=last.success, attempts=last.attempts,
                confirmed_by=last.confirmed_by, error=last.error,
                at=datetime.fromtimestamp(last.at).astimezone().isoformat(timespec="seconds"),
            )
        return attrs
