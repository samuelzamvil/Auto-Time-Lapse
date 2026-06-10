"""Config flow for the Auto Time Lapse integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    StateSelector,
    StateSelectorConfig,
    TextSelector,
    TimeSelector,
)
import voluptuous as vol

from .const import (
    CONF_CAMERA_ENTITY,
    CONF_CAPTURE_MODE,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_TRIGGER_MODE,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_VALUE_ENTITY,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_INTERVAL,
    DEFAULT_KEEP_FRAMES,
    DEFAULT_OUTPUT_FPS,
    DEFAULT_VALUE_DELTA,
    DOMAIN,
    SUBENTRY_TYPE_TRIGGER,
    CaptureMode,
    TriggerMode,
    ValueDirection,
)


def _trigger_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Required(
                CONF_TRIGGER_MODE, default=TriggerMode.MANUAL.value
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[mode.value for mode in TriggerMode],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="trigger_mode",
                )
            ),
            vol.Required(
                CONF_CAPTURE_MODE, default=CaptureMode.TIME.value
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[mode.value for mode in CaptureMode],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="capture_mode",
                )
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
        }
    )


def _validate_output_dir(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if output_dir := user_input.get(CONF_OUTPUT_DIR):
        path = Path(output_dir)
        if not path.is_absolute() or not hass.config.is_allowed_path(output_dir):
            errors[CONF_OUTPUT_DIR] = "path_not_allowed"
    return errors


class AutoTimeLapseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a camera entry; triggers are added as subentries."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the camera for this entry."""
        if user_input is not None:
            camera = user_input[CONF_CAMERA_ENTITY]
            self._async_abort_entries_match({CONF_CAMERA_ENTITY: camera})
            state = self.hass.states.get(camera)
            title = state.name if state else camera
            return self.async_create_entry(
                title=title, data={CONF_CAMERA_ENTITY: camera}
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CAMERA_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="camera")
                    )
                }
            ),
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry flow handlers."""
        return {SUBENTRY_TYPE_TRIGGER: TriggerSubentryFlow}


class TriggerSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a trigger profile on a camera entry."""

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[str, Any] = {}

    @property
    def _is_new(self) -> bool:
        return self.source == SOURCE_USER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new trigger."""
        return await self._async_step_main(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing trigger."""
        if not self._data:
            subentry = self._get_reconfigure_subentry()
            self._data = dict(subentry.data)
            self._data[CONF_NAME] = subentry.title
        return await self._async_step_main(user_input, "reconfigure")

    async def _async_step_main(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_output_dir(self.hass, user_input)
            if not errors:
                self._data.update(user_input)
                if self._data[CONF_CAPTURE_MODE] == CaptureMode.VALUE_CHANGE:
                    return await self.async_step_value_change()
                return await self.async_step_interval()
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _trigger_schema(), suggested or None
            ),
            errors=errors,
        )

    async def _async_next_trigger_step(self) -> SubentryFlowResult:
        mode = self._data[CONF_TRIGGER_MODE]
        if mode == TriggerMode.SCHEDULE:
            return await self.async_step_schedule()
        if mode == TriggerMode.WATCH:
            return await self.async_step_watch()
        return self._finish()

    async def async_step_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the time between snapshots."""
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_next_trigger_step()
        schema = vol.Schema(
            {
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
                )
            }
        )
        return self.async_show_form(
            step_id="interval",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._data or None
            ),
        )

    async def async_step_value_change(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the value-change capture cadence."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[CONF_VALUE_DELTA]) <= 0:
                errors[CONF_VALUE_DELTA] = "delta_positive"
            else:
                self._data.update(user_input)
                return await self._async_next_trigger_step()
        schema = vol.Schema(
            {
                vol.Required(CONF_VALUE_ENTITY): EntitySelector(),
                vol.Required(
                    CONF_VALUE_DELTA, default=DEFAULT_VALUE_DELTA
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            step="any", mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Coerce(float),
                ),
                vol.Required(
                    CONF_VALUE_DIRECTION, default=ValueDirection.ANY.value
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[d.value for d in ValueDirection],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="value_direction",
                    )
                ),
            }
        )
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="value_change",
            data_schema=self.add_suggested_values_to_schema(
                schema, suggested or None
            ),
            errors=errors,
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the daily capture window."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_SCHEDULE_START] == user_input[CONF_SCHEDULE_END]:
                errors[CONF_SCHEDULE_END] = "schedule_start_equals_end"
            else:
                self._data.update(user_input)
                return self._finish()
        schema = vol.Schema(
            {
                vol.Required(CONF_SCHEDULE_START): TimeSelector(),
                vol.Required(CONF_SCHEDULE_END): TimeSelector(),
            }
        )
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="schedule",
            data_schema=self.add_suggested_values_to_schema(
                schema, suggested or None
            ),
            errors=errors,
        )

    async def async_step_watch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the entity to watch."""
        if user_input is not None:
            if self._data.get(CONF_WATCH_ENTITY) != user_input[CONF_WATCH_ENTITY]:
                # Entity changed; previously selected states may not apply.
                self._data.pop(CONF_WATCH_STATES, None)
            self._data[CONF_WATCH_ENTITY] = user_input[CONF_WATCH_ENTITY]
            return await self.async_step_watch_states()
        schema = vol.Schema({vol.Required(CONF_WATCH_ENTITY): EntitySelector()})
        return self.async_show_form(
            step_id="watch",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._data or None
            ),
        )

    async def async_step_watch_states(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the states of the watched entity that mean 'capturing'."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_WATCH_STATES):
                errors[CONF_WATCH_STATES] = "states_required"
            else:
                self._data[CONF_WATCH_STATES] = user_input[CONF_WATCH_STATES]
                return self._finish()
        schema = vol.Schema(
            {
                vol.Required(CONF_WATCH_STATES, default=[STATE_ON]): StateSelector(
                    StateSelectorConfig(
                        entity_id=self._data[CONF_WATCH_ENTITY],
                        multiple=True,
                        hide_states=[STATE_UNAVAILABLE, STATE_UNKNOWN],
                    )
                )
            }
        )
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="watch_states",
            data_schema=self.add_suggested_values_to_schema(
                schema, suggested or None
            ),
            errors=errors,
        )

    @callback
    def _finish(self) -> SubentryFlowResult:
        data = dict(self._data)
        title = data.pop(CONF_NAME)
        mode = data[CONF_TRIGGER_MODE]
        if mode != TriggerMode.SCHEDULE:
            data.pop(CONF_SCHEDULE_START, None)
            data.pop(CONF_SCHEDULE_END, None)
        if mode != TriggerMode.WATCH:
            data.pop(CONF_WATCH_ENTITY, None)
            data.pop(CONF_WATCH_STATES, None)
        if data[CONF_CAPTURE_MODE] == CaptureMode.VALUE_CHANGE:
            data.pop(CONF_INTERVAL, None)
        else:
            data.pop(CONF_VALUE_ENTITY, None)
            data.pop(CONF_VALUE_DELTA, None)
            data.pop(CONF_VALUE_DIRECTION, None)
        if self._is_new:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
            title=title,
        )
