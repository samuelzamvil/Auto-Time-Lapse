"""Tests for the Auto Time Lapse config and subentry flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.auto_time_lapse.const import (
    CONF_CAMERA_ENTITY,
    CONF_CAPTURE_MODE,
    CONF_DURATION_ENTITY,
    CONF_FALLBACK_INTERVAL,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_TARGET_LENGTH,
    CONF_TRIGGER_MODE,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_VALUE_ENTITY,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DOMAIN,
    SUBENTRY_TYPE_TRIGGER,
    CaptureMode,
    TriggerMode,
    ValueDirection,
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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(
        s for s in mock_entry.subentries.values() if s.title == "Garden"
    )
    assert subentry.data[CONF_SCHEDULE_START] == "08:00:00"
    assert subentry.data[CONF_SCHEDULE_END] == "20:00:00"


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
            CONF_TARGET_LENGTH: 0,
            CONF_FALLBACK_INTERVAL: 30,
        },
    )
    assert result["errors"] == {CONF_TARGET_LENGTH: "length_positive"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_DURATION_ENTITY: "sensor.print_duration",
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
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    subentry = mock_entry.subentries[subentry_id]
    assert subentry.title == "Renamed"
    assert subentry.data[CONF_TRIGGER_MODE] == TriggerMode.SCHEDULE.value
    assert subentry.data[CONF_SCHEDULE_START] == "06:00:00"
