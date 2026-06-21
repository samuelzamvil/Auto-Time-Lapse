"""Canonical trigger/entry schema shared by the config flow and YAML I/O.

This module is the single source of truth for the persisted ``subentry.data``
shape. Both the config flow (``config_flow.py``) and the YAML import/export
front-end serialize to and from the helpers defined here, so the stored data
stays byte-for-byte compatible regardless of which front-end produced it.

It must not import ``config_flow`` (that would be circular); it only depends on
``const`` and the Home Assistant helpers it needs for condition validation.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import condition
import voluptuous as vol
import yaml

from .const import (
    CONF_AUTO_PURGE,
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
    DEFAULT_FALLBACK_INTERVAL,
    DEFAULT_INTERVAL,
    DEFAULT_TARGET_LENGTH,
    DEFAULT_VALUE_DELTA,
    FFMPEG_PRESETS,
    OPTION_SERVICE_DEFAULT,
    RULE_CAPTURE_MODES,
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

# Top-level keys of a whole-camera-entry YAML document.
YAML_KEY_OPTIONS = "options"
YAML_KEY_TRIGGERS = "triggers"


class DuplicateTriggerNames(Exception):
    """Raised when a whole-entry import has more than one trigger per name."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(", ".join(names))


class InvalidConditionError(vol.Invalid):
    """A conditional rule's conditions failed Home Assistant validation.

    Subclasses ``vol.Invalid`` so generic handling still catches it, while
    callers that want to can surface a condition-specific error message.
    """


# --- Rule build/validate (shared with the two-hop rule editor) -------------


def validate_rule(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the cadence settings of one conditional rule.

    The required entity fields are enforced by the cadence schema; only the
    value ranges need a custom check.
    """
    errors: dict[str, str] = {}
    if user_input[CONF_CAPTURE_MODE] == CaptureMode.VALUE_CHANGE:
        if float(user_input.get(CONF_VALUE_DELTA, DEFAULT_VALUE_DELTA)) <= 0:
            errors[CONF_VALUE_DELTA] = "delta_positive"
    elif user_input[CONF_CAPTURE_MODE] == CaptureMode.TIME_FIT:
        if float(user_input.get(CONF_TARGET_LENGTH, DEFAULT_TARGET_LENGTH)) <= 0:
            errors[CONF_TARGET_LENGTH] = "length_positive"
    return errors


def build_rule(
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


# --- Pruning (source of truth for the persisted shape) ---------------------


def prune_trigger_data(data: dict[str, Any]) -> dict[str, Any]:
    """Drop keys that don't apply to the chosen modes.

    This is the single definition of the persisted ``subentry.data`` shape
    (``CONF_NAME`` is expected to have already been split off into the title).
    Operates on a copy and is idempotent, so running it on data that is already
    pruned changes nothing.
    """
    data = dict(data)
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
    return data


def prune_options(data: dict[str, Any]) -> dict[str, Any]:
    """Drop quality keys that don't apply to the camera entry options."""
    data = dict(data)
    if data.get(CONF_VIDEO_QUALITY) != VideoQuality.CUSTOM:
        data.pop(CONF_VIDEO_CRF, None)
        data.pop(CONF_VIDEO_PRESET, None)
    if data.get(CONF_SCALE_MODE) == ScaleMode.OFF:
        data.pop(CONF_MAX_WIDTH, None)
    return data


# --- Validation schemas ----------------------------------------------------


def _enum(enum_cls, *extra: str) -> vol.In:
    return vol.In([member.value for member in enum_cls] + list(extra))


# A trigger as written in YAML: required name + modes, every other field
# optional and type-coerced. Unknown keys are preserved so forward-compatible
# or hand-added keys survive a round-trip.
TRIGGER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): vol.Coerce(str),
        vol.Required(CONF_TRIGGER_MODE): _enum(TriggerMode),
        vol.Required(CONF_CAPTURE_MODE): _enum(CaptureMode),
        vol.Optional(CONF_OUTPUT_FPS): vol.Coerce(int),
        vol.Optional(CONF_OUTPUT_DIR): vol.Coerce(str),
        vol.Optional(CONF_FILENAME_PATTERN): vol.Coerce(str),
        vol.Optional(CONF_KEEP_FRAMES): bool,
        vol.Optional(CONF_INTERVAL): vol.Coerce(int),
        vol.Optional(CONF_DURATION_ENTITY): vol.Coerce(str),
        vol.Optional(CONF_DURATION_TYPE): _enum(DurationType),
        vol.Optional(CONF_TARGET_LENGTH): vol.Coerce(float),
        vol.Optional(CONF_FALLBACK_INTERVAL): vol.Coerce(int),
        vol.Optional(CONF_VALUE_ENTITY): vol.Coerce(str),
        vol.Optional(CONF_VALUE_DELTA): vol.Coerce(float),
        vol.Optional(CONF_VALUE_DIRECTION): _enum(ValueDirection),
        vol.Optional(CONF_CONDITIONAL_RULES): [dict],
        vol.Optional(CONF_SCHEDULE_START): vol.Coerce(str),
        vol.Optional(CONF_SCHEDULE_END): vol.Coerce(str),
        vol.Optional(CONF_WATCH_ENTITY): vol.Coerce(str),
        vol.Optional(CONF_WATCH_STATES): [vol.Coerce(str)],
        vol.Optional(CONF_END_BUFFER_MODE): _enum(EndBufferMode),
        vol.Optional(CONF_END_BUFFER_AMOUNT): vol.Coerce(int),
        vol.Optional(CONF_END_BUFFER_INTERVAL): vol.Coerce(int),
        vol.Optional(CONF_END_BUFFER_RETRIGGER): _enum(BufferRetrigger),
        vol.Optional(CONF_VIDEO_QUALITY): _enum(VideoQuality, OPTION_SERVICE_DEFAULT),
        vol.Optional(CONF_VIDEO_CRF): vol.Coerce(int),
        vol.Optional(CONF_VIDEO_PRESET): vol.In(list(FFMPEG_PRESETS)),
        vol.Optional(CONF_SCALE_MODE): _enum(ScaleMode, OPTION_SERVICE_DEFAULT),
        vol.Optional(CONF_MAX_WIDTH): vol.Coerce(int),
        vol.Optional(CONF_AUTO_PURGE): bool,
        vol.Optional(CONF_PURGE_MODE): _enum(PurgeMode),
        vol.Optional(CONF_PURGE_KEEP_SESSIONS): vol.Coerce(int),
        vol.Optional(CONF_PURGE_MAX_AGE_DAYS): vol.Coerce(int),
    },
    extra=vol.ALLOW_EXTRA,
)

