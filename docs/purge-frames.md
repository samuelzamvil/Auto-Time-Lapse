# Keeping disk usage under control

When **Keep frames after rendering** is on, snapshot JPEGs accumulate next to
each finished video. There are two completely separate ways to reclaim that
space, and it is important not to confuse them:

1. **Auto-purge retention** *(recommended)* — a per-trigger policy that
   automatically keeps your frame archive trimmed to a size or age you choose.
   This is the normal, ongoing way to manage disk usage.
2. **The `purge_frames` action** — a manual one-shot that deletes **every**
   retained frame for a trigger at once. This is a cleanup tool, not a retention
   setting, and it is described last because it removes everything.

Either way, your `.mp4` videos are never deleted.

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

### When enforcement runs

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

- **`.mp4` video files** — neither auto-purge nor the manual action ever removes
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

## Manually clearing all loose frames (`purge_frames`)

> ⚠️ **This deletes every saved frame for the trigger — all of them, from every
> session — in one go.** It is **not** a retention setting and does not respect
> the auto-purge options above. It is a manual "clean out all loose frames"
> action. Once it runs, the JPEGs are gone for good (your videos remain, and you
> can re-render from them, but the original frames are not recoverable). Proceed
> with caution and make sure you have selected the right trigger.

`auto_time_lapse.purge_frames` immediately deletes every retained frame set
under a trigger's output folder, leaving only the `.mp4` videos behind. Use it
when you want to wipe a trigger's frame archive entirely, not to maintain a
rolling window — for that, configure auto-purge retention above instead.

This is a manual action with no dedicated button or switch, and nothing inside
the integration ever calls it on its own. You invoke it deliberately, the same
way you would any Home Assistant action.

### Developer Tools → Actions

The simplest way to run it once:

1. Open **Developer Tools → Actions** (older versions call this tab
   *Services*).
2. Search for **Auto Time Lapse: Purge frames** and select it.
3. Pick the **trigger device** whose frames you want to clear.
4. Click **Perform action**.

The device picker only lists Auto Time Lapse trigger devices, so you always
choose exactly which trigger's frames are removed.

### From an automation or script

The same action in YAML:

```yaml
action: auto_time_lapse.purge_frames
data:
  device_id: abc123...   # use the device picker in the UI editor
```

A purge that finds nothing to delete is a quiet no-op. The action holds the
same render lock as the video renderer, so it never interrupts an in-flight
render.
