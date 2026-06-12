"""Tests for the Auto Time Lapse config and subentry flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.auto_time_lapse.const import (
    CONF_CAMERA_ENTITY,
    CONF_CAPTURE_MODE,
    CONF_CONDITIONAL_REEVALUATE,
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
    DOMAIN,
    OPTION_SERVICE_DEFAULT,
    SUBENTRY_TYPE_TRIGGER,
    BufferRetrigger,
    CaptureMode,
    DurationType,
    EndBufferMode,
    ScaleMode,
    TriggerMode,
    ValueDirection,
    VideoQuality,
)

from .conftest import make_entry

BUFFER_KEYS = (
    CONF_END_BUFFER_MODE,
    CONF_END_BUFFER_AMOUNT,
    CONF_END_BUFFER_INTERVAL,
    CONF_END_BUFFER_RETRIGGER,
)

TRIGGER_INPUT = {
    CONF_NAME: "Garden",
    CONF_TRIGGER_MODE: TriggerMode.MANUAL.value,
    CONF_CAPTURE_MODE: CaptureMode.TIME.value,
    CONF_OUTPUT_FPS: 24,
    CONF_FILENAME_PATTERN: "{name}_{timestamp}.mp4",
    CONF_KEEP_FRAMES: False,
}


async def test_user_flow_creates_camera_entry(hass):
    """The user flow creates one entry per camera."""
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden Cam"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.auto_time_lapse.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CAMERA_ENTITY: "camera.garden"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garden Cam"
    assert result["data"] == {CONF_CAMERA_ENTITY: "camera.garden"}


async def test_user_flow_aborts_on_duplicate_camera(hass, mock_entry):
    """Adding the same camera twice is rejected."""
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CAMERA_ENTITY: "camera.demo"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _setup_loaded_entry(hass, mock_entry):
    mock_entry.add_to_hass(hass)
    with patch(
        "custom_components.auto_time_lapse.async_setup_entry", return_value=True
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()


async def _start_trigger_flow(hass, mock_entry):
    return await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_TRIGGER),
        context={"source": config_entries.SOURCE_USER},
    )


async def _pass_interval_step(hass, result, interval: int = 30):
    """Complete the time-interval cadence step."""
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "interval"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_INTERVAL: interval}
    )


async def _pass_end_buffer_step(hass, result, user_input: dict | None = None):
    """Complete the end-buffer step, disabled unless input is given."""
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "end_buffer"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input or {CONF_END_BUFFER_MODE: EndBufferMode.OFF.value},
    )


async def test_trigger_subentry_manual(hass, mock_entry):
    """A manual trigger completes after the main step."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TRIGGER_INPUT)
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s
        for s in mock_entry.subentries.values()
        if s.subentry_type == SUBENTRY_TYPE_TRIGGER and s.title == "Garden"
    )
    assert subentry.data[CONF_TRIGGER_MODE] == TriggerMode.MANUAL.value
    assert subentry.data[CONF_INTERVAL] == 30
    assert CONF_VALUE_ENTITY not in subentry.data
    assert CONF_NAME not in subentry.data


async def test_trigger_subentry_rejects_disallowed_path(hass, mock_entry):
    """A custom output dir outside the allowed paths is rejected."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    with patch.object(hass.config, "is_allowed_path", return_value=False):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], dict(TRIGGER_INPUT) | {CONF_OUTPUT_DIR: "/nope"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_OUTPUT_DIR: "path_not_allowed"}


async def test_trigger_subentry_schedule(hass, mock_entry):
    """The schedule mode adds a second step with time validation."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value},
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SCHEDULE_START: "08:00:00", CONF_SCHEDULE_END: "08:00:00"},
    )
    assert result["errors"] == {CONF_SCHEDULE_END: "schedule_start_equals_end"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SCHEDULE_START: "08:00:00", CONF_SCHEDULE_END: "20:00:00"},
    )
    result = await _pass_end_buffer_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_SCHEDULE_START] == "08:00:00"
    assert subentry.data[CONF_SCHEDULE_END] == "20:00:00"
    # A disabled buffer stores none of its keys.
    assert not any(key in subentry.data for key in BUFFER_KEYS)


