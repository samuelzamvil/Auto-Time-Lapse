# Auto Time Lapse

A [Home Assistant](https://www.home-assistant.io/) custom integration that turns any camera entity into a timelapse factory: it captures snapshots on an interval while a session is active, then stitches them into an H.264 MP4 with ffmpeg when the session ends.

- **Any camera entity** as the source.
- **Four ways to trigger a session:** services (use any HA automation), a built-in daily schedule (overnight windows supported), a watch entity (start on `on`, stop+render on `off`), and a per-profile capture switch.
- **Videos land in your media folder by default**, so they're playable right away in HA's Media Browser — including NAS network storage you've mounted via *Settings → System → Storage* (it appears under `/media`). Custom output paths are supported and validated against HA's allowed paths.
- **Multiple independent profiles** — add the integration once per camera/use-case.
- Frames are cleaned up after a successful render (configurable), and kept for re-rendering if ffmpeg fails.

## Installation

### HACS (recommended)

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/samuelzamvil/auto-time-lapse` with category **Integration**.
3. Install **Auto Time Lapse** and restart Home Assistant.

### Manual

Copy `custom_components/auto_time_lapse` into your config's `custom_components` folder and restart.

> **Note:** ffmpeg must be available to Home Assistant. On Home Assistant OS and the official container images it already is. The integration uses HA's `ffmpeg` component to locate the binary, so a custom `ffmpeg: ffmpeg_bin:` override is honored.

## Configuration

Add a profile via **Settings → Devices & Services → Add Integration → Auto Time Lapse**. Each profile is independent and creates its own device with a capture switch and status sensors.

| Option | Description | Default |
| --- | --- | --- |
| Name | Profile name; also the `{name}` filename placeholder | — |
| Camera | Camera entity to snapshot | — |
| Capture interval | Seconds between snapshots | 60 |
| Video frame rate | Output FPS (e.g. 30 fps with a 60 s interval ≈ 1 s of video per 30 min) | 30 |
| Output directory | Empty = `<media>/auto_time_lapse/`. Custom paths must be in `allowlist_external_dirs` or a media dir | media folder |
| Filename pattern | Supports `{name}`, `{timestamp}`, `{entry_id}` | `{name}_{timestamp}.mp4` |
| Keep frames | Keep snapshot JPEGs after rendering | off |
| Daily schedule | Capture between start/end time daily; renders at window end | off |
| Watch entity | Start when it turns `on`, stop + render when it turns `off` | — |

All options can be changed later via **Configure** on the integration entry.

### Writing to a NAS

Add your share under **Settings → System → Storage** (Home Assistant OS). It mounts under `/media/<name>` and is part of HA's media dirs, so you can either leave the output directory empty and move the `auto_time_lapse` folder logic to it by setting the output directory to e.g. `/media/nas/timelapses`, or keep the default. No `allowlist_external_dirs` changes needed for media mounts.

## Entities

Each profile creates:

| Entity | Description |
| --- | --- |
| `switch.<name>_capture` | On = capturing. Turning off stops the session and renders the video |
| `sensor.<name>_status` | `idle` / `capturing` / `rendering` |
| `sensor.<name>_frame_count` | Frames captured this session (attribute: `failed_frames`) |
| `sensor.<name>_last_video` | Path of the last rendered video (attribute: `media_content_id`) |

## Services

All services target a profile via `config_entry_id` (a picker in the UI).

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
          config_entry_id: abc123...   # use the picker in the UI editor
  - alias: Sunset timelapse stop
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: auto_time_lapse.stop
        data:
          config_entry_id: abc123...
```

## Events

When a video finishes, the integration fires `auto_time_lapse_finished` with `entry_id`, `name`, `path`, and `frame_count` — handy for notifications:

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

- Working frames are stored under `<config>/auto_time_lapse/<entry_id>/<session>/` and never show up in the Media Browser.
- If a snapshot fails (camera offline), the frame is skipped and counted in `failed_frames`; the session keeps going.
- If rendering fails, frames are kept regardless of the *keep frames* setting so you can fix the issue and call `auto_time_lapse.render`.
- A restart mid-session abandons the session; leftover frames are cleaned at startup (unless *keep frames* is on). Session resume is a planned enhancement.
- Stopping a session immediately frees the profile for a new session while the previous video renders in the background.

## Development

```bash
uv venv && uv pip install -r requirements_test.txt
ruff check .
pytest
```

## License

[MIT](LICENSE)
