"""
FuzzyCAD — Fusion 360 add-in trial.

Two audiences, two complexities (FuzzyCAD's boundary-object thesis):
  * The CAD engineer works in Fusion's full interface as normal.
  * A domain expert (therapist, medical staff, designer) touches only a
    **Tinkercad-like layer**: pick an object, grab a big handle, drag. No
    dialogs, no CAD jargon. Easy to onboard, but it really edits the geometry.

The expert layer:
  * **Transform** (the unified gizmo) covers the three Needs-Input ops —
    **Move / Rotate / Scale** — with move arrows, rotate rings, and a scale
    handle on the selected body. Grab any handle and drag; a soft sketchy ghost
    proposes the change. Whichever handle you use makes that kind of card.
  * **Extrude / Fillet** are kept as "advanced" (more CAD-specific) tools.

Every proposal is a CustomGraphics overlay + a card in the right panel (the
async-collaboration sidebar). A teammate fills in the value, comments, and
Accepts — Accept applies the real modeling feature. Nothing is committed until
then, so the .f3d carries the *proposed* change.
"""

import math
import os
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

# Commands and the categories each one produces.
COMMANDS = ("transform", "extrude", "fillet")
CMD_ID = {c: "FuzzyCAD_" + c.capitalize() for c in COMMANDS}
CMD_ID["note"] = "FuzzyCAD_Note"
CMD_LABEL = {"transform": "Transform", "extrude": "Fuzzy Extrude",
             "fillet": "Fuzzy Fillet", "note": "Note"}
CMD_FILTER = {"transform": "SolidBodies", "extrude": "Faces", "fillet": "Edges"}
CMD_HINT = {"transform": "Select a body, then grab a move / rotate / scale handle.",
            "extrude": "Select a planar face, then drag it out.",
            "fillet": "Select an edge, then drag the radius."}
CMD_CATS = {"transform": ("move", "rotate", "scale"),
            "extrude": ("extrude",), "fillet": ("fillet",)}

COLOR_FUZZY = (77, 77, 77)       # ~70% black pencil gray — the sketchy ghost
COLOR_ANSWERED = (70, 154, 104)
COLOR_WARN = (200, 44, 32)
AXIS_COLOR = {"X": (210, 60, 50), "Y": (70, 160, 90), "Z": (70, 110, 190)}
AXIS_SEED = {"X": 11, "Y": 22, "Z": 33}
try:
    _ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
except Exception:
    _ICON_DIR = None


def _icon_path(mtype):
    if not _ICON_DIR:
        return None
    p = os.path.join(_ICON_DIR, mtype + ".png")
    return p if os.path.exists(p) else None

# The three uncertainty mark types (paper's categories).
MTYPES = ("need_input", "constraint", "alternative")
MTYPE_LABEL = {"need_input": "Need Input", "constraint": "Constraint",
               "alternative": "Alternative"}
MTYPE_COLOR = {"need_input": (200, 44, 32), "constraint": (183, 121, 31),
               "alternative": (128, 90, 180)}
MTYPE_GLYPH = {"need_input": u"!", "constraint": u"‖", "alternative": u"⑂"}
GHOST_OPACITY = 0.16
SKETCH_AMP_FRAC = 0.008
EDGE_SAMPLES = 10

# Persistent store — the fuzziness data structure.
_marks = []
_geom = {}
_entity = {}
_body = {}
_next_id = 1
_tool_count = {}

# Transient command state.
_active_cmd = None
_inputs = None
_pending = None
_live = {}          # category -> live mark id for the current drag session
_switching = False  # a command switch is in flight (terminate old -> execute new)
_cmd_session = None  # the toolbar command session that currently owns the globals
_cmd_gen = [0]      # monotonic toolbar command generation
_ghosted = {}       # token -> body (faded originals to restore)
_easy_mode = True   # FuzzyCAD is always continuous; use native Fusion for non-easy
_note_inputs = None
_note_live_id = None
LAUNCH_EVENT_ID = "FuzzyCADLaunch"


def _body_locked(body):
    """A body with an unresolved (needs_input) mark is locked: it can't get a
    new fuzzy op until that one is answered/applied/rejected."""
    if body is None:
        return False
    try:
        tok = body.entityToken
    except Exception:
        return False
    for m in _marks:
        b = _body.get(m["id"])
        if b is None:
            continue
        if m.get("tool") == "note":
            continue  # notes are annotations; they don't lock the object
        try:
            if b.entityToken == tok and m.get("status", "open") == "open":
                return True
        except Exception:
            continue
    return False


# --- CustomGraphics ---------------------------------------------------------
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
    # Delete EVERY group with this id, not just the first — otherwise stale
    # duplicates linger and their graphics (badges, lines) stack up, so one
    # operation looks like several. Iterate backwards since deleteMe shifts indices.
    for i in range(root.customGraphicsGroups.count - 1, -1, -1):
        try:
            g = root.customGraphicsGroups.item(i)
            if g is not None and g.id == gid:
                g.deleteMe()
        except Exception:
            pass


def _solid(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(
        adsk.core.Color.create(r, g, b, 255))


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
        # one gentle low-frequency bow per axis (not multi-frequency noise), so
        # the stroke reads as a smooth hand-drawn curve, not a random zigzag.
        waves = []
        for _ax in range(3):
            waves.append((0.7 + rng.random() * 0.6, rng.random() * 6.2832))
        flat = []
        for i, (x, y, z) in enumerate(pts):
            u = i / (n - 1)
            taper = math.sin(math.pi * u)  # smooth, zero at both ends
            base = [x, y, z]
            for ax in range(3):
                f1, p1 = waves[ax]
                base[ax] += amp * taper * math.sin(u * 6.2832 * f1 + p1)
            flat.extend(base)
        coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(n)), True)
        line.color = color
        line.weight = weight


# --- sampling ---------------------------------------------------------------
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


# --- vectors ----------------------------------------------------------------
def _axis_unit(axis):
    return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}[axis]


