# FuzzyCAD runtime architecture

This document describes the current study/runtime architecture. It is intentionally
more prescriptive than the historical patch stack: new work should move toward
single owners and explicit dependencies rather than adding another hidden wrapper.

## 1. Top-level rule

`FuzzyCAD.py` is the normal owner of module installation order.

The codebase still layers `install(m)` patches over `FuzzyCAD_legacy.py`, so install
order is behavior: a later wrapper sits outside an earlier wrapper. Because of
that, sibling modules should not silently install each other. Shared services are
installed explicitly by `FuzzyCAD.py` and exposed on the shared module object `m`.

Defensive lazy loading may remain temporarily for backward compatibility, but it
must not be the primary dependency path.

## 2. Current single owners

| Concern | Runtime owner | Contract |
|---|---|---|
| mark lifecycle | `core/fuzzycad_mark_phase.py` | `editing`, `proposed`, `resolved` |
| visual policy | `visuals/fuzzycad_uncertainty_visual.py` | derives every layer from mark + phase |
| visual styling | `visuals/fuzzycad_visual_system.py` | semantic colors, line weights, wobble |
| animation ownership | `visuals/fuzzycad_animation_controller.py` | at most one replay animation |
| proposal/runtime cache | `core/fuzzycad_runtime_store.py` | pure-Python data, tokens, group ids |
| source-body opacity | `core/fuzzycad_opacity_runtime.py` | apply/restore exact original opacity |
| persistent comic baseline | `visuals/fuzzycad_fuzzy_boundary.py` | paper fill + sketch boundary only |
| comic self-repair | `visuals/fuzzycad_comic_integrity.py` | repair a visible group that lost lines |
| reopened manipulators | `visuals/fuzzycad_card_manipulator_reopen.py` | native edit command + session ownership |
| reopened Confirm/Accept/Reject | `core/fuzzycad_safe_confirm.py` | close edit via `Command.doExecute(True)` |
| persistence | `core/fuzzycad_persistence.py` | document attribute snapshot + backup |
| final viewport repair | `core/fuzzycad_state_reconcile.py` | drift repair, not lifecycle policy |
| Compare mark semantics | `compare/fuzzycad_compare_inplace.py` + existing Compare renderer stack | keep/drop alternatives |
| Compare creation flow | `compare/fuzzycad_compare_selection_flow.py` | first body -> second body -> Confirm |
| Compare card camera focus | `compare/fuzzycad_compare_card_focus.py` | camera only |

## 3. Data boundaries

Long-lived Python state must not retain Fusion native wrappers such as
`BRepBody`, `BRepFace`, `BRepEdge`, `CustomGraphicsGroup`, or `CommandInput`.
Persist only JSON-safe values, entity tokens, sampled XYZ arrays, signatures, and
graphics group ids. Resolve Fusion objects fresh when an event needs them.

The legacy dictionaries (`_body`, `_entity`, command inputs) still contain live
objects for the active runtime. They are compatibility state, not a pattern to
extend into new caches/controllers.

## 4. Visual lifecycle

The visual contract is in `visuals/VISUAL_STATE_SPEC.md`.

The invariant is:

```
mark data
  -> _mark_phase()
  -> _visual_state() / body aggregation
  -> individual render layers
```

Renderers may draw geometry, but they must not redefine whether a mark is
Editing/Proposed/Resolved. Proposed baseline visibility must not depend on hover.

## 5. Command lifecycle

Do not call `UserInterface.terminateActiveCommand()` from a custom event to close a
reopened proposal edit. That path has hard-crashed Fusion.

For `edit_existing`, `core/fuzzycad_safe_confirm.py` owns finishing through
`Command.doExecute(True)`, then terminal Accept/Reject resolves the plain mark only
after the native command has destroyed itself.

When switching between card edits, let Fusion pre-empt the previous edit by
executing the next edit command. Session generations guard late Destroy/Preview
events from touching the new owner.

## 6. Performance direction

The current main performance debt is wrapper fan-out, not raw line styling.
A logical interaction can still traverse several `_redraw_marks`, palette, opacity,
comic, silhouette, and refresh wrappers.

Optimization order:

1. avoid global redraw for ephemeral hover/animation;
2. throttle or patch sidebar updates during manipulator drag;
3. make viewport refresh a single transaction per logical action;
4. keep orphan-opacity full-document sweeps out of ordinary redraw;
5. invalidate geometry cache only for affected subjects;
6. migrate `GROUP_MARKS` and silhouette toward per-mark dirty updates;
7. replace chained palette handler wrappers with one parsed event router.

## 7. Cleanup rules

Before adding a new module, ask whether an existing owner should absorb the logic.
A new module is justified when it owns a distinct lifecycle, data store, or render
layer. A one-off bug guard should eventually be merged into the layer it protects
once the behavior is proven.

`_attic/` contains replaced modules only. Files there must not be installed.
`dev/` contains diagnostics that may be expensive and should stay behind
`DEV_MODE`. The coarse crash-survival logger is currently intentionally runtime-on
while Fusion lifecycle stabilization continues.
