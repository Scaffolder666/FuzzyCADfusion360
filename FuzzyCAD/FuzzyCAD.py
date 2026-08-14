"""
FuzzyCAD — Fusion 360 add-in trial.

A *series* of fuzzy operation tools, ported from the Onshape FeatureScript set.
Each tool proposes a CAD operation (move / rotate / extrude / fillet) and draws
it as a **sketchy, hand-drawn ghost** in the viewport. The sketchiness *is* the
uncertainty: it reads as "proposed, not final yet." The user modifies it easily
(a slider — no ranges to type), and resolves it (the ghost turns solid + green)
when the decision is made.

Nothing is committed to real geometry — every tool draws CustomGraphics overlay
only. The point of FuzzyCAD is that the file carries the *proposed, uncertain*
operation, to be shared and resolved asynchronously.

Tools:
  move    — a body slides along an axis (sketchy ghost of the whole body)
  rotate  — a body turns about an axis (sketchy ghost + arc)
  extrude — a face pushes out along its normal (sketchy prism)
  fillet  — an edge rounds over (sketchy arcs across the corner)
"""

import math
import random
import traceback

import adsk.core
import adsk.fusion

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
PANEL_ID = "SolidScriptsAddinsPanel"
GRAPHICS_GROUP_ID = "FuzzyCAD_Marks"

COLOR_FUZZY = (217, 47, 28)     # red   — proposed / uncertain
COLOR_RESOLVED = (46, 160, 67)  # green — decided
SKETCH_AMP_FRAC = 0.012         # jitter as a fraction of model size
EDGE_SAMPLES = 6

# In-memory store for the trial. Real port persists to document Attributes.
_marks = []          # list of public dicts (JSON-safe)
_geom = {}           # id -> cached base geometry for redraw (Point tuples)
_next_id = 1


# --- CustomGraphics group --------------------------------------------------
def _graphics_group():
    design = _app.activeProduct
    if not isinstance(design, adsk.fusion.Design):
        return None
    root = design.rootComponent
    for i in range(root.customGraphicsGroups.count):
        g = root.customGraphicsGroups.item(i)
        if g.id == GRAPHICS_GROUP_ID:
            return g
    g = root.customGraphicsGroups.add()
    g.id = GRAPHICS_GROUP_ID
    return g


def _clear_graphics():
    g = _graphics_group()
    if g:
        g.deleteMe()


def _solid(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(
        adsk.core.Color.create(r, g, b, 255))


# --- sketchy line drawing --------------------------------------------------
def _sketchy(group, pts, rgb, amp, seed, weight=2, strokes=2):
    """Draw a polyline (list of (x,y,z)) as hand-drawn strokes.
    amp=0 draws a single clean stroke (used for resolved/solid marks)."""
    if len(pts) < 2:
        return
    if amp <= 0:
        strokes = 1
    color = _solid(rgb)
    for s in range(strokes):
        rng = random.Random(seed * 131 + s * 977)
        flat = []
        for i, (x, y, z) in enumerate(pts):
            # No jitter at the very ends keeps strokes anchored; interior wobbles.
            k = 0.0 if (i == 0 or i == len(pts) - 1) else amp
            flat.extend([x + (rng.random() - 0.5) * 2 * k,
                         y + (rng.random() - 0.5) * 2 * k,
                         z + (rng.random() - 0.5) * 2 * k])
        coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(len(pts))), True)
        line.color = color
        line.weight = weight


# --- sampling geometry into point tuples -----------------------------------
def _sample_edge(edge, n=EDGE_SAMPLES):
    pts = []
    try:
        ev = edge.geometry.evaluator
        ok, tmin, tmax = ev.getParameterExtents()
        for i in range(n + 1):
            t = tmin + (tmax - tmin) * i / n
            ok, p = ev.getPointAtParameter(t)
            if ok:
                pts.append((p.x, p.y, p.z))
    except Exception:
        try:
            s = edge.startVertex.geometry
            e = edge.endVertex.geometry
            pts = [(s.x, s.y, s.z), (e.x, e.y, e.z)]
        except Exception:
            pts = []
    return pts


def _sample_body(body):
    loops = []
    for i in range(body.edges.count):
        p = _sample_edge(body.edges.item(i))
        if len(p) >= 2:
            loops.append(p)
    return loops


def _sample_face(face):
    loops = []
    for i in range(face.edges.count):
        p = _sample_edge(face.edges.item(i))
        if len(p) >= 2:
            loops.append(p)
    return loops


