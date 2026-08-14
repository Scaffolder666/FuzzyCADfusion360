# FuzzyCAD for Fusion 360

A trial port of [FuzzyCAD](https://github.com/Scaffolder666/FuzzyCAD) from Onshape to
Fusion 360, to see whether Fusion's add-in API removes the interaction ceilings we hit
on Onshape.

FuzzyCAD is a tool for **uncertainty in CAD**: propose an operation without committing to
it — "this part should move, roughly this way", "this edge should round over, about this
much" — and let a collaborator resolve it later, asynchronously, by sharing the file.

## The idea: a series of *fuzzy operation* tools

Ported from the Onshape FeatureScript set. Each tool proposes a CAD operation and draws
it as a **sketchy, hand-drawn ghost** in the viewport. **The sketchiness *is* the
uncertainty** — it reads as "proposed, not final." You modify it easily with a slider
(no ranges to type), and hit **Decide** when it's settled (the ghost snaps solid + green).

| Tool | Select | Sketchy ghost shows |
| --- | --- | --- |
| **Move** | a body | the body slid along an axis |
| **Rotate** | a body | the body turned about an axis, + a sweep arc |
| **Extrude** | a planar face | the face pushed out along its normal (a prism) |
| **Fillet** | an edge | arcs rounding the corner across the edge |

Nothing is committed to real geometry — every tool draws **CustomGraphics overlay** only.
The file carries the *proposed, uncertain* operation, to be shared and resolved later.
That non-destructiveness is the whole point.

### Interaction plumbing this leans on (all gated first)

| Capability | Onshape | Here (Fusion add-in) |
| --- | --- | --- |
| Custom toolbar button → stateful side panel | limited iframe | **Palette** (a web view) |
| Panel ⇄ CAD two-way messaging | narrow postMessage | `adsk.fusionSendData` ⇄ `sendInfoToHTML` |
| Annotations in the viewport | drawn as *real bodies* (hacky) | **CustomGraphics** overlays (non-geometry) |
| Hand-drawn "sketchy" strokes | not really possible | jittered `addLines` strokes |
| Camera-facing labels / focus | not possible | camera basis + `viewport.camera` |

## Run it

1. In Fusion 360: **Utilities → Add-Ins → Scripts and Add-Ins** (or press `Shift+S`).
2. **Add-Ins** tab → the green **+** → **Add existing** → pick the `FuzzyCAD` folder.
3. Select **FuzzyCAD** → **Run**. A **FuzzyCAD** button appears in the **Add-Ins** panel.
4. In the panel: pick a **tool** (Move / Rotate / Extrude / Fillet), select the matching
   geometry in the model (body / face / edge), optionally pick an axis, and click
   **+ Add fuzzy … at selection**.
5. A sketchy red ghost of the operation appears. Drag the mark's **slider** to change the
   amount (the ghost updates live). Hit **Decide** to finalize (turns solid green);
   **Reopen** to make it fuzzy again. **◎** focuses the camera; **×** deletes.

If anything errors, Fusion pops a message box with the traceback — paste it back.

## Layout

```
FuzzyCAD/
  FuzzyCAD.manifest     add-in manifest
  FuzzyCAD.py           tools, sketchy-line renderer, messaging, camera
  palette/
    index.html          tool strip + composer + mark list
    palette.css
    palette.js          plain JS + a tiny state store (swap for React later)
```

## Roadmap

- **Sketch** tool (fuzzy 2D profiles) and more operation types.
- In-viewport **drag manipulators** (Fusion `Command` + `DistanceValueCommandInput`)
  instead of panel sliders — modify directly on the model.
- Store marks in the document's **Attributes** so they travel with the `.f3d`
  (lossless async sharing = share the file).
- **Commit** a decided operation to real geometry via the modeling API — a deliberate,
  separate act from proposing it.
- Better fillet visualization (true rolling-ball arcs; current arcs are approximate).
