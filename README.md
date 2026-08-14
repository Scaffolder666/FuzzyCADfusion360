# FuzzyCAD for Fusion 360

A trial port of [FuzzyCAD](https://github.com/Scaffolder666/FuzzyCAD) from Onshape to
Fusion 360. The Onshape right-panel app kept hitting interaction ceilings; this tests
whether Fusion's add-in API lets us build the interaction FuzzyCAD actually wants.

FuzzyCAD is about **uncertainty in CAD**: propose an operation without committing —
"this face should push out, roughly this far", "this edge should round over, about this
much" — and let a collaborator resolve it later, asynchronously, by sharing the file.

## Two halves of the interaction

**1. Direct manipulation in the viewport (SketchUp / Kyub style).**
Pick a tool, then *drag right on the model*. A face pushes out, a body slides, an edge
rounds over — and it draws as a **sketchy, hand-drawn ghost**. The sketchiness *is* the
uncertainty: "proposed, not final." Each tool is a Fusion **Command** with a **drag
manipulator** (`DistanceValueCommandInput` / `AngleValueCommandInput`); the
`executePreview` event redraws the sketchy ghost live as you drag.

| Tool | Select | Drag to |
| --- | --- | --- |
| **Move** | a body | slide it along an axis |
| **Rotate** | a body | turn it about an axis |
| **Extrude** | a planar face | push it out along its normal |
| **Fillet** | an edge | round the corner over |

**2. The right panel = the async-collaboration sidebar (think Overleaf).**
Overleaf's right panel isn't a control surface — it's where collaborators see and resolve
*comments*. FuzzyCAD's panel is the same idea: a list of **open questions**, each a fuzzy
operation someone proposed. A collaborator reads it, nudges the amount if needed, and hits
**Decide** — the ghost snaps solid + green. Reopen puts it back in question.

Nothing is committed to real geometry — every ghost is **CustomGraphics overlay** only, so
the `.f3d` file carries the *proposed, uncertain* operation. Sharing the file = sharing the
open questions. That non-destructiveness is the whole point.

## Run it

1. In Fusion 360: **Utilities → Add-Ins → Scripts and Add-Ins** (or `Shift+S`).
2. **Add-Ins** tab → green **+** → **Add existing** → pick the `FuzzyCAD` folder → **Run**.
3. The **FuzzyCAD** panel opens (dock it wherever). The four tools also appear on the
   toolbar's **Add-Ins** panel.
4. In the panel, click a tool (**Move / Rotate / Extrude / Fillet**). Then in the
   viewport, **select** the matching geometry and **drag** the manipulator arrow — a sketchy
   ghost previews live. Click **OK** to add it as an open question.
5. In the panel's list, drag a mark's slider to adjust the amount, or hit **Decide** to
   finalize (solid green). **◎** focuses the camera; **×** deletes.

If anything errors, Fusion pops a message box with the traceback — paste it back.

## Layout

```
FuzzyCAD/
  FuzzyCAD.manifest     add-in manifest
  FuzzyCAD.py           tool commands + drag manipulators, sketchy renderer,
                        live preview, the collaboration-panel messaging, camera
  palette/
    index.html          tool launcher + open-questions list
    palette.css
    palette.js          plain JS + a tiny state store (swap for React later)
```

## Notes & roadmap

- **Drag manipulators**: if `setManipulator`'s signature differs on your Fusion build, the
  tool falls back to a typed value field (no drag arrow) so it still works — tell us and
  we'll fix the manipulator.
- **Persistence**: store marks in the document's **Attributes** so they travel with the
  `.f3d` (lossless async sharing).
- **Comments**: give each open question a text thread, like an Overleaf comment.
- **Commit**: optionally bake a decided operation into real geometry via the modeling API
  — a deliberate, separate act from proposing it.
- Fillet arcs are approximate (bezier across the corner); true rolling-ball fillets later.
- **Sketch** tool (fuzzy 2D profiles).
