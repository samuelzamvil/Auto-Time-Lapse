"""Capture session management for Auto Time Lapse."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from functools import partial
import logging
from pathlib import Path
import shutil

from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_ON
from homeassistant.core import (
    CALLBACK_TYPE,
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util, slugify

from .const import (
    CONF_CAMERA_ENTITY,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_TRIGGER_MODE,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_INTERVAL,
    DEFAULT_KEEP_FRAMES,
    DEFAULT_OUTPUT_FPS,
    DOMAIN,
    EVENT_TIMELAPSE_FINISHED,
    FRAME_FILENAME,
    MAX_LOGGED_FAILURES,
    OUTPUT_SUBDIR,
    SNAPSHOT_TIMEOUT,
    SessionState,
    TriggerMode,
)
from .renderer import RenderError, async_render_timelapse

_LOGGER = logging.getLogger(__name__)


class TimelapseManager:
    """Drives one trigger profile: trigger wiring, frame capture, rendering."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.subentry = subentry
        self.frame_count = 0
        self.failed_frame_count = 0
        self.last_video_path: str | None = None
        self.session_started_at: datetime | None = None
        self._capturing = False
        self._rendering = False
        self._capture_in_flight = False
        self._session_dir: Path | None = None
        self._last_session_dir: Path | None = None
        self._last_session_frames = 0
        self._render_lock = asyncio.Lock()
        self._unsub_interval: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ options

    @property
    def _options(self) -> dict:
        return dict(self.subentry.data)

    @property
    def title(self) -> str:
        return self.subentry.title

    @property
    def camera_entity(self) -> str:
        return self.entry.data[CONF_CAMERA_ENTITY]

    @property
    def trigger_mode(self) -> TriggerMode:
        try:
            return TriggerMode(
                self._options.get(CONF_TRIGGER_MODE, TriggerMode.MANUAL)
            )
        except ValueError:
            return TriggerMode.MANUAL

    @property
    def interval(self) -> int:
        return int(self._options.get(CONF_INTERVAL, DEFAULT_INTERVAL))

    @property
    def output_fps(self) -> int:
        return int(self._options.get(CONF_OUTPUT_FPS, DEFAULT_OUTPUT_FPS))

    @property
    def keep_frames(self) -> bool:
        return bool(self._options.get(CONF_KEEP_FRAMES, DEFAULT_KEEP_FRAMES))

    @property
    def watch_states(self) -> list[str]:
        return list(self._options.get(CONF_WATCH_STATES) or [STATE_ON])

    @property
    def state(self) -> SessionState:
        if self._capturing:
            return SessionState.CAPTURING
        if self._rendering:
            return SessionState.RENDERING
        return SessionState.IDLE

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    @property
    def media_content_id(self) -> str | None:
        """Media-source URI for the last video if it is inside a media dir."""
        if not self.last_video_path:
            return None
        for name, base in (self.hass.config.media_dirs or {}).items():
            try:
                rel = Path(self.last_video_path).relative_to(base)
            except ValueError:
                continue
            return f"media-source://media_source/{name}/{rel}"
        return None

    @property
    def _frames_base_dir(self) -> Path:
        return Path(self.hass.config.path(DOMAIN, self.subentry.subentry_id))

    # ------------------------------------------------------------------ setup

    async def async_setup(self) -> None:
        """Wire the trigger and clean up stale frames from previous runs."""
        await self._async_cleanup_stale_frames()

        options = self._options
        mode = self.trigger_mode
        if mode is TriggerMode.SCHEDULE:
            self._setup_schedule(options)
        elif mode is TriggerMode.WATCH and (
            watch_entity := options.get(CONF_WATCH_ENTITY)
        ):
            self._setup_watch(watch_entity)

    def _setup_schedule(self, options: dict) -> None:
        start_t = dt_util.parse_time(options.get(CONF_SCHEDULE_START) or "")
        end_t = dt_util.parse_time(options.get(CONF_SCHEDULE_END) or "")
        if start_t is None or end_t is None or start_t == end_t:
            _LOGGER.error(
                "Invalid schedule for %s (start=%s end=%s); schedule disabled",
                self.title,
                options.get(CONF_SCHEDULE_START),
                options.get(CONF_SCHEDULE_END),
            )
            return
        self._unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_on_window_start,
                hour=start_t.hour,
                minute=start_t.minute,
                second=start_t.second,
            )
        )
        self._unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_on_window_end,
                hour=end_t.hour,
                minute=end_t.minute,
                second=end_t.second,
            )
        )

        # If we load mid-window (HA restart or entry reload), start right away.
        @callback
        def _initial_check() -> None:
            if self._is_in_window(start_t, end_t, dt_util.now().time()):
                self.hass.async_create_task(self.async_start())

        self._defer_until_running(_initial_check)

    def _setup_watch(self, watch_entity: str) -> None:
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [watch_entity], self._async_on_watch_change
            )
        )

        # If the entity is already active (e.g. print running at HA restart),
        # start right away.
        @callback
        def _initial_check() -> None:
            state = self.hass.states.get(watch_entity)
            if state is not None and state.state in self.watch_states:
                self.hass.async_create_task(self.async_start())

        self._defer_until_running(_initial_check)

    @callback
    def _defer_until_running(self, check: CALLBACK_TYPE) -> None:
        """Run check now, or once HA has fully started if it is still booting."""
        if self.hass.state is CoreState.running:
            check()
        else:

            @callback
            def _on_started(_: Event) -> None:
                check()

            self._unsubs.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, _on_started
                )
            )

    @staticmethod
    def _is_in_window(start: time, end: time, now: time) -> bool:
        if start < end:
            return start <= now < end
        # Overnight window, e.g. 22:00 -> 06:00.
        return now >= start or now < end

    async def _async_cleanup_stale_frames(self) -> None:
        if self.keep_frames:
            return
        base = self._frames_base_dir

        def _cleanup() -> int:
            if not base.is_dir():
                return 0
            removed = 0
            for child in base.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            return removed

        removed = await self.hass.async_add_executor_job(_cleanup)
        if removed:
            _LOGGER.info(
                "Removed %d stale frame session(s) for %s from a previous run",
                removed,
                self.title,
            )

    async def async_unload(self) -> None:
        """Tear down triggers; abandon any running capture session."""
        if self._capturing:
            _LOGGER.info(
                "Unloading %s while capturing; abandoning current session",
                self.title,
            )
        self._cancel_interval()
        self._capturing = False
        self._session_dir = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._listeners.clear()

    # ------------------------------------------------------------------ triggers

    async def _async_on_window_start(self, now: datetime) -> None:
        await self.async_start()

    async def _async_on_window_end(self, now: datetime) -> None:
        await self.async_stop(render=True)

    @callback
    def _async_on_watch_change(self, event: Event[EventStateChangedData]) -> None:
        active_states = self.watch_states
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        new_active = new_state is not None and new_state.state in active_states
        old_active = old_state is not None and old_state.state in active_states
        if new_active and not old_active:
            self.hass.async_create_task(self.async_start())
        elif old_active and not new_active:
            # Includes the entity being removed or going unavailable/unknown:
            # the session ends and the video is completed.
            self.hass.async_create_task(self.async_stop(render=True))

    # ------------------------------------------------------------------ session

    async def async_start(self) -> None:
        """Begin a capture session."""
        if self._capturing:
            _LOGGER.debug("%s is already capturing", self.title)
            return
        session_dir = self._frames_base_dir / dt_util.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        await self.hass.async_add_executor_job(
            partial(session_dir.mkdir, parents=True, exist_ok=True)
        )
        self._session_dir = session_dir
        self.frame_count = 0
        self.failed_frame_count = 0
        self.session_started_at = dt_util.now()
        self._capturing = True
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_capture_frame, timedelta(seconds=self.interval)
        )
        _LOGGER.info(
            "Started timelapse capture for %s (camera %s, every %d s)",
            self.title,
            self.camera_entity,
            self.interval,
        )
        self._notify()
        await self._async_capture_frame()

    async def async_stop(self, render: bool = True) -> None:
        """End the capture session, optionally rendering the video."""
        if not self._capturing:
            return
        self._cancel_interval()
        self._capturing = False
        session_dir = self._session_dir
        frames = self.frame_count
        self._session_dir = None
        self.session_started_at = None
        _LOGGER.info(
            "Stopped timelapse capture for %s with %d frame(s)",
            self.title,
            frames,
        )
        if session_dir is not None:
            if render and frames > 0:
                self.hass.async_create_task(self._async_render(session_dir, frames))
            elif frames > 0 and self.keep_frames:
                self._last_session_dir = session_dir
                self._last_session_frames = frames
            else:
                await self._async_remove_dir(session_dir)
        self._notify()

    async def async_cancel(self) -> None:
        """Abort the capture session and discard its frames."""
        if not self._capturing:
            return
        self._cancel_interval()
        self._capturing = False
        session_dir = self._session_dir
        self._session_dir = None
        self.session_started_at = None
        if session_dir is not None:
            await self._async_remove_dir(session_dir)
        _LOGGER.info("Cancelled timelapse capture for %s", self.title)
        self._notify()

    async def async_rerender(self) -> None:
        """Render the most recent retained frame set again."""
        session_dir = self._last_session_dir
        if (
            session_dir is None
            or self._last_session_frames == 0
            or not await self.hass.async_add_executor_job(session_dir.is_dir)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_frames"
            )
        await self._async_render(session_dir, self._last_session_frames)

    @callback
    def _cancel_interval(self) -> None:
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

    # ------------------------------------------------------------------ capture

    async def _async_capture_frame(self, now: datetime | None = None) -> None:
        if not self._capturing or self._session_dir is None:
            return
        if self._capture_in_flight:
            _LOGGER.debug("Skipping frame for %s; capture in flight", self.title)
            return
        self._capture_in_flight = True
        session_dir = self._session_dir
        try:
            image = await async_get_image(
                self.hass, self.camera_entity, timeout=SNAPSHOT_TIMEOUT
            )
        except HomeAssistantError as err:
            self.failed_frame_count += 1
            log = (
                _LOGGER.warning
                if self.failed_frame_count <= MAX_LOGGED_FAILURES
                else _LOGGER.debug
            )
            log(
                "Failed to capture frame from %s for %s (%d failure(s) so far): %s",
                self.camera_entity,
                self.title,
                self.failed_frame_count,
                err,
            )
            self._notify()
            return
        finally:
            self._capture_in_flight = False
        # Bail out if the session ended while we were fetching the image.
        if not self._capturing or self._session_dir is not session_dir:
            return
        path = session_dir / FRAME_FILENAME.format(index=self.frame_count)
        await self.hass.async_add_executor_job(path.write_bytes, image.content)
        self.frame_count += 1
        self._notify()

    # ------------------------------------------------------------------ render

    async def _async_render(self, session_dir: Path, frames: int) -> None:
        async with self._render_lock:
            self._rendering = True
            self._notify()
            try:
                output_path = await self._async_prepare_output_path()
                _LOGGER.info(
                    "Rendering %d frame(s) for %s to %s",
                    frames,
                    self.title,
                    output_path,
                )
                await async_render_timelapse(
                    self.hass, session_dir, output_path, self.output_fps
                )
            except (RenderError, HomeAssistantError) as err:
                _LOGGER.error(
                    "Timelapse render failed for %s; frames kept at %s: %s",
                    self.title,
                    session_dir,
                    err,
                )
                self._last_session_dir = session_dir
                self._last_session_frames = frames
            else:
                self.last_video_path = str(output_path)
                self.hass.bus.async_fire(
                    EVENT_TIMELAPSE_FINISHED,
                    {
                        "entry_id": self.entry.entry_id,
                        "subentry_id": self.subentry.subentry_id,
                        "name": self.title,
                        "path": str(output_path),
                        "frame_count": frames,
                    },
                )
                _LOGGER.info(
                    "Timelapse for %s saved to %s", self.title, output_path
                )
                if self.keep_frames:
                    self._last_session_dir = session_dir
                    self._last_session_frames = frames
                else:
                    await self._async_remove_dir(session_dir)
                    if self._last_session_dir == session_dir:
                        self._last_session_dir = None
                        self._last_session_frames = 0
            finally:
                self._rendering = False
                self._notify()

    async def _async_prepare_output_path(self) -> Path:
        options = self._options
        if output_dir := options.get(CONF_OUTPUT_DIR):
            out_dir = Path(output_dir)
            if not self.hass.config.is_allowed_path(str(out_dir)):
                raise RenderError(
                    f"Output directory {out_dir} is not in allowlist_external_dirs "
                    "or a configured media dir"
                )
        else:
            media_dirs = self.hass.config.media_dirs or {}
            base = media_dirs.get("local") or self.hass.config.path("media")
            out_dir = Path(base) / OUTPUT_SUBDIR
        await self.hass.async_add_executor_job(
            partial(out_dir.mkdir, parents=True, exist_ok=True)
        )
        return out_dir / self._build_filename()

    def _build_filename(self) -> str:
        pattern = self._options.get(CONF_FILENAME_PATTERN) or DEFAULT_FILENAME_PATTERN
        values = {
            "name": slugify(self.title),
            "timestamp": dt_util.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "entry_id": self.subentry.subentry_id,
        }
        try:
            filename = pattern.format(**values)
        except (KeyError, IndexError, ValueError):
            _LOGGER.warning(
                "Invalid filename pattern %r for %s; using default",
                pattern,
                self.title,
            )
            filename = DEFAULT_FILENAME_PATTERN.format(**values)
        if not filename.endswith(".mp4"):
            filename += ".mp4"
        return filename

    async def _async_remove_dir(self, path: Path) -> None:
        await self.hass.async_add_executor_job(
            partial(shutil.rmtree, path, ignore_errors=True)
        )

    # ------------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe an entity to state updates; returns an unsubscribe callback."""
        self._listeners.append(listener)

        @callback
        def _unsub() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsub

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
