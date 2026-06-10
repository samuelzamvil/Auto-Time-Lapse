"""Tests for the ffmpeg renderer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.auto_time_lapse.renderer import (
    RenderError,
    async_render_timelapse,
)


def _mock_proc(returncode: int, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.returncode = returncode
    return proc


async def test_render_builds_expected_command(hass):
    """The ffmpeg invocation uses the discovered binary and safe encoding flags."""
    proc = _mock_proc(0)
    with (
        patch(
            "custom_components.auto_time_lapse.renderer.get_ffmpeg_manager"
        ) as mock_manager,
        patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec,
    ):
        mock_manager.return_value.binary = "/usr/bin/ffmpeg"
        await async_render_timelapse(
            hass, Path("/frames"), Path("/out/video.mp4"), fps=24
        )

    argv = list(mock_exec.call_args.args)
    assert argv[0] == "/usr/bin/ffmpeg"
    assert argv[argv.index("-framerate") + 1] == "24"
    assert argv[argv.index("-i") + 1] == "/frames/frame_%06d.jpg"
    assert argv[argv.index("-vf") + 1] == (
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
    )
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-movflags") + 1] == "+faststart"
    assert argv[-1] == "/out/video.mp4"


async def test_render_failure_raises(hass):
    """A nonzero ffmpeg exit code raises RenderError with the stderr tail."""
    proc = _mock_proc(1, stderr=b"boom")
    with (
        patch(
            "custom_components.auto_time_lapse.renderer.get_ffmpeg_manager"
        ) as mock_manager,
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        mock_manager.return_value.binary = "ffmpeg"
        with pytest.raises(RenderError, match="boom"):
            await async_render_timelapse(
                hass, Path("/frames"), Path("/out/video.mp4"), fps=30
            )
