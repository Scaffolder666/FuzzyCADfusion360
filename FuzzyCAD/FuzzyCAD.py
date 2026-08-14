"""
FuzzyCAD — Fusion 360 add-in trial.

Direct-manipulation fuzzy modeling (SketchUp / Kyub / Tinkercad style):

  * A separate bottom toolbar holds the tools (Move / Rotate / Extrude / Fillet).
  * Pick a tool, select geometry, and DRAG right on the model. The original body
    fades translucent and a soft, hand-drawn "sketchy" ghost shows the proposed
    operation. The sketchiness IS the uncertainty ("proposed, not final").
  * Move / Rotate expose draggable axis arrows in the viewport (no axis dropdown).
  * The right panel is the async-collaboration sidebar (Overleaf-style): a list of
    open questions. Click a card to focus its geometry. Click Accept and the real
    geometry actually changes (a real move / rotate / extrude / fillet feature);
    the sketchy proposal is consumed. Delete discards it.

Marks are CustomGraphics overlay while open; Accept turns them into real modeling
features. _marks + _geom + _entity are the fuzziness data structure (roadmap:
serialize to the document's Attributes so it saves with the .f3d).
"""

import math
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
TOOLBAR_ID = "FuzzyCAD_Toolbar"
TOOLBAR_URL = "palette/toolbar.html"
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
TOOL_HINT = {"move": "Select a body, then drag an axis arrow.",
             "rotate": "Select a body, then drag an axis arrow.",
             "extrude": "Select a planar face, then drag it out.",
             "fillet": "Select an edge, then drag the radius."}

COLOR_FUZZY = (96, 120, 168)     # soft blue — needs input / proposed
COLOR_ANSWERED = (70, 154, 104)  # green — answered / decided
COLOR_WARN = (200, 44, 32)       # red — the NEEDS INPUT badge
GHOST_OPACITY = 0.30
SKETCH_AMP_FRAC = 0.010
EDGE_SAMPLES = 10

# Persistent store — the fuzziness data structure.
_marks = []
_geom = {}     # id -> sampled base geometry
_entity = {}   # id -> live entity to operate on when accepted
_body = {}     # id -> body to ghost while open
_next_id = 1
_tool_count = {}  # tool -> running count, for card titles ("Move 1")

# Transient command state.
_active_tool = None
_inputs = None
_sel_input = None
_pending = None
_live_id = None

# Bodies currently faded, so we can restore them: token -> body
_ghosted = {}


# --- CustomGraphics groups -------------------------------------------------
def _design():
    d = _app.activeProduct
    return d if isinstance(d, adsk.fusion.Design) else None


def _group(gid):
    design = _design()
    if design is None:
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
    design = _design()
    if design is None:
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
    n = len(pts)
    if n < 2:
        return
    if amp <= 0:
        strokes = 1
    color = _solid(rgb)
    import random
    for s in range(strokes):
        rng = random.Random(seed * 131 + s * 977)
        waves = []
        for _ax in range(3):
            waves.append((1.0 + rng.random() * 1.4, 2.2 + rng.random() * 2.0,
                          rng.random() * 6.2832, rng.random() * 6.2832))
        flat = []
        for i, (x, y, z) in enumerate(pts):
            u = i / (n - 1)
            taper = math.sin(math.pi * u)
            base = [x, y, z]
            for ax in range(3):
                f1, f2, p1, p2 = waves[ax]
                dev = amp * taper * (0.6 * math.sin(u * 6.2832 * f1 + p1)
                                     + 0.4 * math.sin(u * 6.2832 * f2 + p2))
                base[ax] += dev
            flat.extend(base)
        coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(n)), True)
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


# --- vectors / transforms --------------------------------------------------
def _axis_unit(axis):
    return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0)}[axis]


def _plane_basis(axis):
    if axis == "X":
        return (0.0, 1.0, 0.0)
    if axis == "Y":
        return (0.0, 0.0, 1.0)
    return (1.0, 0.0, 0.0)


