"""Render timelapse videos from captured frames using ffmpeg."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_VIDEO_CRF,
    DEFAULT_VIDEO_PRESET,
    FRAME_PATTERN,
    RENDER_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class RenderError(HomeAssistantError):
    """Raised when ffmpeg fails to render a timelapse."""


async def async_render_timelapse(
    hass: HomeAssistant,
    session_dir: Path,
    output_path: Path,
    fps: int,
    *,
    crf: int = DEFAULT_VIDEO_CRF,
    preset: str = DEFAULT_VIDEO_PRESET,
    max_width: int | None = None,
) -> None:
    """Stitch the numbered JPEG frames in session_dir into an MP4 at output_path."""
    binary = get_ffmpeg_manager(hass).binary
    if max_width:
        # min() caps the width without ever upscaling; trunc(./2)*2 forces an
        # even width and -2 an even aspect-preserving height (libx264 needs
        # even dimensions). The \, escapes the comma inside min() for the
        # filter parser.
        vf = f"scale=trunc(min(iw\\,{max_width})/2)*2:-2,format=yuv420p"
    else:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
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
        vf,
        "-c:v",
        "libx264",
        "-preset",
        str(preset),
        "-crf",
        str(crf),
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

    async def _remove_partial() -> None:
        # ffmpeg runs with -y, so a failed render leaves a truncated file in
        # the user's output dir. Drop it off the event loop, and never let a
        # cleanup error mask the original RenderError.
        try:
            await hass.async_add_executor_job(
                partial(output_path.unlink, missing_ok=True)
            )
        except OSError as err:
            _LOGGER.warning(
                "Could not remove partial render output %s: %s", output_path, err
            )

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=RENDER_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        # The killed process buffered its stderr; a second communicate()
        # drains it so the logged error is not empty.
        _, stderr = await proc.communicate()
        await _remove_partial()
        tail = stderr.decode(errors="replace")[-2000:]
        raise RenderError(
            f"ffmpeg timed out after {RENDER_TIMEOUT} seconds: {tail}"
        ) from None
    if proc.returncode != 0:
        await _remove_partial()
        tail = stderr.decode(errors="replace")[-2000:]
        raise RenderError(f"ffmpeg exited with code {proc.returncode}: {tail}")
