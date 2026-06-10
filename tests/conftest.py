"""Fixtures for Auto Time Lapse tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.auto_time_lapse.const import (
    CONF_CAMERA_ENTITY,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_FPS,
    DOMAIN,
)


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
def base_options() -> dict:
    """Minimal valid options for a profile."""
    return {
        CONF_CAMERA_ENTITY: "camera.demo",
        CONF_INTERVAL: 60,
        CONF_OUTPUT_FPS: 30,
        CONF_FILENAME_PATTERN: "{name}_{timestamp}.mp4",
        CONF_KEEP_FRAMES: False,
    }


@pytest.fixture
def mock_entry(base_options) -> MockConfigEntry:
    """A mock config entry for a timelapse profile."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Lapse",
        data={},
        options=base_options,
        entry_id="test_entry_id",
    )
