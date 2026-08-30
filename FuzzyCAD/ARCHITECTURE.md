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
| mark lifecycle | `core/state/fuzzycad_mark_phase.py` | `editing`, `proposed`, `resolved` |
| visual policy | `visuals/state/fuzzycad_uncertainty_visual.py` | derives every layer from mark + phase |
| visual styling | `visuals/state/fuzzycad_visual_system.py` | semantic colors, line weights, wobble |
| animation ownership | `visuals/manipulator/fuzzycad_animation_controller.py` | at most one replay animation |
| proposal/runtime cache | `core/state/fuzzycad_runtime_store.py` | pure-Python data, tokens, group ids |
| source-body opacity | `core/render/fuzzycad_opacity_runtime.py` | apply/restore exact original opacity |
| persistent comic baseline | `visuals/comic/fuzzycad_fuzzy_boundary.py` | paper fill + sketch boundary only |
| comic self-repair | `visuals/comic/fuzzycad_comic_integrity.py` | repair a visible group that lost lines |
| reopened manipulators | `visuals/manipulator/fuzzycad_card_manipulator_reopen.py` | native edit command + session ownership |
| reopened Confirm/Accept/Reject | `core/lifecycle/fuzzycad_safe_confirm.py` | close edit via `Command.doExecute(True)` |
| shared-subject uncertainty | `core/state/fuzzycad_subject_decisions.py` | same-body compatibility + post-Accept rebase/relink |
| persistence | `core/persistence/fuzzycad_persistence.py` | document attribute snapshot + backup |
| final viewport repair | `core/state/fuzzycad_state_reconcile.py` | targeted redraw reconcile; full scan only startup/Repair |
| Hole semantics | `tools/feature/fuzzycad_hole.py` | face-local U/V position + diameter + depth + Hole reopen additions |
| Compare mark semantics | `compare/fuzzycad_compare_inplace.py` + existing Compare renderer stack | keep/drop alternatives |
| Compare creation flow | `compare/fuzzycad_compare_selection_flow.py` | first body -> second body -> Confirm |
| Compare card camera focus | `compare/fuzzycad_compare_card_focus.py` | camera only |
| Image attachments on marks | `tools/annotation/fuzzycad_image_attach.py` | native face/floating Canvases + leader lines; tokens only |
| Resolved-decision archive | `core/persistence/fuzzycad_decision_archive.py` | JSON-safe history of Accepted/Rejected cards + their comments |

## 3. Data boundaries

Long-lived Python state must not retain Fusion native wrappers such as
`BRepBody`, `BRepFace`, `BRepEdge`, `CustomGraphicsGroup`, or `CommandInput`.
Persist only JSON-safe values, entity tokens, sampled XYZ arrays, signatures, and
graphics group ids. Resolve Fusion objects fresh when an event needs them.

The legacy dictionaries (`_body`, `_entity`, command inputs) still contain live
objects for the active runtime. They are compatibility state, not a pattern to
extend into new caches/controllers.

### Shared-subject decisions

One body may carry more than one unresolved decision. The body-level comic state
means "this geometry still contains unresolved decisions"; the individual cards
encode what each decision is.

Current compatibility policy:

- Move / Rotate / Uniform Scale / Directional Scale / Axis Rotate may coexist on
  one body.
- Rough may coexist with any geometric decision.
- Extrude / Fillet / Hole may coexist with transforms and with one another.
- Accepting one topology-changing decision may invalidate another decision's
  face/edge token. `core/state/fuzzycad_subject_decisions.py` captures a pure-Python
  geometric fingerprint before the commit and conservatively relinks the surviving
  decision to the closest unambiguous face/edge on the same body afterwards.
- If relinking is ambiguous or no credible match exists, the surviving card stays
  open with `reference_lost`; it is never silently rebound to a guessed entity.

Accepting one decision commits only that decision. The surviving marks on the
same subject are then rebased onto the current body geometry while preserving
their own uncertain values. This rebase/relink is owned only by
`core/state/fuzzycad_subject_decisions.py`; individual tools should not invent their own
same-body lock or peer-update rule.

`FuzzyCAD_legacy.py::_body_locked` is historical compatibility code. It is not the
product rule. The installed runtime replaces it with the shared-subject policy
above; new code must not infer one-question-per-body semantics from the legacy
implementation.

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

