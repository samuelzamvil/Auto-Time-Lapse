# Configuration reference

Every option Auto Time Lapse offers, in one place. Configuration lives at
**three levels**:

1. **The camera entry** — created when you add the integration; one per camera.
2. **Camera-wide defaults** — the **Configure** button on the camera entry sets
   video quality and image scaling defaults that apply to every trigger on that
   camera.
3. **Triggers** — added with **Add trigger** on the camera entry. Each trigger
   is an independent timelapse profile with its own trigger mode, capture
   cadence, and output settings, and can override the camera-wide defaults.

Everything can be changed later: triggers via **Reconfigure**, camera-wide
defaults via **Configure**. With every quality and scaling option left at its
default, the integration behaves exactly like releases that predate these
settings (CRF 23, `medium` preset, native resolution).

## The camera entry

| Option | Description |
| --- | --- |
| Camera | The camera entity snapshots are taken from. One entry per camera; add more timelapses to the same camera as additional triggers. |

## Camera-wide defaults (the Configure button)

Defaults for every trigger on this camera. Individual triggers can override
any of these (see [trigger-level overrides](#video-quality-and-image-scaling-overrides)).

### Video quality

Encoding quality of the finished video. Higher quality means larger files and
a slower render.

| Level | CRF | x264 preset |
| --- | --- | --- |
| Low (smaller files) | 30 | faster |
| Medium *(default)* | 23 | medium |
| High | 19 | slow |
| Maximum (largest files) | 16 | slower |
| Custom | your choice (0–51) | your choice |

Picking **Custom** opens an extra step:

| Option | Description | Default |
| --- | --- | --- |
| CRF (0–51) | x264 constant rate factor: lower is higher quality and a bigger file. 0 is lossless, 23 is the default, 51 is worst. Steps of ~4 roughly halve or double the file size. | 23 |
| x264 preset | Encoding speed versus compression efficiency, from `ultrafast` to `veryslow`. Slower presets produce smaller files at the same quality but render longer. | medium |

### Image scaling

Downscales frames to a **maximum width**. The aspect ratio is always
preserved, and nothing is ever upscaled — a maximum width at or above the
camera's native width is a no-op.

| Mode | What happens | When to use it |
| --- | --- | --- |
| Off *(default)* | Frames and video keep the camera's native resolution. | You want full quality. |
| During render *(recommended)* | Frames are stored at full size; ffmpeg downscales once while rendering the video. | You want a smaller video without per-frame cost. |
| During capture | Home Assistant scales every snapshot the moment it is taken, so smaller frames hit the disk. | Frame disk usage during long sessions matters more than CPU. |

> ⚠️ **During capture costs CPU on every single frame.** Home Assistant
> decodes and re-encodes each JPEG at capture time, for the whole session.
> It is also best-effort: it requires TurboJPEG and a JPEG-delivering camera,
> and the result lands on the nearest supported scaling factor rather than the
> exact width. The render-time clamp still runs afterwards either way, so the
> finished video always respects the maximum width.

| Option | Description |
| --- | --- |
| Maximum width | Frames or video wider than this are scaled down (120–7680 px). Required whenever image scaling is enabled. |

## Trigger options (the main form)

Every trigger starts with this form. Follow-up steps depend on the trigger
mode and capture cadence you pick here.

| Option | Description | Default |
| --- | --- | --- |
| Name | Device name, and the `{name}` placeholder in filenames. | — |
| Trigger mode | What starts and stops a session: [Manual](#manual), [Daily time window](#daily-time-window), or [Entity state watch](#entity-state-watch). | Manual |
| Capture cadence | What paces the frames: [Time interval](#time-interval), [Fit target video length](#fit-target-video-length), [Entity value change](#entity-value-change), or [Conditional](#conditional-rule-based). | Time interval |
| Video frame rate (FPS) | Output frames per second (1–120). 30 fps with a 60 s interval ≈ 1 s of video per 30 min of capture. | 30 |
| Output directory | Folder for finished videos, created automatically. Empty = `<media>/auto_time_lapse/`. Anything under `/media` shows up in the Media Browser; paths outside a media folder must be in `allowlist_external_dirs`. See [save locations](save-locations.md). | media folder |
| Filename pattern | Pattern for the video filename, with `{name}`, `{timestamp}`, and `{entry_id}` placeholders. `.mp4` is appended if missing. | `{name}_{timestamp}.mp4` |
| Keep frames after rendering | Keep the snapshot JPEGs after the video renders, [moved next to the video](save-locations.md). | off |
| Video quality | Per-trigger override of the [camera-wide quality](#video-quality). | Use service default |
| Image scaling | Per-trigger override of the [camera-wide scaling](#image-scaling). | Use service default |
| Maximum width | The width for this trigger's own scaling override. | — |

### Video quality and image scaling overrides

The three quality fields on the trigger form mirror the camera-wide defaults,
with one extra choice — **Use service default** — which inherits the camera
entry's setting (and is what you get if you never touch them). Resolution
rules:

- A trigger-level setting **wins** over the camera-wide default; the camera-wide
  default wins over the built-in (Medium quality, scaling off).
- **Use service default** is inheritance, not a value: pick it and the trigger
  follows whatever the camera entry says, now and after future changes.
- An explicit **Off** scaling override is a real value: it disables scaling for
  this trigger even when the camera-wide default scales.
- Quality and its CRF/preset resolve together, and scaling mode and maximum
  width resolve together: a trigger that overrides the scaling mode uses its
  own maximum width, while a trigger that inherits the mode also inherits the
  width. To override only the width, set the mode explicitly too.
- Choosing **Custom** quality on a trigger opens the same CRF/preset step as
  the camera-wide flow, before the cadence steps.

## Trigger modes

### Manual

No further options. Start and stop with the capture switch or the
`auto_time_lapse.start` / `auto_time_lapse.stop` services, from any automation.
Manual triggers never show the [end delay buffer](#end-delay-buffer) step —
manual stops always take effect immediately.

### Daily time window

| Option | Description |
| --- | --- |
| Start time | Capture starts at this time every day. |
| End time | Capture stops and the video renders at this time. Must differ from the start time. Overnight windows (e.g. 22:00 → 06:00) are supported. |

If Home Assistant starts or the entry reloads in the middle of the window, the
session starts (or resumes) right away.

### Entity state watch

| Option | Description |
| --- | --- |
| Entity | The entity that drives the capture — a 3D printer status sensor, a motion sensor, anything. |
| Active states | Shown in a second step, listing **that entity's actual states**. Capture runs while the entity is in any of them (default `on`). Leaving them — including becoming `unavailable` — stops the session and renders the video. |

## Capture cadences

### Time interval

| Option | Description | Default |
| --- | --- | --- |
| Capture interval | Seconds between snapshots (1–86400). | 60 |

### Fit target video length

The snapshot interval is computed **once at session start** —
duration ÷ (frame rate × target length), never below 1 second — and stays
fixed for the whole session, so the finished video always comes out about the
target length regardless of how long the session runs.

| Option | Description | Default |
| --- | --- | --- |
| Duration entity | Entity reporting the expected total session duration, e.g. a printer's estimated print time sensor. | — |
| Duration entity reports | How to read it: a number of **seconds**, **minutes**, or **hours**, or an **end time** timestamp (duration = end time minus now, computed at session start). | seconds |
| Target video length | Desired length of the finished video, in seconds. | 30 |
| Fallback interval | Seconds between snapshots when the duration entity can't be read as a positive number at session start. | 60 |

### Entity value change

One frame per movement of a numeric entity — per 3D-printer layer, per
0.5 kWh, per km.

| Option | Description | Default |
| --- | --- | --- |
| Value entity | Numeric entity that paces the frames. | — |
| Capture every | A frame is captured each time the value moves by at least this amount (any float > 0) since the last frame. Use 1 for one frame per layer. | 1.0 |
| Direction | **Any change**, **Increase only**, or **Decrease only**. With a direction set, movement the other way silently re-baselines — a layer counter resetting for a new print just starts counting again. | any |

### Conditional (rule based)

A list of rules, each pairing **Home Assistant conditions** (the same editor
automations use) with its own cadence, plus a mandatory **default** for when
no rule matches. Rules are checked top to bottom; the first match wins.

Each rule step asks for:

| Option | Description |
| --- | --- |
| Conditions | All must hold for the rule to apply (not on the default rule). |
| Cadence while these conditions hold | **Time interval** or **Entity value change** (Fit target length is incompatible with live switching). |
| Capture interval / Value entity / Capture every / Direction | The chosen cadence's settings, same as above. |
| Add another rule | Adds one more rule after this one; the default rule is configured last either way. |

The default-rule step carries one trigger-wide toggle:

| Option | Description | Default |
| --- | --- | --- |
| Re-evaluate rules during a session | **On:** rules are re-checked while capturing (on condition-entity changes and after every frame, which also covers template/time/sun conditions) and the cadence switches live as soon as another rule matches. **Off:** the rule matching at session start is locked in for the whole session. | on |

## End delay buffer

Offered after the schedule or watch steps (never for manual triggers). Keeps
capturing past the trigger end — catch the build plate presenting the
finished print, or the scene settling down — before the video renders.

| Option | Description | Default |
| --- | --- | --- |
| Keep capturing after the trigger ends | **Off**, **Extra frames**, or **Extra seconds**. | off |
| Buffer amount | How many extra frames or seconds, depending on the mode. | 10 |
| Buffer capture interval | Seconds between snapshots during the buffer, overriding the session cadence. **Required** when the session can be paced by value changes (the value-change cadence, or a conditional cadence with a value-change rule), since the watched value typically stops moving once the trigger ends. Leave empty otherwise to keep the session cadence. | — |
| If the trigger fires again during the buffer | **Resume** the same session (also debounces a flapping trigger) or **Finish** the buffer, render, and start a fresh session. | resume |

In *extra frames* mode, a camera that stops delivering snapshots can't run
the buffer forever: it ends after a generous time budget (three times the
expected duration, at least a minute).

## Defaults at a glance

| Setting | Built-in default |
| --- | --- |
| Trigger mode | Manual |
| Capture cadence | Time interval, 60 s |
| Video frame rate | 30 fps |
| Output directory | `<media>/auto_time_lapse/` |
| Filename pattern | `{name}_{timestamp}.mp4` |
| Keep frames | off |
| Video quality | Medium (CRF 23, `medium` preset) |
| Image scaling | off (native resolution) |
| End delay buffer | off |
| Conditional rule re-evaluation | on |
