"""Tests for the timelapse session manager."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from homeassistant.components import persistent_notification
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.auto_time_lapse.const import (
    ATTR_DEVICE_ID,
    CONF_AUTO_PURGE,
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
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_MAX_WIDTH,
    CONF_OUTPUT_DIR,
    CONF_PURGE_KEEP_SESSIONS,
    CONF_PURGE_MAX_AGE_DAYS,
    CONF_PURGE_MODE,
    CONF_RULE_CONDITIONS,
    CONF_SCALE_MODE,
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
    EVENT_TIMELAPSE_FAILED,
    EVENT_TIMELAPSE_FINISHED,
    SERVICE_CANCEL,
    SERVICE_PURGE,
    SERVICE_RENDER,
    SERVICE_START,
    SERVICE_STOP,
    BufferRetrigger,
    CaptureMode,
    DurationType,
    EndBufferMode,
    PurgeMode,
    ScaleMode,
    SessionState,
    TriggerMode,
    ValueDirection,
    VideoQuality,
)
from custom_components.auto_time_lapse.renderer import RenderError

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

    interval_sensor = "sensor.test_lapse_capture_interval"
    assert hass.states.get(interval_sensor).state == STATE_UNKNOWN

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1  # first frame captured immediately
    assert hass.states.get(interval_sensor).state == "60.0"

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
    assert hass.states.get(interval_sensor).state == STATE_UNKNOWN


async def test_render_failure_fires_failed_event_and_notifies(
    hass, mock_entry, mock_camera_image, mock_render, tmp_path
):
    """A failed render fires the failed event, notifies, and records the error."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)

    events = []
    hass.bus.async_listen(EVENT_TIMELAPSE_FAILED, events.append)

    mock_render.side_effect = RenderError("ffmpeg exploded")

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_render.assert_called_once()
    assert manager.state is SessionState.IDLE

    # The failed event carries the same shape as the finished event plus error.
    assert len(events) == 1
    data = events[0].data
    assert data["entry_id"] == mock_entry.entry_id
    assert data["subentry_id"] == TEST_SUBENTRY_ID
    assert data["name"] == manager.title
    assert data["frame_count"] == 1
    assert data["error"] == "ffmpeg exploded"
    assert data["session_dir"] and isinstance(data["session_dir"], str)

    # The error is recorded and surfaced through the diagnostic sensor.
    assert manager.last_error == "ffmpeg exploded"
    assert hass.states.get("sensor.test_lapse_last_error").state == "ffmpeg exploded"

    # A persistent notification is created, keyed per trigger so repeats replace.
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    notification_id = f"{DOMAIN}_render_failed_{TEST_SUBENTRY_ID}"
    assert notification_id in notifications

    # Frames are retained for a re-render after a failure.
    assert list(_frames_dir(tmp_path).rglob("*.jpg"))


async def test_successful_render_clears_error_and_notification(
    hass, mock_entry, mock_camera_image, mock_render, tmp_path
):
    """A later successful render clears the error and dismisses the notification."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)
    notification_id = f"{DOMAIN}_render_failed_{TEST_SUBENTRY_ID}"

    # First, drive a failure.
    mock_render.side_effect = RenderError("ffmpeg exploded")
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.last_error == "ffmpeg exploded"
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert notification_id in notifications

    # Then a clean render: the error and notification both clear.
    mock_render.side_effect = None
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert manager.last_error is None
    assert hass.states.get("sensor.test_lapse_last_error").state == STATE_UNKNOWN
    assert notification_id not in notifications


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


async def test_keep_frames_moved_to_output_dir(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """With keep_frames, frames move next to the video after rendering."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {CONF_KEEP_FRAMES: True, CONF_OUTPUT_DIR: str(output_dir)}
    )
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert manager.frame_count == 2

        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_render.assert_called_once()
    video = Path(manager.last_video_path)
    # New layout: output_dir/<camera>/<trigger>/<datetime>/<video>.mp4
    assert video.parent.parent.parent.parent == output_dir
    assert video.parent.parent.parent.name == "demo_camera"
    assert video.parent.parent.name == "test_lapse"
    # Frames land in the same folder as the video (not a separate stem subdir).
    frames_dest = video.parent
    assert len(list(frames_dest.glob("frame_*.jpg"))) == 2
    # The working dir under the config folder holds nothing afterwards.
    frames_dir = _frames_dir(tmp_path)
    assert not frames_dir.exists() or not any(frames_dir.iterdir())
    # Rerender finds the retained frames at their new home.
    assert manager._last_session_dir == frames_dest


