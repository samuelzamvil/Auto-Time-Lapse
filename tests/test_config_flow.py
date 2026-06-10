"""Tests for the Auto Time Lapse config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.auto_time_lapse.const import (
    CONF_CAMERA_ENTITY,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    DOMAIN,
)

BASE_INPUT = {
    CONF_NAME: "Garden",
    CONF_CAMERA_ENTITY: "camera.garden",
    CONF_INTERVAL: 30,
    CONF_OUTPUT_FPS: 24,
    CONF_FILENAME_PATTERN: "{name}_{timestamp}.mp4",
    CONF_KEEP_FRAMES: False,
    CONF_SCHEDULE_ENABLED: False,
}


async def test_user_flow_creates_entry(hass):
    """The happy path creates an entry with all settings in options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    with patch(
        "custom_components.auto_time_lapse.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(BASE_INPUT)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garden"
    assert result["data"] == {}
    assert result["options"][CONF_CAMERA_ENTITY] == "camera.garden"
    assert result["options"][CONF_INTERVAL] == 30
    assert CONF_NAME not in result["options"]


async def test_user_flow_rejects_disallowed_path(hass):
    """A custom output dir outside the allowed paths is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    user_input = dict(BASE_INPUT) | {CONF_OUTPUT_DIR: "/not/allowed"}
    with patch.object(hass.config, "is_allowed_path", return_value=False):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_OUTPUT_DIR: "path_not_allowed"}


async def test_user_flow_requires_schedule_times(hass):
    """Enabling the schedule without times is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    user_input = dict(BASE_INPUT) | {CONF_SCHEDULE_ENABLED: True}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SCHEDULE_ENABLED: "schedule_times_required"}

    user_input |= {
        CONF_SCHEDULE_START: "08:00:00",
        CONF_SCHEDULE_END: "08:00:00",
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["errors"] == {CONF_SCHEDULE_END: "schedule_start_equals_end"}


async def test_options_flow_updates_options(hass, mock_entry):
    """The options flow round-trips and updates values."""
    mock_entry.add_to_hass(hass)
    with patch(
        "custom_components.auto_time_lapse.async_setup_entry", return_value=True
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    new_input = dict(mock_entry.options) | {CONF_INTERVAL: 5, CONF_KEEP_FRAMES: True}
    new_input.setdefault(CONF_SCHEDULE_ENABLED, False)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], new_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options[CONF_INTERVAL] == 5
    assert mock_entry.options[CONF_KEEP_FRAMES] is True
