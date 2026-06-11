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
    ConditionSelector,
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
    CONF_CONDITIONAL_REEVALUATE,
    CONF_CONDITIONAL_RULES,
    CONF_DURATION_ENTITY,
    CONF_END_BUFFER_AMOUNT,
    CONF_END_BUFFER_INTERVAL,
    CONF_END_BUFFER_MODE,
    CONF_END_BUFFER_RETRIGGER,
    CONF_FALLBACK_INTERVAL,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_RULE_ADD_ANOTHER,
    CONF_RULE_CONDITIONS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_TARGET_LENGTH,
    CONF_TRIGGER_MODE,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_VALUE_ENTITY,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DEFAULT_CONDITIONAL_REEVALUATE,
    DEFAULT_END_BUFFER_AMOUNT,
    DEFAULT_FALLBACK_INTERVAL,
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_INTERVAL,
    DEFAULT_KEEP_FRAMES,
    DEFAULT_OUTPUT_FPS,
    DEFAULT_TARGET_LENGTH,
    DEFAULT_VALUE_DELTA,
    DOMAIN,
    RULE_CAPTURE_MODES,
    SUBENTRY_TYPE_TRIGGER,
    BufferRetrigger,
    CaptureMode,
    EndBufferMode,
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


def _conditional_rule_schema(*, is_default: bool) -> vol.Schema:
    """Schema for one rule of the conditional cadence.

    The default (else) rule has no conditions and carries the trigger-wide
    re-evaluation toggle instead of the add-another checkbox.
    """
    fields: dict[Any, Any] = {}
    if not is_default:
        fields[vol.Required(CONF_RULE_CONDITIONS)] = ConditionSelector()
    fields[vol.Required(CONF_CAPTURE_MODE, default=CaptureMode.TIME.value)] = (
        SelectSelector(
            SelectSelectorConfig(
                options=[mode.value for mode in RULE_CAPTURE_MODES],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="rule_capture_mode",
            )
        )
    )
    fields[vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL)] = vol.All(
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
    fields[vol.Optional(CONF_VALUE_ENTITY)] = EntitySelector()
    fields[vol.Optional(CONF_VALUE_DELTA, default=DEFAULT_VALUE_DELTA)] = vol.All(
        NumberSelector(NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)),
        vol.Coerce(float),
    )
    fields[vol.Optional(CONF_VALUE_DIRECTION, default=ValueDirection.ANY.value)] = (
        SelectSelector(
            SelectSelectorConfig(
                options=[d.value for d in ValueDirection],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="value_direction",
            )
        )
    )
    if is_default:
        fields[
            vol.Required(
                CONF_CONDITIONAL_REEVALUATE, default=DEFAULT_CONDITIONAL_REEVALUATE
            )
        ] = BooleanSelector()
    else:
        fields[vol.Required(CONF_RULE_ADD_ANOTHER, default=False)] = BooleanSelector()
    return vol.Schema(fields)


