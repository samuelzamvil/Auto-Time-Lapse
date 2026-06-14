# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-06-14

The current feature set. Add a camera once, then stack independent timelapse
triggers on it — each with its own trigger, cadence, and output settings.

### Added

- **One camera, many triggers.** A camera is configured once; timelapses are
  added as independent **trigger subentries**, each with its own frame rate,
  filename pattern, and output options.
- **Three trigger modes.**
  - *Manual* — a capture switch plus `auto_time_lapse.start` / `.stop`
    services, drivable from any automation.
  - *Daily schedule window* — capture between two times each day, including
    overnight windows (e.g. 22:00 → 06:00).
  - *Entity-state watch* — pick an entity and the states that mean *recording*
    (e.g. a 3D printer's `printing`); the video renders itself when the state
    ends, even if the device drops offline mid-job.
- **Four capture cadences.**
  - *Time* — a frame every N seconds.
  - *Fit target length* — the interval is computed so a job of any length comes
    out as the same target video length, from a duration entity (seconds,
    minutes, hours, or an end-time timestamp), with a fallback interval.
  - *Value change* — a frame per step of a numeric entity (per printer layer,
    per 0.5 kWh, per km), rising, falling, or either direction.
  - *Conditional rules* — pick between cadences with Home Assistant conditions,
    re-evaluated live mid-session (or locked at session start).
- **End-delay buffer** for schedule and watch triggers — keep capturing for a
  number of extra seconds or frames after the trigger ends, with an optional
  override interval and resume / finish behavior when the trigger re-fires. A
  frames buffer with a dead camera ends after a safety budget instead of
  waiting forever.
- **Video quality and image scaling** — per-camera defaults with per-trigger
  overrides for encoder quality (CRF / preset, including a custom level) and a
  maximum width applied at capture time or render time.
- **Keep frames** — retain the snapshot JPEGs after rendering, archived next to
  the finished video.
- **Crash resume and salvage** — a session survives a restart and resumes into
  one continuous timelapse; a session that can no longer continue is rendered
  from the frames captured so far at startup, and orphaned frames are cleaned.
- **Capture interval sensor** per trigger, reflecting the active cadence.

### Fixed

- A failed or timed-out render no longer leaves a truncated, unplayable `.mp4`
  in the output directory — the partial file is removed and the frames are
  retained so the session can be re-rendered.
- Frames are written atomically, so a crash mid-write can no longer leave a
  truncated JPEG that a resume scan would mistake for a valid frame.
- Output filenames that would collide within the same second (startup salvage
  renders, or re-rendering kept frames) now get a unique suffix instead of
  silently overwriting an existing video.

### Migration from 0.1.x

0.2.0 restructured profiles into **one entry per camera with trigger
subentries**, with no automatic migration. Delete the old entries, re-add the
camera, then add triggers. Services now target the trigger's **device** instead
of a config entry id — update any automations that call them.
