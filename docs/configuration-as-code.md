# Configuration as code (YAML import/export)

Auto Time Lapse normally stores its configuration behind the UI: each camera is
a config entry, and each timelapse trigger on it is a config subentry. **YAML
import/export** is a second front-end onto that exact same storage. It lets you
view a trigger — or a whole camera — as a YAML document, copy it, edit it, and
paste it back, without changing how anything is stored.

Nothing here is a separate config file you have to keep in sync. The YAML is
generated from, and applied to, the same configuration the UI edits. Exporting
then importing the same document is a no-op.

## When to use it

- **Back up or share a trigger.** Export it to YAML and keep the text, or hand it
  to someone with the same kind of camera and printer.
- **Duplicate a trigger** with small tweaks: export, change the name and an entity
  or two, then create a new trigger by pasting it in.
- **Bulk-edit a camera.** Export the whole camera, edit several triggers at once
  in a real text editor, and import the result.
- **Review the configuration as text**, including the conditional-cadence rules,
  which are easier to read as YAML than to click through.

If you only want to change one field, the guided UI is usually quicker. YAML
shines when you want to see or move several settings at once.

## Per-trigger YAML

When **adding** a trigger, the first screen offers **Guided setup** or
**Paste YAML**. When **reconfiguring** an existing trigger, the editing hub gains
two extra entries:

- **View as YAML** — shows the current trigger as a YAML document to copy.
- **Edit as YAML** — opens an editor pre-filled with the current trigger; paste a
  new document over it and save. The trigger is validated and replaced in place.

A single trigger looks like this:

```yaml
name: Garden Print
trigger_mode: watch
watch_entity: sensor.printer_status
watch_states: [printing, paused]
capture_mode: conditional
conditional_rules:
  - conditions:
      - condition: numeric_state
        entity_id: sensor.current_layer
        below: 20
    capture_mode: time
    interval: 30
  - capture_mode: value_change
    value_entity: sensor.current_layer
    value_delta: 1
    value_direction: any
output_fps: 30
filename_pattern: "{name}_{timestamp}.mp4"
keep_frames: false
```

The `name` becomes the trigger's title; every other key is the same one the UI
uses. Only the keys that apply to the chosen modes are kept — exactly as when you
save the trigger from the UI — so an exported document never carries settings
that wouldn't take effect.

The last entry in `conditional_rules` may omit `conditions`; that one is the
**default cadence** used when no other rule matches (see
[Conditional at start capture cadence](conditional-cadence.md)).

## Whole-camera YAML

A camera's **options** flow (the gear/cog menu on the integration) adds **Export
this camera as YAML** and **Import this camera from YAML**. The whole-camera
document wraps the camera's quality defaults and all of its triggers:

```yaml
options:
  video_quality: high
  scale_mode: render
  max_width: 1920
triggers:
  - name: Garden Print
    trigger_mode: watch
    # ... same shape as a per-trigger document ...
  - name: Front Door
    trigger_mode: schedule
    schedule_start: "08:00:00"
    schedule_end: "18:00:00"
    capture_mode: time
    interval: 60
    output_fps: 30
    filename_pattern: "{name}_{timestamp}.mp4"
    keep_frames: false
```

### Import is a full sync

Importing a whole-camera document makes the camera **match the document**:

- Triggers are matched to existing ones **by name**. A name in the document that
  already exists is updated in place; a new name creates a new trigger.
- Any existing trigger **whose name is not in the document is deleted.** Before
  anything is applied, a confirmation step lists exactly which triggers will be
  created/updated and which will be deleted.

Two consequences are worth calling out:

- **Renaming a trigger is a recreate, not a rename.** Because matching is by name,
  changing a trigger's `name` deletes the trigger under the old name and creates a
  fresh one under the new name. The new trigger gets new entities and a new
  history — the old device's sensors and recorded history do not carry over. The
  rename shows up in the confirmation step as the old name being deleted. If you
  only want to rename, do it from the trigger's device page instead.
- **Trigger names must be unique** within the document. Two triggers sharing a
  name is rejected with an error, because a by-name sync could not tell which
  existing trigger each one maps to.

## Validation

Pasted YAML is validated before anything is saved:

- It must be a mapping with the expected keys and value types; bad enums or types
  are reported with the offending field.
- Each conditional rule's `conditions` are checked with the **same validator Home
  Assistant uses for automation conditions**, so a broken condition (for example a
  reference to a deleted entity) is caught at import time rather than silently
  failing when a session starts.

If validation fails, the form is shown again with the error and nothing is
changed.

## Round-trip stability

Export → import → export produces the same configuration. Imported data is
validated and pruned to the same shape the UI saves, and export preserves every
stored key (including any future keys this version doesn't recognize), so moving
configuration through YAML never loses or rewrites settings.
