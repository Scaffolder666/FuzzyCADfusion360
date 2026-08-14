"""
FuzzyCAD — Fusion 360 add-in trial.

Proves the interaction loop that Onshape's right-panel app could not do:
  1. A custom toolbar button that opens a stateful, dockable side panel (Palette).
  2. Two-way messaging between the panel (HTML/JS) and Fusion (Python).
  3. Annotations drawn in the viewport as CustomGraphics (NOT real geometry).
  4. Text that always faces the camera (billboarded CustomGraphics text).
  5. Clicking a mark in the panel recenters the camera on it.

This is intentionally small. It is the "hello world" of the interaction layer,
not a port of every FuzzyCAD feature. See README.md for the roadmap.
"""

import traceback

import adsk.core
import adsk.fusion

# Keep handlers alive for the lifetime of the add-in. If these are garbage
# collected, Fusion silently stops delivering their events.
_handlers = []
_app = None
_ui = None

# --- identifiers -----------------------------------------------------------
CMD_ID = "FuzzyCAD_ShowPanel"
CMD_NAME = "FuzzyCAD"
CMD_TOOLTIP = "Open the FuzzyCAD uncertainty panel"
PALETTE_ID = "FuzzyCAD_Palette"
PALETTE_NAME = "FuzzyCAD"
PALETTE_URL = "palette/index.html"
PANEL_ID = "SolidScriptsAddinsPanel"  # the "Add-Ins" panel on the Design toolbar

# CustomGraphics group id, so we can clear/redraw our overlays without touching
# the real model.
GRAPHICS_GROUP_ID = "FuzzyCAD_Marks"

# In-memory mark store for the trial. Real port persists to document Attributes
# (see README roadmap) so marks travel with the .f3d.
# Each mark: {"id": int, "label": str, "kind": str, "point": [x, y, z] cm}
_marks = []
_next_mark_id = 1


# --- CustomGraphics --------------------------------------------------------
def _graphics_group():
    """Return (creating if needed) our dedicated CustomGraphics group on the
    active design's root component."""
    design = _app.activeProduct
    if not isinstance(design, adsk.fusion.Design):
        return None
    root = design.rootComponent
    for i in range(root.customGraphicsGroups.count):
        group = root.customGraphicsGroups.item(i)
        if group.id == GRAPHICS_GROUP_ID:
            return group
    group = root.customGraphicsGroups.add()
    group.id = GRAPHICS_GROUP_ID
    return group


def _clear_graphics():
    group = _graphics_group()
    if group:
        group.deleteMe()


_MARK_COLORS = {
    # FuzzyCAD's uncertainty families, mapped to overlay colors.
    "needs_input": (217, 47, 28),   # red   — open parameter / geometric concern
    "note": (63, 124, 186),         # blue  — comment / consideration
    "conflict": (240, 160, 20),     # amber — competing alternatives
}


def _redraw_graphics():
    """Rebuild all overlays from the mark store."""
    _clear_graphics()
    group = _graphics_group()
    if group is None:
        return

    for mark in _marks:
        x, y, z = mark["point"]
        r, g, b = _MARK_COLORS.get(mark["kind"], _MARK_COLORS["needs_input"])
        color = adsk.core.Color.create(r, g, b, 255)
        colored = adsk.fusion.CustomGraphicsSolidColorEffect.create(color)

        # --- a small 3D cross at the mark point ---
        s = 0.6  # cm (internal units are centimeters)
        coords = adsk.fusion.CustomGraphicsCoordinates.create(
            [
                x - s, y, z,  x + s, y, z,
                x, y - s, z,  x, y + s, z,
                x, y, z - s,  x, y, z + s,
            ]
        )
        line = group.addLines(coords, [0, 1, 2, 3, 4, 5], True)
        line.color = colored
        line.weight = 3

        # --- label placed just above the cross, oriented to face the camera ---
        anchor = adsk.core.Point3D.create(x, y + s, z)
        transform = _label_transform(anchor)
        text = group.addText(mark["label"], "Arial", 1.2, transform)
        text.color = colored

        # If this Fusion build exposes true billboarding, add it so the label
        # keeps facing the camera as you orbit. Otherwise the transform above
        # already faces it from the current view.
        _apply_billboard(text, anchor)

    _app.activeViewport.refresh()


def _label_transform(anchor):
    """A placement matrix that stands the label up and faces it toward the
    current camera. This is 'good from the view where you added it' even on
    builds without the live billboarding API."""
    transform = adsk.core.Matrix3D.create()
    try:
        camera = _app.activeViewport.camera
        eye, target = camera.eye, camera.target
        # local Z = toward the viewer
        z = adsk.core.Vector3D.create(eye.x - target.x, eye.y - target.y, eye.z - target.z)
        z.normalize()
        up = camera.upVector.copy()
        up.normalize()
        # local X = up x z, then re-derive a clean local Y
        x = up.crossProduct(z)
        x.normalize()
        y = z.crossProduct(x)
        y.normalize()
        transform.setWithCoordinateSystem(anchor, x, y, z)
    except Exception:
        transform.translation = adsk.core.Vector3D.create(anchor.x, anchor.y, anchor.z)
    return transform


