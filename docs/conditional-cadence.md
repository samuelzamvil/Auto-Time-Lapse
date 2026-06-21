# Conditional at start capture cadence

The **Conditional at start** capture cadence lets a single trigger choose
between several capture cadences based on **Home Assistant conditions**, decided
**once when the session starts**. Instead of committing a trigger to one fixed
cadence, you write a short list of rules — each pairing a set of conditions with
its own cadence — and the integration picks the first rule whose conditions hold
at the moment capture begins.

The chosen rule is then **locked for the entire session**. It does not change
mid-session, even if the conditions that selected it stop holding later. This is
what the *at start* in the name means: the decision happens once, at session
start, and stays fixed until the next session.

## When to use it

Reach for this cadence when the *right* pacing for a session depends on
something you only know at the moment capture begins. For example:

- A 3D-printer timelapse where short prints should be paced one frame per layer,
  but very tall prints (thousands of layers) would produce an unwatchably long
  video, so they should instead be fit to a target length.
- A camera that should capture every 30 seconds in summer but every 2 minutes in
  winter, decided by the date or a season helper when the session starts.
- A job that picks its cadence from the value of an input helper or a sensor read
  once at the start.

If you instead want a cadence that adapts continuously *during* a session, this
is not the right tool — every cadence here freezes at session start by design.

## How rules are evaluated

1. When a session starts, the rules are checked **top to bottom**.
2. The **first** rule whose conditions all hold is selected.
3. That rule's cadence paces the whole session. Nothing re-evaluates afterwards.
4. If no rule matches, the mandatory **default cadence** is used.

Conditions use the same condition editor as Home Assistant automations, so any
condition you can write there — numeric state, state, template, time, sun, and
so on — works in a rule.

> **Note:** A rule whose conditions cannot be validated (for example, a deleted
> entity) is treated as never matching, and a warning is logged at session
> start. Evaluation simply moves on to the next rule.

## Building the rules

Pick **Conditional at start** as the capture cadence on the trigger form. The
flow then walks you through one rule at a time:

1. **Conditions** — the Home Assistant conditions that must all hold for this
   rule to apply.
2. **Cadence while these conditions hold** — one of:
   - **Time interval** — a snapshot every N seconds.
   - **Fit target video length** — the interval is computed once from a duration
     entity so the finished video comes out about a chosen length.
   - **Entity value change** — a snapshot each time a numeric entity moves by a
     chosen step.
3. **Add another rule** — check this to add one more rule after the current one.

Fill in only the fields for the cadence you picked; the others are ignored.
After the last rule, you configure a **default cadence** (no conditions) that
applies whenever none of the rules match. The default rule is required, so a
conditional trigger always has a cadence to fall back on.

### Per-rule cadence settings

Each rule carries its own copy of the settings for its chosen cadence, so
different rules can use entirely different duration entities, intervals, or value
entities:

| Cadence | Settings the rule stores |
| --- | --- |
| Time interval | Capture interval (seconds) |
| Fit target video length | Duration entity, duration type, target video length, fallback interval |
| Entity value change | Value entity, capture-every step, direction |

When the selected rule uses **Fit target video length**, the interval is computed
once at session start — duration ÷ (frame rate × target length), never below 1
second — exactly as it is for the standalone *Fit target video length* cadence,
and then frozen for the rest of the session. If the duration entity cannot be
read as a positive number at that moment, the rule's fallback interval is used.

## Example: layer-aware printer timelapse

A trigger watching a printer's `printing` state, with these rules:

1. **If** the estimated total layer count is **above 2000** →
   *Fit target video length*, target 20 seconds. Very tall prints become a short
   clip instead of thousands of frames.
2. **If** the layer count is **below 50** → *Time interval*, every 5 seconds.
   Tiny prints still get enough frames to be watchable.
3. **Default** → *Entity value change* on the current-layer sensor, step 1. The
   classic one-frame-per-layer print timelapse for everything in between.

Whichever rule matches when the print starts paces that entire print. A print
that begins as a "tall" job stays fit-to-length even if you cancel and the layer
count drops — the decision was made at the start.

## Relationship to other cadences

The three cadences a rule can use behave exactly like their standalone
counterparts described in the [README](../README.md#-trigger-options):

- [Time interval](../README.md#-trigger-options)
- [Fit target video length](../README.md#-trigger-options)
- [Entity value change](../README.md#-trigger-options)

The only difference is that **Conditional at start** chooses between them — once,
when the session starts.