async def test_trigger_subentry_watch(hass, mock_entry):
    """The watch mode asks for an entity, then its active states."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_TRIGGER_MODE: TriggerMode.WATCH.value},
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "watch"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH_ENTITY: "sensor.printer_status"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "watch_states"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH_STATES: ["printing", "paused"]}
    )
    result = await _pass_end_buffer_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_WATCH_ENTITY] == "sensor.printer_status"
    assert subentry.data[CONF_WATCH_STATES] == ["printing", "paused"]


async def test_trigger_subentry_value_change(hass, mock_entry):
    """The value-change cadence asks for entity, step, and direction."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "value_change"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 0,
            CONF_VALUE_DIRECTION: ValueDirection.ANY.value,
        },
    )
    assert result["errors"] == {CONF_VALUE_DELTA: "delta_positive"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1,
            CONF_VALUE_DIRECTION: ValueDirection.INCREASE.value,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_CAPTURE_MODE] == CaptureMode.VALUE_CHANGE.value
    assert subentry.data[CONF_VALUE_ENTITY] == "sensor.current_layer"
    assert subentry.data[CONF_VALUE_DELTA] == 1
    assert subentry.data[CONF_VALUE_DIRECTION] == ValueDirection.INCREASE.value
    assert CONF_INTERVAL not in subentry.data
    assert CONF_DURATION_TYPE not in subentry.data


async def test_trigger_subentry_fit_length(hass, mock_entry):
    """The fit-length cadence asks for a duration entity, target, and fallback."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_CAPTURE_MODE: CaptureMode.TIME_FIT.value},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "fit_length"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_DURATION_ENTITY: "sensor.print_duration",
            CONF_DURATION_TYPE: DurationType.MINUTES.value,
            CONF_TARGET_LENGTH: 0,
            CONF_FALLBACK_INTERVAL: 30,
        },
    )
    assert result["errors"] == {CONF_TARGET_LENGTH: "length_positive"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_DURATION_ENTITY: "sensor.print_duration",
            CONF_DURATION_TYPE: DurationType.MINUTES.value,
            CONF_TARGET_LENGTH: 10,
            CONF_FALLBACK_INTERVAL: 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_CAPTURE_MODE] == CaptureMode.TIME_FIT.value
    assert subentry.data[CONF_DURATION_ENTITY] == "sensor.print_duration"
    assert subentry.data[CONF_DURATION_TYPE] == DurationType.MINUTES.value
    assert subentry.data[CONF_TARGET_LENGTH] == 10
    assert subentry.data[CONF_FALLBACK_INTERVAL] == 30
    assert CONF_INTERVAL not in subentry.data
    assert CONF_VALUE_ENTITY not in subentry.data


async def test_trigger_subentry_reconfigure(hass, mock_entry):
    """Reconfiguring a trigger updates its data and strips stale mode keys."""
    await _setup_loaded_entry(hass, mock_entry)
    subentry_id = next(iter(mock_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_TRIGGER),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT)
        | {
            CONF_NAME: "Renamed",
            CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value,
        },
    )
    result = await _pass_interval_step(hass, result)
    assert result["step_id"] == "schedule"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SCHEDULE_START: "06:00:00", CONF_SCHEDULE_END: "20:00:00"},
    )
    result = await _pass_end_buffer_step(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    subentry = mock_entry.subentries[subentry_id]
    assert subentry.title == "Renamed"
    assert subentry.data[CONF_TRIGGER_MODE] == TriggerMode.SCHEDULE.value
    assert subentry.data[CONF_SCHEDULE_START] == "06:00:00"


async def test_trigger_subentry_manual_skips_buffer_step(hass, mock_entry):
    """A manual trigger never shows the end-buffer step."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TRIGGER_INPUT)
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert not any(key in subentry.data for key in BUFFER_KEYS)