def _plane_basis(axis):
    if axis == "X":
        return (0.0, 1.0, 0.0)
    if axis == "Y":
        return (0.0, 0.0, 1.0)
    return (1.0, 0.0, 0.0)


def _op_matrix(mark):
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


def _scale_pts(pts, a, f):
    return [(a[0] + (x - a[0]) * f, a[1] + (y - a[1]) * f, a[2] + (z - a[2]) * f)
            for (x, y, z) in pts]


def _dominant_axis(rot):
    idx = max(range(3), key=lambda i: abs(rot[i]))
    return "XYZ"[idx] if abs(rot[idx]) > 1e-9 else "Z"


def _draw_ring(group, anchor, axis, r, rgb, seed, weight=3):
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


def _draw_rotate_guides(group, anchor, size, active):
    r = size * 0.62
    for axis in ("X", "Y", "Z"):
        if axis == active:
            continue
        _draw_ring(group, anchor, axis, r, AXIS_COLOR[axis], AXIS_SEED[axis], weight=1)


# --- per-tool ghost drawing -------------------------------------------------
def _draw_move(group, mark, rgb, amp):
    v = mark["vec"]
    m = _op_matrix(mark)
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _apply_matrix(loop, m), rgb, amp * 0.8,
                 mark["id"] * 100 + i, weight=1, strokes=2)
    a = mark["anchor"]
    _sketchy(group, [tuple(a), (a[0] + v[0], a[1] + v[1], a[2] + v[2])],
             rgb, amp, mark["id"] * 7, weight=2)


def _draw_rotate(group, mark, rgb, amp):
    m = _op_matrix(mark)
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _apply_matrix(loop, m), rgb, amp * 0.8,
                 mark["id"] * 100 + i, weight=1, strokes=2)
    _draw_ring(group, mark["anchor"], _dominant_axis(mark["rot"]),
               mark.get("size", 3.0) * 0.62, COLOR_WARN, mark["id"])


def _draw_scale(group, mark, rgb, amp):
    f = mark["factor"]; a = mark["anchor"]
    for i, loop in enumerate(_geom[mark["id"]]["edges"]):
        _sketchy(group, _scale_pts(loop, a, f), rgb, amp * 0.8,
                 mark["id"] * 100 + i, weight=1, strokes=2)


def _draw_real(group, mark, rgb, amp):
    """Sketchy-fy the cached real-geometry edges. Returns True if it drew."""
    real = _geom[mark["id"]].get("real")
    if not real:
        return False
    for i, poly in enumerate(real):
        _sketchy(group, poly, rgb, amp, mark["id"] * 400 + i, weight=1, strokes=2)
    return True


def _draw_extrude(group, mark, rgb, amp):
    if _draw_real(group, mark, rgb, amp):
        return
    g = _geom[mark["id"]]; d = g["normal"]; amt = mark["amount"]
    for i, loop in enumerate(g["loops"]):
        off = _translate(loop, d, amt)
        _sketchy(group, off, rgb, amp, mark["id"] * 100 + i, weight=1, strokes=2)
        _sketchy(group, [loop[0], off[0]], rgb, amp, mark["id"] * 200 + i, weight=1)
        _sketchy(group, [loop[-1], off[-1]], rgb, amp, mark["id"] * 300 + i, weight=1)


def _draw_fillet(group, mark, rgb, amp):
    if _draw_real(group, mark, rgb, amp):
        return
    g = _geom[mark["id"]]
    r = mark["amount"]
    stations = g.get("stations") or []
    for i, (P, t1, t2) in enumerate(stations):
        a = (P[0] + t1[0] * r, P[1] + t1[1] * r, P[2] + t1[2] * r)
        b = (P[0] + t2[0] * r, P[1] + t2[1] * r, P[2] + t2[2] * r)
        pts = []
        for k in range(9):
            u = k / 8.0; mu = 1 - u
            pts.append((mu * mu * a[0] + 2 * mu * u * P[0] + u * u * b[0],
                        mu * mu * a[1] + 2 * mu * u * P[1] + u * u * b[1],
                        mu * mu * a[2] + 2 * mu * u * P[2] + u * u * b[2]))
        _sketchy(group, pts, rgb, amp * 0.6, mark["id"] * 100 + i, weight=2)
    if not stations:
        # no rolling-ball profile available (curved edge) — approximate: the edge
        # plus two offset curves whose spread grows with the radius, so the ghost
        # visibly changes as you drag/edit r.
        edge = g.get("edge") or []
        if len(edge) >= 2:
            n = len(edge)
            off1, off2 = [], []
            for i in range(n):
                a2 = edge[min(i + 1, n - 1)]
                b2 = edge[max(i - 1, 0)]
                px, py = (a2[1] - b2[1]), -(a2[0] - b2[0])   # perp to tangent, in XY
                pl = math.hypot(px, py)
                if pl < 1e-9:
                    px, py, pl = 1.0, 0.0, 1.0
                px, py = px / pl, py / pl
                off1.append((edge[i][0] + px * r, edge[i][1] + py * r, edge[i][2]))
                off2.append((edge[i][0] - px * r, edge[i][1] - py * r, edge[i][2]))
            _sketchy(group, edge, rgb, amp, mark["id"] * 99, weight=1, strokes=1)
            _sketchy(group, off1, rgb, amp, mark["id"] * 98, weight=2, strokes=2)
            _sketchy(group, off2, rgb, amp, mark["id"] * 97, weight=2, strokes=2)


def _draw_note(group, mark, rgb, amp):
    """A hand-drawn callout: a leader line from the picked point out to a
    floating text label (the note content)."""
    a = mark["anchor"]; s = mark.get("size", 3.0)
    (xx, xy, xz), (yx, yy, yz) = _camera_xy()
    off = s * 1.1
    dx, dy, dz = (xx + yx) * 0.7, (xy + yy) * 0.7, (xz + yz) * 0.7
    tp = (a[0] + dx * off, a[1] + dy * off, a[2] + dz * off)
    _sketchy(group, [tuple(a), tp], rgb, s * SKETCH_AMP_FRAC, mark["id"] * 3, weight=2)
    cp = adsk.core.Point3D.create(*tp)
    txt = mark.get("text") or "(note)"
    if len(txt) > 40:
        txt = txt[:38] + "…"
    t = group.addText(txt, "Arial", s * 0.4, _label_transform(cp))
    t.color = _solid(rgb)
    _apply_billboard(t, cp)


