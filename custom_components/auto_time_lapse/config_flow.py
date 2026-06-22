"""Config flow for the Auto Time Lapse integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    StateSelector,
    StateSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
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
from .schema import (
    DuplicateTriggerNames,
    InvalidConditionError,
    build_rule,
    entry_to_yaml,
    parse_entry_yaml,
    parse_trigger_yaml,
    prune_options,
    prune_trigger_data,
    trigger_to_yaml,
    validate_rule,
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


def _basics_fields() -> dict[Any, Any]:
    """Name, trigger mode, capture cadence, and output naming."""
    return {
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
    }


def _output_fields() -> dict[Any, Any]:
    """Frame retention, purge policy, and video/image quality overrides."""
    return {
        vol.Required(CONF_KEEP_FRAMES, default=DEFAULT_KEEP_FRAMES): BooleanSelector(),
        vol.Required(CONF_AUTO_PURGE, default=DEFAULT_AUTO_PURGE): BooleanSelector(),
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


def _trigger_schema() -> vol.Schema:
    return vol.Schema({**_basics_fields(), **_output_fields()})


def _yaml_schema() -> vol.Schema:
    """A single multiline text field for pasting/showing YAML."""
    return vol.Schema(
        {
            vol.Required("yaml"): TextSelector(
                TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
            )
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


def _interval_fields() -> dict[Any, Any]:
    """The single field of the time-interval cadence."""
    return {
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


def _fit_fields() -> dict[Any, Any]:
    """The fields of the fit-target-video-length cadence."""
    return {
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
        vol.Required(CONF_TARGET_LENGTH, default=DEFAULT_TARGET_LENGTH): vol.All(
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


def _value_fields() -> dict[Any, Any]:
    """The fields of the entity-value-change cadence."""
    return {
        vol.Required(CONF_VALUE_ENTITY): EntitySelector(),
        vol.Required(CONF_VALUE_DELTA, default=DEFAULT_VALUE_DELTA): vol.All(
            NumberSelector(
                NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)
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


_CADENCE_FIELD_BUILDERS = {
    CaptureMode.TIME: _interval_fields,
    CaptureMode.TIME_FIT: _fit_fields,
    CaptureMode.VALUE_CHANGE: _value_fields,
}


def _rule_conditions_schema(*, is_default: bool) -> vol.Schema:
    """First hop of the rule editor: conditions and which cadence to use.

    The default (else) rule has no conditions.
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
    return vol.Schema(fields)


def _rule_cadence_schema(mode: str) -> vol.Schema:
    """Second hop of the rule editor: only the chosen cadence's fields."""
    builder = _CADENCE_FIELD_BUILDERS.get(CaptureMode(mode), _interval_fields)
    return vol.Schema(builder())


# Picker key for the "which rule" select steps; never persisted.
CONF_RULE_INDEX = "rule_index"


def _describe_condition(cond: dict[str, Any]) -> str:
    """A terse one-line summary of a single Home Assistant condition."""
    kind = cond.get("condition", "condition")
    entity = cond.get("entity_id")
    if isinstance(entity, list):
        entity = ", ".join(entity)
    if kind == "numeric_state":
        bounds = []
        if (below := cond.get("below")) is not None:
            bounds.append(f"< {below}")
        if (above := cond.get("above")) is not None:
            bounds.append(f"> {above}")
        return f"{entity} {' and '.join(bounds)}".strip()
    if kind == "state":
        state = cond.get("state")
        if isinstance(state, list):
            state = ", ".join(str(s) for s in state)
        return f"{entity} is {state}"
    if entity:
        return f"{kind}: {entity}"
    return kind


def _describe_conditions(rule: dict[str, Any]) -> str:
    """Summarise the AND-ed conditions of a rule."""
    conditions = rule.get(CONF_RULE_CONDITIONS) or []
    parts = [_describe_condition(c) for c in conditions if isinstance(c, dict)]
    return " AND ".join(parts) if parts else "(always)"


