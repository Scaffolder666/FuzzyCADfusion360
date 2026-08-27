# FuzzyCAD — Fusion 360 add-in

FuzzyCAD explores uncertainty-aware CAD for asynchronous, cross-disciplinary
collaboration. The current study build lets collaborators create unresolved
geometry proposals directly in Fusion, keep them visibly provisional, reopen them
for adjustment, and later Accept or Reject them.

Current interaction types are primarily:

- **Need Input** — an unresolved geometry/parameter decision.
- **Conflict / Compare** — two competing alternatives that need a choice.

Legacy persisted Note/Constraint data may still be tolerated for compatibility,
but Note is not part of the current tool set.

## Current tools

- Move / Rotate
- Scale All
- Scale X/Y/Z
- Axis Rotate
- Extrude
- Fillet
- Hole
- Rough Shape
- Compare

The user-facing lifecycle is:

```text
Drafting -> Editing -> Confirm -> Proposed -> Accept / Reject
```

Confirm means “done adjusting.” It does **not** resolve the uncertainty.

## Code structure

Fusion loads:

```text
FuzzyCAD.py          top-level loader and explicit install order
FuzzyCAD_legacy.py   compatibility base implementation
```

The codebase still uses `install(m)` runtime patches over the shared legacy module
object. Install order therefore matters: later wrappers sit outside earlier ones.
`FuzzyCAD.py` is the normal owner of that dependency/order graph.

For the full ownership map and cleanup rules, see `ARCHITECTURE.md`.
For the exact viewport contract, see `visuals/VISUAL_STATE_SPEC.md`.

### Directory responsibilities

| Folder | Responsibility |
|---|---|
| `core/` | lifecycle, persistence, runtime cache, opacity, command finishing, state repair, panel/stage infrastructure |
| `tools/` | operation-specific interaction and commit logic |
| `compare/` | Compare/Conflict creation, rendering semantics, choice, card behavior |
| `visuals/` | visual policy consumers, comic baseline, cues, silhouette, animation, focus |
| `references/` | reference-loss and hover/reference guards |
| `palette/` | right decision panel + left tool rail HTML/JS/CSS |
| `dev/` | diagnostics enabled only by `DEV_MODE` |
| `_attic/` | superseded modules; never installed |

## Runtime authorities

The code should move toward one owner per concern:

| Concern | Owner |
|---|---|
| lifecycle phase | `core/fuzzycad_mark_phase.py` |
| visual policy | `visuals/fuzzycad_uncertainty_visual.py` |
| visual styling | `visuals/fuzzycad_visual_system.py` |
| replay animation ownership | `visuals/fuzzycad_animation_controller.py` |
| geometry/render cache | `core/fuzzycad_runtime_store.py` |
| source-body opacity | `core/fuzzycad_opacity_runtime.py` |
| comic fill + sketch boundary | `visuals/fuzzycad_fuzzy_boundary.py` |
| reopened native manipulator | `visuals/fuzzycad_card_manipulator_reopen.py` |
| reopened Confirm/Accept/Reject | `core/fuzzycad_safe_confirm.py` |
| persistence | `core/fuzzycad_persistence.py` |
| final visual drift repair | `core/fuzzycad_state_reconcile.py` |

Individual renderers draw one layer. They should not invent their own lifecycle
rules.

## Visual state contract

The shared unresolved **Proposed** baseline is:

```text
paper/white fill + sketchy boundary + badge
```

Orange means the locus/direction/control of a change, not the entire proposal
volume.

Editing normally suppresses the comic baseline and shows a clean adjustable
proposal. Explicit variations are centralized:

- Fillet / Hole Editing use source opacity `0.50`.
- Rough Shape keeps comic styling while Editing.
- Fillet / Hole / Rough do not gain extra Proposed detail on hover.
- Compare expands alternatives only through explicit Compare/focus, not passive
  hover.

See `visuals/VISUAL_STATE_SPEC.md` before changing any visual behavior.

## Fusion wrapper safety

Long-lived caches/controllers must not retain Fusion native wrappers such as
`BRepBody`, `BRepFace`, `BRepEdge`, `CustomGraphicsGroup`, or `CommandInput`.
Store entity tokens, pure XYZ arrays, ids, signatures, and JSON-safe values; resolve
native objects fresh when needed.

This rule matters because Fusion may invalidate a wrapper after `deleteMe()`,
command destruction, feature edits, or document rebuilds even when a Python
reference still exists.

### Reopened-card finishing

Do not close `edit_existing` with `UserInterface.terminateActiveCommand()` from a
custom event. That path has produced native Fusion crashes.

`core/fuzzycad_safe_confirm.py` closes the current edit with
`Command.doExecute(True)`, allowing Fusion to run its own Execute/Destroy
lifecycle. Accept/Reject then resolves the plain proposal only after the native
edit command has closed.

## Geometry / preview rules

Transform tools can preview by applying matrices to sampled geometry and should
remain lightweight.

For geometry-generating tools:

- avoid creating/deleting real Fusion features on drag frames;
- cache expensive kernel-derived geometry;
- perform real commit geometry only on Accept when possible;
- use temporary BRep / CustomGraphics for previews.

STEP/imported direct-modeling documents may not support timeline feature APIs, so
Hole and similar operations need BaseFeature/temporary-BRep-compatible paths.

## Persistence

Collaboration state is stored in Fusion `Design.attributes` under the `FuzzyCAD`
group. Geometry references are persisted as entity tokens and re-resolved on
load. A lost geometry reference should degrade the viewport representation, not
silently delete the collaboration card.

The opacity runtime also stores the original numeric body opacity while a visual
override is active, allowing normal stop and later recovery to restore the source
presentation.

## Performance direction

Current performance work should reduce unnecessary global work before reducing
visual quality. The main priorities are:

1. keep hover replay ephemeral and out of full `_redraw_marks()`;
2. throttle/patch sidebar state during manipulator drag;
3. collapse repeated viewport refreshes into one refresh per logical action;
4. keep full-document opacity recovery scans out of normal interaction;
5. invalidate cached geometry only for affected bodies;
6. migrate global mark/silhouette redraw toward dirty per-mark updates;
7. eventually replace chained `PaletteHTMLHandler` wrappers with one event router.

## Development

`DEV_MODE = False` in `FuzzyCAD.py` is the normal study/runtime configuration.
`dev/` contains heavier diagnostics and performance tracing that should stay gated.
A coarse crash-survival lifecycle logger is currently intentionally runtime-on
while native command lifecycle stabilization continues.