_DRAW = {"move": _draw_move, "rotate": _draw_rotate, "scale": _draw_scale,
         "extrude": _draw_extrude, "fillet": _draw_fillet, "note": _draw_note}


def _style(mark):
    # Absolute jitter (~0.4mm), like the Onshape version's 0.38mm variance — NOT
    # a fraction of the body size, which made big assemblies look wildly jittery.
    return COLOR_FUZZY, 0.04


def _camera_xy():
    """Right/up unit vectors of the current camera plane (for camera-facing art)."""
    try:
        cam = _app.activeViewport.camera
        eye, target = cam.eye, cam.target
        z = adsk.core.Vector3D.create(eye.x - target.x, eye.y - target.y, eye.z - target.z)
        z.normalize()
        up = cam.upVector.copy(); up.normalize()
        x = up.crossProduct(z); x.normalize()
        y = z.crossProduct(x); y.normalize()
        return (x.x, x.y, x.z), (y.x, y.y, y.z)
    except Exception:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)


def _draw_badge(group, mark):
    """The uncertainty badge floating over the ghost. Preferably a real PNG icon
    (screen-constant, crisp); falls back to a drawn triangle if the image can't
    be used. Notes show themselves via their callout, so they get no badge."""
    if mark.get("tool") == "note":
        return
    mtype = mark.get("mtype", "need_input")
    rgb = MTYPE_COLOR.get(mtype, COLOR_WARN)
    a = mark["anchor"]; s = mark.get("size", 3.0)
    (xx, xy, xz), (yx, yy, yz) = _camera_xy()
    bs = max(0.7, min(s * 0.14, 1.8))
    lift = max(1.0, min(s * 0.3, 3.0)) + bs
    center = (a[0] + yx * lift, a[1] + yy * lift, a[2] + yz * lift)

    # 1) real image icon — always faces the camera, constant screen size
    img = _icon_path(mtype)
    if img:
        try:
            coords = adsk.fusion.CustomGraphicsCoordinates.create(list(center))
            group.addPointSet(coords, [0],
                              adsk.fusion.CustomGraphicsPointTypes.UserDefinedCustomGraphicsPointType,
                              img)
            return
        except Exception:
            pass

    # 2) fallback: a crisp drawn triangle + glyph
    def P(ux, uy):
        return (center[0] + bs * (ux * xx + uy * yx),
                center[1] + bs * (ux * xy + uy * yy),
                center[2] + bs * (ux * xz + uy * yz))
    top, bl, br = P(0.0, 1.0), P(-0.92, -0.72), P(0.92, -0.72)
    _sketchy(group, [top, br, bl, top], rgb, 0.0, mark["id"] * 555, weight=5, strokes=1)
    cp = adsk.core.Point3D.create(*P(0.0, -0.12))
    text = group.addText(MTYPE_GLYPH.get(mtype, u"!"), "Arial", bs * 0.95, _label_transform(cp))
    text.color = _solid(rgb)
    _apply_billboard(text, cp)


def _draw_label(group, mark, rgb):
    a = mark["anchor"]; off = mark.get("size", 3.0) * 0.9
    tip = adsk.core.Point3D.create(a[0], a[1] + off, a[2])
    tag = mark["label"] or "{} {}".format(mark["tool"].capitalize(), mark.get("num", 1))
    text = group.addText(tag, "Arial", 1.0, _label_transform(tip))
    text.color = _solid(rgb)
    _apply_billboard(text, tip)


def _draw_one(group, mark):
    rgb, amp = _style(mark)
    _DRAW[mark["tool"]](group, mark, rgb, amp)
    _draw_label(group, mark, rgb)
    if mark.get("status", "open") == "open":
        _draw_badge(group, mark)


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


# --- ghost the original bodies ----------------------------------------------
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


# --- camera-facing labels ---------------------------------------------------
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


# --- selection -> pending ---------------------------------------------------
def _entity_body(ent):
    if isinstance(ent, adsk.fusion.BRepBody):
        return ent
    if isinstance(ent, (adsk.fusion.BRepFace, adsk.fusion.BRepEdge)):
        return ent.body
    return None


def _build_pending(cmd, ent):
    body = _entity_body(ent)
    if cmd == "transform":
        if not isinstance(ent, adsk.fusion.BRepBody):
            return None
        center, size = _bbox_center_size(ent)
        return {"geom": {"edges": _sample_edges(ent.edges)}, "anchor": center,
                "size": size, "entity": ent, "body": body,
                "scale_len": size * 0.5}
    if cmd == "extrude":
        if not isinstance(ent, adsk.fusion.BRepFace):
            return None
        center, size = _bbox_center_size(ent)
        nrm = _face_normal(ent)
        return {"geom": {"loops": _sample_edges(ent.edges), "normal": nrm},
                "anchor": center, "size": size, "normal": nrm,
                "entity": ent, "body": body}
    if cmd == "fillet":
        if not isinstance(ent, adsk.fusion.BRepEdge):
            return None
        center, size = _bbox_center_size(ent)
        stations = _fillet_stations(ent)   # may be [] on tricky edges
        edge = _sample_edge(ent)
        return {"geom": {"stations": stations, "edge": edge}, "anchor": center,
                "size": size, "stations": stations, "edge": edge,
                "entity": ent, "body": body}
    return None


# --- reading manipulators ---------------------------------------------------
def _val(cid):
    try:
        it = _inputs.itemById(cid)
        return it.value if it else 0.0
    except Exception:
        return 0.0


