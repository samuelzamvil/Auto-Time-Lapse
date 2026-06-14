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
    OptionsFlow,
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
    CONF_AUTO_PURGE,
    CONF_CAMERA_ENTITY,
    CONF_CAPTURE_MODE,
    CONF_CONDITIONAL_RULES,
    CONF_DURATION_ENTITY,
    CONF_DURATION_TYPE,
    CONF_END_BUFFER_AMOUNT,
    CONF_END_BUFFER_INTERVAL,
    CONF_END_BUFFER_MODE,
    CONF_END_BUFFER_RETRIGGER,
    CONF_FALLBACK_INTERVAL,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_MAX_WIDTH,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_PURGE_KEEP_SESSIONS,
    CONF_PURGE_MAX_AGE_DAYS,
    CONF_PURGE_MODE,
    CONF_RULE_ADD_ANOTHER,
    CONF_RULE_CONDITIONS,
    CONF_SCALE_MODE,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_TARGET_LENGTH,
    CONF_TRIGGER_MODE,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_VALUE_ENTITY,
    CONF_VIDEO_CRF,
    CONF_VIDEO_PRESET,
    CONF_VIDEO_QUALITY,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DEFAULT_AUTO_PURGE,
    DEFAULT_END_BUFFER_AMOUNT,
    DEFAULT_FALLBACK_INTERVAL,
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_INTERVAL,
    DEFAULT_KEEP_FRAMES,
    DEFAULT_OUTPUT_FPS,
    DEFAULT_PURGE_KEEP_SESSIONS,
    DEFAULT_PURGE_MAX_AGE_DAYS,
    DEFAULT_TARGET_LENGTH,
    DEFAULT_VALUE_DELTA,
    DEFAULT_VIDEO_CRF,
    DEFAULT_VIDEO_PRESET,
    DOMAIN,
    FFMPEG_PRESETS,
    OPTION_SERVICE_DEFAULT,
    RULE_CAPTURE_MODES,
    SUBENTRY_TYPE_TRIGGER,
    BufferRetrigger,
    CaptureMode,
    DurationType,
    EndBufferMode,
    PurgeMode,
    ScaleMode,
    TriggerMode,
    ValueDirection,
    VideoQuality,
)


def _quality_fields(*, with_inherit: bool) -> dict[Any, Any]:
    """Video-quality and image-scaling fields shared by both flows.

    With with_inherit, the selects gain a leading "use service default"
    option (the trigger-level forms); without it they default to the
    built-in values (the camera entry's options flow).
    """
    quality_options = [q.value for q in VideoQuality]
    scale_options = [m.value for m in ScaleMode]
    if with_inherit:
        quality_options.insert(0, OPTION_SERVICE_DEFAULT)
        scale_options.insert(0, OPTION_SERVICE_DEFAULT)
        quality_default = OPTION_SERVICE_DEFAULT
        scale_default = OPTION_SERVICE_DEFAULT
        suffix = "_override"
    else:
        quality_default = VideoQuality.MEDIUM.value
        scale_default = ScaleMode.OFF.value
        suffix = ""
    return {
        vol.Required(CONF_VIDEO_QUALITY, default=quality_default): SelectSelector(
            SelectSelectorConfig(
                options=quality_options,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key=f"video_quality{suffix}",
            )
        ),
        vol.Required(CONF_SCALE_MODE, default=scale_default): SelectSelector(
            SelectSelectorConfig(
                options=scale_options,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key=f"scale_mode{suffix}",
            )
        ),
        vol.Optional(CONF_MAX_WIDTH): vol.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=120,
                    max=7680,
                    step=1,
                    unit_of_measurement="px",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Coerce(int),
        ),
    }


def _custom_video_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_VIDEO_CRF, default=DEFAULT_VIDEO_CRF): vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=51, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Coerce(int),
            ),
            vol.Required(
                CONF_VIDEO_PRESET, default=DEFAULT_VIDEO_PRESET
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(FFMPEG_PRESETS),
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="video_preset",
                )
            ),
        }
    )


