"""Tests for session resume and salvage after a crash or restart."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from homeassistant.const import STATE_ON
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.auto_time_lapse.const import (
    ATTR_DEVICE_ID,
    CONF_CAPTURE_MODE,
    CONF_CONDITIONAL_RULES,
    CONF_END_BUFFER_AMOUNT,
    CONF_END_BUFFER_MODE,
    CONF_INTERVAL,
    CONF_RULE_CONDITIONS,
    CONF_TRIGGER_MODE,
    CONF_WATCH_ENTITY,
    DOMAIN,
    SERVICE_CANCEL,
    SERVICE_START,
    SERVICE_STOP,
    CaptureMode,
    EndBufferMode,
    SessionPhase,
    SessionState,
    TriggerMode,
)
from custom_components.auto_time_lapse.storage import STORAGE_KEY

from .conftest import (
    TEST_SUBENTRY_ID,
    get_device_id,
    get_manager,
    make_entry,
    setup_integration,
)

SESSION_DIR_NAME = "20260101_000000_000000"


def _frames_base(tmp_path: Path) -> Path:
    return tmp_path / DOMAIN / TEST_SUBENTRY_ID


def _seed_session(
    hass_storage: dict,
    tmp_path: Path,
    *,
    frames: int,
    phase: SessionPhase = SessionPhase.CAPTURING,
    create_dir: bool = True,
) -> Path:
    """Pretend a session was interrupted: frame files plus a stored record."""
    session_dir = _frames_base(tmp_path) / SESSION_DIR_NAME
    if create_dir:
        session_dir.mkdir(parents=True)
        for index in range(frames):
            (session_dir / f"frame_{index:06d}.jpg").write_bytes(b"old-jpeg")
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {
            TEST_SUBENTRY_ID: {
                SESSION_DIR_NAME: {
                    "entry_id": "test_entry_id",
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "phase": phase.value,
                }
            }
        },
    }
    return session_dir


def _stored_records(hass_storage: dict) -> dict:
    return hass_storage.get(STORAGE_KEY, {}).get("data", {})


async def simulate_restart(hass, entry: MockConfigEntry) -> None:
    """Unload and set up the entry again, as an HA restart would."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_manual_session_resumes_after_restart(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """A restart mid-session continues the same session and numbering."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert get_manager(mock_entry).frame_count == 2
    (session_dir,) = list(_frames_base(tmp_path).iterdir())

    await simulate_restart(hass, mock_entry)

    manager = get_manager(mock_entry)
    assert manager.state is SessionState.CAPTURING
    # Two frames adopted from disk plus the immediate frame on resume.
    assert manager.frame_count == 3
    assert list(_frames_base(tmp_path).iterdir()) == [session_dir]
    assert (session_dir / "frame_000002.jpg").is_file()

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_render.assert_called_once()
    assert mock_render.call_args.args[1] == session_dir
    assert _stored_records(hass_storage) == {}


async def test_record_written_on_start_and_cleared_after_render(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage
):
    """The persisted record tracks the session lifecycle."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    records = _stored_records(hass_storage)[TEST_SUBENTRY_ID]
    (record,) = records.values()
    assert record["phase"] == SessionPhase.CAPTURING.value
    assert record["entry_id"] == mock_entry.entry_id

    await hass.services.async_call(
        DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _stored_records(hass_storage) == {}


async def test_record_cleared_on_cancel(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage
):
    """Cancelling a session discards its persisted record."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _stored_records(hass_storage)

    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _stored_records(hass_storage) == {}
    mock_render.assert_not_called()


