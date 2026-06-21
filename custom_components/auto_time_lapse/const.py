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
CONF_RULE_CONDITIONS = "conditions"
CONF_RULE_ADD_ANOTHER = "add_another"
CONF_DURATION_TYPE = "duration_type"
CONF_VIDEO_QUALITY = "video_quality"
CONF_VIDEO_CRF = "video_crf"
CONF_VIDEO_PRESET = "video_preset"
CONF_SCALE_MODE = "scale_mode"
CONF_MAX_WIDTH = "max_width"
CONF_AUTO_PURGE = "auto_purge"
CONF_PURGE_MODE = "purge_mode"
CONF_PURGE_KEEP_SESSIONS = "purge_keep_sessions"
CONF_PURGE_MAX_AGE_DAYS = "purge_max_age_days"

# Trigger-level select option meaning "inherit the camera entry's setting".
# Pruned in TriggerSubentryFlow._finish() and therefore never persisted.
OPTION_SERVICE_DEFAULT = "service_default"

DEFAULT_INTERVAL = 60
DEFAULT_TARGET_LENGTH = 30.0
DEFAULT_FALLBACK_INTERVAL = DEFAULT_INTERVAL
DEFAULT_VALUE_DELTA = 1.0
DEFAULT_OUTPUT_FPS = 30
DEFAULT_FILENAME_PATTERN = "{name}_{timestamp}.mp4"
DEFAULT_KEEP_FRAMES = False
DEFAULT_AUTO_PURGE = False
DEFAULT_PURGE_KEEP_SESSIONS = 10
DEFAULT_PURGE_MAX_AGE_DAYS = 30
DEFAULT_END_BUFFER_AMOUNT = 10
DEFAULT_VIDEO_CRF = 23
DEFAULT_VIDEO_PRESET = "medium"

# Frames-mode buffer watchdog: end the buffer after
# amount * interval * factor seconds (at least the minimum) even if the
# camera stops delivering frames.
BUFFER_SAFETY_FACTOR = 3
BUFFER_SAFETY_MIN = 60.0

SERVICE_START = "start"
SERVICE_STOP = "stop"
SERVICE_RENDER = "render"
SERVICE_CANCEL = "cancel"
SERVICE_PURGE = "purge_frames"

ATTR_DEVICE_ID = "device_id"

EVENT_TIMELAPSE_FINISHED = "auto_time_lapse_finished"
EVENT_TIMELAPSE_FAILED = "auto_time_lapse_failed"

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


# Cadences a conditional rule may use. The rule is locked at session start
# so TIME_FIT can freeze its computed interval for the whole session.
RULE_CAPTURE_MODES = (CaptureMode.TIME, CaptureMode.TIME_FIT, CaptureMode.VALUE_CHANGE)


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


class DurationType(StrEnum):
    """How the duration entity's state is interpreted."""

    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"
    END_TIME = "end_time"


class VideoQuality(StrEnum):
    """Encoding quality of the finished video."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAXIMUM = "maximum"
    CUSTOM = "custom"


# (crf, preset) per quality level. MEDIUM must stay equal to the historical
# hardcoded encoder settings so "unset" and "medium" behave identically.
VIDEO_QUALITY_PARAMS: dict[VideoQuality, tuple[int, str]] = {
    VideoQuality.LOW: (30, "faster"),
    VideoQuality.MEDIUM: (DEFAULT_VIDEO_CRF, DEFAULT_VIDEO_PRESET),
    VideoQuality.HIGH: (19, "slow"),
    VideoQuality.MAXIMUM: (16, "slower"),
}

FFMPEG_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]


class ScaleMode(StrEnum):
    """Where frames are downscaled to the maximum width, if anywhere."""

    OFF = "off"
    CAPTURE = "capture"
    RENDER = "render"


class PurgeMode(StrEnum):
    """How auto-purge decides which frame sets to delete."""

    KEEP_RECENT = "keep_recent"
    MAX_AGE = "max_age"
