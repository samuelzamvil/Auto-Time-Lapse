"""Base entity for the Auto Time Lapse integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import TimelapseManager


class AutoTimeLapseEntity(Entity):
    """Entity tied to a timelapse profile, updated by manager pushes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: TimelapseManager, key: str) -> None:
        self._manager = manager
        entry = manager.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Auto Time Lapse",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager updates."""
        self.async_on_remove(
            self._manager.async_add_listener(self.async_write_ha_state)
        )
