"""Config flow for the Auto Time Lapse integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TimeSelector,
)
import voluptuous as vol

from .const import (
    CONF_CAMERA_ENTITY,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_WATCH_ENTITY,
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_INTERVAL,
    DEFAULT_KEEP_FRAMES,
    DEFAULT_OUTPUT_FPS,
    DOMAIN,
)


def _options_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CAMERA_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="camera")
            ),
            vol.Required(CONF_INTERVAL, default=DEFAULT_INTERVAL): vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=86400,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Coerce(int),
            ),
            vol.Required(CONF_OUTPUT_FPS, default=DEFAULT_OUTPUT_FPS): vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=120, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Coerce(int),
            ),
            vol.Optional(CONF_OUTPUT_DIR): TextSelector(),
            vol.Required(
                CONF_FILENAME_PATTERN, default=DEFAULT_FILENAME_PATTERN
            ): TextSelector(),
            vol.Required(
                CONF_KEEP_FRAMES, default=DEFAULT_KEEP_FRAMES
            ): BooleanSelector(),
            vol.Required(CONF_SCHEDULE_ENABLED, default=False): BooleanSelector(),
            vol.Optional(CONF_SCHEDULE_START): TimeSelector(),
            vol.Optional(CONF_SCHEDULE_END): TimeSelector(),
            vol.Optional(CONF_WATCH_ENTITY): EntitySelector(),
        }
    )


def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if output_dir := user_input.get(CONF_OUTPUT_DIR):
        path = Path(output_dir)
        if not path.is_absolute() or not hass.config.is_allowed_path(output_dir):
            errors[CONF_OUTPUT_DIR] = "path_not_allowed"
    if user_input.get(CONF_SCHEDULE_ENABLED):
        start = user_input.get(CONF_SCHEDULE_START)
        end = user_input.get(CONF_SCHEDULE_END)
        if not start or not end:
            errors[CONF_SCHEDULE_ENABLED] = "schedule_times_required"
        elif start == end:
            errors[CONF_SCHEDULE_END] = "schedule_start_equals_end"
    return errors


class AutoTimeLapseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle creating a timelapse profile."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                name = user_input.pop(CONF_NAME)
                return self.async_create_entry(title=name, data={}, options=user_input)

        schema = vol.Schema({vol.Required(CONF_NAME): TextSelector()}).extend(
            _options_schema().schema
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AutoTimeLapseOptionsFlow:
        """Get the options flow for this handler."""
        return AutoTimeLapseOptionsFlow()


class AutoTimeLapseOptionsFlow(OptionsFlow):
    """Handle editing a timelapse profile."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=user_input)

        suggested = user_input if user_input is not None else self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _options_schema(), suggested
            ),
            errors=errors,
        )
