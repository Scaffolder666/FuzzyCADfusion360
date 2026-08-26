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
- **Confirm must switch visual state immediately; it must not wait for a new geometry sample or command destroy.**

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

## Body-level precedence

A body can be referenced by more than one unresolved mark. Body presentation is aggregated centrally:

1. An actively edited non-comic proposal suppresses the comic baseline for that body.
2. Rough Shape is the explicit exception: its Editing state keeps the comic baseline.
3. If no non-comic Editing mark owns the body, any Proposed comic mark makes the body comic.
4. A comic body hides the underlying real body's surface almost completely and renders the paper fill/sketch boundary above it.
5. Fillet and Hole Editing use 0.50 source opacity so overlapping proposal geometry remains legible.

## No Note tool

Note/Constraint is not part of the current interaction tool set and is intentionally absent from this matrix. Legacy persisted note data may still be tolerated for backward compatibility, but it must not define current visual behavior.