async def test_trigger_subentry_schedule_with_buffer(hass, mock_entry):
    """An enabled buffer stores its mode, amount, interval, and retrigger."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value},
    )
    result = await _pass_interval_step(hass, result)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SCHEDULE_START: "08:00:00", CONF_SCHEDULE_END: "20:00:00"},
    )
    result = await _pass_end_buffer_step(
        hass,
        result,
        {
            CONF_END_BUFFER_MODE: EndBufferMode.FRAMES.value,
            CONF_END_BUFFER_AMOUNT: 5,
            CONF_END_BUFFER_INTERVAL: 2,
            CONF_END_BUFFER_RETRIGGER: BufferRetrigger.FINISH.value,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_END_BUFFER_MODE] == EndBufferMode.FRAMES.value
    assert subentry.data[CONF_END_BUFFER_AMOUNT] == 5
    assert subentry.data[CONF_END_BUFFER_INTERVAL] == 2
    assert (
        subentry.data[CONF_END_BUFFER_RETRIGGER] == BufferRetrigger.FINISH.value
    )


async def test_buffer_requires_interval_for_value_change(hass, mock_entry):
    """The value-change cadence needs an override interval for the buffer."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT)
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1,
            CONF_VALUE_DIRECTION: ValueDirection.ANY.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH_ENTITY: "sensor.printer_status"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH_STATES: ["printing"]}
    )
    assert result["step_id"] == "end_buffer"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value},
    )
    assert result["errors"] == {
        CONF_END_BUFFER_INTERVAL: "buffer_interval_required"
    }

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_INTERVAL: 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_END_BUFFER_INTERVAL] == 10


LAYER_BELOW_20 = [
    {
        "condition": "numeric_state",
        "entity_id": "sensor.current_layer",
        "below": 20,
    }
]
LAYER_BELOW_40 = [
    {
        "condition": "numeric_state",
        "entity_id": "sensor.current_layer",
        "below": 40,
    }
]


async def test_trigger_subentry_conditional(hass, mock_entry):
    """The conditional cadence collects rules and a default, in order."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_CAPTURE_MODE: CaptureMode.CONDITIONAL.value},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "conditional_rule"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_RULE_CONDITIONS: LAYER_BELOW_20,
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 30,
            CONF_RULE_ADD_ANOTHER: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "conditional_rule"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_RULE_CONDITIONS: LAYER_BELOW_40,
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 60,
            CONF_RULE_ADD_ANOTHER: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "conditional_default"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1,
            CONF_VALUE_DIRECTION: ValueDirection.INCREASE.value,
            CONF_CONDITIONAL_REEVALUATE: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_CAPTURE_MODE] == CaptureMode.CONDITIONAL.value
    assert subentry.data[CONF_CONDITIONAL_REEVALUATE] is True
    rules = subentry.data[CONF_CONDITIONAL_RULES]
    assert len(rules) == 3
    # The selector normalizes condition configs (entity_id becomes a list).
    (condition_0,) = rules[0][CONF_RULE_CONDITIONS]
    assert condition_0["condition"] == "numeric_state"
    assert condition_0["entity_id"] == ["sensor.current_layer"]
    assert condition_0["below"] == 20
    assert rules[0][CONF_CAPTURE_MODE] == CaptureMode.TIME.value
    assert rules[0][CONF_INTERVAL] == 30
    assert CONF_VALUE_ENTITY not in rules[0]
    assert rules[1][CONF_INTERVAL] == 60
    # The default rule has no conditions and only value-change keys.
    assert CONF_RULE_CONDITIONS not in rules[2]
    assert rules[2][CONF_CAPTURE_MODE] == CaptureMode.VALUE_CHANGE.value
    assert rules[2][CONF_VALUE_ENTITY] == "sensor.current_layer"
    assert CONF_INTERVAL not in rules[2]
    # No per-rule cadence keys leak to the top level.
    assert CONF_INTERVAL not in subentry.data
    assert CONF_VALUE_ENTITY not in subentry.data


async def test_conditional_rule_validation(hass, mock_entry):
    """Rules need conditions, and value-change rules need entity and step."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_CAPTURE_MODE: CaptureMode.CONDITIONAL.value},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_RULE_CONDITIONS: [],
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_DELTA: 0,
            CONF_RULE_ADD_ANOTHER: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        CONF_RULE_CONDITIONS: "conditions_required",
        CONF_VALUE_ENTITY: "value_entity_required",
        CONF_VALUE_DELTA: "delta_positive",
    }

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_RULE_CONDITIONS: LAYER_BELOW_20,
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 30,
            CONF_RULE_ADD_ANOTHER: False,
        },
    )
    assert result["step_id"] == "conditional_default"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_DELTA: 0,
            CONF_CONDITIONAL_REEVALUATE: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        CONF_VALUE_ENTITY: "value_entity_required",
        CONF_VALUE_DELTA: "delta_positive",
    }


