# FuzzyCAD for Fusion 360

A trial port of [FuzzyCAD](https://github.com/Scaffolder666/FuzzyCAD) from Onshape to
Fusion 360. The Onshape right-panel app kept hitting interaction ceilings; this tests
whether Fusion's add-in API lets us build the interaction FuzzyCAD actually wants.

FuzzyCAD is about **uncertainty in CAD**: propose an operation without committing —
"this face should push out, roughly this far", "this edge should round over, about this
much" — and let a collaborator resolve it later, asynchronously, by sharing the file.

## Two halves of the interaction

**1. Direct manipulation in the viewport (SketchUp / Kyub / Tinkercad style).**
The tools live on a **separate bottom toolbar**, not in the sidebar. Pick one, select
geometry, and *drag right on the model*. The original body **fades translucent** and a
**soft, hand-drawn "sketchy" ghost** shows the proposed operation — the sketchiness *is*
the uncertainty ("proposed, not final"). The mark commits **the moment you drag** (no OK
needed to make it exist). Move / Rotate give you **draggable axis arrows** in the viewport
— no axis dropdown in a dialog.

| Tool | Select | Drag to |
| --- | --- | --- |
| **Move** | a body | slide it (X / Y / Z arrows) |
| **Rotate** | a body | turn it (X / Y / Z arrows) |
| **Extrude** | a planar face | push it out along its normal |
| **Fillet** | an edge | round the corner over |

Each tool is a Fusion **Command** with **drag manipulators**
(`DistanceValueCommandInput` / `AngleValueCommandInput`); `executePreview` redraws the
sketchy ghost live as you drag.

**2. The right panel = the async-collaboration sidebar (think Overleaf).**
Overleaf's right panel isn't a control surface — it's where collaborators see and resolve
*comments*. FuzzyCAD's panel is the same: a list of **open questions**, each a fuzzy
operation someone proposed. **Click a card** to focus its geometry in the model. Hit
**Accept** and the **real geometry actually changes** — a real move / rotate / extrude /
fillet feature is applied, the original un-fades, and the proposal is consumed. **×**
discards it.

While a proposal is open it's **CustomGraphics overlay** only — the `.f3d` carries the
*proposed, uncertain* operation, and sharing the file = sharing the open questions. Accept
is the deliberate, separate act that turns a question into committed geometry.

## Run it

1. In Fusion 360: **Utilities → Add-Ins → Scripts and Add-Ins** (or `Shift+S`).
2. **Add-Ins** tab → green **+** → **Add existing** → pick the `FuzzyCAD` folder → **Run**.
3. The **FuzzyCAD** panel opens (dock it wherever). The four tools also appear on the
   toolbar's **Add-Ins** panel.
4. On the **bottom toolbar**, click a tool (**Move / Rotate / Extrude / Fillet**). Then in
   the viewport, **select** the matching geometry and **drag** a manipulator arrow — the
   original fades and a sketchy ghost previews live; it's already an open question. Close
   the dialog (OK/Enter) to move on.
5. In the right panel, **click a card** to focus its geometry; hit **Accept** to apply it
   to the real model; **×** to discard.

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
