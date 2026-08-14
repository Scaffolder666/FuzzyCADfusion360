# FuzzyCAD for Fusion 360

A trial port of [FuzzyCAD](https://github.com/Scaffolder666/FuzzyCAD) from Onshape to
Fusion 360, to see whether Fusion's add-in API removes the interaction ceilings we hit
on Onshape.

FuzzyCAD is a tool for **uncertainty in CAD**: mark something as "not decided yet"
(this angle is unknown, this part should move but by how much is open) and let a
collaborator resolve it later — asynchronously, by sharing the file.

## What this trial proves

Two layers. First the **interaction plumbing** was gated — the things that were hard or
impossible in an Onshape right‑panel app:

| Capability | Onshape | Here (Fusion add‑in) |
| --- | --- | --- |
| Custom toolbar button → stateful side panel | limited iframe | **Palette** (a web view; put React in it) |
| Panel ⇄ CAD two‑way messaging | narrow postMessage | `adsk.fusionSendData` ⇄ `sendInfoToHTML` |
| Annotations in the viewport | drawn as *real bodies* (hacky) | **CustomGraphics overlays** (non‑geometry) |
| Text that always faces the camera | not possible | camera‑facing / **billboarded** text |
| Focus the camera on a mark | not possible | `viewport.camera` (recenters on click) |

Then — the part that is the actual research contribution — a real **uncertainty
representation**, not a generic marker:

### Open Range (Needs Input)

A rotation angle marked as **not decided yet**: an open range `[min, max]`. Instead of a
label, we render the *space of possibilities* in the viewport —

- the real body stays put (the current design),
- two translucent **ghost copies** show it rotated to `min` and to `max`,
- an **arc** sweeps the angular range,
- a camera‑facing label reads `θ ∈ [min°, max°]`.

A collaborator resolves it asynchronously by typing a value → the envelope **collapses**
to a single confirmed (green) ghost. We deliberately **do not commit** the rotation to
real geometry: the whole point of FuzzyCAD is that the file carries the *uncertainty
itself*, to be shared and resolved later. Committing is a separate, deliberate act.

Everything is CustomGraphics overlay — no real bodies are created — so the mark is
non‑destructive annotation on top of the model.

## Run it

1. In Fusion 360: **Utilities → Add‑Ins → Scripts and Add‑Ins** (or press `Shift+S`).
2. **Add‑Ins** tab → the green **+** → **Add existing** → pick the `FuzzyCAD` folder in
   this repo.
3. Select **FuzzyCAD** → **Run**. A **FuzzyCAD** button appears in the **Add‑Ins** panel
   of the Design toolbar.
4. Click it to open the panel. Select a **body** (or a face of one), set an axis and a
   min/max angle, click **+ Add open range at selection** — the body ghosts at both ends
   of the range, an arc sweeps it, and a label reads `θ ∈ [min°, max°]`.
5. In a mark's row, type a value and hit **Resolve** — the envelope collapses to one
   green ghost. **Reopen** puts it back to a range. **◎** recenters the camera; **×**
   deletes.

If anything errors, Fusion pops a message box with the traceback — paste it back and
we'll fix it (same loop as the FeatureScript work).

## Layout

```
FuzzyCAD/
  FuzzyCAD.manifest     add-in manifest
  FuzzyCAD.py           entry: toolbar button, palette, messaging, the Open Range
                        representation (ghost envelope + arc + label), camera
  palette/
    index.html          the side panel
    palette.css
    palette.js          plain JS + a tiny state store (swap for React later)
```

## Roadmap (porting FuzzyCAD's real features)

- The other three representations from the paper:
  - **Competing alternatives (Compare)** — two geometric states overlaid (A solid, B ghost).
  - **Unaddressed concern** — a highlighted region/face + concern note.
  - **Established / consensus** — the confirmed baseline state.
- Drag **manipulators** on the range ends instead of numeric-only entry.
- Interactive picking via a proper Fusion **Command** with `SelectionCommandInput`
  (event-driven: activate / inputChanged / preview / execute).
- Store marks in the document's **Attributes** so they travel with the `.f3d`
  (lossless async sharing = share the file).
- **Commit** a resolved value to real geometry via the modeling API (a deliberate,
  separate act from resolving the uncertainty).
