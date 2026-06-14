# Keeping disk usage under control

When **Keep frames after rendering** is on, snapshot JPEGs accumulate next to
each finished video. Two tools let you reclaim that space without touching your
videos.

## The `purge_frames` service

Call `auto_time_lapse.purge_frames` for any trigger device to immediately
delete every retained frame set under that trigger's output folder. All `.mp4`
videos are left untouched.

```yaml
service: auto_time_lapse.purge_frames
data:
  device_id: abc123...   # use the picker in the UI editor
```

A purge that finds nothing to delete is a quiet no-op — safe to call from an
automation on a schedule. The service holds the same render lock as the video
renderer, so it never interrupts an in-flight render.

## Auto-purge retention (per-trigger)

The trigger form has an **Auto-purge** section (visible when *Keep frames* is
on) that lets the integration enforce a retention policy automatically.

| Option | Description | Default |
| --- | --- | --- |
| Auto-purge | Enable automatic frame cleanup | off |
| Retention mode | *Keep recent sessions* or *Delete old sessions* | Keep recent sessions |
| Keep sessions (recent mode) | Number of most-recent session frame sets to keep | 10 |
| Max age in days (age mode) | Delete frame sets whose session timestamp is older than this many days | 30 |

### Keep recent sessions

Keeps the frame sets from the N most recent render sessions and deletes all
others. Videos from older sessions are untouched.

With **Keep sessions** set to `1`, each new render purges the previous
session's frames, leaving you with exactly one current set at all times.
Set to `0` to delete frames immediately after every render — effectively a
"render and discard" mode with an audit trail of videos.

### Delete old sessions

Deletes frame sets whose session folder timestamp is older than the configured
number of days. The timestamp is read from the folder name (the local time the
render ran), so it is stable and not affected by file system changes.

Videos in those folders are never deleted.

## When enforcement runs

Both policies enforce on the same three occasions:

- **At startup** — stale frame sets left over from before a policy was enabled
  are cleaned on the next Home Assistant restart.
- **After every successful render** — the newest session is archived first,
  then the policy trims anything that exceeds the limit.
- **Once every 24 hours** — a background timer catches any drift between
  renders without requiring a restart.

Enforcement always holds the render lock, so a policy sweep and an in-flight
render never overlap.

## What is never deleted

- **`.mp4` video files** — neither `purge_frames` nor auto-purge ever removes
  a finished video, regardless of how old the session is.
- **The active session's working frames** — frames in the temporary working
  folder (`<config>/auto_time_lapse/`) are not touched; only retained frame
  sets in the output directory are considered.

## Relationship to the output layout

Every render lands in its own session folder:

```
<output dir>/<camera>/<trigger>/<date_time>/
    ├── <video>.mp4
    └── frame_000001.jpg, frame_000002.jpg, …   (only with Keep frames on)
```

Purging targets the `frame_*.jpg` files inside these session folders. The
session folders and their videos stay in place. See
[Where Auto Time Lapse saves your files](save-locations.md) for the full
layout.