def _face_normal(face):
    try:
        geo = face.geometry
        nrm = getattr(geo, "normal", None)
        if nrm is not None:
            v = nrm.copy()
            v.normalize()
            return (v.x, v.y, v.z)
    except Exception:
        pass
    try:
        ev = face.evaluator
        ok, prange = ev.parametricRange()
        u = (prange.minPoint.x + prange.maxPoint.x) / 2
        v = (prange.minPoint.y + prange.maxPoint.y) / 2
        ok, nrm = ev.getNormalAtParameter(adsk.core.Point2D.create(u, v))
        nrm.normalize()
        return (nrm.x, nrm.y, nrm.z)
    except Exception:
        return (0.0, 0.0, 1.0)


def _fillet_stations(edge, n=5):
    """Sample stations along an edge, each with two in-face directions to bevel
    across. Returns list of (P, t1, t2) as tuples, or [] on failure."""
    out = []
    try:
        faces = edge.faces
        if faces.count < 2:
            return out
        f1, f2 = faces.item(0), faces.item(1)
        ev = edge.geometry.evaluator
        ok, tmin, tmax = ev.getParameterExtents()
        for i in range(1, n):
            t = tmin + (tmax - tmin) * i / n
            ok, P = ev.getPointAtParameter(t)
            ok2, tan = ev.getFirstDerivative(t)
            if not (ok and ok2):
                continue
            tan.normalize()
            ok3, n1 = f1.evaluator.getNormalAtPoint(P)
            ok4, n2 = f2.evaluator.getNormalAtPoint(P)
            if not (ok3 and ok4):
                continue
            # in-face directions perpendicular to the edge tangent
            d1 = n1.crossProduct(tan); d1.normalize()
            d2 = tan.crossProduct(n2); d2.normalize()
            # orient each to point away from the other face
            if d1.dotProduct(n2) > 0:
                d1.scaleBy(-1)
            if d2.dotProduct(n1) > 0:
                d2.scaleBy(-1)
            out.append(((P.x, P.y, P.z),
                        (d1.x, d1.y, d1.z),
                        (d2.x, d2.y, d2.z)))
    except Exception:
        return []
    return out


# --- transforms ------------------------------------------------------------
def _translate(pts, dir_unit, amount):
    dx, dy, dz = dir_unit
    return [(x + dx * amount, y + dy * amount, z + dz * amount) for (x, y, z) in pts]


def _rotate_pts(pts, anchor, axis, angle_deg):
    m = adsk.core.Matrix3D.create()
    ax, ay, az = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(axis, (0, 0, 1))
    m.setToRotation(math.radians(angle_deg),
                    adsk.core.Vector3D.create(ax, ay, az),
                    adsk.core.Point3D.create(*anchor))
    out = []
    for (x, y, z) in pts:
        p = adsk.core.Point3D.create(x, y, z)
        p.transformBy(m)
        out.append((p.x, p.y, p.z))
    return out


def _axis_unit(axis):
    return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}.get(axis, (0.0, 0.0, 1.0))


# --- per-tool drawing ------------------------------------------------------
def _draw_move(group, mark, rgb, amp):
    d = _axis_unit(mark["axis"])
    amt = mark["amount"]
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _translate(loop, d, amt), rgb, amp, mark["id"] * 100 + i)
    a = mark["anchor"]
    tip = (a[0] + d[0] * amt, a[1] + d[1] * amt, a[2] + d[2] * amt)
    _sketchy(group, [tuple(a), tip], rgb, amp, mark["id"] * 7, weight=3)


def _draw_rotate(group, mark, rgb, amp):
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        rot = _rotate_pts(loop, mark["anchor"], mark["axis"], mark["amount"])
        _sketchy(group, rot, rgb, amp, mark["id"] * 100 + i)
    _draw_arc(group, mark, rgb, amp)


def _draw_arc(group, mark, rgb, amp):
    a = mark["anchor"]
    r = mark.get("size", 3.0) * 0.6
    (ux, uy, uz), (wx, wy, wz) = _plane_basis(mark["axis"])
    steps = 28
    pts = []
    for i in range(steps + 1):
        t = math.radians((mark["amount"]) * i / steps)
        c, s = math.cos(t), math.sin(t)
        pts.append((a[0] + r * (c * ux + s * wx),
                    a[1] + r * (c * uy + s * wy),
                    a[2] + r * (c * uz + s * wz)))
    _sketchy(group, pts, rgb, amp * 0.5, mark["id"] * 13, weight=2)