def _category_raw(cat):
    if cat == "move":
        return {"vec": [_val("mX"), _val("mY"), _val("mZ")]}
    if cat == "rotate":
        return {"rot": [math.degrees(_val("rX")), math.degrees(_val("rY")), math.degrees(_val("rZ"))]}
    if cat == "scale":
        length = _pending.get("scale_len", 1.0) if _pending else 1.0
        f = 1.0 + _val("sc") / (length if length > 1e-6 else 1.0)
        return {"factor": max(0.05, f)}
    return {"amount": _val("d")}  # extrude / fillet


def _is_default(cat, op):
    if cat == "move":
        return all(abs(v) < 1e-9 for v in op["vec"])
    if cat == "rotate":
        return all(abs(v) < 1e-9 for v in op["rot"])
    if cat == "scale":
        return abs(op["factor"] - 1.0) < 1e-3
    return abs(op["amount"]) < 1e-9


def _make_mark(tool, op):
    num = _tool_count.get(tool, 0) + 1
    _tool_count[tool] = num
    mark = {"tool": tool, "label": "", "anchor": _pending["anchor"],
            "size": _pending["size"], "num": num,
            "status": "open", "mtype": "need_input", "comments": []}
    mark.update(op)
    return mark


def _seed_single(cat, amount):
    """Create an extrude/fillet mark immediately with a default amount, so a
    ghost shows on select even if the drag manipulator is unresponsive on this
    build. The card's numeric field can then adjust it reliably."""
    global _next_id
    if _live.get(cat) is not None:
        _find(_live[cat])["amount"] = amount
        return
    mid = _next_id
    _next_id += 1
    mark = _make_mark(cat, {"amount": amount})
    mark["id"] = mid
    _geom[mid] = _pending["geom"]
    _entity[mid] = _pending["entity"]
    _body[mid] = _pending["body"]
    _marks.append(mark)
    _live[cat] = mid
    _send_state()


def _sync_category(cat):
    """Create-or-update the live mark for a category as its handle is dragged."""
    global _next_id
    op = _category_raw(cat)
    if _live.get(cat) is None:
        if _is_default(cat, op):
            return None
        mid = _next_id
        _next_id += 1
        mark = _make_mark(cat, op)
        mark["id"] = mid
        _geom[mid] = _pending["geom"]
        _entity[mid] = _pending["entity"]
        _body[mid] = _pending["body"]
        _marks.append(mark)
        _live[cat] = mid
        _send_state()
    else:
        _find(_live[cat]).update(op)
    return _find(_live[cat])


# --- manipulators -----------------------------------------------------------
def _show(cid):
    it = _inputs.itemById(cid)
    if it:
        it.isVisible = True
        it.isEnabled = True