async def test_buffer_requires_interval_for_conditional_value_rule(
    hass, mock_entry
):
    """A conditional cadence with a value-change rule needs a buffer interval."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT)
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_CAPTURE_MODE: CaptureMode.CONDITIONAL.value,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_RULE_CONDITIONS: LAYER_BELOW_20,
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 30,
            CONF_RULE_ADD_ANOTHER: False,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1,
            CONF_VALUE_DIRECTION: ValueDirection.ANY.value,
            CONF_CONDITIONAL_REEVALUATE: True,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH_ENTITY: "sensor.printer_status"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH_STATES: ["printing"]}
    )
    assert result["step_id"] == "end_buffer"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value},
    )
    assert result["errors"] == {
        CONF_END_BUFFER_INTERVAL: "buffer_interval_required"
    }

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_INTERVAL: 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_conditional_to_time_strips_rules(hass):
    """Switching a conditional trigger to a plain cadence drops the rules."""
    entry = make_entry(
        {
            CONF_TRIGGER_MODE: TriggerMode.MANUAL.value,
            CONF_CAPTURE_MODE: CaptureMode.CONDITIONAL.value,
            CONF_CONDITIONAL_RULES: [
                {
                    CONF_RULE_CONDITIONS: LAYER_BELOW_20,
                    CONF_CAPTURE_MODE: CaptureMode.TIME.value,
                    CONF_INTERVAL: 30,
                },
                {
                    CONF_CAPTURE_MODE: CaptureMode.TIME.value,
                    CONF_INTERVAL: 60,
                },
            ],
            CONF_CONDITIONAL_REEVALUATE: True,
            CONF_OUTPUT_FPS: 30,
            CONF_FILENAME_PATTERN: "{name}_{timestamp}.mp4",
            CONF_KEEP_FRAMES: False,
        },
        title="Conditional",
    )
    await _setup_loaded_entry(hass, entry)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_TRIGGER),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_NAME: "Conditional"},
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    subentry = entry.subentries[subentry_id]
    assert subentry.data[CONF_CAPTURE_MODE] == CaptureMode.TIME.value
    assert subentry.data[CONF_INTERVAL] == 30
    assert CONF_CONDITIONAL_RULES not in subentry.data
    assert CONF_CONDITIONAL_REEVALUATE not in subentry.data


QUALITY_KEYS = (
    CONF_VIDEO_QUALITY,
    CONF_VIDEO_CRF,
    CONF_VIDEO_PRESET,
    CONF_SCALE_MODE,
    CONF_MAX_WIDTH,
)


async def test_options_flow_preset_quality(hass, mock_entry):
    """A preset quality level saves without raw encoder keys."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_VIDEO_QUALITY: VideoQuality.HIGH.value,
            CONF_SCALE_MODE: ScaleMode.RENDER.value,
            CONF_MAX_WIDTH: 1280,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(mock_entry.options) == {
        CONF_VIDEO_QUALITY: VideoQuality.HIGH.value,
        CONF_SCALE_MODE: ScaleMode.RENDER.value,
        CONF_MAX_WIDTH: 1280,
    }