# Camera-entry options as written in YAML.
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_VIDEO_QUALITY): _enum(VideoQuality, OPTION_SERVICE_DEFAULT),
        vol.Optional(CONF_VIDEO_CRF): vol.Coerce(int),
        vol.Optional(CONF_VIDEO_PRESET): vol.In(list(FFMPEG_PRESETS)),
        vol.Optional(CONF_SCALE_MODE): _enum(ScaleMode, OPTION_SERVICE_DEFAULT),
        vol.Optional(CONF_MAX_WIDTH): vol.Coerce(int),
    },
    extra=vol.ALLOW_EXTRA,
)

_RULE_MODE_VALUES = [mode.value for mode in RULE_CAPTURE_MODES]


def _normalize_rule(rule: Any) -> dict[str, Any]:
    """Validate one conditional rule and rebuild it into the stored shape."""
    if not isinstance(rule, dict):
        raise vol.Invalid("each conditional rule must be a mapping")
    mode = rule.get(CONF_CAPTURE_MODE)
    if mode not in _RULE_MODE_VALUES:
        raise vol.Invalid(
            f"conditional rule capture_mode must be one of {_RULE_MODE_VALUES}, "
            f"got {mode!r}"
        )
    if errors := validate_rule(rule):
        raise vol.Invalid(f"invalid conditional rule cadence: {errors}")
    conditions = rule.get(CONF_RULE_CONDITIONS)
    if conditions is not None and not isinstance(conditions, list):
        raise vol.Invalid("conditional rule 'conditions' must be a list")
    try:
        return build_rule(rule, conditions=conditions)
    except KeyError as err:
        raise vol.Invalid(
            f"conditional rule for capture_mode {mode!r} is missing {err}"
        ) from err


async def _async_validate_conditions(
    hass: HomeAssistant, data: dict[str, Any]
) -> None:
    """Validate each rule's conditions the way the manager does at runtime."""
    for index, rule in enumerate(data.get(CONF_CONDITIONAL_RULES) or []):
        conditions = rule.get(CONF_RULE_CONDITIONS)
        if not conditions:
            continue
        config = {"condition": "and", "conditions": conditions}
        try:
            await condition.async_validate_condition_config(hass, config)
        except (vol.Invalid, HomeAssistantError, KeyError, ValueError) as err:
            # Any failure to validate the user's conditions means the rule is
            # invalid; surface it now instead of letting it crash at runtime.
            raise InvalidConditionError(
                f"invalid conditions in conditional rule {index + 1}: {err}"
            ) from err


