"""Constants for the Auto Time Lapse integration."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "auto_time_lapse"

SUBENTRY_TYPE_TRIGGER = "trigger"

CONF_CAMERA_ENTITY = "camera_entity"
CONF_TRIGGER_MODE = "trigger_mode"
CONF_CAPTURE_MODE = "capture_mode"
CONF_INTERVAL = "interval"
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

DEFAULT_INTERVAL = 60
DEFAULT_VALUE_DELTA = 1.0
DEFAULT_OUTPUT_FPS = 30
DEFAULT_FILENAME_PATTERN = "{name}_{timestamp}.mp4"
DEFAULT_KEEP_FRAMES = False

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
    RENDERING = "rendering"


class TriggerMode(StrEnum):
    """How a capture session is started and stopped."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    WATCH = "watch"


class CaptureMode(StrEnum):
    """What paces the frames while a session is running."""

    TIME = "time"
    VALUE_CHANGE = "value_change"


class ValueDirection(StrEnum):
    """Which direction of value movement triggers a frame."""

    ANY = "any"
    INCREASE = "increase"
    DECREASE = "decrease"
