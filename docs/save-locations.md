# Where Auto Time Lapse saves your files

Auto Time Lapse uses **two folders with very different jobs**. Understanding them
explains everything about where your videos and snapshots end up.

## The two folders

### 1. The working folder (temporary)

```
<config>/auto_time_lapse/<trigger id>/<session>/frame_000000.jpg, frame_000001.jpg, ...
```

While a session is capturing, snapshots accumulate here — inside Home Assistant's
config directory. This folder is **temporary storage only**. It is not visible in
the Media Browser, and nothing is meant to live here permanently. After every
session it is emptied: the frames are either turned into a video and deleted, or
moved next to the video (see below). On every Home Assistant restart the folder
also cleans itself up — anything left behind that no active session needs is
removed automatically.

### 2. The output directory (yours)

This is the folder you choose in the trigger options (empty = `<media>/auto_time_lapse/`).
Everything you keep ends up here, organised so each capture session is easy to
find and prune. Every render gets its own **per-session folder**:

```
<output dir>/<camera name>/<trigger name>/<date_time>/
    ├── <video>.mp4
    └── frame_000001.jpg, frame_000002.jpg, …   (only with Keep frames on)
```

- `<camera name>` and `<trigger name>` are the titles you gave the camera and
  trigger, slugified (spaces and `/` become underscores).
- `<date_time>` is the moment the render ran, e.g. `2026-06-14_18-30-00`.
- **The video** always lands in the session folder whether or not you keep frames.
- **The frames, if you keep them**: with **Keep frames after rendering** enabled,
  the snapshots move in alongside the video.

A concrete example:
`/media/auto_time_lapse/garage-cam/sunset/2026-06-14_18-30-00/sunset_2026-06-14_18-30-00.mp4`

Anything under `/media` (including NAS shares added via *Settings → System →
Storage*) shows up in the Media Browser.

## What happens when a session ends

1. Capture stops (switch off, stop service, schedule window end, watch entity
   inactive — after the end delay buffer, if configured).
2. ffmpeg renders the video into a fresh per-session folder under your output
   directory. The frames are read from the local working folder, so a slow
   network share never slows the render down.
3. Only after the render **succeeds**:
   - **Keep frames off** (default): the working folder for that session is
     deleted, leaving just the video in its session folder.
   - **Keep frames on**: the frames move into the session folder next to the
     video, then the working folder is deleted.

Want to re-render kept frames yourself? They are ordinary numbered JPEGs:

```bash
ffmpeg -framerate 30 -i sunset/2026-06-14_18-30-00/frame_%06d.jpg -c:v libx264 -pix_fmt yuv420p out.mp4
```

## What happens when something goes wrong

Every failure mode errs on the side of **keeping your frames**:

- **The render fails** (ffmpeg error, output folder unreachable): the frames stay
  in the working folder, regardless of the keep-frames setting, and the session is
  remembered. Rendering is retried automatically at the next Home Assistant
  restart, or on demand with the `auto_time_lapse.render` service. Once a retry
  succeeds, the normal flow above resumes — including the move.
- **The move fails** (NAS dropped, disk full): the video is already safe in the
  session folder; the frames stay in the working folder and a warning is logged.
  Nothing is deleted unless every frame moved successfully.
- **Home Assistant restarts mid-session**: the session resumes where it left off.
  If it can no longer continue (the schedule window ended while HA was down), the
  frames captured so far are rendered into a video at startup instead.

## Disk usage

The working folder can never grow unbounded: frames only exist there while a
session is actively capturing or a failed render is waiting to be retried. Your
output directory holds everything you chose to keep — videos, and per-session
frame sets if **Keep frames after rendering** is on.
