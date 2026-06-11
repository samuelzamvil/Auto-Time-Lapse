"""Capture session management for Auto Time Lapse."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
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
from homeassistant.exceptions import (
    ConditionError,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import condition
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util, slugify
import voluptuous as vol

from .const import (
    BUFFER_SAFETY_FACTOR,
    BUFFER_SAFETY_MIN,
    CONF_CAMERA_ENTITY,
    CONF_CAPTURE_MODE,
    CONF_CONDITIONAL_REEVALUATE,
    CONF_CONDITIONAL_RULES,
    CONF_DURATION_ENTITY,
    CONF_DURATION_TYPE,
    CONF_END_BUFFER_AMOUNT,
    CONF_END_BUFFER_INTERVAL,
    CONF_END_BUFFER_MODE,
    CONF_END_BUFFER_RETRIGGER,
    CONF_FALLBACK_INTERVAL,
    CONF_FILENAME_PATTERN,
    CONF_INTERVAL,
    CONF_KEEP_FRAMES,
    CONF_OUTPUT_DIR,
    CONF_OUTPUT_FPS,
    CONF_RULE_CONDITIONS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_START,
    CONF_TARGET_LENGTH,
    CONF_TRIGGER_MODE,
    CONF_VALUE_DELTA,
    CONF_VALUE_DIRECTION,
    CONF_VALUE_ENTITY,
    CONF_WATCH_ENTITY,
    CONF_WATCH_STATES,
    DEFAULT_CONDITIONAL_REEVALUATE,
    DEFAULT_END_BUFFER_AMOUNT,
    DEFAULT_FALLBACK_INTERVAL,
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_INTERVAL,
    DEFAULT_KEEP_FRAMES,
    DEFAULT_OUTPUT_FPS,
    DEFAULT_TARGET_LENGTH,
    DEFAULT_VALUE_DELTA,
    DOMAIN,
    EVENT_TIMELAPSE_FINISHED,
    FRAME_FILENAME,
    MAX_LOGGED_FAILURES,
    OUTPUT_SUBDIR,
    SNAPSHOT_TIMEOUT,
    BufferRetrigger,
    CaptureMode,
    DurationType,
    EndBufferMode,
    SessionPhase,
    SessionState,
    TriggerMode,
    ValueDirection,
)
from .renderer import RenderError, async_render_timelapse
from .storage import SessionRecord, SessionStore, async_get_session_store

_LOGGER = logging.getLogger(__name__)

_DURATION_MULTIPLIER = {
    DurationType.SECONDS: 1.0,
    DurationType.MINUTES: 60.0,
    DurationType.HOURS: 3600.0,
}


def _scan_existing_frames(session_dir: Path) -> int:
    """Return the next frame index based on the frames already on disk."""
    next_index = 0
    for frame in session_dir.glob("frame_*.jpg"):
        try:
            index = int(frame.stem.removeprefix("frame_"))
        except ValueError:
            continue
        next_index = max(next_index, index + 1)
    return next_index


@dataclass(slots=True)
class ResumeInfo:
    """An interrupted session found on disk at setup."""

    session_dir: Path
    frame_count: int
    started_at: datetime | None
    phase: SessionPhase


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
        self._unsub_capture: CALLBACK_TYPE | None = None
        self._unsub_conditions: CALLBACK_TYPE | None = None
        self._rule_checkers: list[
            tuple[dict, condition.ConditionCheckerType | None]
        ] = []
        self._active_rule: dict | None = None
        self._active_rule_index: int | None = None
        self._value_baseline: float | None = None
        self._buffering = False
        self._buffer_frames_remaining: int | None = None
        self._unsub_buffer_deadline: CALLBACK_TYPE | None = None
        self._buffer_cadence_rewired = False
        self._session_capture_seconds: float | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._store: SessionStore = async_get_session_store(hass)
        self._resume_infos: list[ResumeInfo] = []

    # ------------------------------------------------------------------ options

    @property
    def _options(self) -> dict:
        return dict(self.subentry.data)

    @property
    def _cadence_options(self) -> dict:
        """Where cadence settings come from: the active conditional rule, if any."""
        if self._active_rule is not None:
            return self._active_rule
        return self._options

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
        return int(self._cadence_options.get(CONF_INTERVAL, DEFAULT_INTERVAL))

    @property
    def duration_entity(self) -> str | None:
        return self._options.get(CONF_DURATION_ENTITY)

    @property
    def duration_type(self) -> DurationType:
        try:
            return DurationType(
                self._options.get(CONF_DURATION_TYPE, DurationType.SECONDS)
            )
        except ValueError:
            return DurationType.SECONDS

    @property
    def target_length(self) -> float:
        return float(self._options.get(CONF_TARGET_LENGTH, DEFAULT_TARGET_LENGTH))

    @property
    def fallback_interval(self) -> int:
        return int(
            self._options.get(CONF_FALLBACK_INTERVAL, DEFAULT_FALLBACK_INTERVAL)
        )

    @property
    def capture_mode(self) -> CaptureMode:
        try:
            return CaptureMode(self._options.get(CONF_CAPTURE_MODE, CaptureMode.TIME))
        except ValueError:
            return CaptureMode.TIME

    @property
    def value_entity(self) -> str | None:
        return self._cadence_options.get(CONF_VALUE_ENTITY)

    @property
    def value_delta(self) -> float:
        return float(self._cadence_options.get(CONF_VALUE_DELTA, DEFAULT_VALUE_DELTA))

    @property
    def value_direction(self) -> ValueDirection:
        try:
            return ValueDirection(
                self._cadence_options.get(CONF_VALUE_DIRECTION, ValueDirection.ANY)
            )
        except ValueError:
            return ValueDirection.ANY

    @property
    def conditional_rules(self) -> list[dict]:
        return list(self._options.get(CONF_CONDITIONAL_RULES) or [])

    @property
    def conditional_reevaluate(self) -> bool:
        return bool(
            self._options.get(
                CONF_CONDITIONAL_REEVALUATE, DEFAULT_CONDITIONAL_REEVALUATE
            )
        )

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
    def end_buffer_mode(self) -> EndBufferMode:
        try:
            return EndBufferMode(
                self._options.get(CONF_END_BUFFER_MODE, EndBufferMode.OFF)
            )
        except ValueError:
            return EndBufferMode.OFF

    @property
    def end_buffer_amount(self) -> int:
        return int(
            self._options.get(CONF_END_BUFFER_AMOUNT, DEFAULT_END_BUFFER_AMOUNT)
        )

    @property
    def end_buffer_interval(self) -> int | None:
        """Snapshot interval during the buffer; None keeps the session cadence."""
        value = self._options.get(CONF_END_BUFFER_INTERVAL)
        return int(value) if value else None

    @property
    def end_buffer_retrigger(self) -> BufferRetrigger:
        try:
            return BufferRetrigger(
                self._options.get(CONF_END_BUFFER_RETRIGGER, BufferRetrigger.RESUME)
            )
        except ValueError:
            return BufferRetrigger.RESUME

    @property
    def state(self) -> SessionState:
        if self._capturing:
            if self._buffering:
                return SessionState.BUFFERING
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
        """Wire the trigger; adopt interrupted sessions, clean stale frames."""
        self._resume_infos = await self._async_load_resume_infos()
        await self._async_cleanup_stale_frames(
            keep={info.session_dir.name for info in self._resume_infos}
        )

        options = self._options
        mode = self.trigger_mode
        if mode is TriggerMode.SCHEDULE:
            self._setup_schedule(options)
        elif mode is TriggerMode.WATCH and (
            watch_entity := options.get(CONF_WATCH_ENTITY)
        ):
            self._setup_watch(watch_entity)
        else:
            self._setup_manual()

    async def _async_load_resume_infos(self) -> list[ResumeInfo]:
        """Read persisted session records and match them to frames on disk."""
        await self._store.async_load()
        infos: list[ResumeInfo] = []
        seen_capturing = False
        for dir_name, record in self._store.records(
            self.subentry.subentry_id
        ).items():
            session_dir = self._frames_base_dir / dir_name
            if not await self.hass.async_add_executor_job(session_dir.is_dir):
                _LOGGER.warning(
                    "Frames of interrupted session %s for %s are gone; "
                    "dropping its record",
                    dir_name,
                    self.title,
                )
                await self._store.async_remove(self.subentry.subentry_id, dir_name)
                continue
            frame_count = await self.hass.async_add_executor_job(
                _scan_existing_frames, session_dir
            )
            phase = record.phase
            if phase is SessionPhase.CAPTURING:
                # Only one session can resume; render any extras.
                if seen_capturing:
                    phase = SessionPhase.PENDING_RENDER
                seen_capturing = True
            started_at = (
                dt_util.parse_datetime(record.started_at)
                if record.started_at
                else None
            )
            infos.append(ResumeInfo(session_dir, frame_count, started_at, phase))
        return infos

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
            # Still salvage any session interrupted by a restart.
            self._setup_initial_check(lambda: False)
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

        # If we load mid-window (HA restart or entry reload), resume an
        # interrupted session or start right away.
        self._setup_initial_check(
            lambda: self._is_in_window(start_t, end_t, dt_util.now().time())
        )

    def _setup_watch(self, watch_entity: str) -> None:
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [watch_entity], self._async_on_watch_change
            )
        )

        # If the entity is already active (e.g. print running at HA restart),
        # resume an interrupted session or start right away.
        def _is_active() -> bool:
            state = self.hass.states.get(watch_entity)
            return state is not None and state.state in self.watch_states

        self._setup_initial_check(_is_active)

    def _setup_manual(self) -> None:
        # A manual session only needs a startup check when one was interrupted.
        if not self._resume_infos:
            return

        def _was_capturing() -> bool:
            return any(
                info.phase is SessionPhase.CAPTURING for info in self._resume_infos
            )

        self._setup_initial_check(_was_capturing)

    @callback
    def _setup_initial_check(self, conditions_active: Callable[[], bool]) -> None:
        """Run the resume/salvage/start decision once HA is running."""

        @callback
        def _initial_check() -> None:
            self.hass.async_create_task(
                self._async_initial_check(conditions_active=conditions_active())
            )

        self._defer_until_running(_initial_check)

    async def _async_initial_check(self, *, conditions_active: bool) -> None:
        """Resume, salvage, or freshly start sessions at startup."""
        infos, self._resume_infos = self._resume_infos, []
        resumable: ResumeInfo | None = None
        for info in infos:
            if (
                resumable is None
                and info.phase is SessionPhase.CAPTURING
                and conditions_active
                and not self._capturing
            ):
                resumable = info
            else:
                await self._async_salvage(info)
        if resumable is not None:
            await self.async_resume(resumable)
        elif conditions_active and not self._capturing:
            await self.async_start()

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

    async def _async_cleanup_stale_frames(self, keep: set[str]) -> None:
        if self.keep_frames:
            return
        base = self._frames_base_dir

        def _cleanup() -> int:
            if not base.is_dir():
                return 0
            removed = 0
            for child in base.iterdir():
                if child.is_dir() and child.name not in keep:
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
        """Tear down triggers; a running session resumes at the next setup."""
        if self._capturing:
            _LOGGER.info(
                "Unloading %s while capturing; the session will resume after "
                "restart or reload",
                self.title,
            )
        self._clear_buffer_state()
        self._cancel_capture_listener()
        self._cancel_condition_listener()
        self._capturing = False
        self._session_dir = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._listeners.clear()

    # ------------------------------------------------------------------ triggers

    async def _async_on_window_start(self, now: datetime) -> None:
        await self._async_trigger_start()

    async def _async_on_window_end(self, now: datetime) -> None:
        await self._async_trigger_stop()

    @callback
    def _async_on_watch_change(self, event: Event[EventStateChangedData]) -> None:
        active_states = self.watch_states
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        new_active = new_state is not None and new_state.state in active_states
        old_active = old_state is not None and old_state.state in active_states
        if new_active and not old_active:
            self.hass.async_create_task(self._async_trigger_start())
        elif old_active and not new_active:
            # Includes the entity being removed or going unavailable/unknown:
            # the session ends and the video is completed.
            self.hass.async_create_task(self._async_trigger_stop())

    async def _async_trigger_start(self) -> None:
        """Handle the trigger condition becoming active."""
        if self._buffering:
            if self.end_buffer_retrigger is BufferRetrigger.RESUME:
                self._async_resume_from_buffer()
            # FINISH: _async_finish_buffer re-checks the live condition when
            # the buffer ends and starts a fresh session then.
            return
        await self.async_start()

    async def _async_trigger_stop(self) -> None:
        """Handle the trigger condition becoming inactive."""
        if not self._capturing or self._buffering:
            return
        if self.end_buffer_mode is EndBufferMode.OFF:
            await self.async_stop(render=True)
            return
        self._begin_buffer()

    def _trigger_conditions_active(self) -> bool:
        """Return whether the trigger condition currently holds."""
        options = self._options
        if self.trigger_mode is TriggerMode.WATCH:
            if not (entity := options.get(CONF_WATCH_ENTITY)):
                return False
            state = self.hass.states.get(entity)
            return state is not None and state.state in self.watch_states
        if self.trigger_mode is TriggerMode.SCHEDULE:
            start_t = dt_util.parse_time(options.get(CONF_SCHEDULE_START) or "")
            end_t = dt_util.parse_time(options.get(CONF_SCHEDULE_END) or "")
            if start_t is None or end_t is None or start_t == end_t:
                return False
            return self._is_in_window(start_t, end_t, dt_util.now().time())
        return False

    # ------------------------------------------------------------------ buffer

    @callback
    def _begin_buffer(self) -> None:
        """Keep capturing past the trigger end for the configured buffer."""
        self._buffering = True
        override = self.end_buffer_interval
        if override is not None or self._effective_value_change():
            # A value-change cadence always goes time-based during the
            # buffer: the watched value typically stops moving once the
            # trigger ends. The config flow requires an override interval in
            # that case; fall back to the plain interval if it is missing.
            seconds = float(override or self.interval)
            self._cancel_capture_listener()
            self._unsub_capture = async_track_time_interval(
                self.hass, self._async_capture_frame, timedelta(seconds=seconds)
            )
            self._buffer_cadence_rewired = True
        else:
            seconds = self._session_capture_seconds or float(self.interval)
        if self.end_buffer_mode is EndBufferMode.FRAMES:
            self._buffer_frames_remaining = self.end_buffer_amount
            # If the camera stops delivering, only failures arrive and the
            # counter never reaches zero; end the buffer after a generous
            # time budget instead of capturing forever.
            budget = max(
                self.end_buffer_amount * seconds * BUFFER_SAFETY_FACTOR,
                BUFFER_SAFETY_MIN,
            )
            self._unsub_buffer_deadline = async_call_later(
                self.hass, budget, self._async_on_buffer_deadline
            )
        else:
            self._unsub_buffer_deadline = async_call_later(
                self.hass,
                float(self.end_buffer_amount),
                self._async_on_buffer_deadline,
            )
        _LOGGER.info(
            "Trigger ended for %s; buffering %d more %s",
            self.title,
            self.end_buffer_amount,
            self.end_buffer_mode.value,
        )
        self._notify()

    async def _async_on_buffer_deadline(self, now: datetime) -> None:
        if (remaining := self._buffer_frames_remaining) is not None and remaining > 0:
            _LOGGER.warning(
                "Buffer for %s timed out with %d frame(s) outstanding; "
                "ending it now",
                self.title,
                remaining,
            )
        await self._async_finish_buffer()

    async def _async_finish_buffer(self) -> None:
        """End the buffer: render, and restart if configured and re-triggered."""
        if not self._buffering:
            return
        restart = (
            self.end_buffer_retrigger is BufferRetrigger.FINISH
            and self._trigger_conditions_active()
        )
        await self.async_stop(render=True)
        if restart:
            await self.async_start()

    def _effective_value_change(self) -> bool:
        """Whether the cadence currently in effect is value-change paced."""
        if self.capture_mode is CaptureMode.VALUE_CHANGE:
            return True
        return (
            self.capture_mode is CaptureMode.CONDITIONAL
            and self._active_rule is not None
            and self._active_rule.get(CONF_CAPTURE_MODE) == CaptureMode.VALUE_CHANGE
        )

    @callback
    def _async_resume_from_buffer(self) -> None:
        """Abort the buffer and continue capturing in the same session."""
        rewired = self._buffer_cadence_rewired
        self._clear_buffer_state()
        # Rule re-evaluation is suspended while buffering, so a conditional
        # cadence must re-select on resume even if it was not rewired.
        if rewired or (
            self.capture_mode is CaptureMode.CONDITIONAL
            and self.conditional_reevaluate
        ):
            self._cancel_capture_listener()
            self._wire_capture_cadence()
        _LOGGER.info(
            "Trigger re-activated for %s; resuming capture in the same session",
            self.title,
        )
        self._notify()

    @callback
    def _clear_buffer_state(self) -> None:
        if self._unsub_buffer_deadline is not None:
            self._unsub_buffer_deadline()
            self._unsub_buffer_deadline = None
        self._buffering = False
        self._buffer_frames_remaining = None
        self._buffer_cadence_rewired = False

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
        await self._async_persist(session_dir, SessionPhase.CAPTURING)
        await self._begin_capture()

    async def async_resume(self, info: ResumeInfo) -> None:
        """Continue a capture session that a restart interrupted."""
        if self._capturing:
            _LOGGER.debug("%s is already capturing", self.title)
            return
        self._session_dir = info.session_dir
        self.frame_count = info.frame_count
        self.failed_frame_count = 0
        self.session_started_at = info.started_at or dt_util.now()
        self._capturing = True
        _LOGGER.info(
            "Resuming interrupted timelapse session for %s with %d existing "
            "frame(s)",
            self.title,
            info.frame_count,
        )
        await self._async_persist(info.session_dir, SessionPhase.CAPTURING)
        await self._begin_capture()

    async def _begin_capture(self) -> None:
        """Wire the frame cadence for the just-started session."""
        self._session_capture_seconds = None
        if self.capture_mode is CaptureMode.CONDITIONAL:
            await self._async_setup_conditional()
        self._wire_capture_cadence()
        self._notify()
        await self._async_capture_frame()

    async def _async_setup_conditional(self) -> None:
        """Build the rule condition checkers and track their entities."""
        self._cancel_condition_listener()
        checkers: list[tuple[dict, condition.ConditionCheckerType | None]] = []
        entities: set[str] = set()
        for index, rule in enumerate(self.conditional_rules):
            conditions = rule.get(CONF_RULE_CONDITIONS)
            if not conditions:
                # The default rule: always matches.
                checkers.append((rule, None))
                continue
            config = {"condition": "and", "conditions": conditions}
            try:
                config = await condition.async_validate_condition_config(
                    self.hass, config
                )
                checker = await condition.async_from_config(self.hass, config)
            except (vol.Invalid, HomeAssistantError) as err:
                _LOGGER.error(
                    "Invalid conditions in cadence rule %d for %s; the rule "
                    "will never match: %s",
                    index + 1,
                    self.title,
                    err,
                )
                checkers.append((rule, lambda hass, variables: False))
                continue
            checkers.append((rule, checker))
            entities |= condition.async_extract_entities(config)
        self._rule_checkers = checkers
        if self.conditional_reevaluate and entities:
            self._unsub_conditions = async_track_state_change_event(
                self.hass, list(entities), self._async_on_condition_change
            )

    def _select_conditional_rule(self) -> tuple[int | None, dict | None]:
        """Return the first rule whose conditions currently hold."""
        for index, (rule, checker) in enumerate(self._rule_checkers):
            if checker is None:
                return index, rule
            try:
                if checker(self.hass, None):
                    return index, rule
            except ConditionError as err:
                _LOGGER.debug(
                    "Cadence rule %d for %s failed to evaluate: %s",
                    index + 1,
                    self.title,
                    err,
                )
        if self._rule_checkers:
            # No rule matched; the config flow enforces a condition-less
            # default rule, so this only happens with hand-edited data.
            index = len(self._rule_checkers) - 1
            return index, self._rule_checkers[index][0]
        return None, None

    @callback
    def _wire_capture_cadence(self) -> None:
        """Hook up the capture listener for the session's cadence."""
        if self.capture_mode is CaptureMode.CONDITIONAL:
            self._wire_conditional_cadence()
            return
        if self.capture_mode is CaptureMode.VALUE_CHANGE and self.value_entity:
            self._wire_value_change()
        else:
            # The interval is computed once and stays frozen for the whole
            # session, including when capture is re-wired after a buffer.
            if self._session_capture_seconds is None:
                self._session_capture_seconds = self._capture_interval_seconds()
            self._wire_time_interval(self._session_capture_seconds)

    @callback
    def _wire_conditional_cadence(self) -> None:
        """Wire the cadence of the conditional rule that currently matches."""
        # With live re-evaluation off, the rule selected at session start
        # stays locked for the whole session (including buffer rewires).
        if self._active_rule is None or self.conditional_reevaluate:
            self._active_rule_index, self._active_rule = (
                self._select_conditional_rule()
            )
        rule = self._active_rule
        if (
            rule is not None
            and rule.get(CONF_CAPTURE_MODE) == CaptureMode.VALUE_CHANGE
            and self.value_entity
        ):
            self._wire_value_change()
        else:
            # The interval property reads from the active rule here, so the
            # cadence is never frozen via _session_capture_seconds.
            self._wire_time_interval(float(self.interval))

    @callback
    def _wire_value_change(self) -> None:
        """Capture a frame per movement of the watched value entity."""
        self._value_baseline = self._current_value()
        self._unsub_capture = async_track_state_change_event(
            self.hass, [self.value_entity], self._async_on_value_change
        )
        _LOGGER.info(
            "Started timelapse capture for %s (camera %s, frame per %s "
            "change of %s, direction %s)",
            self.title,
            self.camera_entity,
            self.value_delta,
            self.value_entity,
            self.value_direction.value,
        )

    @callback
    def _wire_time_interval(self, seconds: float) -> None:
        """Capture a frame every fixed number of seconds."""
        self._unsub_capture = async_track_time_interval(
            self.hass, self._async_capture_frame, timedelta(seconds=seconds)
        )
        _LOGGER.info(
            "Started timelapse capture for %s (camera %s, every %.1f s)",
            self.title,
            self.camera_entity,
            seconds,
        )

    @callback
    def _async_on_condition_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        self._maybe_switch_conditional_rule()

    @callback
    def _maybe_switch_conditional_rule(self) -> None:
        """Re-evaluate the rules and rewire the cadence on a change."""
        if (
            not self._capturing
            or self._buffering
            or self.capture_mode is not CaptureMode.CONDITIONAL
            or not self.conditional_reevaluate
        ):
            return
        index, rule = self._select_conditional_rule()
        if index == self._active_rule_index:
            return
        self._active_rule_index = index
        self._active_rule = rule
        _LOGGER.info(
            "Conditions changed for %s; switching to cadence rule %s",
            self.title,
            "?" if index is None else index + 1,
        )
        self._cancel_capture_listener()
        self._wire_capture_cadence()

    async def async_stop(self, render: bool = True) -> None:
        """End the capture session, optionally rendering the video."""
        if not self._capturing:
            return
        self._clear_buffer_state()
        self._cancel_capture_listener()
        self._cancel_condition_listener()
        self._capturing = False
        self._session_capture_seconds = None
        session_dir = self._session_dir
        frames = self.frame_count
        started_at = self.session_started_at
        self._session_dir = None
        self.session_started_at = None
        _LOGGER.info(
            "Stopped timelapse capture for %s with %d frame(s)",
            self.title,
            frames,
        )
        if session_dir is not None:
            if render and frames > 0:
                await self._async_persist(
                    session_dir, SessionPhase.PENDING_RENDER, started_at
                )
                self.hass.async_create_task(self._async_render(session_dir, frames))
            elif frames > 0 and self.keep_frames:
                self._last_session_dir = session_dir
                self._last_session_frames = frames
                await self._store.async_remove(
                    self.subentry.subentry_id, session_dir.name
                )
            else:
                await self._async_remove_dir(session_dir)
                await self._store.async_remove(
                    self.subentry.subentry_id, session_dir.name
                )
        self._notify()

    async def async_cancel(self) -> None:
        """Abort the capture session and discard its frames."""
        if not self._capturing:
            return
        self._clear_buffer_state()
        self._cancel_capture_listener()
        self._cancel_condition_listener()
        self._capturing = False
        self._session_capture_seconds = None
        session_dir = self._session_dir
        self._session_dir = None
        self.session_started_at = None
        if session_dir is not None:
            await self._async_remove_dir(session_dir)
            await self._store.async_remove(
                self.subentry.subentry_id, session_dir.name
            )
        _LOGGER.info("Cancelled timelapse capture for %s", self.title)
        self._notify()

    async def _async_persist(
        self,
        session_dir: Path,
        phase: SessionPhase,
        started_at: datetime | None = None,
    ) -> None:
        """Record the session so it survives a crash or restart."""
        started = started_at or self.session_started_at
        await self._store.async_set(
            self.subentry.subentry_id,
            session_dir.name,
            SessionRecord(
                entry_id=self.entry.entry_id,
                started_at=started.isoformat() if started else None,
                phase=phase,
            ),
        )

    async def _async_salvage(self, info: ResumeInfo) -> None:
        """Render the frames of a session that cannot continue."""
        if info.frame_count == 0:
            await self._async_remove_dir(info.session_dir)
            await self._store.async_remove(
                self.subentry.subentry_id, info.session_dir.name
            )
            return
        _LOGGER.info(
            "Rendering %d frame(s) of an interrupted session for %s",
            info.frame_count,
            self.title,
        )
        await self._async_persist(
            info.session_dir, SessionPhase.PENDING_RENDER, info.started_at
        )
        self._last_session_dir = info.session_dir
        self._last_session_frames = info.frame_count
        self.hass.async_create_task(
            self._async_render(info.session_dir, info.frame_count)
        )

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
    def _cancel_capture_listener(self) -> None:
        if self._unsub_capture is not None:
            self._unsub_capture()
            self._unsub_capture = None
        self._value_baseline = None

    @callback
    def _cancel_condition_listener(self) -> None:
        """Drop conditional-rule tracking; the next session rebuilds it."""
        if self._unsub_conditions is not None:
            self._unsub_conditions()
            self._unsub_conditions = None
        self._rule_checkers = []
        self._active_rule = None
        self._active_rule_index = None

    def _capture_interval_seconds(self) -> float:
        """Return the seconds between snapshots for this session."""
        if self.capture_mode is not CaptureMode.TIME_FIT:
            return float(self.interval)
        duration = self._read_duration_seconds()
        if duration is None or duration <= 0:
            _LOGGER.warning(
                "%s: duration entity %s did not yield a positive duration; "
                "falling back to %d s between snapshots",
                self.title,
                self.duration_entity,
                self.fallback_interval,
            )
            return float(self.fallback_interval)
        seconds = duration / (self.output_fps * self.target_length)
        if seconds < 1.0:
            _LOGGER.debug(
                "%s: computed interval %.3f s clamped to 1 s; the video "
                "will be shorter than the %.1f s target",
                self.title,
                seconds,
                self.target_length,
            )
            return 1.0
        return seconds

    def _read_duration_seconds(self) -> float | None:
        """Return the session duration in seconds from the configured entity."""
        if self.duration_type is DurationType.END_TIME:
            return self._read_end_time_remaining()
        value = self._read_float(self.duration_entity)
        return None if value is None else value * _DURATION_MULTIPLIER[self.duration_type]

    def _read_end_time_remaining(self) -> float | None:
        """Return seconds until the end-time entity's timestamp."""
        if not self.duration_entity:
            return None
        state = self.hass.states.get(self.duration_entity)
        if state is None:
            return None
        end = dt_util.parse_datetime(state.state)
        if end is None:
            return None
        if end.tzinfo is None:
            end = dt_util.as_utc(end)
        return (end - dt_util.utcnow()).total_seconds()

    def _read_float(self, entity_id: str | None) -> float | None:
        """Return an entity's state as a float, if numeric."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _current_value(self) -> float | None:
        """Return the watched value entity's state as a float, if numeric."""
        return self._read_float(self.value_entity)

    @callback
    def _async_on_value_change(self, event: Event[EventStateChangedData]) -> None:
        """Capture a frame when the watched value moves by at least the step."""
        if not self._capturing:
            return
        new_state = event.data["new_state"]
        if new_state is None:
            return
        try:
            value = float(new_state.state)
        except (TypeError, ValueError):
            return
        baseline = self._value_baseline
        if baseline is None:
            self._value_baseline = value
            return
        delta = value - baseline
        direction = self.value_direction
        capture = False
        if direction is ValueDirection.ANY:
            capture = abs(delta) >= self.value_delta
        elif direction is ValueDirection.INCREASE:
            if delta >= self.value_delta:
                capture = True
            elif delta < 0:
                # Counter reset (e.g. new print started): follow it down.
                self._value_baseline = value
        else:  # ValueDirection.DECREASE
            if -delta >= self.value_delta:
                capture = True
            elif delta > 0:
                self._value_baseline = value
        if capture:
            self._value_baseline = value
            self.hass.async_create_task(self._async_capture_frame())

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
        if self._buffering and self._buffer_frames_remaining is not None:
            self._buffer_frames_remaining -= 1
            if self._buffer_frames_remaining <= 0:
                self._notify()
                await self._async_finish_buffer()
                return
        self._notify()
        # Conditions that reference no trackable entity (template, time, sun)
        # produce no state-change events; re-check the rules per frame too.
        self._maybe_switch_conditional_rule()

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
                await self._store.async_remove(
                    self.subentry.subentry_id, session_dir.name
                )
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
