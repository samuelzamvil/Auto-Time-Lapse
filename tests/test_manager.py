"""Tests for the timelapse session manager."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.auto_time_lapse.const import (
    ATTR_DEVICE_ID,
    CONF_CAPTURE_MODE,
    CONF_DURATION_ENTITY,
    CONF_FALLBACK_INTERVAL,
    CONF_TARGET_LENGTH,
    CONF_TRIGGER_MODE,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_VALUE_ENTITY,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DOMAIN,
    EVENT_TIMELAPSE_FINISHED,
    SERVICE_CANCEL,
    SERVICE_START,
    SERVICE_STOP,
    CaptureMode,
    SessionState,
    TriggerMode,
    ValueDirection,
)

from .conftest import (
    TEST_SUBENTRY_ID,
    get_device_id,
    get_manager,
    make_entry,
    setup_integration,
)


def _frames_dir(tmp_path: Path) -> Path:
    return tmp_path / DOMAIN / TEST_SUBENTRY_ID


async def test_capture_stop_render_cycle(
    hass, mock_entry, mock_camera_image, mock_render, tmp_path
):
    """Start captures frames on an interval; stop renders exactly once."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)

    events = []
    hass.bus.async_listen(EVENT_TIMELAPSE_FINISHED, events.append)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1  # first frame captured immediately

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2
    assert len(list(_frames_dir(tmp_path).rglob("*.jpg"))) == 2

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_render.assert_called_once()
    assert manager.state is SessionState.IDLE
    assert manager.last_video_path is not None
    assert manager.last_video_path.endswith(".mp4")
    assert len(events) == 1
    assert events[0].data["frame_count"] == 2
    assert events[0].data["subentry_id"] == TEST_SUBENTRY_ID
    # Frames are cleaned up after a successful render (keep_frames is off).
    assert not list(_frames_dir(tmp_path).rglob("*.jpg"))


async def test_camera_failure_skips_frame(
    hass, mock_entry, mock_camera_image, mock_render
):
    """A failed snapshot is skipped and counted; the session continues."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)

    mock_camera_image.side_effect = HomeAssistantError("camera unavailable")
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 0
    assert manager.failed_frame_count == 1

    # Camera recovers; capture continues with a contiguous frame sequence.
    mock_camera_image.side_effect = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1
    assert manager.failed_frame_count == 1


async def test_cancel_discards_frames(
    hass, mock_entry, mock_camera_image, mock_render, tmp_path
):
    """Cancel stops the session, deletes frames, and renders nothing."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_not_called()
    assert not list(_frames_dir(tmp_path).rglob("*.jpg"))


async def test_watch_custom_states(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """The watch trigger follows custom active states, e.g. a printer."""
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "sensor.printer_status",
            CONF_WATCH_STATES: ["printing", "paused"],
        },
        title="Print Watch",
    )
    hass.states.async_set("sensor.printer_status", "idle")
    await setup_integration(hass, entry)
    manager = get_manager(entry)

    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING

    # Moving between active states does not stop the session.
    hass.states.async_set("sensor.printer_status", "paused")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING
    mock_render.assert_not_called()

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_watch_unavailable_completes_video(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Going unavailable mid-session stops capture and renders the video."""
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "sensor.printer_status",
            CONF_WATCH_STATES: ["printing"],
        },
        title="Print Watch",
    )
    hass.states.async_set("sensor.printer_status", "idle")
    await setup_integration(hass, entry)
    manager = get_manager(entry)

    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING

    hass.states.async_set("sensor.printer_status", STATE_UNAVAILABLE)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_watch_already_active_at_setup(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A watch entity already in an active state starts capture on setup."""
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "input_boolean.motion",
        },
        title="Watched",
    )
    hass.states.async_set("input_boolean.motion", STATE_ON)
    await setup_integration(hass, entry)
    assert get_manager(entry).state is SessionState.CAPTURING


async def test_value_change_cadence(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Frames follow a numeric entity (e.g. one frame per printer layer)."""
    entry = make_entry(
        base_trigger_data
        | {
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1.0,
            CONF_VALUE_DIRECTION: ValueDirection.ANY.value,
        },
        title="Layer Lapse",
    )
    hass.states.async_set("sensor.current_layer", "0")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1  # immediate first frame

    hass.states.async_set("sensor.current_layer", "1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    # Below the step: no frame.
    hass.states.async_set("sensor.current_layer", "1.5")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    hass.states.async_set("sensor.current_layer", "2")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3

    # Non-numeric values are ignored.
    hass.states.async_set("sensor.current_layer", "unavailable")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3
    assert manager.state is SessionState.CAPTURING


async def test_value_change_increase_rebaselines_on_reset(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """With direction=increase, a counter reset re-baselines silently."""
    entry = make_entry(
        base_trigger_data
        | {
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1.0,
            CONF_VALUE_DIRECTION: ValueDirection.INCREASE.value,
        },
        title="Layer Lapse",
    )
    hass.states.async_set("sensor.current_layer", "300")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    # Counter resets for a new print: no frame, but baseline follows down.
    hass.states.async_set("sensor.current_layer", "0")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.current_layer", "1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


def _make_fit_entry(base_trigger_data, **overrides):
    return make_entry(
        base_trigger_data
        | {
            CONF_CAPTURE_MODE: CaptureMode.TIME_FIT.value,
            CONF_DURATION_ENTITY: "sensor.print_duration",
            CONF_TARGET_LENGTH: 2.0,
            CONF_FALLBACK_INTERVAL: 5,
        }
        | overrides,
        title="Fit Lapse",
    )


async def test_fit_length_cadence(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """The interval is computed from the duration entity and frozen at start."""
    # 600 s of print at 30 fps for a 2 s video -> one frame every 10 s.
    entry = _make_fit_entry(base_trigger_data)
    hass.states.async_set("sensor.print_duration", "600")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1  # immediate first frame

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    # The interval is frozen at session start: a new (much longer) estimate
    # mid-session does not slow the cadence down.
    hass.states.async_set("sensor.print_duration", "60000")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=22))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3


async def test_fit_length_falls_back_when_unreadable(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """An unreadable duration entity falls back to the configured interval."""
    entry = _make_fit_entry(base_trigger_data)
    hass.states.async_set("sensor.print_duration", STATE_UNAVAILABLE)
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    # The 5 s fallback is in effect (the fixed 60 s interval would not fire).
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_fit_length_clamps_to_one_second(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A computed sub-second interval is clamped to one second."""
    # 30 s of print at 30 fps for a 60 s video -> ~0.017 s, clamped to 1 s.
    entry = _make_fit_entry(base_trigger_data, **{CONF_TARGET_LENGTH: 60.0})
    hass.states.async_set("sensor.print_duration", "30")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    with patch(
        "custom_components.auto_time_lapse.manager.async_track_time_interval",
        wraps=async_track_time_interval,
    ) as track_interval:
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1
    assert track_interval.call_args[0][2] == timedelta(seconds=1)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_switch_reflects_and_controls_capture(
    hass, mock_entry, mock_camera_image, mock_render
):
    """The capture switch starts and stops the session."""
    await setup_integration(hass, mock_entry)
    switch_id = "switch.test_lapse_capture"

    assert hass.states.get(switch_id).state == STATE_OFF
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(switch_id).state == STATE_ON
    assert get_manager(mock_entry).state is SessionState.CAPTURING

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(switch_id).state == STATE_OFF
    mock_render.assert_called_once()