def _apply_billboard(text, anchor):
    """Best-effort: make a CustomGraphicsText always face the camera.
    No-op if this Fusion build doesn't expose the billboarding API."""
    factory = getattr(adsk.fusion, "CustomGraphicsBillBoarding", None)
    if factory is None:
        factory = getattr(adsk.core, "CustomGraphicsBillBoarding", None)
    if factory is None or not hasattr(factory, "create"):
        return
    try:
        billboard = factory.create(anchor)
        styles = (getattr(adsk.fusion, "CustomGraphicsBillBoardStyles", None)
                  or getattr(adsk.core, "CustomGraphicsBillBoardStyles", None))
        if styles is not None:
            billboard.billBoardStyle = styles.ScreenBillBoardStyle
        text.billBoarding = billboard
    except Exception:
        # Text still renders; it just won't rotate to face the camera.
        pass


# --- camera ----------------------------------------------------------------
def _focus_camera(point_cm):
    """Recenter the active viewport camera on the given point, keeping the
    current view direction and distance."""
    viewport = _app.activeViewport
    camera = viewport.camera
    target = adsk.core.Point3D.create(*point_cm)

    # Shift eye by the same delta so the view direction/zoom are unchanged.
    old_target = camera.target
    dx = target.x - old_target.x
    dy = target.y - old_target.y
    dz = target.z - old_target.z
    eye = camera.eye
    camera.eye = adsk.core.Point3D.create(eye.x + dx, eye.y + dy, eye.z + dz)
    camera.target = target
    camera.isSmoothTransition = True
    viewport.camera = camera


# --- selection -> point ----------------------------------------------------
def _selection_centroid():
    """Return the centroid (cm) of the current selection, or None."""
    sels = _ui.activeSelections
    if sels.count == 0:
        return None
    ent = sels.item(0).entity

    # Face / body: use bounding-box center.
    try:
        bbox = ent.boundingBox
        if bbox:
            mn, mx = bbox.minPoint, bbox.maxPoint
            return [(mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2]
    except Exception:
        pass

    # Vertex / construction point.
    for attr in ("geometry", "point"):
        try:
            p = getattr(ent, attr)
            return [p.x, p.y, p.z]
        except Exception:
            continue
    return None


# --- palette messaging -----------------------------------------------------
def _send_state(palette):
    """Push the full mark list to the panel."""
    import json
    payload = json.dumps({"marks": _marks})
    palette.sendInfoToHTML("state", payload)


class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    """Receives messages sent from the panel via adsk.fusionSendData(action, data)."""

    def notify(self, args):
        global _next_mark_id
        try:
            import json
            html_args = adsk.core.HTMLEventArgs.cast(args)
            action = html_args.action
            data = json.loads(html_args.data) if html_args.data else {}
            palette = _ui.palettes.itemById(PALETTE_ID)

            if action == "ready":
                _send_state(palette)

            elif action == "addMark":
                point = _selection_centroid()
                if point is None:
                    _ui.messageBox("Select a face, body, or point first, then add a mark.")
                    html_args.returnData = json.dumps({"ok": False})
                    return
                mark = {
                    "id": _next_mark_id,
                    "label": data.get("label") or "Needs input",
                    "kind": data.get("kind") or "needs_input",
                    "point": point,
                }
                _next_mark_id += 1
                _marks.append(mark)
                _redraw_graphics()
                _focus_camera(point)
                _send_state(palette)
                html_args.returnData = json.dumps({"ok": True})

            elif action == "focusMark":
                mark = next((m for m in _marks if m["id"] == data.get("id")), None)
                if mark:
                    _focus_camera(mark["point"])
                html_args.returnData = json.dumps({"ok": True})

            elif action == "deleteMark":
                _marks[:] = [m for m in _marks if m["id"] != data.get("id")]
                _redraw_graphics()
                _send_state(palette)
                html_args.returnData = json.dumps({"ok": True})

        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD panel message failed:\n{}".format(traceback.format_exc()))


class ShowPaletteCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    """Toolbar button clicked -> create (once) and show the palette."""

    def notify(self, args):
        try:
            palette = _ui.palettes.itemById(PALETTE_ID)
            if palette is None:
                palette = _ui.palettes.add(
                    PALETTE_ID, PALETTE_NAME, PALETTE_URL,
                    True,   # isVisible
                    True,   # showCloseButton
                    True,   # isResizable
                    320, 480,
                )
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight

                on_html = PaletteHTMLHandler()
                palette.incomingFromHTML.add(on_html)
                _handlers.append(on_html)
            else:
                palette.isVisible = True
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD failed to open panel:\n{}".format(traceback.format_exc()))


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        # Command definition (the clickable button).
        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def is None:
            cmd_def = _ui.commandDefinitions.addButtonDefinition(
                CMD_ID, CMD_NAME, CMD_TOOLTIP, ""
            )

        on_created = ShowPaletteCommandCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)

        # Put the button on the Add-Ins panel of the Design workspace toolbar.
        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel and panel.controls.itemById(CMD_ID) is None:
            panel.controls.addCommand(cmd_def)

        # Open the panel immediately so the trial is one click to see.
        cmd_def.execute()

    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    try:
        _clear_graphics()
        if _app and _app.activeViewport:
            _app.activeViewport.refresh()

        palette = _ui.palettes.itemById(PALETTE_ID)
        if palette:
            palette.deleteMe()

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            ctrl = panel.controls.itemById(CMD_ID)
            if ctrl:
                ctrl.deleteMe()

        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()

        _handlers.clear()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to stop:\n{}".format(traceback.format_exc()))