def _place_manipulator():
    if not _pending or _inputs is None:
        return
    origin = adsk.core.Point3D.create(*_pending["anchor"])
    cats = CMD_CATS[_active_cmd]
    try:
        if "move" in cats:
            for axis in ("X", "Y", "Z"):
                it = _inputs.itemById("m" + axis)
                it.setManipulator(origin, adsk.core.Vector3D.create(*_axis_unit(axis)))
                _show("m" + axis)
        if "rotate" in cats:
            for axis in ("X", "Y", "Z"):
                it = _inputs.itemById("r" + axis)
                try:
                    it.setManipulator(origin, adsk.core.Vector3D.create(*_axis_unit(axis)),
                                      adsk.core.Vector3D.create(*_plane_basis(axis)))
                except Exception:
                    pass
                _show("r" + axis)
        if "scale" in cats:
            it = _inputs.itemById("sc")
            diag = adsk.core.Vector3D.create(0.5774, 0.5774, 0.5774)
            it.setManipulator(origin, diag)
            _show("sc")
        if "extrude" in cats:
            it = _inputs.itemById("d")
            it.setManipulator(origin, adsk.core.Vector3D.create(*_pending["normal"]))
            _show("d")
        if "fillet" in cats:
            stations = _pending.get("stations") or []
            if stations:
                P, t1, t2 = stations[len(stations) // 2]
                v = adsk.core.Vector3D.create((t1[0] + t2[0]) / 2, (t1[1] + t2[1]) / 2,
                                              (t1[2] + t2[2]) / 2)
                if v.length < 1e-6:
                    v = adsk.core.Vector3D.create(*t1)
                origin_f = adsk.core.Point3D.create(*P)
            else:
                edge = _pending.get("edge") or [_pending["anchor"]]
                origin_f = adsk.core.Point3D.create(*edge[len(edge) // 2])
                v = adsk.core.Vector3D.create(0, 0, 1)
            v.normalize()
            _inputs.itemById("d").setManipulator(origin_f, v)
            _show("d")
    except Exception:
        for cid in ("mX", "mY", "mZ", "rX", "rY", "rZ", "sc", "d"):
            _show(cid)


# --- command handlers -------------------------------------------------------
class FuzzyInputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        global _pending, _inputs
        try:
            if _active_cmd is None:
                return
            _inputs = args.inputs
            if args.input.id != "sel":
                return
            sel = adsk.core.SelectionCommandInput.cast(args.input)
            if sel is None:
                return
            _pending = None
            if sel.selectionCount > 0:
                _pending = _build_pending(_active_cmd, sel.selection(0).entity)
                if _pending is None:
                    _ui.messageBox("FuzzyCAD: can't use that selection for '{}'.\n{}".format(
                        _active_cmd, CMD_HINT[_active_cmd]))
                    try:
                        sel.clearSelection()
                    except Exception:
                        pass
                    return
                if _body_locked(_pending["body"]):
                    _ui.messageBox("This object already has an open question — "
                                   "resolve it in the panel first.")
                    _pending = None
                    try:
                        sel.clearSelection()
                    except Exception:
                        pass
                    return
                if _pending:
                    _place_manipulator()
                    if "rotate" in CMD_CATS[_active_cmd]:
                        _clear(GROUP_PREVIEW)
                        _draw_rotate_guides(_group(GROUP_PREVIEW),
                                            _pending["anchor"], _pending["size"], None)
                        _app.activeViewport.refresh()
                    # Extrude/Fillet: seed a default so a ghost appears the moment
                    # you pick — the manipulator or the card can refine it. (Their
                    # drag can be finicky; this guarantees the tool 'works'.)
                    if _active_cmd in ("extrude", "fillet"):
                        # sensible, capped default (cm): extrude 5–30mm, fillet 2–10mm
                        if _active_cmd == "extrude":
                            default = max(0.5, min(_pending["size"] * 0.2, 3.0))
                        else:
                            default = max(0.2, min(_pending["size"] * 0.08, 1.0))
                        it = _inputs.itemById("d")
                        if it is not None:
                            try:
                                it.value = default
                            except Exception:
                                pass
                        _seed_single(_active_cmd, default)
                        mid = _live.get(_active_cmd)
                        if mid is not None:
                            _clear(GROUP_PREVIEW)
                            _draw_one(_group(GROUP_PREVIEW), _find(mid))
                            _refresh_ghost()   # fade the original live, not just on close
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
            if _pending:
                cats = CMD_CATS[_active_cmd]
                if "rotate" in cats:
                    rop = _category_raw("rotate")
                    active = None if _is_default("rotate", rop) else _dominant_axis(rop["rot"])
                    _draw_rotate_guides(group, _pending["anchor"], _pending["size"], active)
                for cat in cats:
                    if cat in ("extrude", "fillet"):
                        # only let a real drag change the seeded amount; if the
                        # manipulator is unresponsive (value stays 0), keep the seed
                        if abs(_val("d")) > 1e-6:
                            _sync_category(cat)
                        mid = _live.get(cat)
                        mark = _find(mid) if mid is not None else None
                        if mid is not None:
                            # dragging -> drop the cached real geometry so the fast
                            # approximation follows the handle; recomputed on OK/edit
                            _geom[mid].pop("real", None)
                    else:
                        mark = _sync_category(cat)
                    if mark is not None:
                        _draw_one(group, mark)
                _refresh_ghost()   # fade the original live while dragging
                _send_state()      # keep the right-panel card in sync with the drag
            # NOTE: do NOT call activeViewport.refresh() here — forcing a refresh
            # mid-drag releases the manipulator, so the drag only worked once.
            # Fusion refreshes custom graphics after executePreview on its own.
            args.isValidResult = True
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD preview failed:\n{}".format(traceback.format_exc()))


class FuzzyExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _live
        try:
            if _pending:
                for cat in CMD_CATS[_active_cmd]:
                    _sync_category(cat)
                # 'release' -> compute the real geometry ghost for extrude/fillet
                for cat in CMD_CATS[_active_cmd]:
                    mid = _live.get(cat)
                    m = _find(mid) if mid is not None else None
                    if m and m["tool"] in ("extrude", "fillet"):
                        _compute_real(m)
            _clear(GROUP_PREVIEW)
            # Confirm commits the mark. Drop it from the live set NOW so this redraw
            # already shows the proposed (comic) look -- otherwise the mark stayed in
            # the clean "editing" look until Destroy fired, which Fusion can defer, so
            # the comic state didn't appear "immediately on confirm".
            _live = {}
            _redraw_marks()
            if _pending:
                _focus_camera(_pending["anchor"])
            _send_state()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD execute failed:\n{}".format(traceback.format_exc()))


class FuzzyDestroy(adsk.core.CommandEventHandler):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def notify(self, args):
        global _pending, _inputs, _active_cmd, _live, _cmd_session
        sess = self.session
        my_live = sess.get("live") or {}
        current = (_cmd_session is sess)
        # Remove THIS command's own orphan / default seed marks. Iterate the
        # session's own live dict, never the module global -- a successor command
        # replaces `_live` with a fresh dict, and clobbering that would delete the
        # new command's live mark.
        try:
            for cat, mid in list(my_live.items()):
                mk = _find(mid)
                if mk is None or _is_default(cat, mk):
                    _remove_mark(mid)
        except Exception:
            pass
        if not current:
            # A newer command already owns the globals (a tool/card switch: Fusion
            # fires the new command's Created before this stale Destroy). Touch no
            # shared state -- doing so is exactly the race that crashed / janked.
            return
        switching = _switching
        # Genuine current owner tearing down. Ownership-guarded clears: only release
        # a global if it still holds THIS command's value -- a card re-edit
        # (`edit_existing`) or another tool may have grabbed _active_cmd / _inputs
        # in the meantime, and we must never stomp their runtime state.
        try:
            if not switching:
                _clear(GROUP_PREVIEW)
        except Exception:
            pass
        if _active_cmd == sess.get("cmd"):
            _active_cmd = None
        if _inputs is sess.get("inputs"):
            _inputs = None
        if _live is my_live:
            # Cleared BEFORE redrawing: the surviving mark just left the command, so
            # it must read as "proposed" (comic look), not "editing" (clean preview).
            _live = {}
        _pending = None
        if _cmd_session is sess:
            _cmd_session = None
        # Restore the proposed UI only on a genuine close. During a switch the
        # incoming command owns the viewport; a redraw here would just flash.
        if not switching:
            try:
                _redraw_marks()
                _send_state()
            except Exception:
                pass


class LaunchHandler(adsk.core.CustomEventHandler):
    """Launch a command on the main thread. Executing a command directly inside
    a palette HTML event is unreliable, so we defer through this custom event.
    Any still-active command is terminated first — Fusion won't start a new
    command while one is running, which was why the *next* tool wouldn't open
    unless you closed the previous one via its OK button."""
    def notify(self, args):
        global _switching
        try:
            cmd_id = args.additionalInfo
            cd = _ui.commandDefinitions.itemById(cmd_id) if cmd_id else None
            # Mark the window where the old command is torn down and a new one
            # starts, but ONLY when there's a real successor to launch. A Destroy
            # that fires in here then skips its heavy redraw -- the incoming tool
            # owns the viewport, so a restore would just flash and fight it (jank).
            # A terminate-only call (empty id, e.g. the Confirm button) has no
            # successor, so the outgoing command must do its normal close redraw
            # (flip to the proposed/comic look); leaving _switching False ensures it.
            _switching = bool(cd)
            try:
                _ui.terminateActiveCommand()
            except Exception:
                pass
            if cd:
                cd.execute()
        except Exception:
            pass
        finally:
            _switching = False


# --- Note tool (Tinkercad-style callout -> a Constraint mark) ---------------
def _note_point_size(ent):
    """A representative anchor point + a size for the callout."""
    try:
        if isinstance(ent, adsk.fusion.BRepVertex):
            p = ent.geometry
            return [p.x, p.y, p.z], 3.0
        if isinstance(ent, adsk.fusion.BRepFace):
            p = ent.pointOnFace
            _, size = _bbox_center_size(ent)
            return [p.x, p.y, p.z], size
        if isinstance(ent, adsk.fusion.BRepEdge):
            pts = _sample_edge(ent, 2)
            _, size = _bbox_center_size(ent)
            return list(pts[len(pts) // 2]), size
    except Exception:
        pass
    center, size = _bbox_center_size(ent)
    return center, size


class NoteInputChanged(adsk.core.InputChangedEventHandler):
    """Create the note the moment a location is picked — no OK needed — and keep
    its text in sync as you type in the dialog (or later in the card)."""
    def notify(self, args):
        global _note_live_id, _next_id
        try:
            cid = args.input.id
            inputs = args.inputs
            if cid == "nsel":
                sel = adsk.core.SelectionCommandInput.cast(args.input)
                if sel and sel.selectionCount > 0 and _note_live_id is None:
                    ent = sel.selection(0).entity
                    point, size = _note_point_size(ent)
                    num = _tool_count.get("note", 0) + 1
                    _tool_count["note"] = num
                    mid = _next_id
                    _next_id += 1
                    mark = {"id": mid, "tool": "note", "mtype": "constraint", "label": "",
                            "anchor": point, "size": size, "num": num, "status": "open",
                            "comments": [], "text": ""}
                    _geom[mid] = {}
                    _entity[mid] = ent
                    _body[mid] = _entity_body(ent)
                    _marks.append(mark)
                    _note_live_id = mid
                    _redraw_marks()
                    _focus_camera(point)
                    _send_state()
            elif cid == "ntext" and _note_live_id is not None:
                m = _find(_note_live_id)
                it = inputs.itemById("ntext")
                if m and it is not None:
                    m["text"] = it.value
                    _redraw_marks()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD note failed:\n{}".format(traceback.format_exc()))


class NoteDestroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _note_inputs, _note_live_id
        try:
            _send_state()
        except Exception:
            pass
        _note_inputs = None
        _note_live_id = None


class FuzzyNoteCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _note_inputs, _note_live_id
        try:
            cmd = args.command
            cmd.isRepeatable = False
            cmd.okButtonText = "Done"
            inputs = cmd.commandInputs
            sel = inputs.addSelectionInput("nsel", "Location",
                                           "Click a point / face / edge on the object")
            for f in ("Vertices", "Edges", "Faces", "SolidBodies"):
                try:
                    sel.addSelectionFilter(f)
                except Exception:
                    pass
            sel.setSelectionLimits(1, 1)
            inputs.addStringValueInput("ntext", "Note", "")
            _note_inputs = inputs
            _note_live_id = None
            ic = NoteInputChanged()
            cmd.inputChanged.add(ic); _handlers.append(ic)
            dh = NoteDestroy()
            cmd.destroy.add(dh); _handlers.append(dh)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD note setup failed:\n{}".format(traceback.format_exc()))


class FuzzyCommandCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def notify(self, args):
        global _active_cmd, _inputs, _pending, _live, _cmd_session
        try:
            _active_cmd = self.cmd
            _pending = None
            _live = {}
            # This command's ownership token. A stale Destroy from a prior command
            # (Fusion fires the new Created before the old Destroy) will see it is
            # no longer the owner and leave these globals alone.
            _cmd_gen[0] += 1
            session = {"gen": _cmd_gen[0], "cmd": self.cmd, "inputs": None, "live": _live}
            _cmd_session = session
            command = args.command
            command.isRepeatable = False
            command.okButtonText = "Add to panel"
            _inputs = command.commandInputs
            session["inputs"] = _inputs

            sel = _inputs.addSelectionInput("sel", "Geometry", CMD_HINT[self.cmd])
            sel.addSelectionFilter(CMD_FILTER[self.cmd])
            sel.setSelectionLimits(1, 1)

            cats = CMD_CATS[self.cmd]
            if "move" in cats:
                for axis in ("X", "Y", "Z"):
                    it = _inputs.addDistanceValueCommandInput(
                        "m" + axis, "Move " + axis, adsk.core.ValueInput.createByReal(0.0))
                    it.isVisible = False; it.isEnabled = False
            if "rotate" in cats:
                for axis in ("X", "Y", "Z"):
                    it = _inputs.addAngleValueCommandInput(
                        "r" + axis, "Rotate " + axis, adsk.core.ValueInput.createByReal(0.0))
                    it.isVisible = False; it.isEnabled = False
            if "scale" in cats:
                it = _inputs.addDistanceValueCommandInput(
                    "sc", "Scale", adsk.core.ValueInput.createByReal(0.0))
                it.isVisible = False; it.isEnabled = False
            if "extrude" in cats or "fillet" in cats:
                nm = "Depth" if "extrude" in cats else "Radius"
                it = _inputs.addDistanceValueCommandInput(
                    "d", nm, adsk.core.ValueInput.createByReal(0.0))
                it.isVisible = False; it.isEnabled = False

            for handler, event in ((FuzzyInputChanged(), command.inputChanged),
                                   (FuzzyPreview(), command.executePreview),
                                   (FuzzyExecute(), command.execute),
                                   (FuzzyDestroy(session), command.destroy)):
                event.add(handler)
                _handlers.append(handler)
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD command setup failed:\n{}".format(
                    traceback.format_exc()))


# --- accept: real geometry --------------------------------------------------
def _scale_about_center(comp, coll, center, factor, axis_factors=None):
    """Scale the bodies in `coll` about `center` (uniform, or per-axis when
    `axis_factors=(fx, fy, fz)` is given).

    ScaleFeatures needs a point entity as the scale base. A construction point at
    the centre is ideal, but construction geometry requires the parametric
    environment -- imported/direct-modeling designs raise "Environment is not
    supported". In that case scale about the always-present component origin and
    translate by center*(1 - f) per axis, which is algebraically the same as
    scaling about the centre (c + f*(p - c) == f*p + c*(1 - f)).
    """
    vi = adsk.core.ValueInput.createByReal
    sf = comp.features.scaleFeatures

    def add_scale(base):
        si = sf.createInput(coll, base, vi(1.0 if axis_factors else factor))
        if axis_factors:
            fx, fy, fz = axis_factors
            si.setToNonUniform(vi(fx), vi(fy), vi(fz))
        sf.add(si)

    # Only the construction-point creation may hit the unsupported-environment
    # error; guard just that and fall back. setToNonUniform / feature failures
    # are real errors and must propagate to _accept, not silently fall back.
    base = None
    try:
        cpi = comp.constructionPoints.createInput()
        cpi.setByPoint(adsk.core.Point3D.create(*center))
        base = comp.constructionPoints.add(cpi)
    except Exception:
        base = None

    if base is not None:
        add_scale(base)
        return
    # Direct-modeling fallback: no construction geometry available.
    add_scale(comp.originConstructionPoint)
    kx = 1.0 - (axis_factors[0] if axis_factors else float(factor))
    ky = 1.0 - (axis_factors[1] if axis_factors else float(factor))
    kz = 1.0 - (axis_factors[2] if axis_factors else float(factor))
    if abs(kx) > 1e-12 or abs(ky) > 1e-12 or abs(kz) > 1e-12:
        mv = comp.features.moveFeatures
        mtx = adsk.core.Matrix3D.create()
        mtx.translation = adsk.core.Vector3D.create(
            center[0] * kx, center[1] * ky, center[2] * kz)
        mv.add(mv.createInput(coll, mtx))


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
            coll = adsk.core.ObjectCollection.create(); coll.add(body)
            comp.features.moveFeatures.add(
                comp.features.moveFeatures.createInput(coll, _op_matrix(mark)))
        elif tool == "scale":
            comp = body.parentComponent
            coll = adsk.core.ObjectCollection.create(); coll.add(body)
            _scale_about_center(comp, coll, mark["anchor"], mark["factor"])
        elif tool == "extrude":
            comp = ent.body.parentComponent
            ext = comp.features.extrudeFeatures
            ei = ext.createInput(ent, adsk.fusion.FeatureOperations.JoinFeatureOperation)
            ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(mark["amount"]))
            ext.add(ei)
        elif tool == "fillet":
            comp = ent.body.parentComponent
            fil = comp.features.filletFeatures
            coll = adsk.core.ObjectCollection.create(); coll.add(ent)
            fi = fil.createInput()
            fi.addConstantRadiusEdgeSet(coll, adsk.core.ValueInput.createByReal(mark["amount"]), True)
            fil.add(fi)
        return True
    except Exception:
        _ui.messageBox("FuzzyCAD couldn't apply the {} to real geometry:\n{}".format(
            tool, traceback.format_exc()))
        return False


def _real_extrude_edges(face, depth):
    """Create a temporary extrude (new body), sample its edges, then delete it."""
    comp = face.body.parentComponent
    ext = comp.features.extrudeFeatures
    ei = ext.createInput(face, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(depth))
    feat = ext.add(ei)
    polys = []
    try:
        for i in range(feat.bodies.count):
            b = feat.bodies.item(i)
            for j in range(b.edges.count):
                p = _sample_edge(b.edges.item(j))
                if len(p) >= 2:
                    polys.append(p)
    finally:
        try:
            feat.deleteMe()
        except Exception:
            pass
    return polys


def _real_fillet_edges(edge, radius):
    """Apply a temporary fillet, sample the new fillet-face edges, then undo it."""
    comp = edge.body.parentComponent
    fil = comp.features.filletFeatures
    coll = adsk.core.ObjectCollection.create(); coll.add(edge)
    fi = fil.createInput()
    fi.addConstantRadiusEdgeSet(coll, adsk.core.ValueInput.createByReal(radius), True)
    feat = fil.add(fi)
    polys = []
    try:
        for i in range(feat.faces.count):
            f = feat.faces.item(i)
            for j in range(f.edges.count):
                p = _sample_edge(f.edges.item(j))
                if len(p) >= 2:
                    polys.append(p)
    finally:
        try:
            feat.deleteMe()
        except Exception:
            pass
    return polys


def _compute_real(mark):
    """Compute the actual proposed geometry, extract its edges, and cache them so
    the ghost is drawn from the real result. Falls back to the approximation on
    any failure (e.g. radius too large). Deliberately only called at discrete
    moments (drag release / card edit), not every frame — it builds and deletes a
    real feature, which is comparatively expensive."""
    if _design() is None or mark["id"] not in _geom:
        return
    ent = _entity.get(mark["id"])
    if ent is None or abs(mark.get("amount", 0.0)) < 1e-9:
        _geom[mark["id"]].pop("real", None)
        return
    try:
        if mark["tool"] == "extrude":
            polys = _real_extrude_edges(ent, mark["amount"])
        elif mark["tool"] == "fillet":
            polys = _real_fillet_edges(ent, mark["amount"])
        else:
            return
        if polys:
            _geom[mark["id"]]["real"] = polys
        else:
            _geom[mark["id"]].pop("real", None)
    except Exception:
        _geom[mark["id"]].pop("real", None)


def _remove_mark(mid):
    _marks[:] = [m for m in _marks if m["id"] != mid]
    _geom.pop(mid, None)
    _entity.pop(mid, None)
    _body.pop(mid, None)


# --- palette messaging ------------------------------------------------------
def _fields(mark):
    t = mark["tool"]
    if t == "note":
        return [{"key": "text", "label": "Note", "value": mark.get("text", ""),
                 "unit": "", "kind": "text"}]
    if t == "move":
        return [{"key": "x", "label": "Move X", "value": round(mark["vec"][0] * 10, 2), "unit": "mm"},
                {"key": "y", "label": "Move Y", "value": round(mark["vec"][1] * 10, 2), "unit": "mm"},
                {"key": "z", "label": "Move Z", "value": round(mark["vec"][2] * 10, 2), "unit": "mm"}]
    if t == "rotate":
        return [{"key": "x", "label": "Rotate X", "value": round(mark["rot"][0], 1), "unit": "°"},
                {"key": "y", "label": "Rotate Y", "value": round(mark["rot"][1], 1), "unit": "°"},
                {"key": "z", "label": "Rotate Z", "value": round(mark["rot"][2], 1), "unit": "°"}]
    if t == "scale":
        return [{"key": "f", "label": "Scale", "value": round(mark["factor"], 3), "unit": "×"}]
    if t == "extrude":
        return [{"key": "d", "label": "Depth", "value": round(mark["amount"] * 10, 2), "unit": "mm"}]
    return [{"key": "d", "label": "Radius", "value": round(mark["amount"] * 10, 2), "unit": "mm"}]


def _apply_edit(mark, key, value):
    if mark["tool"] == "note":
        mark["text"] = str(value)
        return
    try:
        v = float(value)
    except Exception:
        return
    t = mark["tool"]
    if t == "move":
        mark["vec"][{"x": 0, "y": 1, "z": 2}[key]] = v / 10.0
    elif t == "rotate":
        mark["rot"][{"x": 0, "y": 1, "z": 2}[key]] = v
    elif t == "scale":
        mark["factor"] = max(0.05, v)
    else:
        mark["amount"] = v / 10.0


def _summary(mark):
    t = mark["tool"]
    if t == "note":
        txt = mark.get("text", "")
        return "note: " + (txt if len(txt) <= 40 else txt[:38] + "…") if txt else "note"
    if t == "move":
        v = [round(x * 10, 1) for x in mark["vec"]]
        return "move ({}, {}, {}) mm".format(*v)
    if t == "rotate":
        parts = ["{}{:g}°".format(a, r) for a, r in zip("XYZ", mark["rot"]) if abs(r) > 1e-6]
        return "rotate " + (" ".join(parts) if parts else "0°")
    if t == "scale":
        return "scale ×{:.2f}".format(mark["factor"])
    if t == "extrude":
        return "extrude {:g} mm".format(mark["amount"] * 10)
    return "fillet r {:g} mm".format(mark["amount"] * 10)


def _public(mark):
    return {"id": mark["id"], "tool": mark["tool"], "num": mark.get("num", 1),
            "title": "{} {}".format(mark["tool"].capitalize(), mark.get("num", 1)),
            "label": mark["label"], "status": mark.get("status", "open"),
            "mtype": mark.get("mtype", "need_input"),
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
                # Defer to the main thread — executing a command straight from a
                # palette event is unreliable.
                _app.fireCustomEvent(LAUNCH_EVENT_ID, CMD_ID.get(data.get("tool"), ""))
            elif action == "focus":
                m = _find(data.get("id"))
                if m:
                    _focus_camera(m["anchor"])
            elif action == "edit":
                m = _find(data.get("id"))
                if m:
                    _apply_edit(m, data.get("key"), data.get("value"))
                    # editing the number is a discrete 'release' — recompute the
                    # real geometry ghost for extrude/fillet
                    if m["tool"] in ("extrude", "fillet"):
                        _compute_real(m)
                    # Redraw the 3D only — do NOT push state back, or the panel
                    # re-renders and the field you're typing in loses focus.
                    _redraw_marks()
            elif action == "comment":
                m = _find(data.get("id"))
                if m:
                    txt = (data.get("text") or "").strip()
                    if txt:
                        m.setdefault("comments", []).append({"text": txt})
                        _send_state()
            elif action == "accept":
                # Accept == apply to the real model (geometry ops) + resolve.
                # For a note there's nothing to bake, so accept just resolves it.
                m = _find(data.get("id"))
                if m:
                    ok = True if m["tool"] == "note" else _accept(m)
                    if ok:
                        _remove_mark(m["id"]); _redraw_marks(); _send_state()
            elif action == "reject":
                _remove_mark(data.get("id")); _redraw_marks(); _send_state()
        except Exception:
            if _ui:
                _ui.messageBox("FuzzyCAD panel message failed:\n{}".format(
                    traceback.format_exc()))


# --- palettes + lifecycle ---------------------------------------------------
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
        bar = palettes.add(TOOLBAR_ID, "FuzzyCAD Tools", TOOLBAR_URL, True, True, True, 720, 96)
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
        for cmd in COMMANDS:
            _add_button(panel, CMD_ID[cmd], CMD_LABEL[cmd], CMD_HINT[cmd],
                        FuzzyCommandCreated(cmd))
        _add_button(panel, CMD_ID["note"], "Note",
                    "Drop a note callout on the model (a Constraint mark)",
                    FuzzyNoteCreated())
        try:
            _app.unregisterCustomEvent(LAUNCH_EVENT_ID)
        except Exception:
            pass
        try:
            evt = _app.registerCustomEvent(LAUNCH_EVENT_ID)
            rh = LaunchHandler(); evt.add(rh); _handlers.append(rh)
        except Exception:
            pass
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
        for cmd_id in [PANEL_CMD_ID] + list(CMD_ID.values()):
            if panel:
                ctrl = panel.controls.itemById(cmd_id)
                if ctrl:
                    ctrl.deleteMe()
            cd = _ui.commandDefinitions.itemById(cmd_id)
            if cd:
                cd.deleteMe()
        try:
            _app.unregisterCustomEvent(LAUNCH_EVENT_ID)
        except Exception:
            pass
        _handlers.clear()
        _geom.clear(); _entity.clear(); _body.clear()
    except Exception:
        if _ui:
            _ui.messageBox("FuzzyCAD failed to stop:\n{}".format(traceback.format_exc()))
