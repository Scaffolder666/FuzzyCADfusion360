# FuzzyCAD for Fusion 360

A trial port of [FuzzyCAD](https://github.com/Scaffolder666/FuzzyCAD) from Onshape to
Fusion 360, to see whether Fusion's add-in API removes the interaction ceilings we hit
on Onshape.

FuzzyCAD is a tool for **uncertainty in CAD**: mark something as "not decided yet"
(this angle is unknown, this part should move but by how much is open) and let a
collaborator resolve it later — asynchronously, by sharing the file.

## What this trial proves

The five things that were hard or impossible in an Onshape right‑panel app, all working
here in one loop:

| Capability | Onshape | Here (Fusion add‑in) |
| --- | --- | --- |
| Custom toolbar button → stateful side panel | limited iframe | **Palette** (a web view; put React in it) |
| Panel ⇄ CAD two‑way messaging | narrow postMessage | `adsk.fusionSendData` ⇄ `sendInfoToHTML` |
| Annotations in the viewport | drawn as *real bodies* (hacky) | **CustomGraphics overlays** (non‑geometry) |
| Text that always faces the camera | not possible | **billboarded** CustomGraphics text |
| Focus the camera on a mark | not possible | `viewport.camera` (recenters on click) |

## Run it

1. In Fusion 360: **Utilities → Add‑Ins → Scripts and Add‑Ins** (or press `Shift+S`).
2. **Add‑Ins** tab → the green **+** → **Add existing** → pick the `FuzzyCAD` folder in
   this repo.
3. Select **FuzzyCAD** → **Run**. A **FuzzyCAD** button appears in the **Add‑Ins** panel
   of the Design toolbar.
4. Click it to open the panel. Select a body/face, click **+ Add mark at selection** — a
   red cross + a billboarded label appears in the viewport and the camera recenters.
   **Focus** on any row recenters the camera on that mark.

If anything errors, Fusion pops a message box with the traceback — paste it back and
we'll fix it (same loop as the FeatureScript work).

## Layout

```
FuzzyCAD/
  FuzzyCAD.manifest     add-in manifest
  FuzzyCAD.py           entry: toolbar button, palette, messaging, CustomGraphics, camera
  palette/
    index.html          the side panel
    palette.css
    palette.js          plain JS + a tiny state store (swap for React later)
```

## Roadmap (porting FuzzyCAD's real features)

- Marks (Needs Input / Note / Conflict) as CustomGraphics with drag manipulators.
- Interactive picking via a proper Fusion **Command** with `SelectionCommandInput`
  (event-driven: activate / inputChanged / preview / execute).
- Store marks + comments in the document's **Attributes** so they travel with the `.f3d`
  (lossless async sharing = share the file).
- Apply the real edits (move / rotate / scale / stretch) through the modeling API.
