"""Tests for the integration's services and device resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.auto_time_lapse.const import (
    ATTR_DEVICE_ID,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    DOMAIN,
    SERVICE_RENDER,
    SERVICE_START,
    SERVICE_STOP,
)

from .conftest import get_device_id, get_manager, make_entry, setup_integration


async def test_unknown_device_raises(hass, mock_entry, mock_camera_image):
    """A service call for an unregistered device is rejected."""
    await setup_integration(hass, mock_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: "does-not-exist"}, blocking=True
        )
    assert excinfo.value.translation_key == "device_not_found"


async def test_entry_not_loaded_raises(hass, mock_entry, mock_camera_image):
    """A device whose camera entry is unloaded is rejected."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
    assert excinfo.value.translation_key == "entry_not_loaded"


async def test_valid_device_reaches_manager(hass, mock_entry, mock_camera_image):
    """A valid device resolves to its manager and the call is dispatched."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)
    manager = get_manager(mock_entry)

    with patch.object(manager, "async_start", AsyncMock()) as start:
        await hass.services.async_call(
            DOMAIN, SERVICE_START, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
    start.assert_awaited_once()

    with patch.object(manager, "async_stop", AsyncMock()) as stop:
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
    stop.assert_awaited_once()


async def test_render_without_frames_raises(hass, mock_entry, mock_camera_image):
    """Re-rendering with no retained session is rejected."""
    await setup_integration(hass, mock_entry)
    device_id = get_device_id(hass)

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN, SERVICE_RENDER, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
    assert excinfo.value.translation_key == "no_frames"


async def test_render_rerenders_retained_frames(
    hass, base_trigger_data, mock_camera_image, mock_render, tmp_path
):
    """The render service re-renders the most recent kept-frames session."""
    output_dir = tmp_path / "output"
    entry = make_entry(
        base_trigger_data | {CONF_KEEP_FRAMES: True, CONF_OUTPUT_DIR: str(output_dir)}
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
        mock_render.assert_called_once()
        # Frames were retained next to the rendered video.
        video = Path(manager.last_video_path)
        assert manager._last_session_dir == video.parent / video.stem

        await hass.services.async_call(
            DOMAIN, SERVICE_RENDER, {ATTR_DEVICE_ID: device_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    # The retained frames are rendered again, off the same directory.
    assert mock_render.call_count == 2
