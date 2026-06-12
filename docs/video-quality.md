# Video quality and image scaling

How to control the encoding quality of the finished video and the resolution
of the frames it is built from. These settings live at **two levels**:

1. **Camera-wide defaults** — the **Configure** button on the camera entry
   sets the defaults for every trigger on that camera.
2. **Per-trigger overrides** — each trigger's form carries the same fields,
   defaulting to *Use service default*, and can override any of them.

With everything left at its default, the integration behaves exactly like
releases that predate these settings: CRF 23, `medium` preset, native
resolution.

## Video quality

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

## Image scaling

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

Scaling *during capture* also shrinks the frame JPEGs stored on disk — both
the temporary working folder and, with **Keep frames after rendering** on,
the kept frames [next to the video](save-locations.md).

## Per-trigger overrides and resolution rules

The trigger form mirrors the camera-wide fields, with one extra choice —
**Use service default** — which inherits the camera entry's setting (and is
what you get if you never touch them).

- A trigger-level setting **wins** over the camera-wide default; the
  camera-wide default wins over the built-in (Medium quality, scaling off).
- **Use service default** is inheritance, not a value: pick it and the
  trigger follows whatever the camera entry says, now and after future
  changes.
- An explicit **Off** scaling override is a real value: it disables scaling
  for this trigger even when the camera-wide default scales.
- Quality and its CRF/preset resolve together, and scaling mode and maximum
  width resolve together: a trigger that overrides the scaling mode uses its
  own maximum width, while a trigger that inherits the mode also inherits the
  width. To override only the width, set the mode explicitly too.
- Choosing **Custom** quality on a trigger opens the same CRF/preset step as
  the camera-wide flow, before the cadence steps.

### Examples

- **One camera, one low-bandwidth trigger**: leave the camera-wide defaults
  alone; on the always-on trigger pick quality *Low* and scaling *During
  render* with a width of 1280. Other triggers keep full quality.
- **Everything smaller by default, one pristine exception**: set the
  camera-wide defaults to *High* with render scaling at 1920; on the special
  trigger override quality to *Maximum* and scaling to *Off*.

## Defaults at a glance

| Setting | Camera-wide default | Trigger default |
| --- | --- | --- |
| Video quality | Medium (CRF 23, `medium` preset) | Use service default |
| CRF / preset (custom only) | 23 / medium | 23 / medium |
| Image scaling | Off (native resolution) | Use service default |
| Maximum width | — | — |