def _validate_rule(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the cadence settings of one conditional rule."""
    errors: dict[str, str] = {}
    if user_input[CONF_CAPTURE_MODE] == CaptureMode.VALUE_CHANGE:
        if not user_input.get(CONF_VALUE_ENTITY):
            errors[CONF_VALUE_ENTITY] = "value_entity_required"
        if float(user_input.get(CONF_VALUE_DELTA, DEFAULT_VALUE_DELTA)) <= 0:
            errors[CONF_VALUE_DELTA] = "delta_positive"
    return errors


def _build_rule(
    user_input: dict[str, Any], *, conditions: list | None
) -> dict[str, Any]:
    """Assemble a stored rule dict, keeping only the chosen cadence's keys."""
    rule: dict[str, Any] = {}
    if conditions:
        rule[CONF_RULE_CONDITIONS] = conditions
    mode = user_input[CONF_CAPTURE_MODE]
    rule[CONF_CAPTURE_MODE] = mode
    if mode == CaptureMode.VALUE_CHANGE:
        rule[CONF_VALUE_ENTITY] = user_input[CONF_VALUE_ENTITY]
        rule[CONF_VALUE_DELTA] = float(
            user_input.get(CONF_VALUE_DELTA, DEFAULT_VALUE_DELTA)
        )
        rule[CONF_VALUE_DIRECTION] = user_input.get(
            CONF_VALUE_DIRECTION, ValueDirection.ANY.value
        )
    else:
        rule[CONF_INTERVAL] = int(user_input.get(CONF_INTERVAL, DEFAULT_INTERVAL))
    return rule


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
        self._rules: list[dict[str, Any]] = []
        self._existing_rules: list[dict[str, Any]] = []

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
                if self._data[CONF_CAPTURE_MODE] == CaptureMode.TIME_FIT:
                    return await self.async_step_fit_length()
                if self._data[CONF_CAPTURE_MODE] == CaptureMode.CONDITIONAL:
                    self._rules = []
                    self._existing_rules = list(
                        self._data.get(CONF_CONDITIONAL_RULES) or []
                    )
                    return await self.async_step_conditional_rule()
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

    async def async_step_fit_length(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the fit-target-video-length capture cadence."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[CONF_TARGET_LENGTH]) <= 0:
                errors[CONF_TARGET_LENGTH] = "length_positive"
            else:
                self._data.update(user_input)
                return await self._async_next_trigger_step()
        schema = vol.Schema(
            {
                vol.Required(CONF_DURATION_ENTITY): EntitySelector(),
                vol.Required(
                    CONF_TARGET_LENGTH, default=DEFAULT_TARGET_LENGTH
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            step="any",
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(float),
                ),
                vol.Required(
                    CONF_FALLBACK_INTERVAL, default=DEFAULT_FALLBACK_INTERVAL
                ): vol.All(
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
            }
        )
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="fit_length",
            data_schema=self.add_suggested_values_to_schema(
                schema, suggested or None
            ),
            errors=errors,
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

    async def async_step_conditional_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure one condition rule of the conditional cadence."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_RULE_CONDITIONS):
                errors[CONF_RULE_CONDITIONS] = "conditions_required"
            errors |= _validate_rule(user_input)
            if not errors:
                self._rules.append(
                    _build_rule(
                        user_input, conditions=user_input[CONF_RULE_CONDITIONS]
                    )
                )
                if user_input[CONF_RULE_ADD_ANOTHER]:
                    return await self.async_step_conditional_rule()
                return await self.async_step_conditional_default()
        index = len(self._rules)
        suggested = user_input
        if suggested is None and index < len(self._existing_rules) - 1:
            # Prefill from the same-position rule of the existing config;
            # the last existing rule is the default and prefills that step.
            suggested = dict(self._existing_rules[index])
            suggested[CONF_RULE_ADD_ANOTHER] = (
                index < len(self._existing_rules) - 2
            )
        return self.async_show_form(
            step_id="conditional_rule",
            data_schema=self.add_suggested_values_to_schema(
                _conditional_rule_schema(is_default=False), suggested
            ),
            errors=errors,
            description_placeholders={"rule_number": str(index + 1)},
        )

    async def async_step_conditional_default(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the else/default rule of the conditional cadence."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_rule(user_input)
            if not errors:
                self._rules.append(_build_rule(user_input, conditions=None))
                self._data[CONF_CONDITIONAL_RULES] = self._rules
                self._data[CONF_CONDITIONAL_REEVALUATE] = user_input[
                    CONF_CONDITIONAL_REEVALUATE
                ]
                return await self._async_next_trigger_step()
        suggested = user_input
        if suggested is None and self._existing_rules:
            suggested = dict(self._existing_rules[-1])
            suggested[CONF_CONDITIONAL_REEVALUATE] = self._data.get(
                CONF_CONDITIONAL_REEVALUATE, DEFAULT_CONDITIONAL_REEVALUATE
            )
        return self.async_show_form(
            step_id="conditional_default",
            data_schema=self.add_suggested_values_to_schema(
                _conditional_rule_schema(is_default=True), suggested
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
                return await self.async_step_end_buffer()
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
                return await self.async_step_end_buffer()
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

    async def async_step_end_buffer(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the optional capture buffer after the trigger ends."""
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input[CONF_END_BUFFER_MODE]
            if mode != EndBufferMode.OFF:
                if not user_input.get(CONF_END_BUFFER_AMOUNT):
                    errors[CONF_END_BUFFER_AMOUNT] = "buffer_amount_required"
                if self._buffer_interval_required() and not user_input.get(
                    CONF_END_BUFFER_INTERVAL
                ):
                    errors[CONF_END_BUFFER_INTERVAL] = "buffer_interval_required"
            if not errors:
                self._data.pop(CONF_END_BUFFER_INTERVAL, None)
                self._data.update(user_input)
                return self._finish()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_END_BUFFER_MODE, default=EndBufferMode.OFF.value
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[mode.value for mode in EndBufferMode],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="end_buffer_mode",
                    )
                ),
                vol.Optional(
                    CONF_END_BUFFER_AMOUNT, default=DEFAULT_END_BUFFER_AMOUNT
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=86400, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Optional(CONF_END_BUFFER_INTERVAL): vol.All(
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
                vol.Required(
                    CONF_END_BUFFER_RETRIGGER, default=BufferRetrigger.RESUME.value
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[r.value for r in BufferRetrigger],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="end_buffer_retrigger",
                    )
                ),
            }
        )
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="end_buffer",
            data_schema=self.add_suggested_values_to_schema(
                schema, suggested or None
            ),
            errors=errors,
        )

    def _buffer_interval_required(self) -> bool:
        """Whether the end buffer needs its own time-based interval."""
        cadence = self._data[CONF_CAPTURE_MODE]
        if cadence == CaptureMode.VALUE_CHANGE:
            return True
        # A conditional cadence may be value-change paced when the buffer
        # starts, so it needs the override too.
        return cadence == CaptureMode.CONDITIONAL and any(
            rule.get(CONF_CAPTURE_MODE) == CaptureMode.VALUE_CHANGE
            for rule in self._data.get(CONF_CONDITIONAL_RULES) or []
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
        buffer_mode = data.get(CONF_END_BUFFER_MODE)
        if mode == TriggerMode.MANUAL or buffer_mode in (None, EndBufferMode.OFF):
            data.pop(CONF_END_BUFFER_MODE, None)
            data.pop(CONF_END_BUFFER_AMOUNT, None)
            data.pop(CONF_END_BUFFER_INTERVAL, None)
            data.pop(CONF_END_BUFFER_RETRIGGER, None)
        cadence = data[CONF_CAPTURE_MODE]
        if cadence != CaptureMode.TIME:
            data.pop(CONF_INTERVAL, None)
        if cadence != CaptureMode.TIME_FIT:
            data.pop(CONF_DURATION_ENTITY, None)
            data.pop(CONF_TARGET_LENGTH, None)
            data.pop(CONF_FALLBACK_INTERVAL, None)
        if cadence != CaptureMode.VALUE_CHANGE:
            data.pop(CONF_VALUE_ENTITY, None)
            data.pop(CONF_VALUE_DELTA, None)
            data.pop(CONF_VALUE_DIRECTION, None)
        if cadence != CaptureMode.CONDITIONAL:
            data.pop(CONF_CONDITIONAL_RULES, None)
            data.pop(CONF_CONDITIONAL_REEVALUATE, None)
        if self._is_new:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
            title=title,
        )