def _op_matrix(mark):
    """The rigid transform for a move/rotate mark (also used to commit it)."""
    m = adsk.core.Matrix3D.create()
    if mark["tool"] == "move":
        v = mark["vec"]
        m.translation = adsk.core.Vector3D.create(v[0], v[1], v[2])
    elif mark["tool"] == "rotate":
        anchor = adsk.core.Point3D.create(*mark["anchor"])
        for axis, ang in zip(("X", "Y", "Z"), mark["rot"]):
            if abs(ang) < 1e-9:
                continue
            r = adsk.core.Matrix3D.create()
            ax, ay, az = _axis_unit(axis)
            r.setToRotation(math.radians(ang),
                            adsk.core.Vector3D.create(ax, ay, az), anchor)
            m.transformBy(r)
    return m


def _apply_matrix(pts, m):
    out = []
    for (x, y, z) in pts:
        p = adsk.core.Point3D.create(x, y, z)
        p.transformBy(m)
        out.append((p.x, p.y, p.z))
    return out


def _translate(pts, d, amount):
    return [(x + d[0] * amount, y + d[1] * amount, z + d[2] * amount) for (x, y, z) in pts]


# --- per-tool drawing ------------------------------------------------------
def _dominant_axis(rot):
    idx = max(range(3), key=lambda i: abs(rot[i]))
    return "XYZ"[idx] if abs(rot[idx]) > 1e-9 else "Z"


def _draw_ring(group, anchor, axis, r, rgb, seed, weight=3):
    """A ring in the plane perpendicular to `axis` — the rotate manipulator cue."""
    ax = _axis_unit(axis)
    u = _plane_basis(axis)
    w = (ax[1] * u[2] - ax[2] * u[1], ax[2] * u[0] - ax[0] * u[2], ax[0] * u[1] - ax[1] * u[0])
    steps = 44
    pts = []
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append((anchor[0] + r * (c * u[0] + s * w[0]),
                    anchor[1] + r * (c * u[1] + s * w[1]),
                    anchor[2] + r * (c * u[2] + s * w[2])))
    _sketchy(group, pts, rgb, r * 0.02, seed * 77, weight=weight, strokes=1)


AXIS_COLOR = {"X": (210, 60, 50), "Y": (70, 160, 90), "Z": (70, 110, 190)}
AXIS_SEED = {"X": 11, "Y": 22, "Z": 33}


def _draw_rotate_guides(group, anchor, size, active):
    """Faint grabbable axis rings (X red, Y green, Z blue) shown on the body so
    you can pick which one to turn about. The active axis is skipped here — the
    ghost draws it as a bold red ring instead."""
    r = size * 0.62
    for axis in ("X", "Y", "Z"):
        if axis == active:
            continue
        _draw_ring(group, anchor, axis, r, AXIS_COLOR[axis], AXIS_SEED[axis], weight=1)


def _draw_move(group, mark, rgb, amp):
    # Keep the moving body light (a single faint outline stroke) so the whole
    # part doesn't read as heavy scribble — the arrow carries the "move" meaning.
    v = mark["vec"]
    m = _op_matrix(mark)
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _apply_matrix(loop, m), rgb, amp * 0.8,
                 mark["id"] * 100 + i, weight=1, strokes=1)
    a = mark["anchor"]
    _sketchy(group, [tuple(a), (a[0] + v[0], a[1] + v[1], a[2] + v[2])],
             rgb, amp, mark["id"] * 7, weight=3)


def _draw_rotate(group, mark, rgb, amp):
    m = _op_matrix(mark)
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _apply_matrix(loop, m), rgb, amp * 0.8,
                 mark["id"] * 100 + i, weight=1, strokes=1)
    # a distinct red rotation ring (vs the move arrow) around the dominant axis
    _draw_ring(group, mark["anchor"], _dominant_axis(mark["rot"]),
               mark.get("size", 3.0) * 0.62, COLOR_WARN, mark["id"])


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


def _style(mark):
    if mark.get("status") == "answered":
        return COLOR_ANSWERED, mark.get("size", 3.0) * SKETCH_AMP_FRAC * 0.3
    return COLOR_FUZZY, mark.get("size", 3.0) * SKETCH_AMP_FRAC


