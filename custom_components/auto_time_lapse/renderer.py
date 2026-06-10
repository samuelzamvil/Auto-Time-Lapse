"""Render timelapse videos from captured frames using ffmpeg."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import FRAME_PATTERN, RENDER_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class RenderError(HomeAssistantError):
    """Raised when ffmpeg fails to render a timelapse."""


async def async_render_timelapse(
    hass: HomeAssistant, session_dir: Path, output_path: Path, fps: int
) -> None:
    """Stitch the numbered JPEG frames in session_dir into an MP4 at output_path."""
    binary = get_ffmpeg_manager(hass).binary
    args = [
        "-y",
        "-nostdin",
        "-framerate",
        str(fps),
        "-i",
        str(session_dir / FRAME_PATTERN),
        # libx264 requires even dimensions; yuv420p + faststart make the file
        # playable/streamable in browsers (and HA's Media Browser).
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _LOGGER.debug("Running render: %s %s", binary, " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=RENDER_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RenderError(f"ffmpeg timed out after {RENDER_TIMEOUT} seconds") from None
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-2000:]
        raise RenderError(f"ffmpeg exited with code {proc.returncode}: {tail}")