def _plane_basis(axis):
    if axis == "X":
        return (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    if axis == "Y":
        return (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)


def _draw_extrude(group, mark, rgb, amp):
    g = _geom[mark["id"]]
    d = g["normal"]
    amt = mark["amount"]
    for i, loop in enumerate(g["loops"]):
        off = _translate(loop, d, amt)
        _sketchy(group, off, rgb, amp, mark["id"] * 100 + i)          # top loop
        # vertical connectors at the loop's endpoints
        _sketchy(group, [loop[0], off[0]], rgb, amp, mark["id"] * 200 + i, weight=2)
        _sketchy(group, [loop[-1], off[-1]], rgb, amp, mark["id"] * 300 + i, weight=2)


def _draw_fillet(group, mark, rgb, amp):
    r = mark["amount"]
    for i, (P, t1, t2) in enumerate(_geom[mark["id"]]["stations"]):
        a = (P[0] + t1[0] * r, P[1] + t1[1] * r, P[2] + t1[2] * r)
        b = (P[0] + t2[0] * r, P[1] + t2[1] * r, P[2] + t2[2] * r)
        # quadratic bezier from a to b, pulled toward the sharp corner P -> rounds it
        pts = []
        for k in range(9):
            u = k / 8.0
            mu = 1 - u
            pts.append((mu * mu * a[0] + 2 * mu * u * P[0] + u * u * b[0],
                        mu * mu * a[1] + 2 * mu * u * P[1] + u * u * b[1],
                        mu * mu * a[2] + 2 * mu * u * P[2] + u * u * b[2]))
        _sketchy(group, pts, rgb, amp * 0.6, mark["id"] * 100 + i, weight=3)


_DRAW = {"move": _draw_move, "rotate": _draw_rotate,
         "extrude": _draw_extrude, "fillet": _draw_fillet}


def _redraw():
    _clear_graphics()
    group = _graphics_group()
    if group is None:
        return
    for mark in _marks:
        if mark["id"] not in _geom:
            continue
        resolved = mark.get("resolved")
        rgb = COLOR_RESOLVED if resolved else COLOR_FUZZY
        amp = 0.0 if resolved else mark.get("size", 3.0) * SKETCH_AMP_FRAC
        try:
            _DRAW[mark["tool"]](group, mark, rgb, amp)
            _draw_label(group, mark, rgb)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD draw failed ({}):\n{}".format(
                    mark["tool"], traceback.format_exc()))
    _app.activeViewport.refresh()


def _unit_str(mark):
    return "°" if mark["tool"] in ("rotate",) else "mm"


def _amount_display(mark):
    v = mark["amount"]
    return v if mark["tool"] == "rotate" else v * 10.0  # cm -> mm for display


def _draw_label(group, mark, rgb):
    a = mark["anchor"]
    off = mark.get("size", 3.0) * 0.9
    tip = adsk.core.Point3D.create(a[0], a[1] + off, a[2])
    verb = mark["tool"].capitalize()
    val = "{:g}{}".format(_amount_display(mark), _unit_str(mark))
    tag = mark["label"] or verb
    suffix = " ✓" if mark.get("resolved") else " ~"
    text = group.addText(u"{}  {}{}".format(tag, val, suffix), "Arial", 1.0,
                         _label_transform(tip))
    text.color = _solid(rgb)
    _apply_billboard(text, tip)


# --- camera-facing label ---------------------------------------------------
def _label_transform(anchor):
    transform = adsk.core.Matrix3D.create()
    try:
        camera = _app.activeViewport.camera
        eye, target = camera.eye, camera.target
        z = adsk.core.Vector3D.create(eye.x - target.x, eye.y - target.y, eye.z - target.z)
        z.normalize()
        up = camera.upVector.copy(); up.normalize()
        x = up.crossProduct(z); x.normalize()
        y = z.crossProduct(x); y.normalize()
        transform.setWithCoordinateSystem(anchor, x, y, z)
    except Exception:
        transform.translation = adsk.core.Vector3D.create(anchor.x, anchor.y, anchor.z)
    return transform


def _apply_billboard(text, anchor):
    factory = getattr(adsk.fusion, "CustomGraphicsBillBoarding", None) \
        or getattr(adsk.core, "CustomGraphicsBillBoarding", None)
    if factory is None or not hasattr(factory, "create"):
        return
    try:
        billboard = factory.create(anchor)
        styles = getattr(adsk.fusion, "CustomGraphicsBillBoardStyles", None) \
            or getattr(adsk.core, "CustomGraphicsBillBoardStyles", None)
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


# --- selection -------------------------------------------------------------
def _bbox_center_size(ent):
    bbox = ent.boundingBox
    mn, mx = bbox.minPoint, bbox.maxPoint
    center = [(mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2]
    size = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z, 1.0)
    return center, size


def _selected():
    sels = _ui.activeSelections
    if sels.count == 0:
        return None
    return sels.item(0).entity