def _draw_warning(group, mark):
    """A red, camera-facing NEEDS INPUT badge floating over the mark."""
    a = mark["anchor"]
    s = mark.get("size", 3.0)
    tip = adsk.core.Point3D.create(a[0], a[1] + s * 1.25, a[2])
    text = group.addText(u"▲ NEEDS INPUT", "Arial", s * 0.5, _label_transform(tip))
    text.color = _solid(COLOR_WARN)
    _apply_billboard(text, tip)


def _draw_one(group, mark):
    rgb, amp = _style(mark)
    _DRAW[mark["tool"]](group, mark, rgb, amp)
    _draw_label(group, mark, rgb)
    if mark.get("status", "needs_input") == "needs_input":
        _draw_warning(group, mark)


def _summary(mark):
    t = mark["tool"]
    if t == "move":
        v = [round(x * 10, 1) for x in mark["vec"]]
        return "move ({}, {}, {}) mm".format(*v)
    if t == "rotate":
        parts = ["{}{:g}°".format(a, r) for a, r in zip("XYZ", mark["rot"]) if abs(r) > 1e-6]
        return "rotate " + (" ".join(parts) if parts else "0°")
    if t == "extrude":
        return "extrude {:g} mm".format(mark["amount"] * 10)
    return "fillet r {:g} mm".format(mark["amount"] * 10)


def _draw_label(group, mark, rgb):
    a = mark["anchor"]; off = mark.get("size", 3.0) * 0.9
    tip = adsk.core.Point3D.create(a[0], a[1] + off, a[2])
    tag = "{} {}".format(mark["tool"].capitalize(), mark.get("num", 1))
    if mark["label"]:
        tag = mark["label"]
    text = group.addText(tag, "Arial", 1.0, _label_transform(tip))
    text.color = _solid(rgb)
    _apply_billboard(text, tip)


def _redraw_marks():
    _clear(GROUP_MARKS)
    group = _group(GROUP_MARKS)
    if group is not None:
        for mark in _marks:
            if mark["id"] not in _geom:
                continue
            try:
                _draw_one(group, mark)
            except Exception:
                if _ui:
                    _ui.messageBox("FuzzyCAD draw failed ({}):\n{}".format(
                        mark["tool"], traceback.format_exc()))
    _refresh_ghost()
    _app.activeViewport.refresh()


# --- ghost the original bodies that have open marks ------------------------
def _refresh_ghost():
    global _ghosted
    want = {}
    for m in _marks:
        b = _body.get(m["id"])
        if b:
            try:
                want[b.entityToken] = b
            except Exception:
                pass
    for tok, b in want.items():
        try:
            b.opacity = GHOST_OPACITY
        except Exception:
            pass
    for tok, b in list(_ghosted.items()):
        if tok not in want:
            try:
                b.opacity = 1.0
            except Exception:
                pass
    _ghosted = want


def _restore_all_bodies():
    for tok, b in list(_ghosted.items()):
        try:
            b.opacity = 1.0
        except Exception:
            pass
    _ghosted.clear()


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


# --- pending selection -----------------------------------------------------
def _entity_body(ent):
    if isinstance(ent, adsk.fusion.BRepBody):
        return ent
    if isinstance(ent, adsk.fusion.BRepFace):
        return ent.body
    if isinstance(ent, adsk.fusion.BRepEdge):
        return ent.body
    return None


def _build_pending(tool, ent):
    body = _entity_body(ent)
    if tool in ("move", "rotate"):
        if not isinstance(ent, adsk.fusion.BRepBody):
            return None
        center, size = _bbox_center_size(ent)
        return {"geom": {"edges": _sample_edges(ent.edges)},
                "anchor": center, "size": size, "entity": ent, "body": body}
    if tool == "extrude":
        if not isinstance(ent, adsk.fusion.BRepFace):
            return None
        center, size = _bbox_center_size(ent)
        nrm = _face_normal(ent)
        return {"geom": {"loops": _sample_edges(ent.edges), "normal": nrm},
                "anchor": center, "size": size, "normal": nrm,
                "entity": ent, "body": body}
    if tool == "fillet":
        if not isinstance(ent, adsk.fusion.BRepEdge):
            return None
        center, size = _bbox_center_size(ent)
        stations = _fillet_stations(ent)
        if not stations:
            return None
        return {"geom": {"stations": stations}, "anchor": center, "size": size,
                "stations": stations, "entity": ent, "body": body}
    return None


