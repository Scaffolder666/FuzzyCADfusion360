"""
FuzzyCAD — Fusion 360 add-in trial.

Direct-manipulation fuzzy modeling, SketchUp / Kyub style. Pick a tool, then
drag right on the model — a face pushes out, a body slides, an edge rounds —
and it's drawn as a **sketchy, hand-drawn ghost**. The sketchiness *is* the
uncertainty: "proposed, not final." Decide it later and it snaps solid + green.

Interaction: each tool is a Fusion **Command** with a **drag manipulator**
(`DistanceValueCommandInput` / `AngleValueCommandInput`). As you drag, the
`executePreview` event redraws the sketchy ghost live. On OK we store a fuzzy
mark as CustomGraphics overlay — no real geometry is ever created, so the file
carries the *proposed* operation, to be shared and resolved asynchronously.

A side Palette lists the marks (Decide / Reopen / Focus / Delete) and doubles as
a tool launcher. The tools also live on the native toolbar's Add-Ins panel.
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
PALETTE_ID = "FuzzyCAD_Palette"
PALETTE_NAME = "FuzzyCAD"
PALETTE_URL = "palette/index.html"
PANEL_CMD_ID = "FuzzyCAD_ShowPanel"
PANEL_ID = "SolidScriptsAddinsPanel"
GROUP_MARKS = "FuzzyCAD_Marks"
GROUP_PREVIEW = "FuzzyCAD_Preview"

TOOLS = ("move", "rotate", "extrude", "fillet")
TOOL_CMD = {t: "FuzzyCAD_" + t.capitalize() for t in TOOLS}
TOOL_LABEL = {"move": "Fuzzy Move", "rotate": "Fuzzy Rotate",
              "extrude": "Fuzzy Extrude", "fillet": "Fuzzy Fillet"}
TOOL_FILTER = {"move": "SolidBodies", "rotate": "SolidBodies",
               "extrude": "PlanarFaces", "fillet": "Edges"}
TOOL_HINT = {"move": "Select a body, then drag along the axis.",
             "rotate": "Select a body, then drag the angle.",
             "extrude": "Select a planar face, then drag it out.",
             "fillet": "Select an edge, then drag the radius."}

COLOR_FUZZY = (217, 47, 28)
COLOR_RESOLVED = (46, 160, 67)
SKETCH_AMP_FRAC = 0.012
EDGE_SAMPLES = 6
PREVIEW_ID = -1

# Persistent store.
_marks = []
_geom = {}
_next_id = 1

# Transient command state (only one fuzzy command runs at a time).
_active_tool = None
_sel_input = None
_val_input = None
_axis_input = None
_pending = None  # dict: geom + anchor + size + normal/axis for the live preview


# --- CustomGraphics groups -------------------------------------------------
def _group(gid):
    design = _app.activeProduct
    if not isinstance(design, adsk.fusion.Design):
        return None
    root = design.rootComponent
    for i in range(root.customGraphicsGroups.count):
        g = root.customGraphicsGroups.item(i)
        if g.id == gid:
            return g
    g = root.customGraphicsGroups.add()
    g.id = gid
    return g


def _clear(gid):
    design = _app.activeProduct
    if not isinstance(design, adsk.fusion.Design):
        return
    root = design.rootComponent
    for i in range(root.customGraphicsGroups.count):
        g = root.customGraphicsGroups.item(i)
        if g.id == gid:
            g.deleteMe()
            return


def _solid(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(
        adsk.core.Color.create(r, g, b, 255))


# --- sketchy line drawing --------------------------------------------------
def _sketchy(group, pts, rgb, amp, seed, weight=2, strokes=2):
    if len(pts) < 2:
        return
    if amp <= 0:
        strokes = 1
    color = _solid(rgb)
    for s in range(strokes):
        rng = random.Random(seed * 131 + s * 977)
        flat = []
        for i, (x, y, z) in enumerate(pts):
            k = 0.0 if (i == 0 or i == len(pts) - 1) else amp
            flat.extend([x + (rng.random() - 0.5) * 2 * k,
                         y + (rng.random() - 0.5) * 2 * k,
                         z + (rng.random() - 0.5) * 2 * k])
        coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(len(pts))), True)
        line.color = color
        line.weight = weight


# --- sampling --------------------------------------------------------------
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


def _sample_edges(collection):
    loops = []
    for i in range(collection.count):
        p = _sample_edge(collection.item(i))
        if len(p) >= 2:
            loops.append(p)
    return loops


def _face_normal(face):
    try:
        nrm = getattr(face.geometry, "normal", None)
        if nrm is not None:
            v = nrm.copy(); v.normalize()
            return (v.x, v.y, v.z)
    except Exception:
        pass
    try:
        ev = face.evaluator
        ok, pr = ev.parametricRange()
        u = (pr.minPoint.x + pr.maxPoint.x) / 2
        w = (pr.minPoint.y + pr.maxPoint.y) / 2
        ok, nrm = ev.getNormalAtParameter(adsk.core.Point2D.create(u, w))
        nrm.normalize()
        return (nrm.x, nrm.y, nrm.z)
    except Exception:
        return (0.0, 0.0, 1.0)


def _fillet_stations(edge, n=5):
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
            d1 = n1.crossProduct(tan); d1.normalize()
            d2 = tan.crossProduct(n2); d2.normalize()
            if d1.dotProduct(n2) > 0:
                d1.scaleBy(-1)
            if d2.dotProduct(n1) > 0:
                d2.scaleBy(-1)
            out.append(((P.x, P.y, P.z), (d1.x, d1.y, d1.z), (d2.x, d2.y, d2.z)))
    except Exception:
        return []
    return out


def _bbox_center_size(ent):
    bbox = ent.boundingBox
    mn, mx = bbox.minPoint, bbox.maxPoint
    center = [(mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2]
    size = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z, 1.0)
    return center, size


# --- transforms ------------------------------------------------------------
def _axis_unit(axis):
    return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0)}.get(axis, (0.0, 0.0, 1.0))


def _plane_basis(axis):
    if axis == "X":
        return (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    if axis == "Y":
        return (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)


def _translate(pts, d, amount):
    return [(x + d[0] * amount, y + d[1] * amount, z + d[2] * amount) for (x, y, z) in pts]


def _rotate_pts(pts, anchor, axis, angle_deg):
    m = adsk.core.Matrix3D.create()
    ax, ay, az = _axis_unit(axis)
    m.setToRotation(math.radians(angle_deg),
                    adsk.core.Vector3D.create(ax, ay, az),
                    adsk.core.Point3D.create(*anchor))
    out = []
    for (x, y, z) in pts:
        p = adsk.core.Point3D.create(x, y, z)
        p.transformBy(m)
        out.append((p.x, p.y, p.z))
    return out


# --- per-tool drawing (reads _geom[id]) ------------------------------------
def _draw_move(group, mark, rgb, amp):
    d = _axis_unit(mark["axis"]); amt = mark["amount"]
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _translate(loop, d, amt), rgb, amp, mark["id"] * 100 + i)
    a = mark["anchor"]
    tip = (a[0] + d[0] * amt, a[1] + d[1] * amt, a[2] + d[2] * amt)
    _sketchy(group, [tuple(a), tip], rgb, amp, mark["id"] * 7, weight=3)


def _draw_rotate(group, mark, rgb, amp):
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _rotate_pts(loop, mark["anchor"], mark["axis"], mark["amount"]),
                 rgb, amp, mark["id"] * 100 + i)
    a = mark["anchor"]; r = mark.get("size", 3.0) * 0.6
    (ux, uy, uz), (wx, wy, wz) = _plane_basis(mark["axis"])
    steps = 28; pts = []
    for i in range(steps + 1):
        t = math.radians(mark["amount"] * i / steps)
        c, s = math.cos(t), math.sin(t)
        pts.append((a[0] + r * (c * ux + s * wx),
                    a[1] + r * (c * uy + s * wy),
                    a[2] + r * (c * uz + s * wz)))
    _sketchy(group, pts, rgb, amp * 0.5, mark["id"] * 13, weight=2)


def _draw_extrude(group, mark, rgb, amp):
    g = _geom[mark["id"]]; d = g["normal"]; amt = mark["amount"]
    for i, loop in enumerate(g["loops"]):
        off = _translate(loop, d, amt)
        _sketchy(group, off, rgb, amp, mark["id"] * 100 + i)
        _sketchy(group, [loop[0], off[0]], rgb, amp, mark["id"] * 200 + i, weight=2)
        _sketchy(group, [loop[-1], off[-1]], rgb, amp, mark["id"] * 300 + i, weight=2)


def _draw_fillet(group, mark, rgb, amp):
    r = mark["amount"]
    for i, (P, t1, t2) in enumerate(_geom[mark["id"]]["stations"]):
        a = (P[0] + t1[0] * r, P[1] + t1[1] * r, P[2] + t1[2] * r)
        b = (P[0] + t2[0] * r, P[1] + t2[1] * r, P[2] + t2[2] * r)
        pts = []
        for k in range(9):
            u = k / 8.0; mu = 1 - u
            pts.append((mu * mu * a[0] + 2 * mu * u * P[0] + u * u * b[0],
                        mu * mu * a[1] + 2 * mu * u * P[1] + u * u * b[1],
                        mu * mu * a[2] + 2 * mu * u * P[2] + u * u * b[2]))
        _sketchy(group, pts, rgb, amp * 0.6, mark["id"] * 100 + i, weight=3)


_DRAW = {"move": _draw_move, "rotate": _draw_rotate,
         "extrude": _draw_extrude, "fillet": _draw_fillet}


def _draw_one(group, mark, rgb, amp):
    _DRAW[mark["tool"]](group, mark, rgb, amp)
    _draw_label(group, mark, rgb)


def _redraw_marks():
    _clear(GROUP_MARKS)
    group = _group(GROUP_MARKS)
    if group is None:
        return
    for mark in _marks:
        if mark["id"] not in _geom:
            continue
        resolved = mark.get("resolved")
        rgb = COLOR_RESOLVED if resolved else COLOR_FUZZY
        amp = 0.0 if resolved else mark.get("size", 3.0) * SKETCH_AMP_FRAC
        try:
            _draw_one(group, mark, rgb, amp)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD draw failed ({}):\n{}".format(
                    mark["tool"], traceback.format_exc()))
    _app.activeViewport.refresh()


def _unit(mark):
    return "°" if mark["tool"] == "rotate" else "mm"


def _amount_display(mark):
    return mark["amount"] if mark["tool"] == "rotate" else mark["amount"] * 10.0


def _draw_label(group, mark, rgb):
    a = mark["anchor"]; off = mark.get("size", 3.0) * 0.9
    tip = adsk.core.Point3D.create(a[0], a[1] + off, a[2])
    val = "{:g}{}".format(_amount_display(mark), _unit(mark))
    tag = mark["label"] or mark["tool"].capitalize()
    suffix = " ✓" if mark.get("resolved") else " ~"
    text = group.addText(u"{}  {}{}".format(tag, val, suffix), "Arial", 1.0,
                         _label_transform(tip))
    text.color = _solid(rgb)
    _apply_billboard(text, tip)


# --- camera-facing labels --------------------------------------------------
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


# --- build pending geometry from a selected entity -------------------------
def _build_pending(tool, ent):
    """Sample the selected entity into cached geometry for live preview."""
    if tool in ("move", "rotate"):
        body = ent if isinstance(ent, adsk.fusion.BRepBody) else \
            (ent.body if isinstance(ent, adsk.fusion.BRepFace) else None)
        if body is None:
            return None
        center, size = _bbox_center_size(body)
        return {"geom": {"edges": _sample_edges(body.edges)},
                "anchor": center, "size": size}
    if tool == "extrude":
        if not isinstance(ent, adsk.fusion.BRepFace):
            return None
        center, size = _bbox_center_size(ent)
        return {"geom": {"loops": _sample_edges(ent.edges), "normal": _face_normal(ent)},
                "anchor": center, "size": size, "normal": _face_normal(ent)}
    if tool == "fillet":
        if not isinstance(ent, adsk.fusion.BRepEdge):
            return None
        center, size = _bbox_center_size(ent)
        stations = _fillet_stations(ent)
        if not stations:
            return None
        return {"geom": {"stations": stations}, "anchor": center, "size": size,
                "stations": stations}
    return None


def _pending_axis():
    if _axis_input is not None and _axis_input.selectedItem is not None:
        return _axis_input.selectedItem.name
    return "Z"


def _pending_amount():
    """Read the current manipulator value into internal units (cm / deg)."""
    if _val_input is None:
        return 0.0
    if _active_tool == "rotate":
        return math.degrees(_val_input.value)  # radians -> deg
    return _val_input.value                     # already cm


def _preview_mark():
    if not _pending:
        return None
    mark = {"id": PREVIEW_ID, "tool": _active_tool, "label": "",
            "axis": _pending_axis(), "resolved": False,
            "anchor": _pending["anchor"], "size": _pending["size"],
            "amount": _pending_amount()}
    if _active_tool == "extrude":
        mark["normal"] = _pending["normal"]
    return mark


def _place_manipulator():
    """Point the drag manipulator at the selection along the right direction."""
    if not _pending or _val_input is None:
        return
    a = _pending["anchor"]
    origin = adsk.core.Point3D.create(*a)
    try:
        if _active_tool == "extrude":
            n = _pending["normal"]
            _val_input.setManipulator(origin, adsk.core.Vector3D.create(*n))
        elif _active_tool == "move":
            d = _axis_unit(_pending_axis())
            _val_input.setManipulator(origin, adsk.core.Vector3D.create(*d))
        elif _active_tool == "fillet":
            st = _pending["stations"][len(_pending["stations"]) // 2]
            P, t1, t2 = st
            origin = adsk.core.Point3D.create(*P)
            bx, by, bz = (t1[0] + t2[0]) / 2, (t1[1] + t2[1]) / 2, (t1[2] + t2[2]) / 2
            v = adsk.core.Vector3D.create(bx, by, bz)
            if v.length < 1e-6:
                v = adsk.core.Vector3D.create(*t1)
            v.normalize()
            _val_input.setManipulator(origin, v)
        elif _active_tool == "rotate":
            axis = _pending_axis()
            (ux, uy, uz), _ = _plane_basis(axis)
            ax, ay, az = _axis_unit(axis)
            _val_input.setManipulator(origin,
                                      adsk.core.Vector3D.create(ax, ay, az),
                                      adsk.core.Vector3D.create(ux, uy, uz))
        _val_input.isVisible = True
        _val_input.isEnabled = True
    except Exception:
        # If setManipulator's signature differs on this build, the value field
        # still works (type a number); just no drag arrow.
        try:
            _val_input.isVisible = True
            _val_input.isEnabled = True
        except Exception:
            pass


# --- command handlers ------------------------------------------------------
class FuzzyInputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        global _pending
        try:
            changed = args.input
            if changed == _sel_input:
                _pending = None
                if _sel_input.selectionCount > 0:
                    ent = _sel_input.selection(0).entity
                    _pending = _build_pending(_active_tool, ent)
                    if _pending:
                        _place_manipulator()
            elif _axis_input is not None and changed == _axis_input and _pending:
                _place_manipulator()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD inputChanged failed:\n{}".format(
                    traceback.format_exc()))


class FuzzyPreview(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _clear(GROUP_PREVIEW)
            mark = _preview_mark()
            if mark and abs(mark["amount"]) > 1e-9:
                _geom[PREVIEW_ID] = _pending["geom"]
                group = _group(GROUP_PREVIEW)
                amp = mark["size"] * SKETCH_AMP_FRAC
                _draw_one(group, mark, COLOR_FUZZY, amp)
                _app.activeViewport.refresh()
            args.isValidResult = True
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD preview failed:\n{}".format(
                    traceback.format_exc()))


class FuzzyExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _next_id
        try:
            _clear(GROUP_PREVIEW)
            _geom.pop(PREVIEW_ID, None)
            mark = _preview_mark()
            if not mark or abs(mark["amount"]) < 1e-9:
                return
            mid = _next_id
            _next_id += 1
            mark["id"] = mid
            _geom[mid] = _pending["geom"]
            _marks.append(mark)
            _redraw_marks()
            _focus_camera(mark["anchor"])
            palette = _ui.palettes.itemById(PALETTE_ID)
            if palette:
                _send_state(palette)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD execute failed:\n{}".format(
                    traceback.format_exc()))


class FuzzyDestroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _pending, _sel_input, _val_input, _axis_input, _active_tool
        try:
            _clear(GROUP_PREVIEW)
            _geom.pop(PREVIEW_ID, None)
            _app.activeViewport.refresh()
        except Exception:
            pass
        _pending = None
        _sel_input = _val_input = _axis_input = None
        _active_tool = None


class FuzzyCommandCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self, tool):
        super().__init__()
        self.tool = tool

    def notify(self, args):
        global _active_tool, _sel_input, _val_input, _axis_input, _pending
        try:
            _active_tool = self.tool
            _pending = None
            cmd = args.command
            cmd.isRepeatable = False
            cmd.okButtonText = "Add fuzzy " + self.tool
            inputs = cmd.commandInputs

            _sel_input = inputs.addSelectionInput("sel", "Geometry", TOOL_HINT[self.tool])
            _sel_input.addSelectionFilter(TOOL_FILTER[self.tool])
            _sel_input.setSelectionLimits(1, 1)

            _axis_input = None
            if self.tool in ("move", "rotate"):
                _axis_input = inputs.addDropDownCommandInput(
                    "axis", "Axis", adsk.core.DropDownStyles.TextListDropDownStyle)
                for a in ("X", "Y", "Z"):
                    _axis_input.listItems.add(a, a == "Z")

            if self.tool == "rotate":
                _val_input = inputs.addAngleValueCommandInput(
                    "val", "Angle", adsk.core.ValueInput.createByReal(0.0))
            else:
                _val_input = inputs.addDistanceValueCommandInput(
                    "val", "Amount", adsk.core.ValueInput.createByReal(0.0))
            _val_input.isVisible = False
            _val_input.isEnabled = False

            on_changed = FuzzyInputChanged()
            cmd.inputChanged.add(on_changed); _handlers.append(on_changed)
            on_preview = FuzzyPreview()
            cmd.executePreview.add(on_preview); _handlers.append(on_preview)
            on_execute = FuzzyExecute()
            cmd.execute.add(on_execute); _handlers.append(on_execute)
            on_destroy = FuzzyDestroy()
            cmd.destroy.add(on_destroy); _handlers.append(on_destroy)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD command setup failed:\n{}".format(
                    traceback.format_exc()))


# --- palette messaging -----------------------------------------------------
def _public(mark):
    tool = mark["tool"]; size = mark.get("size", 3.0)
    if tool == "rotate":
        amin, amax, step, unit, val = -90.0, 90.0, 1.0, "°", mark["amount"]
    else:
        val = mark["amount"] * 10.0
        amax = size * 10.0 * (0.6 if tool != "fillet" else 0.25)
        amin, step, unit = 0.0, max(round((size * 10.0) / 40.0, 1), 0.1), "mm"
    return {"id": mark["id"], "tool": tool, "label": mark["label"], "axis": mark["axis"],
            "value": round(val, 2), "min": round(amin, 2), "max": round(amax, 2),
            "step": step, "unit": unit, "resolved": bool(mark["resolved"])}


def _apply_value(mark, display_value):
    mark["amount"] = float(display_value) if mark["tool"] == "rotate" \
        else float(display_value) / 10.0


def _find(mid):
    return next((m for m in _marks if m["id"] == mid), None)


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
            elif action == "tool":
                cmd_def = _ui.commandDefinitions.itemById(TOOL_CMD.get(data.get("tool")))
                if cmd_def:
                    cmd_def.execute()
            elif action == "adjust":
                mark = _find(data.get("id"))
                if mark:
                    _apply_value(mark, data["value"])
                    _redraw_marks()
            elif action == "resolve":
                mark = _find(data.get("id"))
                if mark:
                    mark["resolved"] = True; _redraw_marks(); _send_state(palette)
            elif action == "reopen":
                mark = _find(data.get("id"))
                if mark:
                    mark["resolved"] = False; _redraw_marks(); _send_state(palette)
            elif action == "focus":
                mark = _find(data.get("id"))
                if mark:
                    _focus_camera(mark["anchor"])
            elif action == "delete":
                mid = data.get("id")
                _marks[:] = [m for m in _marks if m["id"] != mid]
                _geom.pop(mid, None); _redraw_marks(); _send_state(palette)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD panel message failed:\n{}".format(
                    traceback.format_exc()))


class ShowPaletteCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            palette = _ui.palettes.itemById(PALETTE_ID)
            if palette is None:
                palette = _ui.palettes.add(
                    PALETTE_ID, PALETTE_NAME, PALETTE_URL, True, True, True, 320, 520)
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


# --- lifecycle -------------------------------------------------------------
def _add_button(panel, cmd_id, name, tooltip, created_handler):
    cmd_def = _ui.commandDefinitions.itemById(cmd_id)
    if cmd_def is None:
        cmd_def = _ui.commandDefinitions.addButtonDefinition(cmd_id, name, tooltip, "")
    cmd_def.commandCreated.add(created_handler)
    _handlers.append(created_handler)
    if panel and panel.controls.itemById(cmd_id) is None:
        panel.controls.addCommand(cmd_def)
    return cmd_def


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        panel = _ui.allToolbarPanels.itemById(PANEL_ID)

        # the panel launcher
        _add_button(panel, PANEL_CMD_ID, "FuzzyCAD", "Open the FuzzyCAD panel",
                    ShowPaletteCreated())
        # the fuzzy tools
        for tool in TOOLS:
            _add_button(panel, TOOL_CMD[tool], TOOL_LABEL[tool], TOOL_HINT[tool],
                        FuzzyCommandCreated(tool))

        _ui.commandDefinitions.itemById(PANEL_CMD_ID).execute()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    try:
        _clear(GROUP_MARKS)
        _clear(GROUP_PREVIEW)
        if _app and _app.activeViewport:
            _app.activeViewport.refresh()
        palette = _ui.palettes.itemById(PALETTE_ID)
        if palette:
            palette.deleteMe()
        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        for cmd_id in [PANEL_CMD_ID] + list(TOOL_CMD.values()):
            if panel:
                ctrl = panel.controls.itemById(cmd_id)
                if ctrl:
                    ctrl.deleteMe()
            cmd_def = _ui.commandDefinitions.itemById(cmd_id)
            if cmd_def:
                cmd_def.deleteMe()
        _handlers.clear()
        _geom.clear()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to stop:\n{}".format(traceback.format_exc()))
