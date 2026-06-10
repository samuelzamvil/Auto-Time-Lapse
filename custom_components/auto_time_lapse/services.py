"""Services for the Auto Time Lapse integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_RENDER,
    DOMAIN,
    SERVICE_CANCEL,
    SERVICE_RENDER,
    SERVICE_START,
    SERVICE_STOP,
)

if TYPE_CHECKING:
    from .manager import TimelapseManager

_BASE_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})
_STOP_SCHEMA = _BASE_SCHEMA.extend(
    {vol.Optional(ATTR_RENDER, default=True): cv.boolean}
)


def _get_manager(hass: HomeAssistant, call: ServiceCall) -> TimelapseManager:
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry.runtime_data


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration services."""

    async def _start(call: ServiceCall) -> None:
        await _get_manager(hass, call).async_start()

    async def _stop(call: ServiceCall) -> None:
        await _get_manager(hass, call).async_stop(render=call.data[ATTR_RENDER])

    async def _render(call: ServiceCall) -> None:
        await _get_manager(hass, call).async_rerender()

    async def _cancel(call: ServiceCall) -> None:
        await _get_manager(hass, call).async_cancel()

    hass.services.async_register(DOMAIN, SERVICE_START, _start, schema=_BASE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, _stop, schema=_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RENDER, _render, schema=_BASE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, _cancel, schema=_BASE_SCHEMA)