When several marks share one body, `visuals/state/fuzzycad_uncertainty_visual.py`
aggregates them into one body presentation. A non-comic Editing mark temporarily
owns the viewport and suppresses the Proposed comic baseline; Confirm returns the
body to the comic baseline if any unresolved mark remains.

### Global redraw invariant

`_redraw_marks()` means **persistent visual meaning changed**. It is not a generic
"something moved" callback and must not be used as an animation/manipulator frame
renderer.

Continuous Editing should update only the active preview layer. Move, Rotate, and
uniform Scale should reuse already-created preview graphics and change only their
transform. `inputChanged` and `executePreview` may both arrive for one numeric
value; render that value once.

The valid reasons for a global redraw are discrete transitions such as Confirm ->
Proposed, Accept/Reject -> Resolved, explicit persistent reveal/focus, startup,
or explicit viewport Repair. A sidebar numeric edit may redraw once after the new
value is applied, never once before and once after.

Full-document recovery work (for example scanning `design.allComponents` to find
legacy orphan opacity) is not part of ordinary redraw. It runs at startup or from
Inspector Repair.

### Persistent overlays

`core/render/fuzzycad_visual_transition.py` replaces `_redraw_marks` with an authoritative
render transaction that does not call the historical draw wrappers. Visual work that
must run on every authoritative redraw but is not owned by that transaction (for
example `tools/annotation/fuzzycad_image_attach.py` drawing floating-image leader lines)
registers a zero-argument callable on `m._persistent_overlays`. The render owner
runs each overlay inside its own transaction, after the silhouette pass and before
the viewport refresh. Overlays clear their own graphics group first so a redundant
run is harmless; they follow §3 and resolve native objects fresh from stored tokens
rather than closing over live wrappers.

### Resolved-decision archive

Accepting or Rejecting a card resolves it and drops the mark. `core/persistence/fuzzycad_decision_archive.py`
keeps a minimal, read-only trail of what was there so a downstream collaborator can
still see which decisions were made and read their discussion. Both resolution paths
(the legacy palette handler and `core/fuzzycad_safe_confirm.resolve_terminal`) funnel
through `_remove_mark`; the archive records the reason at the palette-action edge
(keyed by mark id) and snapshots the card the instant before `_remove_mark` drops it.
Each row is JSON-safe -- id, tool, title, resolution, comments, timestamp only; no
geometry, field history, images, or native wrappers (§3). The trail persists on the
same save/load lifecycle as the marks (wrapping `_persist_state` / `_reload_persisted_state`)
in its own document attribute, and is pushed to the palette as a separate `archive`
message so `palette/panel_archive.js` renders the resolved section without touching
the live card render or the incremental-patch path. Removals that are not resolutions
(Clear all, deleting an unresolved card) set no reason and are not archived. A row can
be dropped from the trail with `removeArchived` (handled in the archive module, not
delegated to the resolution chain).

Persistence completeness: every state-mutating palette action writes the snapshot so
the document is self-contained for an asynchronous handoff. Comment add/remove,
image attach/toggle/remove, edits, resolutions, and Compare choices each trigger
`_persist_state`; `DocumentDeactivating` flushes once more before a document switch or
close. Image handlers persist explicitly because they return before persistence's own
palette handler runs.

## 5. Command lifecycle

Do not call `UserInterface.terminateActiveCommand()` from a custom event to close a
reopened proposal edit. That path has hard-crashed Fusion.

For `edit_existing`, `core/lifecycle/fuzzycad_safe_confirm.py` owns finishing through
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

1. keep global redraw out of manipulator/animation frames;
2. deduplicate equivalent `inputChanged` / `executePreview` values;
3. throttle or patch sidebar updates during manipulator drag;
4. make viewport refresh a single transaction per logical action;
5. keep orphan-opacity full-document sweeps out of ordinary redraw;
6. invalidate geometry cache only for affected subjects;
7. migrate `GROUP_MARKS` and silhouette toward per-mark dirty updates;
8. replace chained palette handler wrappers with one parsed event router.

## 7. Cleanup rules

Before adding a new module, ask whether an existing owner should absorb the logic.
A new module is justified when it owns a distinct lifecycle, data store, or render
layer. A one-off bug guard should eventually be merged into the layer it protects
once the behavior is proven.

`_attic/` contains replaced modules only. Files there must not be installed.
`dev/` contains diagnostics that may be expensive and should stay behind
`DEV_MODE`. The coarse crash-survival logger is currently intentionally runtime-on
while Fusion lifecycle stabilization continues.