async def test_unload_keeps_record_and_frames(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """Unloading mid-capture leaves the session recoverable."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _stored_records(hass_storage)[TEST_SUBENTRY_ID]
    assert list(_frames_base(tmp_path).rglob("*.jpg"))


async def test_watch_resumes_when_entity_still_active(
    hass, base_trigger_data, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """A watch session resumes if the entity is still active at startup."""
    session_dir = _seed_session(hass_storage, tmp_path, frames=2)
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

    manager = get_manager(entry)
    assert manager.state is SessionState.CAPTURING
    # Two adopted frames plus the immediate frame on resume; same directory.
    assert manager.frame_count == 3
    assert (session_dir / "frame_000002.jpg").is_file()
    assert list(_frames_base(tmp_path).iterdir()) == [session_dir]
    mock_render.assert_not_called()


async def test_watch_salvages_when_entity_inactive(
    hass, base_trigger_data, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """Frames of a session that cannot continue are rendered at startup."""
    session_dir = _seed_session(hass_storage, tmp_path, frames=2)
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "input_boolean.motion",
        },
        title="Watched",
    )
    hass.states.async_set("input_boolean.motion", "off")
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()
    assert mock_render.call_args.args[1] == session_dir
    assert _stored_records(hass_storage) == {}
    # Frames are cleaned after the successful salvage render.
    assert not session_dir.exists()


async def test_restart_during_buffer_salvages(
    hass, base_trigger_data, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """The buffer does not survive a restart: the session is salvaged."""
    session_dir = _seed_session(hass_storage, tmp_path, frames=2)
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "input_boolean.motion",
            CONF_END_BUFFER_MODE: EndBufferMode.SECONDS.value,
            CONF_END_BUFFER_AMOUNT: 300,
        },
        title="Watched",
    )
    # The entity is inactive at startup, as it would be mid-buffer.
    hass.states.async_set("input_boolean.motion", "off")
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()
    assert mock_render.call_args.args[1] == session_dir
    assert _stored_records(hass_storage) == {}


async def test_pending_render_is_salvaged_not_resumed(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """A crash during rendering leads to a salvage render, never a resume."""
    session_dir = _seed_session(
        hass_storage, tmp_path, frames=2, phase=SessionPhase.PENDING_RENDER
    )
    await setup_integration(hass, mock_entry)

    manager = get_manager(mock_entry)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()
    assert mock_render.call_args.args[1] == session_dir
    assert _stored_records(hass_storage) == {}


async def test_zero_frame_session_discarded(
    hass, base_trigger_data, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """An interrupted session without frames is dropped without rendering."""
    session_dir = _seed_session(hass_storage, tmp_path, frames=0)
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.WATCH.value,
            CONF_WATCH_ENTITY: "input_boolean.motion",
        },
        title="Watched",
    )
    hass.states.async_set("input_boolean.motion", "off")
    await setup_integration(hass, entry)

    assert get_manager(entry).state is SessionState.IDLE
    mock_render.assert_not_called()
    assert not session_dir.exists()
    assert _stored_records(hass_storage) == {}


async def test_missing_session_dir_drops_record(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """A record whose frames were deleted by hand is dropped quietly."""
    _seed_session(hass_storage, tmp_path, frames=0, create_dir=False)
    await setup_integration(hass, mock_entry)

    assert get_manager(mock_entry).state is SessionState.IDLE
    mock_render.assert_not_called()
    assert _stored_records(hass_storage) == {}


async def test_stale_cleanup_spares_recorded_session(
    hass, mock_entry, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """Startup cleanup removes only directories without a session record."""
    _seed_session(hass_storage, tmp_path, frames=2)
    stale_dir = _frames_base(tmp_path) / "20250101_000000_000000"
    stale_dir.mkdir(parents=True)
    (stale_dir / "frame_000000.jpg").write_bytes(b"stale-jpeg")

    await setup_integration(hass, mock_entry)

    manager = get_manager(mock_entry)
    assert not stale_dir.exists()
    # The recorded manual session resumed with its frames intact.
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 3


async def test_schedule_resumes_inside_window(
    hass,
    base_trigger_data,
    mock_camera_image,
    mock_render,
    hass_storage,
    tmp_path,
    freezer,
):
    """A schedule trigger resumes the interrupted session mid-window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-06-10 12:00:00+00:00")
    _seed_session(hass_storage, tmp_path, frames=2)
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value,
            "schedule_start": "08:00:00",
            "schedule_end": "20:00:00",
        },
        title="Scheduled",
    )
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    assert manager.state is SessionState.CAPTURING
    assert manager.frame_count == 3
    mock_render.assert_not_called()


async def test_conditional_session_resumes_with_matching_rule(
    hass, base_trigger_data, mock_camera_image, mock_render, hass_storage, tmp_path
):
    """A resumed conditional session wires the rule matching at startup."""
    _seed_session(hass_storage, tmp_path, frames=2)
    entry = make_entry(
        base_trigger_data
        | {
            CONF_CAPTURE_MODE: CaptureMode.CONDITIONAL.value,
            CONF_CONDITIONAL_RULES: [
                {
                    CONF_RULE_CONDITIONS: [
                        {
                            "condition": "numeric_state",
                            "entity_id": "sensor.current_layer",
                            "below": 20,
                        }
                    ],
                    CONF_CAPTURE_MODE: CaptureMode.TIME.value,
                    CONF_INTERVAL: 5,
                },
                {
                    CONF_CAPTURE_MODE: CaptureMode.TIME.value,
                    CONF_INTERVAL: 600,
                },
            ],
        },
        title="Conditional",
    )
    hass.states.async_set("sensor.current_layer", "5")
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    assert manager.state is SessionState.CAPTURING
    # Two adopted frames plus the immediate frame on resume.
    assert manager.frame_count == 3

    # The 5 s rule paces the resumed session (the 600 s default would not).
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.frame_count == 4


async def test_schedule_salvages_outside_window(
    hass,
    base_trigger_data,
    mock_camera_image,
    mock_render,
    hass_storage,
    tmp_path,
    freezer,
):
    """A schedule trigger renders the partial session if the window ended."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-06-10 22:00:00+00:00")
    session_dir = _seed_session(hass_storage, tmp_path, frames=2)
    entry = make_entry(
        base_trigger_data
        | {
            CONF_TRIGGER_MODE: TriggerMode.SCHEDULE.value,
            "schedule_start": "08:00:00",
            "schedule_end": "20:00:00",
        },
        title="Scheduled",
    )
    await setup_integration(hass, entry)

    manager = get_manager(entry)
    assert manager.state is SessionState.IDLE
    mock_render.assert_called_once()
    assert mock_render.call_args.args[1] == session_dir
    assert _stored_records(hass_storage) == {}
