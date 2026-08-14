"""
FuzzyCAD — Fusion 360 add-in trial.

This iteration tests FuzzyCAD's *actual contribution*: a real uncertainty
REPRESENTATION, not a generic marker.

Representation built here: **Open Range (Needs Input)**.
A geometric parameter (a rotation angle, here) whose value is not decided — it
is an open range [min, max]. We render the *space of possibilities* directly in
the viewport:

  * the real body stays put (the current design),
  * two translucent ghost copies show the body rotated to min and to max,
  * an arc sweeps the angular range,
  * a camera-facing label reads  theta in [min, max].

A collaborator resolves it asynchronously by picking a value — the envelope
collapses to a single confirmed (green) ghost. Crucially we DO NOT commit the
rotation to real geometry: the point of FuzzyCAD is that the file carries the
*uncertainty itself*, to be shared and resolved later. Committing is a separate,
deliberate act (roadmap).

Everything lives in the CustomGraphics overlay layer — no real bodies are
created — so the uncertainty is non-destructive annotation on top of the model.
"""

import math
import traceback

import adsk.core
import adsk.fusion

# Keep handlers alive for the lifetime of the add-in.
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
GRAPHICS_GROUP_ID = "FuzzyCAD_Marks"

# Overlay palette.
COLOR_RANGE = (217, 47, 28)     # red   — open / undecided
COLOR_RESOLVED = (46, 160, 67)  # green — a value was chosen
GHOST_OPACITY = 0.28

# In-memory store for the trial. Real port persists to document Attributes so
# marks travel with the .f3d. Each mark is a dict; the live BRepBody is held
# separately (not JSON-serializable) keyed by mark id.
_marks = []
_bodies = {}
_next_mark_id = 1


# --- CustomGraphics group --------------------------------------------------
def _graphics_group():
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