def _conditions_for_editing(
    conditions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse single-entity ``entity_id`` lists to strings for the editor.

    HA's ConditionSelector stores ``entity_id`` as a list, but its visual
    editor only accepts a string. Collapsing single-element lists lets an
    existing rule reopen in the visual editor instead of YAML-only mode.
    """
    editable: list[dict[str, Any]] = []
    for cond in conditions:
        entity = cond.get("entity_id") if isinstance(cond, dict) else None
        if isinstance(entity, list) and len(entity) == 1:
            editable.append({**cond, "entity_id": entity[0]})
        else:
            editable.append(cond)
    return editable


def _describe_cadence(rule: dict[str, Any]) -> str:
    """Summarise the cadence a rule paces frames with."""
    mode = rule.get(CONF_CAPTURE_MODE)
    if mode == CaptureMode.TIME_FIT:
        return (
            f"fit ~{rule.get(CONF_TARGET_LENGTH)}s video "
            f"from {rule.get(CONF_DURATION_ENTITY)}"
        )
    if mode == CaptureMode.VALUE_CHANGE:
        return (
            f"a frame every {rule.get(CONF_VALUE_DELTA)} "
            f"of {rule.get(CONF_VALUE_ENTITY)}"
        )
    if mode == CaptureMode.TIME:
        return f"a frame every {rule.get(CONF_INTERVAL)}s"
    return str(mode)


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
        # Parsed whole-entry import held between the paste and confirm steps.
        self._import_options: dict[str, Any] = {}
        self._import_triggers: list[tuple[str, dict[str, Any]]] = []
        self._import_deletions: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between editing quality defaults and the YAML code view."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["quality", "export_yaml", "import_yaml"],
        )

    async def async_step_quality(
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
            step_id="quality",
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

    def _trigger_subentries(self) -> list[ConfigSubentry]:
        return [
            sub
            for sub in self.config_entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_TRIGGER
        ]

    async def async_step_export_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the whole camera entry (options + triggers) as YAML."""
        if user_input is not None:
            return await self.async_step_init()
        yaml_text = entry_to_yaml(
            dict(self.config_entry.options),
            [(sub.title, dict(sub.data)) for sub in self._trigger_subentries()],
        )
        return self.async_show_form(
            step_id="export_yaml",
            data_schema=self.add_suggested_values_to_schema(
                _yaml_schema(), {"yaml": yaml_text}
            ),
            description_placeholders={"yaml": yaml_text},
        )

    async def async_step_import_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a whole-entry YAML document to replace the camera config."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                options, triggers = await parse_entry_yaml(
                    self.hass, user_input.get("yaml", "")
                )
            except DuplicateTriggerNames as err:
                errors["base"] = "duplicate_names"
                placeholders["error"] = ", ".join(err.names)
            except InvalidConditionError as err:
                errors["base"] = "invalid_condition"
                placeholders["error"] = str(err)
            except vol.Invalid as err:
                errors["base"] = "invalid_yaml"
                placeholders["error"] = str(err)
            else:
                self._import_options = options
                self._import_triggers = triggers
                incoming = {name for name, _ in triggers}
                self._import_deletions = sorted(
                    sub.title
                    for sub in self._trigger_subentries()
                    if sub.title not in incoming
                )
                return await self.async_step_import_confirm()
        return self.async_show_form(
            step_id="import_yaml",
            data_schema=_yaml_schema(),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_import_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the full sync, listing triggers that will be deleted."""
        if user_input is not None:
            self._apply_import()
            return self._finish_with(self._import_options)
        deletions = (
            "\n".join(f"- {name}" for name in self._import_deletions)
            if self._import_deletions
            else "(none)"
        )
        return self.async_show_form(
            step_id="import_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "deletions": deletions,
                "kept": "\n".join(
                    f"- {name}" for name, _ in self._import_triggers
                )
                or "(none)",
            },
        )

    @callback
    def _apply_import(self) -> None:
        """Full sync: upsert incoming triggers (by name) and delete the rest."""
        existing = {sub.title: sub for sub in self._trigger_subentries()}
        incoming = {name for name, _ in self._import_triggers}
        for name, data in self._import_triggers:
            if (sub := existing.get(name)) is not None:
                self.hass.config_entries.async_update_subentry(
                    self.config_entry, sub, data=data, title=name
                )
            else:
                self.hass.config_entries.async_add_subentry(
                    self.config_entry,
                    ConfigSubentry(
                        data=data,
                        subentry_type=SUBENTRY_TYPE_TRIGGER,
                        title=name,
                        unique_id=None,
                    ),
                )
        for title, sub in existing.items():
            if title not in incoming:
                self.hass.config_entries.async_remove_subentry(
                    self.config_entry, sub.subentry_id
                )

    @callback
    def _finish(self) -> ConfigFlowResult:
        return self._finish_with(self._data)

    @callback
    def _finish_with(self, data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=prune_options(data))


class TriggerSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a trigger profile on a camera entry."""

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[str, Any] = {}
        # Working copy of the conditional cadence while editing it. The default
        # (else) rule is held separately; the persisted list is rules + default.
        self._rules: list[dict[str, Any]] = []
        self._default_rule: dict[str, Any] | None = None
        self._rules_loaded = False
        # Draft state for the two-hop rule editor.
        self._rule_draft: dict[str, Any] = {}
        self._rule_index: int | None = None
        self._rule_is_default = False

    @property
    def _is_new(self) -> bool:
        return self.source == SOURCE_USER

    @property
    def _editing(self) -> bool:
        """True when navigating the reconfigure hub rather than creating."""
        return not self._is_new

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new trigger: guided wizard or pasted YAML."""
        return self.async_show_menu(
            step_id="user", menu_options=["guided", "yaml_edit"]
        )

    async def async_step_guided(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Walk through the guided trigger wizard."""
        return await self._async_step_main(user_input, "guided")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing trigger from the editing hub."""
        if not self._data:
            subentry = self._get_reconfigure_subentry()
            self._data = dict(subentry.data)
            self._data[CONF_NAME] = subentry.title
        return await self.async_step_hub()

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
            if self._editing:
                return await self.async_step_hub()
            return await self._async_after_main()
        return self.async_show_form(
            step_id="custom_video",
            data_schema=self.add_suggested_values_to_schema(
                _custom_video_schema(), self._data or None
            ),
        )

    async def _async_after_main(self) -> SubentryFlowResult:
        """Continue with the cadence step for the chosen capture mode."""
        return await self.async_step_cadence()

    async def async_step_cadence(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Route to the cadence editor for the chosen capture mode."""
        cadence = self._data[CONF_CAPTURE_MODE]
        if cadence == CaptureMode.VALUE_CHANGE:
            return await self.async_step_value_change()
        if cadence == CaptureMode.TIME_FIT:
            return await self.async_step_fit_length()
        if cadence == CaptureMode.CONDITIONAL:
            self._load_rules()
            return await self.async_step_conditional_rules()
        return await self.async_step_interval()

    async def _after_cadence(self) -> SubentryFlowResult:
        """Return to the hub when editing, else continue the create wizard."""
        if self._editing:
            return await self.async_step_hub()
        return await self._async_next_trigger_step()

    async def _after_trigger_setup(self) -> SubentryFlowResult:
        """After schedule/watch: hub when editing, else the end-buffer step."""
        if self._editing:
            return await self.async_step_hub()
        return await self.async_step_end_buffer()

    async def _async_next_trigger_step(self) -> SubentryFlowResult:
        mode = self._data[CONF_TRIGGER_MODE]
        if mode == TriggerMode.SCHEDULE:
            return await self.async_step_schedule()
        if mode == TriggerMode.WATCH:
            return await self.async_step_watch()
        return self._finish()

    # --- Reconfigure hub: jump-edit one section at a time ------------------

    async def async_step_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Menu hub for editing a trigger without re-walking the wizard."""
        menu_options = ["basics", "cadence"]
        mode = self._data[CONF_TRIGGER_MODE]
        if mode == TriggerMode.SCHEDULE:
            menu_options.append("schedule")
        elif mode == TriggerMode.WATCH:
            menu_options.append("watch")
        if mode != TriggerMode.MANUAL:
            menu_options.append("end_buffer")
        menu_options.append("output")
        menu_options.append("yaml_export")
        menu_options.append("yaml_edit")
        menu_options.append("save")
        return self.async_show_menu(step_id="hub", menu_options=menu_options)

    async def async_step_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit name, trigger mode, capture cadence, and output naming."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_output_dir(self.hass, user_input)
            if not errors:
                old_cadence = self._data.get(CONF_CAPTURE_MODE)
                old_mode = self._data.get(CONF_TRIGGER_MODE)
                self._data.update(user_input)
                if self._data[CONF_CAPTURE_MODE] != old_cadence:
                    # Switching cadence invalidates the old cadence config;
                    # walk the user through the new one before returning.
                    self._reset_cadence()
                    return await self.async_step_cadence()
                if self._data[CONF_TRIGGER_MODE] != old_mode:
                    return await self._setup_trigger_mode()
                return await self.async_step_hub()
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="basics",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_basics_fields()), suggested or None
            ),
            errors=errors,
        )

    async def async_step_output(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit frame retention, purge policy, and quality overrides."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_scaling(user_input)
            if not errors:
                self._data.update(user_input)
                if self._data[CONF_VIDEO_QUALITY] == VideoQuality.CUSTOM:
                    return await self.async_step_custom_video()
                return await self.async_step_hub()
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="output",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_output_fields()), suggested or None
            ),
            errors=errors,
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Validate completeness, then persist the trigger and exit."""
        if (missing := self._missing_step()) is not None:
            return await missing()
        return self._finish()

    # --- YAML code view: export the trigger / create or replace from YAML ---

    def _current_trigger_yaml(self) -> str:
        """Render the working trigger as YAML (title + pruned data)."""
        data = dict(self._data)
        title = data.pop(CONF_NAME, "")
        return trigger_to_yaml(title, prune_trigger_data(data))

    async def async_step_yaml_export(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the current trigger as YAML; submitting returns to the hub."""
        if user_input is not None:
            return await self.async_step_hub()
        yaml_text = self._current_trigger_yaml()
        return self.async_show_form(
            step_id="yaml_export",
            data_schema=self.add_suggested_values_to_schema(
                _yaml_schema(), {"yaml": yaml_text}
            ),
            description_placeholders={"yaml": yaml_text},
        )

    async def async_step_yaml_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Create (new flow) or replace (reconfigure) the trigger from YAML."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        suggested: dict[str, Any] | None = None
        if user_input is not None:
            try:
                name, data = await parse_trigger_yaml(
                    self.hass, user_input.get("yaml", "")
                )
            except InvalidConditionError as err:
                errors["base"] = "invalid_condition"
                placeholders["error"] = str(err)
                suggested = user_input
            except vol.Invalid as err:
                errors["base"] = "invalid_yaml"
                placeholders["error"] = str(err)
                suggested = user_input
            else:
                self._data = {CONF_NAME: name, **data}
                return self._finish()
        elif self._editing:
            suggested = {"yaml": self._current_trigger_yaml()}
        return self.async_show_form(
            step_id="yaml_edit",
            data_schema=self.add_suggested_values_to_schema(
                _yaml_schema(), suggested
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _reset_cadence(self) -> None:
        """Drop every cadence-specific key and reset the rule working copy."""
        for key in (
            CONF_INTERVAL,
            CONF_DURATION_ENTITY,
            CONF_DURATION_TYPE,
            CONF_TARGET_LENGTH,
            CONF_FALLBACK_INTERVAL,
            CONF_VALUE_ENTITY,
            CONF_VALUE_DELTA,
            CONF_VALUE_DIRECTION,
            CONF_CONDITIONAL_RULES,
        ):
            self._data.pop(key, None)
        self._rules = []
        self._default_rule = None
        self._rules_loaded = True

    async def _setup_trigger_mode(self) -> SubentryFlowResult:
        """After a trigger-mode change, configure (or clear) its settings."""
        mode = self._data[CONF_TRIGGER_MODE]
        if mode == TriggerMode.SCHEDULE:
            return await self.async_step_schedule()
        if mode == TriggerMode.WATCH:
            return await self.async_step_watch()
        for key in (
            CONF_SCHEDULE_START,
            CONF_SCHEDULE_END,
            CONF_WATCH_ENTITY,
            CONF_WATCH_STATES,
            CONF_END_BUFFER_MODE,
            CONF_END_BUFFER_AMOUNT,
            CONF_END_BUFFER_INTERVAL,
            CONF_END_BUFFER_RETRIGGER,
        ):
            self._data.pop(key, None)
        return await self.async_step_hub()

    def _missing_step(self):
        """Return a step coroutine for required config the user hasn't set."""
        mode = self._data[CONF_TRIGGER_MODE]
        if mode == TriggerMode.SCHEDULE and not self._data.get(CONF_SCHEDULE_START):
            return self.async_step_schedule
        if mode == TriggerMode.WATCH and not self._data.get(CONF_WATCH_ENTITY):
            return self.async_step_watch
        cadence = self._data[CONF_CAPTURE_MODE]
        if cadence == CaptureMode.VALUE_CHANGE and not self._data.get(
            CONF_VALUE_ENTITY
        ):
            return self.async_step_value_change
        if cadence == CaptureMode.TIME_FIT and not self._data.get(
            CONF_DURATION_ENTITY
        ):
            return self.async_step_fit_length
        if cadence == CaptureMode.CONDITIONAL:
            rules = self._data.get(CONF_CONDITIONAL_RULES) or []
            if not rules or CONF_RULE_CONDITIONS in rules[-1]:
                return self.async_step_cadence
        return None

    async def async_step_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure the time between snapshots."""
        if user_input is not None:
            self._data.update(user_input)
            return await self._after_cadence()
        return self.async_show_form(
            step_id="interval",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_interval_fields()), self._data or None
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
                return await self._after_cadence()
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="fit_length",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_fit_fields()), suggested or None
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
                return await self._after_cadence()
        suggested = user_input if user_input is not None else self._data
        return self.async_show_form(
            step_id="value_change",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_value_fields()), suggested or None
            ),
            errors=errors,
        )

    # --- Conditional cadence: overview + two-hop rule editor ---------------

    def _load_rules(self) -> None:
        """Split the stored rule list into editable rules and a default."""
        if self._rules_loaded:
            return
        existing = list(self._data.get(CONF_CONDITIONAL_RULES) or [])
        if existing and CONF_RULE_CONDITIONS not in existing[-1]:
            self._default_rule = existing[-1]
            self._rules = existing[:-1]
        else:
            self._default_rule = None
            self._rules = existing
        self._rules_loaded = True

    def _rules_summary(self) -> str:
        """A readable, top-to-bottom map of the conditional decision tree."""
        lines: list[str] = []
        if not self._rules:
            lines.append("No conditional rules yet.")
        for index, rule in enumerate(self._rules, start=1):
            lines.append(
                f"{index}. IF {_describe_conditions(rule)} "
                f"→ {_describe_cadence(rule)}"
            )
        if self._default_rule is not None:
            lines.append(f"Otherwise → {_describe_cadence(self._default_rule)}")
        else:
            lines.append("Otherwise → not set yet (choose “Set the default cadence”)")
        return "\n".join(lines)

    def _rule_label(self) -> str:
        if self._rule_is_default:
            return "the default cadence"
        if self._rule_index is None:
            return f"new rule {len(self._rules) + 1}"
        return f"rule {self._rule_index + 1}"

    def _rule_select_schema(self) -> vol.Schema:
        options = [
            SelectOptionDict(
                value=str(index),
                label=f"{index + 1}. {_describe_conditions(rule)} "
                f"→ {_describe_cadence(rule)}",
            )
            for index, rule in enumerate(self._rules)
        ]
        return vol.Schema(
            {
                vol.Required(CONF_RULE_INDEX): SelectSelector(
                    SelectSelectorConfig(
                        options=options, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )

    async def async_step_conditional_rules(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Overview of the conditional cadence: see the whole tree, edit a part."""
        self._load_rules()
        menu_options = ["rule_add"]
        if self._rules:
            menu_options.append("rule_edit")
            menu_options.append("rule_delete")
        menu_options.append("rule_default")
        if self._default_rule is not None:
            menu_options.append("cadence_done")
        return self.async_show_menu(
            step_id="conditional_rules",
            menu_options=menu_options,
            description_placeholders={"rules_summary": self._rules_summary()},
        )

    async def async_step_cadence_done(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Persist the rule list and leave the conditional overview."""
        self._data[CONF_CONDITIONAL_RULES] = [*self._rules, self._default_rule]
        return await self._after_cadence()

    async def async_step_rule_add(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Start adding a new conditional rule."""
        self._rule_index = None
        self._rule_is_default = False
        self._rule_draft = {}
        return await self.async_step_rule_conditions()

    async def async_step_rule_default(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit the default (else) cadence."""
        self._rule_index = None
        self._rule_is_default = True
        self._rule_draft = dict(self._default_rule or {})
        return await self.async_step_rule_conditions()

    async def async_step_rule_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick which existing rule to edit."""
        if user_input is not None:
            self._rule_index = int(user_input[CONF_RULE_INDEX])
            self._rule_is_default = False
            self._rule_draft = dict(self._rules[self._rule_index])
            return await self.async_step_rule_conditions()
        return self.async_show_form(
            step_id="rule_edit", data_schema=self._rule_select_schema()
        )

    async def async_step_rule_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick which existing rule to delete."""
        if user_input is not None:
            del self._rules[int(user_input[CONF_RULE_INDEX])]
            return await self.async_step_conditional_rules()
        return self.async_show_form(
            step_id="rule_delete", data_schema=self._rule_select_schema()
        )

    async def async_step_rule_conditions(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """First hop: the rule's conditions and which cadence it uses."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not self._rule_is_default and not user_input.get(
                CONF_RULE_CONDITIONS
            ):
                errors[CONF_RULE_CONDITIONS] = "conditions_required"
            if not errors:
                if not self._rule_is_default:
                    self._rule_draft[CONF_RULE_CONDITIONS] = user_input[
                        CONF_RULE_CONDITIONS
                    ]
                self._rule_draft[CONF_CAPTURE_MODE] = user_input[CONF_CAPTURE_MODE]
                return await self.async_step_rule_cadence()
        suggested = user_input if user_input is not None else self._rule_draft
        if (
            suggested
            and not self._rule_is_default
            and suggested.get(CONF_RULE_CONDITIONS)
        ):
            suggested = {
                **suggested,
                CONF_RULE_CONDITIONS: _conditions_for_editing(
                    suggested[CONF_RULE_CONDITIONS]
                ),
            }
        return self.async_show_form(
            step_id="rule_conditions",
            data_schema=self.add_suggested_values_to_schema(
                _rule_conditions_schema(is_default=self._rule_is_default),
                suggested or None,
            ),
            errors=errors,
            description_placeholders={"rule_label": self._rule_label()},
        )

    async def async_step_rule_cadence(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Second hop: only the chosen cadence's settings."""
        mode = self._rule_draft[CONF_CAPTURE_MODE]
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**self._rule_draft, **user_input}
            errors = validate_rule(merged)
            if not errors:
                conditions = (
                    None
                    if self._rule_is_default
                    else self._rule_draft.get(CONF_RULE_CONDITIONS)
                )
                rule = build_rule(merged, conditions=conditions)
                if self._rule_is_default:
                    self._default_rule = rule
                elif self._rule_index is None:
                    self._rules.append(rule)
                else:
                    self._rules[self._rule_index] = rule
                return await self.async_step_conditional_rules()
        suggested = user_input if user_input is not None else self._rule_draft
        return self.async_show_form(
            step_id="rule_cadence",
            data_schema=self.add_suggested_values_to_schema(
                _rule_cadence_schema(mode), suggested or None
            ),
            errors=errors,
            description_placeholders={"rule_label": self._rule_label()},
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
                return await self._after_trigger_setup()
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
                return await self._after_trigger_setup()
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
                if self._editing:
                    return await self.async_step_hub()
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
        data = prune_trigger_data(data)
        if self._is_new:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
            title=title,
        )