# --- reading the manipulators ----------------------------------------------
def _val(cid):
    try:
        it = _inputs.itemById(cid)
        return it.value if it else 0.0
    except Exception:
        return 0.0


def _current_op():
    if not _pending or _inputs is None:
        return None
    t = _active_tool
    if t == "move":
        vec = [_val("mX"), _val("mY"), _val("mZ")]
        return {"vec": vec} if any(abs(v) > 1e-9 for v in vec) else None
    if t == "rotate":
        rot = [math.degrees(_val("rX")), math.degrees(_val("rY")), math.degrees(_val("rZ"))]
        return {"rot": rot} if any(abs(v) > 1e-9 for v in rot) else None
    a = _val("d")
    return {"amount": a} if abs(a) > 1e-9 else None


def _make_mark(op):
    global _tool_count
    num = _tool_count.get(_active_tool, 0) + 1
    _tool_count[_active_tool] = num
    mark = {"tool": _active_tool, "label": "", "anchor": _pending["anchor"],
            "size": _pending["size"], "num": num,
            "status": "needs_input", "comments": []}
    mark.update(op)
    return mark


def _sync_live_mark(op):
    global _live_id, _next_id
    if op is None:
        return None
    if _live_id is None:
        mid = _next_id
        _next_id += 1
        mark = _make_mark(op)
        mark["id"] = mid
        _geom[mid] = _pending["geom"]
        _entity[mid] = _pending["entity"]
        _body[mid] = _pending["body"]
        _marks.append(mark)
        _live_id = mid
        _refresh_ghost()
        _send_state()
    else:
        _find(_live_id).update(op)
    return _find(_live_id)


# --- manipulators ----------------------------------------------------------
def _place_manipulator():
    if not _pending or _inputs is None:
        return
    origin = adsk.core.Point3D.create(*_pending["anchor"])
    try:
        if _active_tool == "move":
            for axis in ("X", "Y", "Z"):
                it = _inputs.itemById("m" + axis)
                it.setManipulator(origin, adsk.core.Vector3D.create(*_axis_unit(axis)))
                it.isVisible = True; it.isEnabled = True
        elif _active_tool == "rotate":
            for axis in ("X", "Y", "Z"):
                it = _inputs.itemById("r" + axis)
                ax = adsk.core.Vector3D.create(*_axis_unit(axis))
                ref = adsk.core.Vector3D.create(*_plane_basis(axis))
                try:
                    it.setManipulator(origin, ax, ref)
                except Exception:
                    pass
                it.isVisible = True; it.isEnabled = True
        elif _active_tool == "extrude":
            it = _inputs.itemById("d")
            it.setManipulator(origin, adsk.core.Vector3D.create(*_pending["normal"]))
            it.isVisible = True; it.isEnabled = True
        elif _active_tool == "fillet":
            st = _pending["stations"][len(_pending["stations"]) // 2]
            P, t1, t2 = st
            v = adsk.core.Vector3D.create((t1[0] + t2[0]) / 2, (t1[1] + t2[1]) / 2,
                                          (t1[2] + t2[2]) / 2)
            if v.length < 1e-6:
                v = adsk.core.Vector3D.create(*t1)
            v.normalize()
            it = _inputs.itemById("d")
            it.setManipulator(adsk.core.Point3D.create(*P), v)
            it.isVisible = True; it.isEnabled = True
    except Exception:
        # setManipulator signature differences: fields still typeable.
        for cid in ("mX", "mY", "mZ", "rX", "rY", "rZ", "d"):
            it = _inputs.itemById(cid)
            if it:
                it.isVisible = True; it.isEnabled = True


# --- command handlers ------------------------------------------------------
class FuzzyInputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        global _pending, _inputs, _sel_input
        try:
            if _active_tool is None:
                return
            # Refresh from the event itself instead of trusting module globals
            # (they can go stale across Stop/Run or duplicate handlers).
            _inputs = args.inputs
            changed = args.input
            if changed.id != "sel":
                return
            sel = adsk.core.SelectionCommandInput.cast(changed)
            if sel is None:
                return
            _sel_input = sel
            _pending = None
            if sel.selectionCount > 0:
                _pending = _build_pending(_active_tool, sel.selection(0).entity)
                if _pending:
                    _place_manipulator()
                    if _active_tool == "rotate":
                        # show the axis rings the moment a body is picked
                        _clear(GROUP_PREVIEW)
                        _draw_rotate_guides(_group(GROUP_PREVIEW),
                                            _pending["anchor"], _pending["size"], None)
                        _app.activeViewport.refresh()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD inputChanged failed:\n{}".format(
                    traceback.format_exc()))