async def test_keep_frames_move_failure_leaves_frames(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """A failed move leaves the frames in the working dir, not lost."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {CONF_KEEP_FRAMES: True, CONF_OUTPUT_DIR: str(output_dir)}
    )
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        with patch(
            "custom_components.auto_time_lapse.manager.shutil.move",
            side_effect=OSError("disk full"),
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
            )
            await hass.async_block_till_done(wait_background_tasks=True)

    mock_render.assert_called_once()
    assert len(list(_frames_dir(tmp_path).rglob("frame_*.jpg"))) == 1
    assert manager._last_session_dir is not None
    assert manager._last_session_dir.parent == _frames_dir(tmp_path)


async def test_keep_frames_rerender_does_not_delete_original_video(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """Re-rendering kept frames must not delete the video in the session folder."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {CONF_KEEP_FRAMES: True, CONF_OUTPUT_DIR: str(output_dir)}
    )
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        # First render — creates session folder with video + frames.
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

        first_video = Path(manager.last_video_path)
        # Simulate the video being on disk (mock_render doesn't write it).
        first_video.write_bytes(b"first-render")
        first_session_dir = manager._last_session_dir

        # Re-render from the kept frames.
        await hass.services.async_call(
            DOMAIN, SERVICE_RENDER, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    # The original session folder and its video must still exist.
    assert first_session_dir.exists(), "original session folder was deleted"
    assert first_video.exists(), "original video was deleted by re-render"


async def test_media_content_id_new_layout(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """media_content_id resolves correctly under the new nested output tree."""
    media_root = tmp_path / "media"
    media_root.mkdir()
    hass.config.media_dirs = {"local": str(media_root)}

    entry = make_entry(base_trigger_data)
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert manager.last_video_path is not None
    content_id = manager.media_content_id
    assert content_id is not None
    assert content_id.startswith("media-source://media_source/local/")
    # URI path should include the nested camera/trigger/datetime segments.
    rel = content_id.removeprefix("media-source://media_source/local/")
    parts = Path(rel).parts
    assert parts[0] == "auto_time_lapse"
    assert parts[1] == "demo_camera"
    assert parts[2] == "test_lapse"


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
    # Value-change paced: interval sensor is unknown while capturing.
    assert hass.states.get("sensor.layer_lapse_capture_interval").state == STATE_UNKNOWN

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
    interval_sensor = "sensor.fit_lapse_capture_interval"

    assert hass.states.get(interval_sensor).state == STATE_UNKNOWN

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1  # immediate first frame
    assert hass.states.get(interval_sensor).state == "10.0"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    # The interval is frozen at session start: a new (much longer) estimate
    # mid-session does not slow the cadence down.
    hass.states.async_set("sensor.print_duration", "60000")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=22))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(interval_sensor).state == STATE_UNKNOWN


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