async def _async_parse_trigger_mapping(
    hass: HomeAssistant, raw: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Validate, normalize and prune a single trigger mapping from YAML."""
    data = dict(TRIGGER_SCHEMA(raw))
    if CONF_CONDITIONAL_RULES in data:
        data[CONF_CONDITIONAL_RULES] = [
            _normalize_rule(rule) for rule in data[CONF_CONDITIONAL_RULES]
        ]
    await _async_validate_conditions(hass, data)
    name = data.pop(CONF_NAME)
    return name, prune_trigger_data(data)


# --- YAML serialization ----------------------------------------------------

# Known keys in a readable order; any remaining keys are appended verbatim so
# nothing in ``subentry.data`` is ever dropped on export.
_TRIGGER_KEY_ORDER = (
    CONF_NAME,
    CONF_TRIGGER_MODE,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_END,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    CONF_CAPTURE_MODE,
    CONF_INTERVAL,
    CONF_DURATION_ENTITY,
    CONF_DURATION_TYPE,
    CONF_TARGET_LENGTH,
    CONF_FALLBACK_INTERVAL,
    CONF_VALUE_ENTITY,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_CONDITIONAL_RULES,
    CONF_END_BUFFER_MODE,
    CONF_END_BUFFER_AMOUNT,
    CONF_END_BUFFER_INTERVAL,
    CONF_END_BUFFER_RETRIGGER,
    CONF_OUTPUT_FPS,
    CONF_OUTPUT_DIR,
    CONF_FILENAME_PATTERN,
    CONF_KEEP_FRAMES,
    CONF_AUTO_PURGE,
    CONF_PURGE_MODE,
    CONF_PURGE_KEEP_SESSIONS,
    CONF_PURGE_MAX_AGE_DAYS,
    CONF_VIDEO_QUALITY,
    CONF_VIDEO_CRF,
    CONF_VIDEO_PRESET,
    CONF_SCALE_MODE,
    CONF_MAX_WIDTH,
)

_OPTIONS_KEY_ORDER = (
    CONF_VIDEO_QUALITY,
    CONF_VIDEO_CRF,
    CONF_VIDEO_PRESET,
    CONF_SCALE_MODE,
    CONF_MAX_WIDTH,
)


def _ordered(data: dict[str, Any], key_order: tuple[str, ...]) -> dict[str, Any]:
    """Order known keys for readability, then append the rest verbatim."""
    out: dict[str, Any] = {}
    for key in key_order:
        if key in data:
            out[key] = data[key]
    for key, value in data.items():
        if key not in out:
            out[key] = value
    return out


def _trigger_yaml_dict(title: str, data: dict[str, Any]) -> dict[str, Any]:
    return _ordered({CONF_NAME: title, **data}, _TRIGGER_KEY_ORDER)


def _dump(data: Any) -> str:
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def trigger_to_yaml(title: str, data: dict[str, Any]) -> str:
    """Render one trigger (title + ``subentry.data``) as a YAML document."""
    return _dump(_trigger_yaml_dict(title, data))


def entry_to_yaml(
    options: dict[str, Any], triggers: list[tuple[str, dict[str, Any]]]
) -> str:
    """Render a whole camera entry (options + all triggers) as one document."""
    doc = {
        YAML_KEY_OPTIONS: _ordered(dict(options), _OPTIONS_KEY_ORDER),
        YAML_KEY_TRIGGERS: [
            _trigger_yaml_dict(title, data) for title, data in triggers
        ],
    }
    return _dump(doc)


# --- YAML parsing ----------------------------------------------------------


def _load_mapping(text: str, what: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise vol.Invalid(f"could not parse YAML: {err}") from err
    if not isinstance(raw, dict):
        raise vol.Invalid(f"YAML must be a mapping of {what}")
    return raw


async def parse_trigger_yaml(
    hass: HomeAssistant, text: str
) -> tuple[str, dict[str, Any]]:
    """Parse a per-trigger YAML document into ``(title, subentry.data)``."""
    raw = _load_mapping(text, "trigger settings")
    return await _async_parse_trigger_mapping(hass, raw)


async def parse_entry_yaml(
    hass: HomeAssistant, text: str
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Parse a whole-entry YAML document into ``(options, [(title, data)])``.

    Trigger names must be unique within the document; otherwise a full sync
    could not tell which existing trigger each one maps to.
    """
    raw = _load_mapping(text, "'options' and 'triggers'")
    raw_options = raw.get(YAML_KEY_OPTIONS) or {}
    if not isinstance(raw_options, dict):
        raise vol.Invalid("'options' must be a mapping")
    options = prune_options(dict(OPTIONS_SCHEMA(raw_options)))
    raw_triggers = raw.get(YAML_KEY_TRIGGERS) or []
    if not isinstance(raw_triggers, list):
        raise vol.Invalid("'triggers' must be a list")
    triggers: list[tuple[str, dict[str, Any]]] = []
    for item in raw_triggers:
        if not isinstance(item, dict):
            raise vol.Invalid("each trigger must be a mapping")
        triggers.append(await _async_parse_trigger_mapping(hass, item))
    names = [name for name, _ in triggers]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        raise DuplicateTriggerNames(duplicates)
    return options, triggers
