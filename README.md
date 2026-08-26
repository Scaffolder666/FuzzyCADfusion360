# FuzzyCAD for Fusion 360

A trial port of [FuzzyCAD](https://github.com/Scaffolder666/FuzzyCAD) from Onshape to
Fusion 360. The Onshape right-panel app kept hitting interaction ceilings; this tests
whether Fusion's add-in API lets us build the interaction FuzzyCAD actually wants.

FuzzyCAD is about **uncertainty in CAD**: propose an operation without committing —
"this face should push out, roughly this far", "this edge should round over, about this
much", "this whole lump is about the right size, someone please make it real" — and let a
collaborator resolve it later, asynchronously, by sharing the file.

## Two halves of the interaction

**1. Direct manipulation in the viewport (SketchUp / Kyub / Tinkercad style).**
The tools live on a **separate bottom toolbar**, not in the sidebar. Pick one, select
geometry, and *drag right on the model*. The original body **fades translucent** and a
**soft, hand-drawn "sketchy" ghost** shows the proposed operation — the sketchiness *is*
the uncertainty ("proposed, not final"). The mark commits **the moment you drag** (no OK
needed to make it exist). Move / Rotate give you **draggable axis arrows** in the viewport
— no axis dropdown in a dialog.

| Tool | Select | Propose |
| --- | --- | --- |
| **Move** | a body | slide it (X / Y / Z arrows) |
| **Rotate** / **Axis Rotate** | a body | turn it |
| **Scale All** / **Scale X/Y/Z** | a body | resize it, uniform or per-axis |
| **Extrude** | a planar face | push it out along its normal |
| **Fillet** | an edge | round the corner over |
| **Hole** | a face | diameter + depth, both Need Input |
| **Rough Shape** | a whole body | flag the entire shape as "about right, make it real" |
| **Compare** | two bodies | present competing alternatives at one spot (Conflict) |

Each geometric tool is a Fusion **Command** with **drag manipulators**
(`DistanceValueCommandInput` / `AngleValueCommandInput`); the exact result is refreshed
live (throttled for the expensive ones) as you drag.

**2. The right panel = the async-collaboration sidebar (think Overleaf).**
Overleaf's right panel isn't a control surface — it's where collaborators see and resolve
*comments*. FuzzyCAD's panel is the same: a list of **open questions**, each a fuzzy
operation someone proposed. **Click a card** to focus its geometry in the model (and, for
an editable proposal, reopen its manipulator). Hit **Accept** and the **real geometry
actually changes** — a real move / rotate / scale / extrude / fillet / hole feature is
applied, the original un-fades, and the proposal is consumed. **Reject** discards it.

Every card also carries a lightweight **annotation layer**, independent of the operation:

- **Comments** — a text thread per question, like an Overleaf comment.
- **Reference images** — attach a picture with no upload: **on a face** (a native Canvas
  pasted flat onto a picked planar face, auto-oriented to read upright) or **floating** (a
  leader line out to a screen-facing billboard).
- **Size dimensions** — a Rough Shape automatically shows its **length / width / height**
  as CAD-style dimensions (witness lines + arrows) on the body, from an oriented bounding
  box aligned to the body's own axes.

While a proposal is open it's **CustomGraphics overlay** only — the `.f3d` carries the
*proposed, uncertain* operation in its document attributes, and sharing the file = sharing
the open questions. Accept is the deliberate, separate act that turns a question into
committed geometry.

## Visualization state model

Every mark resolves to one **phase** (`m._mark_phase(mark)`), the single source of truth
for what gets drawn:

| Phase | When | Look |
| --- | --- | --- |
| **editing** | being adjusted right now (new command, or its card reopened) | clean live preview; the comic "fuzzy" look is suppressed |
| **proposed** | committed, open, awaiting a decision | the comic / cel-shaded uncertainty look (per tool) |
| **resolved** | accepted or rejected | nothing — overlays cleared |

**Fillet is special.** It generates *new* geometry, so it carries its own translucent
solid preview computed from the kernel. That preview is shown continuously from editing
through to Accept (fillet never takes the comic look — only the radius arrow's
interactivity changes between phases), and it is cached in a dedicated graphics group so it
is re-tessellated only when a fillet's radius actually changes — never on a plain redraw or
camera move.

## Run it

1. In Fusion 360: **Utilities → Add-Ins → Scripts and Add-Ins** (or `Shift+S`).
2. **Add-Ins** tab → green **+** → **Add existing** → pick the `FuzzyCAD` folder → **Run**.
3. The **FuzzyCAD** panel opens (dock it wherever). The tools also appear on the toolbar's
   **Add-Ins** panel.
4. On the **bottom toolbar**, click a tool. Then in the viewport, **select** the matching
   geometry and **drag** a manipulator arrow — the original fades and a preview shows live;
   it's already an open question. Close the dialog (OK/Enter) to move on.
5. In the right panel, **click a card** to focus (and reopen) its geometry; add a
   **comment** or a **reference image**; hit **Accept** to apply it to the real model, or
   **Reject** to discard.

If anything errors, Fusion pops a message box with the traceback — paste it back.

## Layout

```
FuzzyCAD/
  FuzzyCAD.manifest     add-in manifest (points Fusion at FuzzyCAD.py)
  FuzzyCAD.py           entry point + loader: loads the base, then installs each patch
  FuzzyCAD_legacy.py    base implementation (commands, marks, sketchy renderer, persistence)
  core/                 lifecycle, mark-phase, commit bridge, persistence/hydration,
                        opacity + state reconcile, panel resync, stage UI, layout, clear-all
  tools/                the operations: move/scale/rotate, extrude, fillet, hole,
                        rough shape, image attach, dependent-follow, scope prompts
  visuals/              sketchy line rendering, silhouettes, the fuzzy/ghost look,
                        dimensions, operation cues, cards, uncertainty badges
  compare/              Conflict / Compare (stable, in-place, orientation-preserving)
  references/           bad-reference warnings + hover guard
  palette/              panel + toolbar HTML / CSS / JS (tool launcher, open-questions list)
  icons/                command icons
```

The code is **one base implementation plus a stack of small patches**: each `install(m)`
wraps or replaces functions on a shared module object `m`, and patches never import each
other — they only read/write attributes on `m`. Load order = wrapping order. See
`FuzzyCAD/README.md` for the architecture in detail.

## Notes & roadmap

- **Persistence** — done: marks live in the document's **Attributes** (group `FuzzyCAD`), so
  they travel with the `.f3d` for lossless async sharing; internal units are centimetres.
- **Imported STEP is direct modelling** (no timeline), so timeline features like
  `HoleFeatures` raise "Environment is not supported"; those paths go through
  BaseFeature / temporary BRep instead.
- **Drag manipulators**: if `setManipulator`'s signature differs on your Fusion build, the
  tool falls back to a typed value field (no drag arrow) so it still works.
- **Decisions belong to the user, not the system**: detected couplings are either confirmed
  by the user or shown as awareness only — never rigid auto-decisions.
- Fillet arcs in the lightweight overlay are approximate; the accepted feature is a real
  rolling-ball fillet.
- Migrate the other tools onto the `m._mark_phase` model so every tool's editing/proposed
  look is driven from one place.
- **Sketch** tool (fuzzy 2D profiles).