async def test_fit_length_minutes(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Duration type 'minutes' multiplies the entity value by 60."""
    # 10 min = 600 s at 30 fps for a 2 s video -> 10 s interval.
    entry = _make_fit_entry(
        base_trigger_data, **{CONF_DURATION_TYPE: DurationType.MINUTES.value}
    )
    hass.states.async_set("sensor.print_duration", "10")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_fit_length_hours(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Duration type 'hours' multiplies the entity value by 3600."""
    # 0.5 h = 1800 s at 30 fps for a 2 s video -> 30 s interval.
    entry = _make_fit_entry(
        base_trigger_data, **{CONF_DURATION_TYPE: DurationType.HOURS.value}
    )
    hass.states.async_set("sensor.print_duration", "0.5")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    # 11 s is less than the 30 s interval — no second frame yet.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_fit_length_end_time_future(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Duration type 'end_time' computes remaining seconds from a timestamp."""
    # end = now + 600 s -> ~600 s duration -> 10 s interval.
    end_ts = (dt_util.utcnow() + timedelta(seconds=600)).isoformat()
    entry = _make_fit_entry(
        base_trigger_data, **{CONF_DURATION_TYPE: DurationType.END_TIME.value}
    )
    hass.states.async_set("sensor.print_duration", end_ts)
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_fit_length_end_time_past_falls_back(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """An end-time already in the past falls back to the fallback interval."""
    past_ts = (dt_util.utcnow() - timedelta(seconds=60)).isoformat()
    entry = _make_fit_entry(
        base_trigger_data, **{CONF_DURATION_TYPE: DurationType.END_TIME.value}
    )
    hass.states.async_set("sensor.print_duration", past_ts)
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    # Fallback of 5 s is in effect; the fixed 60 s interval would not fire.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_fit_length_end_time_garbage_falls_back(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """An unparseable end-time state falls back to the fallback interval."""
    entry = _make_fit_entry(
        base_trigger_data, **{CONF_DURATION_TYPE: DurationType.END_TIME.value}
    )
    hass.states.async_set("sensor.print_duration", "not-a-timestamp")
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    # Fallback of 5 s is in effect.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


def _make_buffer_entry(base_trigger_data, **overrides):
    """A watch trigger on a printer sensor with an end buffer configured."""
    return make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "sensor.printer_status",
            CONF_WATCH_STATES: ["printing"],
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_AMOUNT: 120,
            CONF_END_BUFFER_RETRIGGER: BufferRetrigger.RESUME.value,
        }
        | overrides,
        title="Buffered",
    )


async def _start_buffered_watch(hass, entry):
    """Set up the entry and drive the watch entity into its active state."""
    hass.states.async_set("sensor.printer_status", "idle")
    await setup_integration(hass, entry)
    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    return get_manager(entry)


