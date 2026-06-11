"""Constants for the Auto Time Lapse integration."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "auto_time_lapse"

SUBENTRY_TYPE_TRIGGER = "trigger"

CONF_CAMERA_ENTITY = "camera_entity"
CONF_TRIGGER_MODE = "trigger_mode"
CONF_CAPTURE_MODE = "capture_mode"
CONF_INTERVAL = "interval"
CONF_DURATION_ENTITY = "duration_entity"
CONF_TARGET_LENGTH = "target_length"
CONF_FALLBACK_INTERVAL = "fallback_interval"
CONF_VALUE_ENTITY = "value_entity"
CONF_VALUE_DELTA = "value_delta"
CONF_VALUE_DIRECTION = "value_direction"
CONF_OUTPUT_FPS = "output_fps"
CONF_OUTPUT_DIR = "output_dir"
CONF_FILENAME_PATTERN = "filename_pattern"
CONF_KEEP_FRAMES = "keep_frames"
CONF_SCHEDULE_START = "schedule_start"
CONF_SCHEDULE_END = "schedule_end"
CONF_WATCH_ENTITY = "watch_entity"
CONF_WATCH_STATES = "watch_states"
CONF_END_BUFFER_MODE = "end_buffer_mode"
CONF_END_BUFFER_AMOUNT = "end_buffer_amount"
CONF_END_BUFFER_INTERVAL = "end_buffer_interval"
CONF_END_BUFFER_RETRIGGER = "end_buffer_retrigger"
CONF_CONDITIONAL_RULES = "conditional_rules"
CONF_CONDITIONAL_REEVALUATE = "conditional_reevaluate"
CONF_RULE_CONDITIONS = "conditions"
CONF_RULE_ADD_ANOTHER = "add_another"

DEFAULT_INTERVAL = 60
DEFAULT_TARGET_LENGTH = 30.0
DEFAULT_FALLBACK_INTERVAL = DEFAULT_INTERVAL
DEFAULT_VALUE_DELTA = 1.0
DEFAULT_OUTPUT_FPS = 30
DEFAULT_FILENAME_PATTERN = "{name}_{timestamp}.mp4"
DEFAULT_KEEP_FRAMES = False
DEFAULT_END_BUFFER_AMOUNT = 10
DEFAULT_CONDITIONAL_REEVALUATE = True

# Frames-mode buffer watchdog: end the buffer after
# amount * interval * factor seconds (at least the minimum) even if the
# camera stops delivering frames.
BUFFER_SAFETY_FACTOR = 3
BUFFER_SAFETY_MIN = 60.0

SERVICE_START = "start"
SERVICE_STOP = "stop"
SERVICE_RENDER = "render"
SERVICE_CANCEL = "cancel"

ATTR_DEVICE_ID = "device_id"
ATTR_RENDER = "render"

EVENT_TIMELAPSE_FINISHED = "auto_time_lapse_finished"

OUTPUT_SUBDIR = "auto_time_lapse"
FRAME_FILENAME = "frame_{index:06d}.jpg"
FRAME_PATTERN = "frame_%06d.jpg"
SNAPSHOT_TIMEOUT = 10
MAX_LOGGED_FAILURES = 10
RENDER_TIMEOUT = 600


class SessionState(StrEnum):
    """State of a timelapse trigger profile."""

    IDLE = "idle"
    CAPTURING = "capturing"
    BUFFERING = "buffering"
    RENDERING = "rendering"


class SessionPhase(StrEnum):
    """Lifecycle phase of a persisted session record."""

    CAPTURING = "capturing"
    PENDING_RENDER = "pending_render"


class TriggerMode(StrEnum):
    """How a capture session is started and stopped."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    WATCH = "watch"


class CaptureMode(StrEnum):
    """What paces the frames while a session is running."""

    TIME = "time"
    TIME_FIT = "time_fit"
    VALUE_CHANGE = "value_change"
    CONDITIONAL = "conditional"


# Cadences a conditional rule may use (TIME_FIT's per-session frozen
# interval is incompatible with live rule switching).
RULE_CAPTURE_MODES = (CaptureMode.TIME, CaptureMode.VALUE_CHANGE)


class ValueDirection(StrEnum):
    """Which direction of value movement triggers a frame."""

    ANY = "any"
    INCREASE = "increase"
    DECREASE = "decrease"


class EndBufferMode(StrEnum):
    """How long capture continues after the trigger period ends."""

    OFF = "off"
    FRAMES = "frames"
    SECONDS = "seconds"


class BufferRetrigger(StrEnum):
    """What a re-activated trigger does to a running end buffer."""

    RESUME = "resume"
    FINISH = "finish"