async def test_options_flow_custom_quality(hass, mock_entry):
    """The custom quality level adds a step for raw CRF and preset."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_VIDEO_QUALITY: VideoQuality.CUSTOM.value,
            CONF_SCALE_MODE: ScaleMode.OFF.value,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "custom_video"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_VIDEO_CRF: 18, CONF_VIDEO_PRESET: "slow"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(mock_entry.options) == {
        CONF_VIDEO_QUALITY: VideoQuality.CUSTOM.value,
        CONF_SCALE_MODE: ScaleMode.OFF.value,
        CONF_VIDEO_CRF: 18,
        CONF_VIDEO_PRESET: "slow",
    }


async def test_options_flow_requires_max_width(hass, mock_entry):
    """An enabled scale mode without a width re-shows the form."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_VIDEO_QUALITY: VideoQuality.MEDIUM.value,
            CONF_SCALE_MODE: ScaleMode.CAPTURE.value,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MAX_WIDTH: "max_width_required"}


async def test_options_flow_prunes_width_when_off(hass, mock_entry):
    """A width entered with scaling off is not stored."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_VIDEO_QUALITY: VideoQuality.MEDIUM.value,
            CONF_SCALE_MODE: ScaleMode.OFF.value,
            CONF_MAX_WIDTH: 640,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_MAX_WIDTH not in mock_entry.options


async def test_trigger_custom_quality_step(hass, mock_entry):
    """A custom quality override inserts the encoder step before the cadence."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_VIDEO_QUALITY: VideoQuality.CUSTOM.value},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "custom_video"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_VIDEO_CRF: 28, CONF_VIDEO_PRESET: "veryfast"},
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_VIDEO_QUALITY] == VideoQuality.CUSTOM.value
    assert subentry.data[CONF_VIDEO_CRF] == 28
    assert subentry.data[CONF_VIDEO_PRESET] == "veryfast"
    assert CONF_SCALE_MODE not in subentry.data


async def test_trigger_quality_defaults_store_nothing(hass, mock_entry):
    """Leaving every override at 'use service default' stores no quality keys."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(TRIGGER_INPUT)
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert not any(key in subentry.data for key in QUALITY_KEYS)


async def test_trigger_explicit_scale_off_is_kept(hass, mock_entry):
    """An explicit off override survives, unlike the inherit sentinel."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT)
        | {
            CONF_VIDEO_QUALITY: OPTION_SERVICE_DEFAULT,
            CONF_SCALE_MODE: ScaleMode.OFF.value,
            CONF_MAX_WIDTH: 640,
        },
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_SCALE_MODE] == ScaleMode.OFF.value
    assert CONF_MAX_WIDTH not in subentry.data
    assert CONF_VIDEO_QUALITY not in subentry.data


async def test_trigger_main_requires_max_width(hass, mock_entry):
    """A trigger-level scale mode without a width errors on the main step."""
    await _setup_loaded_entry(hass, mock_entry)
    result = await _start_trigger_flow(hass, mock_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_SCALE_MODE: ScaleMode.RENDER.value},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MAX_WIDTH: "max_width_required"}


async def test_reconfigure_to_manual_strips_buffer_keys(hass):
    """Switching a buffered schedule trigger to manual drops the buffer."""
    entry = make_entry(
        {
            CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value,
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 60,
            CONF_OUTPUT_FPS: 30,
            CONF_FILENAME_PATTERN: "{name}_{timestamp}.mp4",
            CONF_KEEP_FRAMES: False,
            CONF_SCHEDULE_START: "08:00:00",
            CONF_SCHEDULE_END: "20:00:00",
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_AMOUNT: 30,
            CONF_END_BUFFER_RETRIGGER: BufferRetrigger.RESUME.value,
        },
        title="Buffered",
    )
    await _setup_loaded_entry(hass, entry)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_TRIGGER),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        dict(TRIGGER_INPUT) | {CONF_NAME: "Buffered"},
    )
    result = await _pass_interval_step(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    subentry = entry.subentries[subentry_id]
    assert subentry.data[CONF_TRIGGER_MODE] == TriggerMode.MANUAL.value
    assert not any(key in subentry.data for key in BUFFER_KEYS)