class FuzzyPreview(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _clear(GROUP_PREVIEW)
            group = _group(GROUP_PREVIEW)
            op = _current_op()
            if _active_tool == "rotate" and _pending:
                active = _dominant_axis(op["rot"]) if op else None
                _draw_rotate_guides(group, _pending["anchor"], _pending["size"], active)
            mark = _sync_live_mark(op)
            if mark is not None:
                _draw_one(group, mark)
            _app.activeViewport.refresh()
            args.isValidResult = True
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD preview failed:\n{}".format(traceback.format_exc()))


class FuzzyExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _sync_live_mark(_current_op())
            _clear(GROUP_PREVIEW)
            _redraw_marks()
            if _live_id is not None:
                _focus_camera(_find(_live_id)["anchor"])
            _send_state()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD execute failed:\n{}".format(traceback.format_exc()))


class FuzzyDestroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _pending, _inputs, _sel_input, _active_tool, _live_id
        try:
            _clear(GROUP_PREVIEW)
            if _live_id is not None and _find(_live_id) is None:
                pass
            _redraw_marks()
            _send_state()
        except Exception:
            pass
        _pending = None
        _inputs = _sel_input = None
        _active_tool = None
        _live_id = None


class FuzzyCommandCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self, tool):
        super().__init__()
        self.tool = tool

    def notify(self, args):
        global _active_tool, _inputs, _sel_input, _pending, _live_id
        try:
            _active_tool = self.tool
            _pending = None
            _live_id = None
            cmd = args.command
            cmd.isRepeatable = False
            cmd.okButtonText = "Add to panel"
            _inputs = cmd.commandInputs

            _sel_input = _inputs.addSelectionInput("sel", "Geometry", TOOL_HINT[self.tool])
            _sel_input.addSelectionFilter(TOOL_FILTER[self.tool])
            _sel_input.setSelectionLimits(1, 1)

            if self.tool == "move":
                for axis in ("X", "Y", "Z"):
                    it = _inputs.addDistanceValueCommandInput(
                        "m" + axis, "Move " + axis, adsk.core.ValueInput.createByReal(0.0))
                    it.isVisible = False; it.isEnabled = False
            elif self.tool == "rotate":
                for axis in ("X", "Y", "Z"):
                    it = _inputs.addAngleValueCommandInput(
                        "r" + axis, "Rotate " + axis, adsk.core.ValueInput.createByReal(0.0))
                    it.isVisible = False; it.isEnabled = False
            else:
                nm = "Depth" if self.tool == "extrude" else "Radius"
                it = _inputs.addDistanceValueCommandInput(
                    "d", nm, adsk.core.ValueInput.createByReal(0.0))
                it.isVisible = False; it.isEnabled = False

            for handler, event in ((FuzzyInputChanged(), cmd.inputChanged),
                                   (FuzzyPreview(), cmd.executePreview),
                                   (FuzzyExecute(), cmd.execute),
                                   (FuzzyDestroy(), cmd.destroy)):
                event.add(handler)
                _handlers.append(handler)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD command setup failed:\n{}".format(
                    traceback.format_exc()))


