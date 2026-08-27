# FuzzyCAD visual state contract

This file is the design contract for viewport uncertainty. `fuzzycad_uncertainty_visual.py` is the runtime authority that implements it. Individual renderer files draw one visual layer and must not redefine lifecycle state.

## Shared moments

- **Drafting**: tool is open but no proposal mark exists yet.
- **Editing**: initial creation or a reopened proposal is actively adjustable.
- **Confirm -> Proposed**: Confirm ends Editing but does not resolve the uncertainty. The switch to Proposed is immediate.
- **Proposed default**: persistent unresolved state.
- **Proposed revealed**: optional extra proposal detail. Baseline comic uncertainty never disappears because of reveal.
- **Accept / Reject**: resolve the mark and remove uncertainty visuals.

## Shared visual grammar

- **Comic baseline** = paper/white fill + sketchy boundary + badge.
- **Orange** = operation locus/direction/control only.
- **Editing source opacity** is a tool variation. `original` means preserve the body's original Fusion opacity.
- **Initial Editing and Reopen Editing use the same visual policy.**
- **Confirm must switch visual state immediately; it must not wait for a new geometry sample or a later unrelated event.**

## Tool matrix

| Tool | Editing source | Comic in Editing | Editing detail | Proposed default | Proposed reveal |
| --- | --- | --- | --- | --- | --- |
| Move | original | OFF | clean moved candidate + move cue + manipulator | comic baseline | keep baseline + moved destination + move cue |
| Rotate | original | OFF | clean rotated candidate + rotation arc/axis + manipulator | comic baseline | keep baseline + rotated destination + rotation cue |
| Scale | original | OFF | clean scaled candidate + pivot/stretch cue + manipulator | comic baseline | keep baseline + scaled destination + scale cue |
| Directional Scale (`scale_axis`) | original | OFF | clean candidate + fixed side + active axis + moving-side cue + manipulator | comic baseline | keep baseline + candidate + fixed-side/axis/stretch cue |
| Axis Rotate | original | OFF | clean candidate + selected axis + rotation arc + manipulator | comic baseline | keep baseline + candidate + axis/rotation cue |
| Extrude | original | OFF | clean local extrude delta + affected face/depth cue + manipulator | comic baseline | keep baseline + local delta + depth cue |
| Fillet | **0.50** | OFF | exact/local fillet candidate + radius/affected-edge cue + manipulator | comic baseline | **same as default; no extra detail** |
| Hole | **0.50** | OFF | translucent cut volume + diameter/depth/placement cue + manipulators | comic baseline | **same as default; no extra detail** |
| Rough Shape | comic-owned | **ON** | comic body remains visible while the rough mark is active | comic baseline | **same as default; no extra detail** |
| Compare / Conflict | original | OFF while editing an alternative | active alternative editing presentation | comic baseline + Conflict badge | baseline remains + alternatives, only on explicit compare/focus (not hover) |

## Redraw transaction contract

`_redraw_marks()` is a **global state-transition transaction**, not a frame renderer. It may rebuild persistent mark layers, synchronize opacity/comic state, reconcile the viewport, and refresh Fusion. Do not call it merely because a manipulator produced another frame.

### Continuous Editing

- Opening Editing may build the active preview once and transition the source body from Proposed presentation to Editing presentation.
- During a Move / Rotate / uniform Scale drag, reuse the active preview graphics and update only their transform. Do not clear/rebuild all persistent marks.
- Other tools may update their **active preview group** when geometry truly changes, but they still must not redraw unrelated persistent marks on every frame.
- `inputChanged` and `executePreview` may both fire for the same value. They are compatibility event sources, not permission to render the same value twice.
- Do not write source opacity repeatedly when its target did not change.
- Do not call `activeViewport.refresh()` from every drag frame. Fusion's command preview cycle already refreshes live CustomGraphics.

### Discrete transitions

A global redraw is appropriate when persistent visual meaning changes, including:

- **Confirm -> Proposed**: remove the Editing preview and show the Proposed comic baseline immediately.
- **Accept / Reject -> Resolved**: remove uncertainty visuals and restore the original source presentation; Accept may first commit real geometry.
- **Explicit Proposed reveal/focus**: show or collapse persistent proposal detail while keeping the comic baseline.
- **Startup / explicit Repair**: reconstruct authoritative persistent state and recover drift.

A numeric edit from the sidebar may cause **at most one** persistent redraw after the new value has been applied. Never redraw once to reveal the old value and then redraw again for the new value. Entering `editManipulator` must not first redraw the Proposed scene immediately before the Editing command performs its own transition.

### Recovery work is not ordinary redraw work

A normal redraw may synchronize only FuzzyCAD-owned marks/tokens. A scan of `design.allComponents` / every BRep body is an expensive recovery operation and belongs only to startup or explicit Inspector Repair.

## Animation ownership

`fuzzycad_animation_controller.py` is the single authority for proposal replay animation. Tool-specific animation files only provide geometry/transform rendering.

- **At most one animation may be active in the viewport.**
- Starting an animation for another card or another tool **immediately stops and clears the previous animation first**.
- A stale hover-end/frame event may only affect the animation owner + mark that created it; it must never stop a newer animation.
- Focus, editing, Confirm, Accept, Reject, Compare choice, or switching tools cancels the active replay.
- The controller owns start/frame/stop, active owner, active mark, timing, frame throttling, easing, and supersession.
- Animation state stores only pure Python data. Renderer files resolve CustomGraphics groups fresh and never retain Fusion wrappers across events.

| Tool | Proposed replay animation |
| --- | --- |
| Move | translation replay + movement arrow |
| Rotate | rotation replay |
| Scale | uniform scale replay |
| Directional Scale (`scale_axis`) | axis-scale replay |
| Axis Rotate | arbitrary-axis rotation replay |
| Extrude | face/depth translation replay |
| Fillet | none |
| Hole | none |
| Rough Shape | none |
| Compare / Conflict | none; explicit compare/focus only |

## Body-level precedence

A body can be referenced by more than one unresolved mark. Body presentation is aggregated centrally:

1. An actively edited non-comic proposal suppresses the comic baseline for that body.
2. Rough Shape is the explicit exception: its Editing state keeps the comic baseline.
3. If no non-comic Editing mark owns the body, any Proposed comic mark makes the body comic.
4. A comic body hides the underlying real body's surface almost completely and renders the paper fill/sketch boundary above it.
5. Fillet and Hole Editing use 0.50 source opacity so overlapping proposal geometry remains legible.

## No Note tool

Note/Constraint is not part of the current interaction tool set and is intentionally absent from this matrix. Legacy persisted note data may still be tolerated for backward compatibility, but it must not define current visual behavior.