# --- geometry helpers ------------------------------------------------------
def _axis_vector(axis):
    return {"X": (1.0, 0.0, 0.0),
            "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0)}.get(axis, (0.0, 0.0, 1.0))


def _plane_basis(axis):
    """Two orthonormal vectors spanning the rotation plane for the given axis."""
    if axis == "X":
        return (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    if axis == "Y":
        return (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)  # Z


def _rotation_matrix(anchor, axis, angle_deg):
    m = adsk.core.Matrix3D.create()
    ax, ay, az = _axis_vector(axis)
    m.setToRotation(
        math.radians(angle_deg),
        adsk.core.Vector3D.create(ax, ay, az),
        adsk.core.Point3D.create(*anchor),
    )
    return m


def _solid_color(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(
        adsk.core.Color.create(r, g, b, 255)
    )


# --- drawing one mark's envelope -------------------------------------------
def _ghost_body(group, body, matrix, rgb, opacity):
    """Draw a translucent transformed copy of `body` as overlay graphics.
    Falls back to a wireframe bounding box if addBRepBody is unavailable."""
    try:
        ghost = group.addBRepBody(body)
        ghost.transform = matrix
        ghost.color = _solid_color(rgb)
        try:
            ghost.setOpacity(opacity, True)
        except Exception:
            pass
        return
    except Exception:
        pass
    # Fallback: transformed bounding-box wireframe so the envelope still reads.
    bbox = body.boundingBox
    mn, mx = bbox.minPoint, bbox.maxPoint
    corners = [
        (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z), (mx.x, mx.y, mn.z), (mn.x, mx.y, mn.z),
        (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z), (mx.x, mx.y, mx.z), (mn.x, mx.y, mx.z),
    ]
    pts = []
    for cx, cy, cz in corners:
        p = adsk.core.Point3D.create(cx, cy, cz)
        p.transformBy(matrix)
        pts.extend([p.x, p.y, p.z])
    coords = adsk.fusion.CustomGraphicsCoordinates.create(pts)
    edges = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7]
    line = group.addLines(coords, edges, False)
    line.color = _solid_color(rgb)
    line.weight = 2


def _arc_lines(group, anchor, axis, radius, a0_deg, a1_deg, rgb):
    """A polyline arc in the rotation plane from a0 to a1 degrees."""
    (ux, uy, uz), (wx, wy, wz) = _plane_basis(axis)
    steps = 40
    pts = []
    for i in range(steps + 1):
        t = math.radians(a0_deg + (a1_deg - a0_deg) * i / steps)
        c, s = math.cos(t), math.sin(t)
        pts.extend([
            anchor[0] + radius * (c * ux + s * wx),
            anchor[1] + radius * (c * uy + s * wy),
            anchor[2] + radius * (c * uz + s * wz),
        ])
    coords = adsk.fusion.CustomGraphicsCoordinates.create(pts)
    strip = list(range(steps + 1))
    line = group.addLines(coords, strip, True)
    line.color = _solid_color(rgb)
    line.weight = 3


def _draw_mark(group, mark):
    anchor = mark["anchor"]
    axis = mark["axis"]
    radius = mark.get("radius", 3.0)
    body = _bodies.get(mark["id"])
    resolved = mark.get("resolved")

    if resolved is not None:
        # Collapsed: a single confirmed ghost at the chosen value.
        if body is not None:
            _ghost_body(group, body, _rotation_matrix(anchor, axis, resolved),
                        COLOR_RESOLVED, GHOST_OPACITY + 0.12)
        _arc_lines(group, anchor, axis, radius, 0.0, resolved, COLOR_RESOLVED)
        label = u"{}  θ = {:g}°".format(mark["label"], resolved)
        rgb = COLOR_RESOLVED
    else:
        # Open range: ghosts at min and max bracket the real body.
        if body is not None:
            _ghost_body(group, body, _rotation_matrix(anchor, axis, mark["min"]),
                        COLOR_RANGE, GHOST_OPACITY)
            _ghost_body(group, body, _rotation_matrix(anchor, axis, mark["max"]),
                        COLOR_RANGE, GHOST_OPACITY)
        _arc_lines(group, anchor, axis, radius, mark["min"], mark["max"], COLOR_RANGE)
        label = u"{}  θ ∈ [{:g}°, {:g}°]".format(mark["label"], mark["min"], mark["max"])
        rgb = COLOR_RANGE

    _draw_label(group, anchor, radius, axis, label, rgb)


def _draw_label(group, anchor, radius, axis, text_str, rgb):
    (ux, uy, uz), _ = _plane_basis(axis)
    tip = adsk.core.Point3D.create(
        anchor[0] + radius * 1.15 * ux,
        anchor[1] + radius * 1.15 * uy,
        anchor[2] + radius * 1.15 * uz,
    )
    text = group.addText(text_str, "Arial", 1.0, _label_transform(tip))
    text.color = _solid_color(rgb)
    _apply_billboard(text, tip)


def _redraw_graphics():
    _clear_graphics()
    group = _graphics_group()
    if group is None:
        return
    for mark in _marks:
        try:
            _draw_mark(group, mark)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD draw failed:\n{}".format(traceback.format_exc()))
    _app.activeViewport.refresh()


# --- camera-facing label ---------------------------------------------------
def _label_transform(anchor):
    transform = adsk.core.Matrix3D.create()
    try:
        camera = _app.activeViewport.camera
        eye, target = camera.eye, camera.target
        z = adsk.core.Vector3D.create(eye.x - target.x, eye.y - target.y, eye.z - target.z)
        z.normalize()
        up = camera.upVector.copy()
        up.normalize()
        x = up.crossProduct(z)
        x.normalize()
        y = z.crossProduct(x)
        y.normalize()
        transform.setWithCoordinateSystem(anchor, x, y, z)
    except Exception:
        transform.translation = adsk.core.Vector3D.create(anchor.x, anchor.y, anchor.z)
    return transform


def _apply_billboard(text, anchor):
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
        pass


# --- camera ----------------------------------------------------------------
def _focus_camera(point_cm):
    viewport = _app.activeViewport
    camera = viewport.camera
    target = adsk.core.Point3D.create(*point_cm)
    old = camera.target
    dx, dy, dz = target.x - old.x, target.y - old.y, target.z - old.z
    eye = camera.eye
    camera.eye = adsk.core.Point3D.create(eye.x + dx, eye.y + dy, eye.z + dz)
    camera.target = target
    camera.isSmoothTransition = True
    viewport.camera = camera


# --- selection -> body + anchor + size -------------------------------------
def _selection_body():
    """Return (body, anchor_cm, radius_cm) for the current selection, or (None, ...)."""
    sels = _ui.activeSelections
    if sels.count == 0:
        return None, None, None
    ent = sels.item(0).entity
    body = None
    if isinstance(ent, adsk.fusion.BRepBody):
        body = ent
    elif isinstance(ent, adsk.fusion.BRepFace):
        body = ent.body
    if body is None:
        return None, None, None
    bbox = body.boundingBox
    mn, mx = bbox.minPoint, bbox.maxPoint
    anchor = [(mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2]
    extent = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z, 1.0)
    return body, anchor, extent * 0.7


# --- palette messaging -----------------------------------------------------
def _mark_public(mark):
    return {k: mark[k] for k in
            ("id", "label", "axis", "min", "max", "resolved", "hasBody")}


def _send_state(palette):
    import json
    payload = json.dumps({"marks": [_mark_public(m) for m in _marks]})
    palette.sendInfoToHTML("state", payload)


def _find(mark_id):
    return next((m for m in _marks if m["id"] == mark_id), None)


class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
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

            elif action == "addRange":
                body, anchor, radius = _selection_body()
                if body is None:
                    _ui.messageBox("Select a body (or a face of one) first, "
                                   "then add an open range.")
                    html_args.returnData = json.dumps({"ok": False})
                    return
                lo = float(data.get("min", 0))
                hi = float(data.get("max", 0))
                if hi < lo:
                    lo, hi = hi, lo
                mark = {
                    "id": _next_mark_id,
                    "label": (data.get("label") or "Angle").strip(),
                    "axis": data.get("axis") or "Z",
                    "anchor": anchor,
                    "radius": radius,
                    "min": lo,
                    "max": hi,
                    "resolved": None,
                    "hasBody": True,
                }
                _bodies[_next_mark_id] = body
                _next_mark_id += 1
                _marks.append(mark)
                _redraw_graphics()
                _focus_camera(anchor)
                _send_state(palette)
                html_args.returnData = json.dumps({"ok": True})

            elif action == "resolveMark":
                mark = _find(data.get("id"))
                if mark:
                    mark["resolved"] = float(data.get("value"))
                    _redraw_graphics()
                    _send_state(palette)
                html_args.returnData = json.dumps({"ok": True})

            elif action == "reopenMark":
                mark = _find(data.get("id"))
                if mark:
                    mark["resolved"] = None
                    _redraw_graphics()
                    _send_state(palette)
                html_args.returnData = json.dumps({"ok": True})

            elif action == "focusMark":
                mark = _find(data.get("id"))
                if mark:
                    _focus_camera(mark["anchor"])
                html_args.returnData = json.dumps({"ok": True})

            elif action == "deleteMark":
                mid = data.get("id")
                _marks[:] = [m for m in _marks if m["id"] != mid]
                _bodies.pop(mid, None)
                _redraw_graphics()
                _send_state(palette)
                html_args.returnData = json.dumps({"ok": True})

        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD panel message failed:\n{}".format(
                    traceback.format_exc()))


class ShowPaletteCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            palette = _ui.palettes.itemById(PALETTE_ID)
            if palette is None:
                palette = _ui.palettes.add(
                    PALETTE_ID, PALETTE_NAME, PALETTE_URL,
                    True, True, True, 340, 520,
                )
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
                on_html = PaletteHTMLHandler()
                palette.incomingFromHTML.add(on_html)
                _handlers.append(on_html)
            else:
                palette.isVisible = True
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD failed to open panel:\n{}".format(
                    traceback.format_exc()))


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def is None:
            cmd_def = _ui.commandDefinitions.addButtonDefinition(
                CMD_ID, CMD_NAME, CMD_TOOLTIP, "")

        on_created = ShowPaletteCommandCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel and panel.controls.itemById(CMD_ID) is None:
            panel.controls.addCommand(cmd_def)

        cmd_def.execute()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to start:\n{}".format(
                traceback.format_exc()))


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
        _bodies.clear()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to stop:\n{}".format(
                traceback.format_exc()))