def _validate_scaling(user_input: dict[str, Any]) -> dict[str, str]:
    """An enabled scale mode needs a maximum width to scale to."""
    errors: dict[str, str] = {}
    if user_input.get(CONF_SCALE_MODE) in (
        ScaleMode.CAPTURE,
        ScaleMode.RENDER,
    ) and not user_input.get(CONF_MAX_WIDTH):
        errors[CONF_MAX_WIDTH] = "max_width_required"
    return errors


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
            vol.Required(
                CONF_AUTO_PURGE, default=DEFAULT_AUTO_PURGE
            ): BooleanSelector(),
            vol.Required(
                CONF_PURGE_MODE, default=PurgeMode.KEEP_RECENT.value
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[m.value for m in PurgeMode],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="purge_mode",
                )
            ),
            vol.Required(
                CONF_PURGE_KEEP_SESSIONS, default=DEFAULT_PURGE_KEEP_SESSIONS
            ): vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=365, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Coerce(int),
            ),
            vol.Required(
                CONF_PURGE_MAX_AGE_DAYS, default=DEFAULT_PURGE_MAX_AGE_DAYS
            ): vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=3650,
                        step=1,
                        unit_of_measurement="d",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Coerce(int),
            ),
            **_quality_fields(with_inherit=True),
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

    The default (else) rule has no conditions and no add-another checkbox.
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
    fields[vol.Optional(CONF_DURATION_ENTITY)] = EntitySelector()
    fields[vol.Optional(
        CONF_DURATION_TYPE, default=DurationType.SECONDS.value
    )] = SelectSelector(
        SelectSelectorConfig(
            options=[t.value for t in DurationType],
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="duration_type",
        )
    )
    fields[vol.Optional(
        CONF_TARGET_LENGTH, default=DEFAULT_TARGET_LENGTH
    )] = vol.All(
        NumberSelector(
            NumberSelectorConfig(
                step="any",
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Coerce(float),
    )
    fields[vol.Optional(
        CONF_FALLBACK_INTERVAL, default=DEFAULT_FALLBACK_INTERVAL
    )] = vol.All(
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
    if not is_default:
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
    elif user_input[CONF_CAPTURE_MODE] == CaptureMode.TIME_FIT:
        if not user_input.get(CONF_DURATION_ENTITY):
            errors[CONF_DURATION_ENTITY] = "duration_entity_required"
        if float(user_input.get(CONF_TARGET_LENGTH, DEFAULT_TARGET_LENGTH)) <= 0:
            errors[CONF_TARGET_LENGTH] = "length_positive"
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
    elif mode == CaptureMode.TIME_FIT:
        rule[CONF_DURATION_ENTITY] = user_input[CONF_DURATION_ENTITY]
        rule[CONF_DURATION_TYPE] = user_input.get(
            CONF_DURATION_TYPE, DurationType.SECONDS.value
        )
        rule[CONF_TARGET_LENGTH] = float(
            user_input.get(CONF_TARGET_LENGTH, DEFAULT_TARGET_LENGTH)
        )
        rule[CONF_FALLBACK_INTERVAL] = int(
            user_input.get(CONF_FALLBACK_INTERVAL, DEFAULT_FALLBACK_INTERVAL)
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AutoTimeLapseOptionsFlow:
        """Return the options flow for the camera entry."""
        return AutoTimeLapseOptionsFlow()


class AutoTimeLapseOptionsFlow(OptionsFlow):
    """Camera-wide video and image quality defaults for all triggers."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the quality level and image scaling."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_scaling(user_input)
            if not errors:
                self._data = dict(user_input)
                if user_input[CONF_VIDEO_QUALITY] == VideoQuality.CUSTOM:
                    return await self.async_step_custom_video()
                return self._finish()
        suggested = (
            user_input if user_input is not None else dict(self.config_entry.options)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_quality_fields(with_inherit=False)), suggested or None
            ),
            errors=errors,
        )

    async def async_step_custom_video(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the raw encoder parameters for the custom quality level."""
        if user_input is not None:
            self._data.update(user_input)
            return self._finish()
        return self.async_show_form(
            step_id="custom_video",
            data_schema=self.add_suggested_values_to_schema(
                _custom_video_schema(), dict(self.config_entry.options) or None
            ),
        )

    @callback
    def _finish(self) -> ConfigFlowResult:
        data = dict(self._data)
        if data.get(CONF_VIDEO_QUALITY) != VideoQuality.CUSTOM:
            data.pop(CONF_VIDEO_CRF, None)
            data.pop(CONF_VIDEO_PRESET, None)
        if data.get(CONF_SCALE_MODE) == ScaleMode.OFF:
            data.pop(CONF_MAX_WIDTH, None)
        return self.async_create_entry(title="", data=data)


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
            errors |= _validate_scaling(user_input)
            if not errors:
                self._data.update(user_input)
                if self._data[CONF_VIDEO_QUALITY] == VideoQuality.CUSTOM:
                    return await self.async_step_custom_video()
                return await self._async_after_main()
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _trigger_schema(), suggested or None
            ),
            errors=errors,
        )

    async def async_step_custom_video(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Set the raw encoder parameters for the custom quality override."""
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_after_main()
        return self.async_show_form(
            step_id="custom_video",
            data_schema=self.add_suggested_values_to_schema(
                _custom_video_schema(), self._data or None
            ),
        )

    async def _async_after_main(self) -> SubentryFlowResult:
        """Continue with the cadence step for the chosen capture mode."""
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
                    CONF_DURATION_TYPE, default=DurationType.SECONDS.value
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[t.value for t in DurationType],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="duration_type",
                    )
                ),
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
                return await self._async_next_trigger_step()
        suggested = user_input
        if suggested is None and self._existing_rules:
            suggested = dict(self._existing_rules[-1])
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
            data.pop(CONF_DURATION_TYPE, None)
            data.pop(CONF_TARGET_LENGTH, None)
            data.pop(CONF_FALLBACK_INTERVAL, None)
        if cadence != CaptureMode.VALUE_CHANGE:
            data.pop(CONF_VALUE_ENTITY, None)
            data.pop(CONF_VALUE_DELTA, None)
            data.pop(CONF_VALUE_DIRECTION, None)
        if cadence != CaptureMode.CONDITIONAL:
            data.pop(CONF_CONDITIONAL_RULES, None)
        if data.get(CONF_VIDEO_QUALITY) in (None, OPTION_SERVICE_DEFAULT):
            data.pop(CONF_VIDEO_QUALITY, None)
        if data.get(CONF_VIDEO_QUALITY) != VideoQuality.CUSTOM:
            data.pop(CONF_VIDEO_CRF, None)
            data.pop(CONF_VIDEO_PRESET, None)
        scale_mode = data.get(CONF_SCALE_MODE)
        if scale_mode in (None, OPTION_SERVICE_DEFAULT):
            data.pop(CONF_SCALE_MODE, None)
            data.pop(CONF_MAX_WIDTH, None)
        elif scale_mode == ScaleMode.OFF:
            # An explicit off override of a service-level capture/render
            # setting must survive, unlike the inherit sentinel.
            data.pop(CONF_MAX_WIDTH, None)
        if not data.get(CONF_KEEP_FRAMES):
            data.pop(CONF_AUTO_PURGE, None)
            data.pop(CONF_PURGE_MODE, None)
            data.pop(CONF_PURGE_KEEP_SESSIONS, None)
            data.pop(CONF_PURGE_MAX_AGE_DAYS, None)
        elif not data.get(CONF_AUTO_PURGE):
            data.pop(CONF_PURGE_MODE, None)
            data.pop(CONF_PURGE_KEEP_SESSIONS, None)
            data.pop(CONF_PURGE_MAX_AGE_DAYS, None)
        else:
            purge_mode = data.get(CONF_PURGE_MODE)
            if purge_mode == PurgeMode.KEEP_RECENT:
                data.pop(CONF_PURGE_MAX_AGE_DAYS, None)
            elif purge_mode == PurgeMode.MAX_AGE:
                data.pop(CONF_PURGE_KEEP_SESSIONS, None)
        if self._is_new:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
            title=title,
        )
