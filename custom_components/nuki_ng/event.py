from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback

import logging

from . import NukiEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    entities = []
    coordinator = entry.runtime_data

    if coordinator.api.can_bridge():
        for dev_id in coordinator.data.get("devices", {}):
            if coordinator.is_opener(dev_id):
                entities.append(DoorbellRing(coordinator, dev_id))
    async_add_entities(entities)
    return True


class DoorbellRing(NukiEntity, EventEntity):
    """Fires 'ring' when the Opener reports a doorbell ring via the bridge callback."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self.set_id("event", "doorbell")
        self.set_name("Doorbell")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.add_ring_listener(self.device_id, self._handle_ring)
        )

    @callback
    def _handle_ring(self, update: dict) -> None:
        self._trigger_event("ring", {"timestamp": update.get("ringactionTimestamp")})
        self.async_write_ha_state()