# --- accept: turn a fuzzy mark into real geometry --------------------------
def _accept(mark):
    design = _design()
    if design is None:
        return False
    ent = _entity.get(mark["id"])
    if ent is None:
        _ui.messageBox("Lost the reference to that geometry; can't apply it.")
        return False
    tool = mark["tool"]
    body = _body.get(mark["id"])
    if body:
        try:
            body.opacity = 1.0
        except Exception:
            pass
    try:
        if tool in ("move", "rotate"):
            comp = body.parentComponent
            coll = adsk.core.ObjectCollection.create()
            coll.add(body)
            comp.features.moveFeatures.add(
                comp.features.moveFeatures.createInput(coll, _op_matrix(mark)))
        elif tool == "extrude":
            comp = ent.body.parentComponent
            ext = comp.features.extrudeFeatures
            ei = ext.createInput(ent, adsk.fusion.FeatureOperations.JoinFeatureOperation)
            ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(mark["amount"]))
            ext.add(ei)
        elif tool == "fillet":
            comp = ent.body.parentComponent
            fil = comp.features.filletFeatures
            coll = adsk.core.ObjectCollection.create()
            coll.add(ent)
            fi = fil.createInput()
            fi.addConstantRadiusEdgeSet(coll, adsk.core.ValueInput.createByReal(mark["amount"]), True)
            fil.add(fi)
        return True
    except Exception:
        _ui.messageBox("FuzzyCAD couldn't apply the {} to real geometry:\n{}".format(
            tool, traceback.format_exc()))
        return False


def _remove_mark(mid):
    _marks[:] = [m for m in _marks if m["id"] != mid]
    _geom.pop(mid, None)
    _entity.pop(mid, None)
    _body.pop(mid, None)


# --- palette messaging -----------------------------------------------------
def _fields(mark):
    t = mark["tool"]
    if t == "move":
        return [{"key": "x", "label": "Move X", "value": round(mark["vec"][0] * 10, 2), "unit": "mm"},
                {"key": "y", "label": "Move Y", "value": round(mark["vec"][1] * 10, 2), "unit": "mm"},
                {"key": "z", "label": "Move Z", "value": round(mark["vec"][2] * 10, 2), "unit": "mm"}]
    if t == "rotate":
        return [{"key": "x", "label": "Rotate X", "value": round(mark["rot"][0], 1), "unit": "°"},
                {"key": "y", "label": "Rotate Y", "value": round(mark["rot"][1], 1), "unit": "°"},
                {"key": "z", "label": "Rotate Z", "value": round(mark["rot"][2], 1), "unit": "°"}]
    if t == "extrude":
        return [{"key": "d", "label": "Depth", "value": round(mark["amount"] * 10, 2), "unit": "mm"}]
    return [{"key": "d", "label": "Radius", "value": round(mark["amount"] * 10, 2), "unit": "mm"}]


def _apply_edit(mark, key, value):
    try:
        v = float(value)
    except Exception:
        return
    t = mark["tool"]
    if t == "move":
        mark["vec"][{"x": 0, "y": 1, "z": 2}[key]] = v / 10.0
    elif t == "rotate":
        mark["rot"][{"x": 0, "y": 1, "z": 2}[key]] = v
    else:
        mark["amount"] = v / 10.0


def _public(mark):
    return {"id": mark["id"], "tool": mark["tool"], "num": mark.get("num", 1),
            "title": "{} {}".format(mark["tool"].capitalize(), mark.get("num", 1)),
            "label": mark["label"], "status": mark.get("status", "needs_input"),
            "summary": _summary(mark), "fields": _fields(mark),
            "comments": mark.get("comments", [])}


def _find(mid):
    return next((m for m in _marks if m["id"] == mid), None)


def _send_state():
    palette = _ui.palettes.itemById(PALETTE_ID)
    if not palette:
        return
    import json
    palette.sendInfoToHTML("state", json.dumps({"marks": [_public(m) for m in _marks]}))


