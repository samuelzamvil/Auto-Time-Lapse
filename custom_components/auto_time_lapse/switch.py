"""Switch platform for the Auto Time Lapse integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import AutoTimeLapseEntity
from .manager import TimelapseManager

if TYPE_CHECKING:
    from . import AutoTimeLapseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutoTimeLapseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the capture switch."""
    async_add_entities([TimelapseCaptureSwitch(entry.runtime_data)])


class TimelapseCaptureSwitch(AutoTimeLapseEntity, SwitchEntity):
    """Switch that starts/stops a capture session."""

    def __init__(self, manager: TimelapseManager) -> None:
        super().__init__(manager, "capture")

    @property
    def is_on(self) -> bool:
        return self._manager.is_capturing

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._manager.async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._manager.async_stop(render=True)
