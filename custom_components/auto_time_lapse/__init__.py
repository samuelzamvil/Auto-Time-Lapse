"""The Auto Time Lapse integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SUBENTRY_TYPE_TRIGGER
from .manager import TimelapseManager
from .services import async_setup_services

type AutoTimeLapseConfigEntry = ConfigEntry[dict[str, TimelapseManager]]

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AutoTimeLapseConfigEntry
) -> bool:
    """Set up a camera entry and one manager per trigger subentry."""
    managers: dict[str, TimelapseManager] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_TRIGGER:
            continue
        manager = TimelapseManager(hass, entry, subentry)
        await manager.async_setup()
        managers[subentry.subentry_id] = manager
    entry.runtime_data = managers
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: AutoTimeLapseConfigEntry
) -> None:
    """Reload the entry when subentries are added, changed, or removed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: AutoTimeLapseConfigEntry
) -> bool:
    """Unload a camera entry and all its trigger managers."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for manager in entry.runtime_data.values():
            await manager.async_unload()
    return unload_ok