class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            import json
            e = adsk.core.HTMLEventArgs.cast(args)
            action = e.action
            data = json.loads(e.data) if e.data else {}

            if action == "ready":
                _send_state()
            elif action == "tool":
                cd = _ui.commandDefinitions.itemById(TOOL_CMD.get(data.get("tool")))
                if cd:
                    cd.execute()
            elif action == "focus":
                m = _find(data.get("id"))
                if m:
                    _focus_camera(m["anchor"])
            elif action == "edit":
                m = _find(data.get("id"))
                if m:
                    _apply_edit(m, data.get("key"), data.get("value"))
                    _redraw_marks()
                    _send_state()
            elif action == "status":
                m = _find(data.get("id"))
                if m:
                    m["status"] = data.get("status", "needs_input")
                    _redraw_marks()
                    _send_state()
            elif action == "comment":
                m = _find(data.get("id"))
                if m:
                    txt = (data.get("text") or "").strip()
                    if txt:
                        m.setdefault("comments", []).append({"text": txt})
                        _send_state()
            elif action == "apply":
                m = _find(data.get("id"))
                if m and _accept(m):
                    _remove_mark(m["id"])
                    _redraw_marks()
                    _send_state()
            elif action == "reject":
                _remove_mark(data.get("id"))
                _redraw_marks()
                _send_state()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD panel message failed:\n{}".format(
                    traceback.format_exc()))


# --- palettes + lifecycle --------------------------------------------------
def _dock_state(name):
    docks = adsk.core.PaletteDockingStates
    return getattr(docks, name, None) or docks.PaletteDockStateFloating


def _ensure_palettes():
    palettes = _ui.palettes
    side = palettes.itemById(PALETTE_ID)
    if side is None:
        side = palettes.add(PALETTE_ID, PALETTE_NAME, PALETTE_URL, True, True, True, 300, 480)
        side.dockingState = _dock_state("PaletteDockStateRight")
        h = PaletteHTMLHandler(); side.incomingFromHTML.add(h); _handlers.append(h)
    else:
        side.isVisible = True
    bar = palettes.itemById(TOOLBAR_ID)
    if bar is None:
        bar = palettes.add(TOOLBAR_ID, "FuzzyCAD Tools", TOOLBAR_URL, True, True, True, 760, 96)
        bar.dockingState = _dock_state("PaletteDockStateBottom")
        h2 = PaletteHTMLHandler(); bar.incomingFromHTML.add(h2); _handlers.append(h2)
    else:
        bar.isVisible = True


class ShowPaletteCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _ensure_palettes()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD failed to open panel:\n{}".format(
                    traceback.format_exc()))


def _add_button(panel, cmd_id, name, tooltip, handler):
    # Delete any leftover definition/control from a prior Run so we never stack
    # duplicate commandCreated handlers (that was corrupting the command state).
    existing = _ui.commandDefinitions.itemById(cmd_id)
    if existing:
        existing.deleteMe()
    if panel:
        ctrl = panel.controls.itemById(cmd_id)
        if ctrl:
            ctrl.deleteMe()
    cd = _ui.commandDefinitions.addButtonDefinition(cmd_id, name, tooltip, "")
    cd.commandCreated.add(handler)
    _handlers.append(handler)
    if panel:
        panel.controls.addCommand(cd)
    return cd


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        _add_button(panel, PANEL_CMD_ID, "FuzzyCAD", "Open the FuzzyCAD panel",
                    ShowPaletteCreated())
        for tool in TOOLS:
            _add_button(panel, TOOL_CMD[tool], TOOL_LABEL[tool], TOOL_HINT[tool],
                        FuzzyCommandCreated(tool))
        _ensure_palettes()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    try:
        _restore_all_bodies()
        _clear(GROUP_MARKS)
        _clear(GROUP_PREVIEW)
        if _app and _app.activeViewport:
            _app.activeViewport.refresh()
        for pid in (PALETTE_ID, TOOLBAR_ID):
            p = _ui.palettes.itemById(pid)
            if p:
                p.deleteMe()
        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        for cmd_id in [PANEL_CMD_ID] + list(TOOL_CMD.values()):
            if panel:
                ctrl = panel.controls.itemById(cmd_id)
                if ctrl:
                    ctrl.deleteMe()
            cd = _ui.commandDefinitions.itemById(cmd_id)
            if cd:
                cd.deleteMe()
        _handlers.clear()
        _geom.clear(); _entity.clear(); _body.clear()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to stop:\n{}".format(traceback.format_exc()))