# --- build a mark per tool -------------------------------------------------
def _new_mark(tool, label, axis):
    global _next_id
    ent = _selected()
    if ent is None:
        _ui.messageBox("Select geometry first.")
        return None

    mid = _next_id
    mark = {"id": mid, "tool": tool, "label": (label or "").strip(),
            "axis": axis or "Z", "resolved": False}

    if tool in ("move", "rotate"):
        body = ent if isinstance(ent, adsk.fusion.BRepBody) else \
            (ent.body if isinstance(ent, adsk.fusion.BRepFace) else None)
        if body is None:
            _ui.messageBox("Select a body (or a face of one) for {}.".format(tool))
            return None
        center, size = _bbox_center_size(body)
        _geom[mid] = {"edges": _sample_body(body)}
        mark["anchor"] = center
        mark["size"] = size
        mark["amount"] = size * 0.25 if tool == "move" else 30.0  # cm or deg

    elif tool == "extrude":
        if not isinstance(ent, adsk.fusion.BRepFace):
            _ui.messageBox("Select a planar face for extrude.")
            return None
        center, size = _bbox_center_size(ent)
        _geom[mid] = {"loops": _sample_face(ent), "normal": _face_normal(ent)}
        mark["anchor"] = center
        mark["size"] = size
        mark["amount"] = size * 0.25

    elif tool == "fillet":
        if not isinstance(ent, adsk.fusion.BRepEdge):
            _ui.messageBox("Select an edge for fillet.")
            return None
        center, size = _bbox_center_size(ent)
        stations = _fillet_stations(ent)
        if not stations:
            _ui.messageBox("Couldn't read that edge's faces for a fillet.")
            return None
        _geom[mid] = {"stations": stations}
        mark["anchor"] = center
        mark["size"] = size
        mark["amount"] = size * 0.08

    else:
        return None

    _next_id += 1
    _marks.append(mark)
    return mark


def _public(mark):
    tool = mark["tool"]
    size = mark.get("size", 3.0)
    if tool == "rotate":
        amin, amax, step, unit = -90.0, 90.0, 1.0, "°"
        val = mark["amount"]
    else:  # move / extrude / fillet -> distance in mm
        val = mark["amount"] * 10.0
        amax = size * 10.0 * (0.6 if tool != "fillet" else 0.25)
        amin = 0.0
        step = max(round(amax / 40.0, 1), 0.1)
        unit = "mm"
    return {"id": mark["id"], "tool": tool, "label": mark["label"],
            "axis": mark["axis"], "value": round(val, 2),
            "min": round(amin, 2), "max": round(amax, 2), "step": step,
            "unit": unit, "resolved": bool(mark["resolved"])}


def _apply_value(mark, display_value):
    if mark["tool"] == "rotate":
        mark["amount"] = float(display_value)
    else:
        mark["amount"] = float(display_value) / 10.0  # mm -> cm


def _find(mid):
    return next((m for m in _marks if m["id"] == mid), None)


# --- palette messaging -----------------------------------------------------
def _send_state(palette):
    import json
    palette.sendInfoToHTML("state", json.dumps({"marks": [_public(m) for m in _marks]}))


class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            import json
            e = adsk.core.HTMLEventArgs.cast(args)
            action = e.action
            data = json.loads(e.data) if e.data else {}
            palette = _ui.palettes.itemById(PALETTE_ID)

            if action == "ready":
                _send_state(palette)

            elif action == "add":
                mark = _new_mark(data.get("tool"), data.get("label"), data.get("axis"))
                if mark:
                    _redraw()
                    _focus_camera(mark["anchor"])
                    _send_state(palette)
                e.returnData = json.dumps({"ok": bool(mark)})

            elif action == "adjust":
                mark = _find(data.get("id"))
                if mark:
                    if "value" in data:
                        _apply_value(mark, data["value"])
                    if "axis" in data:
                        mark["axis"] = data["axis"]
                    _redraw()
                e.returnData = json.dumps({"ok": True})

            elif action == "resolve":
                mark = _find(data.get("id"))
                if mark:
                    mark["resolved"] = True
                    _redraw(); _send_state(palette)
                e.returnData = json.dumps({"ok": True})

            elif action == "reopen":
                mark = _find(data.get("id"))
                if mark:
                    mark["resolved"] = False
                    _redraw(); _send_state(palette)
                e.returnData = json.dumps({"ok": True})

            elif action == "focus":
                mark = _find(data.get("id"))
                if mark:
                    _focus_camera(mark["anchor"])
                e.returnData = json.dumps({"ok": True})

            elif action == "delete":
                mid = data.get("id")
                _marks[:] = [m for m in _marks if m["id"] != mid]
                _geom.pop(mid, None)
                _redraw(); _send_state(palette)
                e.returnData = json.dumps({"ok": True})

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
                    PALETTE_ID, PALETTE_NAME, PALETTE_URL, True, True, True, 340, 560)
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
                h = PaletteHTMLHandler()
                palette.incomingFromHTML.add(h)
                _handlers.append(h)
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
        h = ShowPaletteCommandCreatedHandler()
        cmd_def.commandCreated.add(h)
        _handlers.append(h)

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel and panel.controls.itemById(CMD_ID) is None:
            panel.controls.addCommand(cmd_def)

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
        _geom.clear()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to stop:\n{}".format(traceback.format_exc()))
