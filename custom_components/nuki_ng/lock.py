import logging

from homeassistant.components.lock import LockEntity, LockEntityFeature

from . import NukiEntity
from .states import LockModes, LockStates

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(Lock(coordinator, dev_id) for dev_id in coordinator.data.get("devices", {}))
    return True


class Lock(NukiEntity, LockEntity):
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("lock", "lock")
        self.set_name("Lock")

    @property
    def _state(self):
        return LockStates.parse(self.last_state.get("state"))

    @property
    def _mode(self):
        return LockModes.parse(self.last_state.get("mode", LockModes.DOOR_MODE.value))

    @property
    def is_locked(self):
        state = self._state
        if state is None or state == LockStates.UNDEFINED:
            return None
        return state == LockStates.LOCKED and self._mode != LockModes.CONTINUOUS_MODE

    @property
    def is_locking(self):
        return self._state == LockStates.LOCKING

    @property
    def is_unlocking(self):
        return self._state == LockStates.UNLOCKING

    @property
    def is_open(self):
        # Smart lock: unlatched, opener: door buzzer active.
        return self._state == LockStates.UNLATCHED

    @property
    def is_opening(self):
        return self._state == LockStates.UNLATCHING

    @property
    def is_jammed(self):
        return self._state == LockStates.MOTOR_BLOCKED

    async def async_lock(self, **kwargs):
        """Lock; for the opener this also disables continuous mode."""
        if self.coordinator.is_opener(self.device_id) and self._mode == LockModes.CONTINUOUS_MODE:
            await self.coordinator.action(self.device_id, "deactivate_continuous_mode")
        await self.coordinator.action(self.device_id, "lock")

    async def async_unlock(self, **kwargs):
        await self.coordinator.action(self.device_id, "unlock")

    async def async_open(self, **kwargs):
        await self.coordinator.action(self.device_id, "open")