async def test_buffer_seconds_on_watch_exit(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A seconds buffer keeps the session cadence running past the trigger."""
    entry = _make_buffer_entry(base_trigger_data)
    manager = await _start_buffered_watch(hass, entry)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING
    mock_render.assert_not_called()

    # The 60 s session cadence keeps capturing during the buffer.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2
    assert manager.state is SessionState.BUFFERING

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=125))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_buffer_frames_on_watch_exit(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A frames buffer captures exactly N more frames, then renders."""
    entry = _make_buffer_entry(
        base_trigger_data,
        **{
            CONF_END_BUFFER_MODE: EndBufferMode.FRAMES.value,
            CONF_END_BUFFER_AMOUNT: 2,
        },
    )
    manager = await _start_buffered_watch(hass, entry)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2
    assert manager.state is SessionState.BUFFERING
    mock_render.assert_not_called()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_buffer_override_interval(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """An override interval replaces the session cadence during the buffer."""
    entry = _make_buffer_entry(
        base_trigger_data, **{CONF_END_BUFFER_INTERVAL: 5}
    )
    manager = await _start_buffered_watch(hass, entry)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING
    assert hass.states.get("sensor.buffered_capture_interval").state == "5.0"

    # The 5 s override is in effect (the 60 s session cadence would not fire).
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_buffer_value_change_switches_to_time(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """The value-change cadence goes time-based during the buffer."""
    entry = _make_buffer_entry(
        base_trigger_data,
        **{
            CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
            CONF_VALUE_ENTITY: "sensor.current_layer",
            CONF_VALUE_DELTA: 1.0,
            CONF_VALUE_DIRECTION: ValueDirection.ANY.value,
            CONF_END_BUFFER_INTERVAL: 5,
        },
    )
    hass.states.async_set("sensor.current_layer", "0")
    manager = await _start_buffered_watch(hass, entry)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.current_layer", "1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    # Value changes no longer pace frames during the buffer.
    hass.states.async_set("sensor.current_layer", "2")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    # The override interval does.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3


async def test_manual_stop_ends_buffer_early(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """The stop service ends an in-progress buffer immediately and renders."""
    entry = _make_buffer_entry(base_trigger_data)
    manager = await _start_buffered_watch(hass, entry)
    device_id = get_device_id(hass)

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()

    # The cancelled buffer deadline has no late side effects.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=125))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_cancel_during_buffer_discards(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """Cancel during the buffer discards the frames without rendering."""
    entry = _make_buffer_entry(base_trigger_data)
    manager = await _start_buffered_watch(hass, entry)
    device_id = get_device_id(hass)

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_not_called()
    assert not list(_frames_dir(tmp_path).rglob("*.jpg"))


async def test_retrigger_resume_continues_session(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """With resume, a re-trigger cancels the buffer and keeps the session."""
    entry = _make_buffer_entry(base_trigger_data)
    manager = await _start_buffered_watch(hass, entry)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING
    mock_render.assert_not_called()

    # Same session keeps accumulating frames in the same directory.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2
    assert len(list(_frames_dir(tmp_path).iterdir())) == 1

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=125))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_retrigger_finish_starts_fresh_session(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """With finish, the buffer completes, renders, and a new session starts."""
    entry = _make_buffer_entry(
        base_trigger_data,
        **{
            CONF_END_BUFFER_AMOUNT: 30,
            CONF_END_BUFFER_RETRIGGER: BufferRetrigger.FINISH.value,
        },
    )
    manager = await _start_buffered_watch(hass, entry)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    # Re-activation does not interrupt the buffer.
    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_render.assert_called_once()
    # A fresh session began because the trigger is active again.
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1


async def test_buffer_frames_watchdog(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A frames buffer with a dead camera ends after the safety budget."""
    entry = _make_buffer_entry(
        base_trigger_data,
        **{
            CONF_END_BUFFER_MODE: EndBufferMode.FRAMES.value,
            CONF_END_BUFFER_AMOUNT: 2,
        },
    )
    manager = await _start_buffered_watch(hass, entry)
    assert manager.frame_count == 1

    mock_camera_image.side_effect = HomeAssistantError("camera unavailable")
    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    # Failed snapshots do not count towards the frame budget.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1
    assert manager.state is SessionState.BUFFERING

    # The watchdog (2 frames * 60 s * factor 3 = 360 s) ends the buffer.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=365))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_schedule_window_end_enters_buffer(
    hass, base_trigger_data, mock_camera_image, mock_render, freezer
):
    """The end of a schedule window starts the buffer instead of stopping."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-06-10 19:59:00+00:00")
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value,
            "schedule_start": "08:00:00",
            "schedule_end": "20:00:00",
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_AMOUNT: 120,
            CONF_END_BUFFER_RETRIGGER: BufferRetrigger.RESUME.value,
        },
        title="Scheduled",
    )
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    assert manager.state is SessionState.CAPTURING

    target = dt_util.utcnow() + timedelta(seconds=61)
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING
    mock_render.assert_not_called()

    target = dt_util.utcnow() + timedelta(seconds=125)
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


def _layer_below(threshold: float) -> list[dict]:
    return [
        {
            "condition": "numeric_state",
            "entity_id": "sensor.current_layer",
            "below": threshold,
        }
    ]


CONDITIONAL_RULES = [
    {
        CONF_RULE_CONDITIONS: _layer_below(20),
        CONF_CAPTURE_MODE: CaptureMode.TIME.value,
        CONF_INTERVAL: 30,
    },
    {
        CONF_RULE_CONDITIONS: _layer_below(40),
        CONF_CAPTURE_MODE: CaptureMode.TIME.value,
        CONF_INTERVAL: 60,
    },
    {
        CONF_CAPTURE_MODE: CaptureMode.VALUE_CHANGE.value,
        CONF_VALUE_ENTITY: "sensor.current_layer",
        CONF_VALUE_DELTA: 1.0,
        CONF_VALUE_DIRECTION: ValueDirection.INCREASE.value,
    },
]


def _make_conditional_entry(
    base_trigger_data, rules=CONDITIONAL_RULES, reevaluate=True, **overrides
):
    data = base_trigger_data | {
        CONF_CAPTURE_MODE: CaptureMode.CONDITIONAL.value,
        CONF_CONDITIONAL_RULES: rules,
        CONF_CONDITIONAL_REEVALUATE: reevaluate,
    }
    data.pop(CONF_INTERVAL)
    return make_entry(data | overrides, title="Conditional Lapse")


async def _start_conditional(hass, entry):
    await setup_integration(hass, entry)
    device_id = get_device_id(hass)
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    return get_manager(entry)


async def test_conditional_rules_switch_live(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """The matching rule paces the frames and switches mid-session."""
    entry = _make_conditional_entry(base_trigger_data)
    hass.states.async_set("sensor.current_layer", "5")
    manager = await _start_conditional(hass, entry)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1  # immediate first frame
    interval_sensor = "sensor.conditional_lapse_capture_interval"
    assert hass.states.get(interval_sensor).state == "30.0"

    # Layer 5 -> first rule: a frame every 30 s.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    # Layer 25 -> second rule: 60 s between frames, effective immediately.
    hass.states.async_set("sensor.current_layer", "25")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2  # the switch itself captures nothing
    assert hass.states.get(interval_sensor).state == "60.0"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2  # the 30 s rule no longer paces

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3

    # Layer 45 -> default rule: one frame per layer increase.
    hass.states.async_set("sensor.current_layer", "45")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3
    assert hass.states.get(interval_sensor).state == STATE_UNKNOWN

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3  # time no longer paces

    hass.states.async_set("sensor.current_layer", "46")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 4

    # Below the step: no frame.
    hass.states.async_set("sensor.current_layer", "46.5")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 4


async def test_conditional_locked_without_reevaluation(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """With re-evaluation off, the start rule holds until the next session."""
    entry = _make_conditional_entry(base_trigger_data, reevaluate=False)
    hass.states.async_set("sensor.current_layer", "5")
    manager = await _start_conditional(hass, entry)
    device_id = get_device_id(hass)
    assert manager.frame_count == 1

    # Crossing into the default rule's range changes nothing mid-session.
    hass.states.async_set("sensor.current_layer", "45")
    hass.states.async_set("sensor.current_layer", "46")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1  # value changes do not pace

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2  # the locked 30 s rule still does

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    # A new session re-selects: layer 46 -> the value-change default rule.
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    hass.states.async_set("sensor.current_layer", "47")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_conditional_invalid_condition_falls_to_default(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """An unparseable condition never matches; the default rule applies."""
    rules = [
        {
            CONF_RULE_CONDITIONS: [{"condition": "bogus"}],
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 5,
        },
        {
            CONF_CAPTURE_MODE: CaptureMode.TIME.value,
            CONF_INTERVAL: 30,
        },
    ]
    entry = _make_conditional_entry(base_trigger_data, rules=rules)
    manager = await _start_conditional(hass, entry)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1  # the broken 5 s rule never paces

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2


async def test_conditional_stop_clears_tracking(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """After stop, neither condition nor value changes capture frames."""
    entry = _make_conditional_entry(base_trigger_data)
    hass.states.async_set("sensor.current_layer", "45")
    manager = await _start_conditional(hass, entry)
    device_id = get_device_id(hass)
    assert manager.frame_count == 1

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE

    hass.states.async_set("sensor.current_layer", "5")
    hass.states.async_set("sensor.current_layer", "46")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()


async def test_conditional_buffer_and_resume_reselects(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A value-change rule goes time-based in the buffer; resume re-selects."""
    entry = _make_conditional_entry(
        base_trigger_data,
        **{
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "sensor.printer_status",
            CONF_WATCH_STATES: ["printing"],
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_AMOUNT: 120,
            CONF_END_BUFFER_RETRIGGER: BufferRetrigger.RESUME.value,
            CONF_END_BUFFER_INTERVAL: 5,
        },
    )
    hass.states.async_set("sensor.current_layer", "45")
    hass.states.async_set("sensor.printer_status", "idle")
    await setup_integration(hass, entry)
    manager = get_manager(entry)

    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 1  # default value-change rule active

    hass.states.async_set("sensor.printer_status", "complete")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.BUFFERING

    # Value changes no longer pace during the buffer; the override does.
    hass.states.async_set("sensor.current_layer", "46")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    # The layer dropped below 20 during the buffer (new print): the resumed
    # session must pick up the 30 s rule even though re-evaluation was
    # suspended while buffering.
    hass.states.async_set("sensor.current_layer", "5")
    hass.states.async_set("sensor.printer_status", "printing")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.state is SessionState.CAPTURING

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 3


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


async def _run_capture_cycle(hass) -> None:
    """Start a session, capture the immediate frame, and stop to render."""
    device_id = get_device_id(hass)
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_capture_default_no_scaling(
    hass, mock_entry, mock_camera_image, mock_render
):
    """Without scaling, snapshots are requested at native resolution."""
    await setup_integration(hass, mock_entry)
    await _run_capture_cycle(hass)
    kwargs = mock_camera_image.call_args.kwargs
    assert "width" not in kwargs
    assert "height" not in kwargs


async def test_capture_scaling_passes_dimensions(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Capture-time scaling asks the camera for a bounded snapshot size."""
    entry = make_entry(
        base_trigger_data,
        options={CONF_SCALE_MODE: ScaleMode.CAPTURE.value, CONF_MAX_WIDTH: 640},
    )
    await setup_integration(hass, entry)
    await _run_capture_cycle(hass)
    kwargs = mock_camera_image.call_args.kwargs
    assert kwargs["width"] == 640
    assert kwargs["height"] == 360
    # The renderer still clamps: capture scaling is best-effort.
    assert mock_render.call_args.kwargs["max_width"] == 640


async def test_render_default_params(
    hass, mock_entry, mock_camera_image, mock_render
):
    """With nothing configured, the historical encoder settings are used."""
    await setup_integration(hass, mock_entry)
    await _run_capture_cycle(hass)
    assert mock_render.call_args.kwargs == {
        "crf": 23,
        "preset": "medium",
        "max_width": None,
    }


async def test_service_level_quality_applies(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A quality level on the camera entry applies to its triggers."""
    entry = make_entry(
        base_trigger_data, options={CONF_VIDEO_QUALITY: VideoQuality.HIGH.value}
    )
    await setup_integration(hass, entry)
    await _run_capture_cycle(hass)
    assert mock_render.call_args.kwargs["crf"] == 19
    assert mock_render.call_args.kwargs["preset"] == "slow"


async def test_trigger_override_beats_service_default(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """A trigger-level quality wins over the camera entry's default."""
    entry = make_entry(
        base_trigger_data | {CONF_VIDEO_QUALITY: VideoQuality.MAXIMUM.value},
        options={CONF_VIDEO_QUALITY: VideoQuality.LOW.value},
    )
    await setup_integration(hass, entry)
    await _run_capture_cycle(hass)
    assert mock_render.call_args.kwargs["crf"] == 16
    assert mock_render.call_args.kwargs["preset"] == "slower"


async def test_custom_quality_resolution(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """The custom level passes its raw CRF and preset through."""
    entry = make_entry(
        base_trigger_data
        | {
            CONF_VIDEO_QUALITY: VideoQuality.CUSTOM.value,
            CONF_VIDEO_CRF: 28,
            CONF_VIDEO_PRESET: "veryfast",
        }
    )
    await setup_integration(hass, entry)
    await _run_capture_cycle(hass)
    assert mock_render.call_args.kwargs["crf"] == 28
    assert mock_render.call_args.kwargs["preset"] == "veryfast"


async def test_render_scale_mode_passes_width(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """Render-time scaling leaves snapshots alone and clamps in ffmpeg."""
    entry = make_entry(
        base_trigger_data,
        options={CONF_SCALE_MODE: ScaleMode.RENDER.value, CONF_MAX_WIDTH: 640},
    )
    await setup_integration(hass, entry)
    await _run_capture_cycle(hass)
    assert "width" not in mock_camera_image.call_args.kwargs
    assert mock_render.call_args.kwargs["max_width"] == 640


async def test_trigger_scale_off_overrides_service_scaling(
    hass, base_trigger_data, mock_camera_image, mock_render
):
    """An explicit off override disables the camera entry's scaling."""
    entry = make_entry(
        base_trigger_data | {CONF_SCALE_MODE: ScaleMode.OFF.value},
        options={CONF_SCALE_MODE: ScaleMode.CAPTURE.value, CONF_MAX_WIDTH: 640},
    )
    await setup_integration(hass, entry)
    await _run_capture_cycle(hass)
    assert "width" not in mock_camera_image.call_args.kwargs
    assert mock_render.call_args.kwargs["max_width"] is None


async def test_render_failure_keeps_frames_and_fires_no_event(
    hass, mock_entry, mock_camera_image, tmp_path
):
    """A failed render keeps the frames and record, and fires no event."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)

    events = []
    hass.bus.async_listen(EVENT_TIMELAPSE_FINISHED, events.append)

    with patch(
        "custom_components.auto_time_lapse.manager.async_render_timelapse",
        side_effect=RenderError("ffmpeg blew up"),
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert manager.state is SessionState.IDLE
    # Frames are kept on disk so the session can be re-rendered.
    assert len(list(_frames_dir(tmp_path).rglob("frame_*.jpg"))) == 1
    assert manager._last_session_dir is not None
    assert manager._last_session_frames == 1
    # The storage record survives so the session is salvaged after a restart.
    assert manager._store.records(TEST_SUBENTRY_ID)
    # No completion event for a render that never produced a video.
    assert events == []


async def test_atomic_frame_write_leaves_no_part_files(
    hass, mock_entry, mock_camera_image, mock_render, tmp_path
):
    """The atomic write leaves only finished frames, never a .part temp."""
    await setup_integration(hass, mock_entry)
    manager = get_manager(mock_entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 2

    session_dir = manager._session_dir
    files = list(session_dir.iterdir())
    assert not any(f.name.endswith(".part") for f in files)
    assert len(list(session_dir.glob("frame_*.jpg"))) == 2


async def test_output_filename_collision_gets_unique_suffix(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path, freezer
):
    """An existing output filename gets a numeric suffix, not an overwrite."""
    freezer.move_to("2026-06-14 12:00:00")
    output_dir = tmp_path / "output"
    timestamp = dt_util.now().strftime("%Y-%m-%d_%H-%M-%S")
    # New layout: output/<camera>/<trigger>/<datetime>/
    session_dir = output_dir / "demo_camera" / "test_lapse" / timestamp
    session_dir.mkdir(parents=True)
    preexisting = session_dir / f"test_lapse_{timestamp}.mp4"
    preexisting.write_bytes(b"existing video")

    entry = make_entry(base_trigger_data | {CONF_OUTPUT_DIR: str(output_dir)})
    await setup_integration(hass, entry)
    manager = get_manager(entry)

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await _run_capture_cycle(hass)

    mock_render.assert_called_once()
    assert manager.last_video_path == str(
        session_dir / f"test_lapse_{timestamp}_1.mp4"
    )
    # The pre-existing video is left untouched.
    assert preexisting.read_bytes() == b"existing video"


async def test_purge_frames_deletes_jpgs_keeps_videos(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """purge_frames removes frame JPEGs but leaves MP4 videos intact."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {CONF_KEEP_FRAMES: True, CONF_OUTPUT_DIR: str(output_dir)}
    )
    await setup_integration(hass, entry)
    manager = get_manager(entry)
    device_id = get_device_id(hass)

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    session_dir = Path(manager.last_video_path).parent
    # Simulate video written by ffmpeg.
    video = Path(manager.last_video_path)
    video.write_bytes(b"fake-video")

    assert any(session_dir.glob("frame_*.jpg"))

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await hass.services.async_call(
            DOMAIN, SERVICE_PURGE, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    # All frames gone, video untouched, _last_session_dir cleared.
    assert not any(session_dir.glob("frame_*.jpg"))
    assert video.read_bytes() == b"fake-video"
    assert manager._last_session_dir is None
    assert manager._last_session_frames == 0


async def test_auto_purge_keep_recent_removes_oldest(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path, freezer
):
    """KEEP_RECENT=1 purges frames from older sessions, keeps newest."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {
            CONF_KEEP_FRAMES: True,
            CONF_OUTPUT_DIR: str(output_dir),
            CONF_AUTO_PURGE: True,
            CONF_PURGE_MODE: PurgeMode.KEEP_RECENT,
            CONF_PURGE_KEEP_SESSIONS: 1,
        }
    )
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    with patch.object(hass.config, "is_allowed_path", return_value=True):
        # First session.
        freezer.move_to("2026-06-14 10:00:00")
        await _run_capture_cycle(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        first_session = Path(manager.last_video_path).parent

        # Second session — triggers purge of the first.
        freezer.move_to("2026-06-14 11:00:00")
        await _run_capture_cycle(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        second_session = Path(manager.last_video_path).parent

    # Oldest session's frames deleted; newest kept.
    assert not any(first_session.glob("frame_*.jpg")), "old frames should be purged"
    assert any(second_session.glob("frame_*.jpg")), "new frames should be retained"


async def test_auto_purge_max_age_removes_old_sessions(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path, freezer
):
    """MAX_AGE purges frames from sessions older than the configured threshold."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {
            CONF_KEEP_FRAMES: True,
            CONF_OUTPUT_DIR: str(output_dir),
            CONF_AUTO_PURGE: True,
            CONF_PURGE_MODE: PurgeMode.MAX_AGE,
            CONF_PURGE_MAX_AGE_DAYS: 7,
        }
    )
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    with patch.object(hass.config, "is_allowed_path", return_value=True):
        # Old session — 10 days ago.
        freezer.move_to("2026-06-04 10:00:00")
        await _run_capture_cycle(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        old_session = Path(manager.last_video_path).parent

        # Recent session — triggers purge of the old one.
        freezer.move_to("2026-06-14 10:00:00")
        await _run_capture_cycle(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        recent_session = Path(manager.last_video_path).parent

    assert not any(old_session.glob("frame_*.jpg")), "old frames should be purged"
    assert any(recent_session.glob("frame_*.jpg")), "recent frames should be retained"


async def test_auto_purge_daily_timer_enforces_retention(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """The daily timer fires retention enforcement without a new render."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data
        | {
            CONF_KEEP_FRAMES: True,
            CONF_OUTPUT_DIR: str(output_dir),
            CONF_AUTO_PURGE: True,
            CONF_PURGE_MODE: PurgeMode.KEEP_RECENT,
            CONF_PURGE_KEEP_SESSIONS: 0,
        }
    )
    await setup_integration(hass, entry)

    # Plant a fake retained frame set directly in the output tree.
    base = output_dir / "demo_camera" / "test_lapse"
    session_dir = base / "old_session"
    session_dir.mkdir(parents=True)
    (session_dir / "frame_000001.jpg").write_bytes(b"retained")

    assert any(session_dir.glob("frame_*.jpg"))

    # Fire the daily timer; enforcement must purge the retained frames.
    with patch.object(hass.config, "is_allowed_path", return_value=True):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=1, seconds=1))
        await hass.async_block_till_done(wait_background_tasks=True)

    assert not any(session_dir.glob("frame_*.jpg")), "daily timer should purge frames"
