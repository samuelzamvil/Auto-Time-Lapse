"""Fixtures for Auto Time Lapse tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from homeassistant.components.camera import Image
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.auto_time_lapse.const import (
    CONF_CAMERA_ENTITY,
    CONF_CAPTURE_MODE,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_FPS,
    CONF_TRIGGER_MODE,
    DOMAIN,
    SUBENTRY_TYPE_TRIGGER,
    CaptureMode,
    TriggerMode,
)

TEST_SUBENTRY_ID = "test_sub_id"


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


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    return


@pytest.fixture(autouse=True)
def temp_config_dir(hass, tmp_path: Path):
    """Point the HA config dir at a writable temp dir for frame/video files."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture
def base_trigger_data() -> dict:
    """Minimal valid data for a manual trigger subentry."""
    return {
        CONF_TRIGGER_MODE: TriggerMode.MANUAL.value,
        CONF_CAPTURE_MODE: CaptureMode.TIME.value,
        CONF_INTERVAL: 60,
        CONF_OUTPUT_FPS: 30,
        CONF_FILENAME_PATTERN: "{name}_{timestamp}.mp4",
        CONF_KEEP_FRAMES: False,
    }


def make_entry(trigger_data: dict, title: str = "Test Lapse") -> MockConfigEntry:
    """Build a camera entry with a single trigger subentry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Demo Camera",
        data={CONF_CAMERA_ENTITY: "camera.demo"},
        entry_id="test_entry_id",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=trigger_data,
                subentry_id=TEST_SUBENTRY_ID,
                subentry_type=SUBENTRY_TYPE_TRIGGER,
                title=title,
                unique_id=None,
            )
        ],
    )


@pytest.fixture
def mock_entry(base_trigger_data) -> MockConfigEntry:
    """A camera entry with one manual trigger."""
    return make_entry(base_trigger_data)
