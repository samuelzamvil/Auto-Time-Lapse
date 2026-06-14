"""Sensor platform for the Auto Time Lapse integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SessionState
from .entity import AutoTimeLapseEntity
from .manager import TimelapseManager

if TYPE_CHECKING:
    from . import AutoTimeLapseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutoTimeLapseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors per trigger subentry."""
    for subentry_id, manager in entry.runtime_data.items():
        async_add_entities(
            [
                TimelapseStatusSensor(manager),
                TimelapseFrameCountSensor(manager),
                TimelapseLastVideoSensor(manager),
                TimelapseLastErrorSensor(manager),
                TimelapseCaptureIntervalSensor(manager),
            ],
            config_subentry_id=subentry_id,
        )


class TimelapseStatusSensor(AutoTimeLapseEntity, SensorEntity):
    """Current state of the trigger profile: idle, capturing, buffering or rendering."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [state.value for state in SessionState]

    def __init__(self, manager: TimelapseManager) -> None:
        super().__init__(manager, "status")

    @property
    def native_value(self) -> str:
        return self._manager.state.value


class TimelapseFrameCountSensor(AutoTimeLapseEntity, SensorEntity):
    """Frames captured in the current/last session."""

    def __init__(self, manager: TimelapseManager) -> None:
        super().__init__(manager, "frame_count")

    @property
    def native_value(self) -> int:
        return self._manager.frame_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"failed_frames": self._manager.failed_frame_count}


class TimelapseLastVideoSensor(AutoTimeLapseEntity, SensorEntity):
    """Path of the most recently rendered video."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: TimelapseManager) -> None:
        super().__init__(manager, "last_video")

    @property
    def native_value(self) -> str | None:
        return self._manager.last_video_path

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"media_content_id": self._manager.media_content_id}


class TimelapseLastErrorSensor(AutoTimeLapseEntity, SensorEntity):
    """Error from the most recent failed render; clears on the next success."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: TimelapseManager) -> None:
        super().__init__(manager, "last_error")

    @property
    def native_value(self) -> str | None:
        return self._manager.last_error


class TimelapseCaptureIntervalSensor(AutoTimeLapseEntity, SensorEntity):
    """Seconds between snapshots in effect; unknown while idle or value-change paced."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: TimelapseManager) -> None:
        super().__init__(manager, "capture_interval")

    @property
    def native_value(self) -> float | None:
        return self._manager.capture_interval
