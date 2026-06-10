"""Tests for the timelapse session manager."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from homeassistant.components.camera import Image
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.auto_time_lapse.const import (
    ATTR_DEVICE_ID,
    CONF_TRIGGER_MODE,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DOMAIN,
    EVENT_TIMELAPSE_FINISHED,
    SERVICE_CANCEL,
    SERVICE_START,
    SERVICE_STOP,
    SessionState,
    TriggerMode,
)

from .conftest import TEST_SUBENTRY_ID, make_entry


async def setup_integration(hass, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def get_device_id(hass) -> str:
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, TEST_SUBENTRY_ID)}
    )
    assert device is not None
    return device.id


def get_manager(entry: MockConfigEntry):
    return entry.runtime_data[TEST_SUBENTRY_ID]


@pytest.fixture
def mock_camera_image():
    """Return a fake JPEG for every snapshot request."""
    with patch(
        "custom_components.auto_time_lapse.manager.async_get_image",
        return_value=Image("image/jpeg", b"fake-jpeg"),
    ) as mock:
        yield mock


@pytest.fixture
def mock_render():
    """Skip the real ffmpeg invocation."""
    with patch(
        "custom_components.auto_time_lapse.manager.async_render_timelapse"
    ) as mock:
        yield mock


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
