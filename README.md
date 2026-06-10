# Auto Time Lapse

A [Home Assistant](https://www.home-assistant.io/) custom integration that turns any camera entity into a timelapse factory: it captures snapshots on an interval while a session is active, then stitches them into an H.264 MP4 with ffmpeg when the session ends.

- **One entry per camera, any number of triggers.** Add a camera once, then add independent trigger profiles to it — each with its own mode, interval, frame rate, and output settings.
- **Three trigger modes**, picked from a dropdown per trigger:
  - **Manual** — start/stop with the capture switch or services (drive it from any automation).
  - **Daily time window** — capture between two times every day, render at window end (overnight windows supported).
  - **Entity state watch** — pick any entity and the states that mean "active" (e.g. a 3D printer's `printing` state). Capture starts when the entity enters those states and stops + renders when it leaves them — including going unavailable, so the video always completes.
- **Videos land in your media folder by default**, so they're playable right away in HA's Media Browser — including NAS network storage you've mounted via *Settings → System → Storage* (it appears under `/media`). Custom output paths are supported, validated, and created automatically.
- Frames are cleaned up after a successful render (configurable per trigger), and kept for re-rendering if ffmpeg fails.

Requires Home Assistant 2025.7 or newer.

## Installation

### HACS (recommended)

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/samuelzamvil/auto-time-lapse` with category **Integration**.
3. Install **Auto Time Lapse** and restart Home Assistant.

### Manual

Copy `custom_components/auto_time_lapse` into your config's `custom_components` folder and restart.

> **Note:** ffmpeg must be available to Home Assistant. On Home Assistant OS and the official container images it already is. The integration uses HA's `ffmpeg` component to locate the binary, so a custom `ffmpeg: ffmpeg_bin:` override is honored.

## Configuration

1. **Add the camera:** Settings → Devices & Services → Add Integration → Auto Time Lapse → pick the camera entity.
2. **Add triggers:** on the integration's card, choose **Add trigger**. Each trigger asks for:

| Option | Description | Default |
| --- | --- | --- |
| Name | Trigger name; device name and the `{name}` filename placeholder | — |
| Trigger mode | Manual / Daily time window / Entity state watch | Manual |
| Capture interval | Seconds between snapshots | 60 |
| Video frame rate | Output FPS (e.g. 30 fps with a 60 s interval ≈ 1 s of video per 30 min) | 30 |
| Output directory | Created automatically; empty = `<media>/auto_time_lapse/` | media folder |
| Filename pattern | Supports `{name}`, `{timestamp}`, `{entry_id}` | `{name}_{timestamp}.mp4` |
| Keep frames | Keep snapshot JPEGs after rendering | off |

Mode-specific steps follow: the schedule asks for start/end times; the watch asks for the entity and then shows a picker with **that entity's actual states** so you can choose which ones count as active (default `on`).

Triggers can be reconfigured or deleted individually from the integration page at any time.

### Example: 3D printer timelapse

Add a trigger with mode *Entity state watch*, pick your printer's status sensor, and select `printing` (and `paused`, if you don't want pauses to split the video). The video renders automatically when the print finishes — or if the printer drops offline mid-print, the session ends and the video is completed with the frames captured so far.

### Writing to a NAS

Add your share under **Settings → System → Storage** (Home Assistant OS). It mounts under `/media/<name>` and is part of HA's media dirs, so set the output directory to e.g. `/media/nas/timelapses` — no `allowlist_external_dirs` changes needed for media mounts.

## Entities

Each trigger creates a device with:

| Entity | Description |
| --- | --- |
| `switch.<name>_capture` | On = capturing. Turning off stops the session and renders the video |
| `sensor.<name>_status` | `idle` / `capturing` / `rendering` |
| `sensor.<name>_frame_count` | Frames captured this session (attribute: `failed_frames`) |
| `sensor.<name>_last_video` | Path of the last rendered video (attribute: `media_content_id`) |

## Services

All services target a trigger via its device (a device picker in the UI editor).

| Service | Description |
| --- | --- |
| `auto_time_lapse.start` | Start a capture session |
| `auto_time_lapse.stop` | Stop and render (set `render: false` to skip rendering) |
| `auto_time_lapse.render` | Re-render the most recent retained frame set |
| `auto_time_lapse.cancel` | Abort the session and discard frames |

```yaml
# Example: capture while the sun is up, rendered automatically at stop
automation:
  - alias: Sunrise timelapse start
    trigger:
      - platform: sun
        event: sunrise
    action:
      - service: auto_time_lapse.start
        data:
          device_id: abc123...   # use the picker in the UI editor
  - alias: Sunset timelapse stop
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: auto_time_lapse.stop
        data:
          device_id: abc123...
```

## Events

When a video finishes, the integration fires `auto_time_lapse_finished` with `entry_id`, `subentry_id`, `name`, `path`, and `frame_count` — handy for notifications:

```yaml
automation:
  - alias: Notify on finished timelapse
    trigger:
      - platform: event
        event_type: auto_time_lapse_finished
    action:
      - service: notify.mobile_app_phone
        data:
          message: "Timelapse {{ trigger.event.data.name }} ready ({{ trigger.event.data.frame_count }} frames)"
```

## Behavior notes

- Working frames are stored under `<config>/auto_time_lapse/<trigger id>/<session>/` and never show up in the Media Browser.
- If a snapshot fails (camera offline), the frame is skipped and counted in `failed_frames`; the session keeps going.
- If rendering fails, frames are kept regardless of the *keep frames* setting so you can fix the issue and call `auto_time_lapse.render`.
- A restart mid-session abandons the session; leftover frames are cleaned at startup (unless *keep frames* is on). A watch or schedule trigger that should be active at startup starts a fresh session immediately.
- Stopping a session immediately frees the trigger for a new session while the previous video renders in the background.
- There is no pause/resume: if a watched device drops out mid-session, the video is completed with the frames captured so far.

## Upgrading from 0.1.x

0.2.0 restructured profiles into one entry per camera with trigger subentries, with no automatic migration. Delete the old Auto Time Lapse entries, then re-add: camera first, triggers on top. Services now target the trigger's **device** instead of a config entry id — update any automations that call them.

## Development

```bash
uv venv && uv pip install -r requirements_test.txt
ruff check .
pytest
```

## License

[MIT](LICENSE)
