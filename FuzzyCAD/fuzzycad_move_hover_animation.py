"""Lightweight hover animation for Move proposal cards.

Hover replay only needs to communicate movement tendency. It avoids adding the
full Fusion BRep to CustomGraphics. The primary body is represented by a sparse
subset of the already-sampled proposal edge polylines; Together bodies use very
cheap bounding-box wireframes. Graphics are built once and one group transform
is updated at a deliberately slow cadence.

Interaction rule:
- enter a Move card -> move once from current position toward the proposal;
- stay hovered -> remain at the proposed position, never reverse;
- leave/click -> remove the replay immediately.

A separate static, filled orange arrow sits along the motion path while replay is
active. It is a tiny CustomGraphics triangle mesh, not a BRep, so it makes the
movement direction obvious without bringing back expensive solid rendering.
"""

import math
import time

HOVER_GROUP = "FuzzyCAD_HoverAnimation"
HOVER_ARROW_GROUP = "FuzzyCAD_HoverDirectionArrow"
ANIM_RGB = (64, 68, 72)
ANIM_WEIGHT = 1.6
ARROW_RGB = (225, 126, 38)
ARROW_EDGE_RGB = (159, 82, 24)
ARROW_OPACITY = 0.80
MAX_PRIMARY_POLYS = 26
MAX_POINTS_PER_POLY = 14
FRAME_INTERVAL_SEC = 0.12       # ~8.3 viewport refreshes/sec
FORWARD_SEC = 1.90              # slower, readable one-way motion


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {
        "mid": None,
        "group": None,
        "arrow_group": None,
        "line_count": 0,
        "frame": 0,
        "started": 0.0,
        "last_refresh": 0.0,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD ANIM] " + msg)
        except Exception:
            pass

    def refresh():
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass

    def clear_groups():
        for gid in (HOVER_GROUP, HOVER_ARROW_GROUP):
            try:
                m._clear(gid)
            except Exception:
                pass

    def stop_animation(refresh_view=True):
        had_animation = (
            state["mid"] is not None or
            state["group"] is not None or
            state["arrow_group"] is not None
        )
        clear_groups()
        state.update({
            "mid": None,
            "group": None,
            "arrow_group": None,
            "line_count": 0,
            "frame": 0,
            "started": 0.0,
            "last_refresh": 0.0,
        })
        if had_animation and refresh_view:
            refresh()

    def move_matrix(mark, t):
        vec = mark.get("vec") or [0.0, 0.0, 0.0]
        mat = adsk.core.Matrix3D.create()
        mat.translation = adsk.core.Vector3D.create(
            float(vec[0]) * t,
            float(vec[1]) * t,
            float(vec[2]) * t,
        )
        return mat

    def valid_body(body):
        if body is None:
            return False
        try:
            return bool(body.isValid)
        except Exception:
            return True

    def decimate_poly(poly):
        if not poly or len(poly) < 2:
            return []
        if len(poly) <= MAX_POINTS_PER_POLY:
            return list(poly)
        step = max(1, int(math.ceil((len(poly) - 1) / float(MAX_POINTS_PER_POLY - 1))))
        out = list(poly[::step])
        if out[-1] != poly[-1]:
            out.append(poly[-1])
        return out[:MAX_POINTS_PER_POLY]

    def bbox_polys(body):
        if not valid_body(body):
            return []
        try:
            bb = body.boundingBox
            x0, y0, z0 = bb.minPoint.x, bb.minPoint.y, bb.minPoint.z
            x1, y1, z1 = bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z
            p = [
                (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
            ]
            edges = ((0,1),(1,2),(2,3),(3,0),
                     (4,5),(5,6),(6,7),(7,4),
                     (0,4),(1,5),(2,6),(3,7))
            return [[p[a], p[b]] for a, b in edges]
        except Exception:
            return []

    def sparse_primary(mark, body):
        polys = list((m._geom.get(mark.get("id"), {}) or {}).get("edges") or [])
        if not polys:
            return bbox_polys(body)
        step = max(1, int(math.ceil(len(polys) / float(MAX_PRIMARY_POLYS))))
        chosen = polys[::step][:MAX_PRIMARY_POLYS]
        return [decimate_poly(poly) for poly in chosen if poly and len(poly) >= 2]

    def add_polyline(group, poly):
        pts = decimate_poly(poly)
        if len(pts) < 2:
            return False
        try:
            flat = []
            for xyz in pts:
                flat.extend((float(xyz[0]), float(xyz[1]), float(xyz[2])))
            coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
            line = group.addLines(coords, list(range(len(pts))), True)
            line.color = m._solid(ANIM_RGB)
            line.weight = ANIM_WEIGHT
            return True
        except Exception:
            return False

    def animation_polys(mark, primary):
        rows = list(sparse_primary(mark, primary))
        if mark.get("move_scope") == "together":
            # Related bodies only need to make the shared motion unmistakable.
            # Bounding boxes are cheap and visually strong enough for replay.
            for body in mark.get("related_bodies") or []:
                rows.extend(bbox_polys(body))
        return rows

    def vlen(v):
        return math.sqrt(sum(float(x) * float(x) for x in v))

    def normalized(v):
        n = vlen(v)
        if n < 1e-9:
            return (0.0, 0.0, 0.0)
        return tuple(float(x) / n for x in v)

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def addv(a, b, scale=1.0):
        return (
            float(a[0]) + float(b[0]) * scale,
            float(a[1]) + float(b[1]) * scale,
            float(a[2]) + float(b[2]) * scale,
        )

    def body_center(body):
        try:
            bb = body.boundingBox
            return (
                (bb.minPoint.x + bb.maxPoint.x) * 0.5,
                (bb.minPoint.y + bb.maxPoint.y) * 0.5,
                (bb.minPoint.z + bb.maxPoint.z) * 0.5,
            )
        except Exception:
            return (0.0, 0.0, 0.0)

    def camera_view():
        try:
            cam = m._app.activeViewport.camera
            return normalized((
                cam.eye.x - cam.target.x,
                cam.eye.y - cam.target.y,
                cam.eye.z - cam.target.z,
            ))
        except Exception:
            return (0.0, 0.0, 1.0)

    def arrow_side(direction):
        # Keep the arrow face roughly screen-readable by using a width direction
        # perpendicular to both movement and the current camera direction.
        side = cross(direction, camera_view())
        if vlen(side) < 1e-6:
            helper = (0.0, 1.0, 0.0) if abs(direction[1]) < 0.85 else (1.0, 0.0, 0.0)
            side = cross(direction, helper)
        if vlen(side) < 1e-6:
            side = cross(direction, (0.0, 0.0, 1.0))
        return normalized(side)

    def add_direction_arrow(mark, primary):
        vec = tuple(float(x) for x in (mark.get("vec") or [0.0, 0.0, 0.0]))
        distance = vlen(vec)
        if distance < 1e-6:
            return None

        group = m._group(HOVER_ARROW_GROUP)
        if group is None:
            return None

        direction = normalized(vec)
        side = arrow_side(direction)
        if vlen(side) < 1e-6:
            return group

        anchor = mark.get("anchor") or body_center(primary)
        anchor = tuple(float(x) for x in anchor)
        # Leave a little breathing room at each end so the arrow reads as a path
        # cue rather than another exact geometric edge.
        start = addv(anchor, direction, distance * 0.08)
        tip = addv(anchor, direction, distance * 0.92)
        visible_len = max(distance * 0.84, 1e-6)

        size = max(0.25, float(mark.get("size", 3.0) or 3.0))
        shaft_half = max(0.07, min(max(size * 0.030, visible_len * 0.035), 0.34))
        head_half = min(max(shaft_half * 2.45, shaft_half + 0.08), max(shaft_half * 2.45, visible_len * 0.22))
        head_len = min(visible_len * 0.44, max(shaft_half * 2.7, visible_len * 0.24))
        shoulder = addv(tip, direction, -head_len)

        # Seven points describe a classic filled 2D arrow. The shaft rectangle
        # and triangular head are only six triangles total including back faces.
        p0 = addv(start, side, -shaft_half)
        p1 = addv(shoulder, side, -shaft_half)
        p2 = addv(shoulder, side, -head_half)
        p3 = tip
        p4 = addv(shoulder, side, head_half)
        p5 = addv(shoulder, side, shaft_half)
        p6 = addv(start, side, shaft_half)
        pts = (p0, p1, p2, p3, p4, p5, p6)
        flat = []
        for p in pts:
            flat.extend((p[0], p[1], p[2]))

        try:
            coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
            # Front and reverse winding make the flat arrow visible from either
            # side without any geometry/kernel operation.
            indices = [
                0, 1, 5, 0, 5, 6, 2, 3, 4,
                5, 1, 0, 6, 5, 0, 4, 3, 2,
            ]
            mesh = group.addMesh(coords, indices, [], [])
            mesh.color = m._solid(ARROW_RGB)
            mesh.setOpacity(ARROW_OPACITY, True)
            try:
                mesh.depthPriority = 8
            except Exception:
                pass

            outline = group.addLines(coords, [0, 1, 2, 3, 4, 5, 6, 0], True)
            outline.color = m._solid(ARROW_EDGE_RGB)
            outline.weight = 1.15
            try:
                outline.depthPriority = 9
            except Exception:
                pass
        except Exception:
            pass
        return group

    def eased(t):
        t = max(0.0, min(1.0, float(t)))
        return t * t * (3.0 - 2.0 * t)

    def motion_t(now):
        # One-way only. Once the proposal is reached, stay there for as long as
        # the pointer remains on the card. No return leg and no looping reset.
        elapsed = max(0.0, now - state["started"])
        if elapsed >= FORWARD_SEC:
            return 1.0
        return eased(elapsed / FORWARD_SEC)

    def start_animation(mid):
        try:
            mid = int(mid)
        except Exception:
            return

        mark = m._find(mid)
        if mark is None or mark.get("tool") != "move":
            stop_animation()
            return
        primary = m._body.get(mid)
        if not valid_body(primary):
            stop_animation()
            return

        stop_animation(refresh_view=False)
        group = m._group(HOVER_GROUP)
        if group is None:
            return

        count = 0
        for poly in animation_polys(mark, primary):
            if add_polyline(group, poly):
                count += 1
        if count < 1:
            clear_groups()
            return

        arrow_group = add_direction_arrow(mark, primary)
        now = time.perf_counter()
        try:
            group.transform = move_matrix(mark, 0.0)
        except Exception:
            clear_groups()
            return

        state.update({
            "mid": mid,
            "group": group,
            "arrow_group": arrow_group,
            "line_count": count,
            "frame": 0,
            "started": now,
            "last_refresh": 0.0,
        })
        refresh()

    def animation_frame(mid, _browser_t):
        try:
            mid = int(mid)
        except Exception:
            return
        if state["mid"] != mid or state["group"] is None:
            return

        mark = m._find(mid)
        if mark is None or mark.get("tool") != "move":
            stop_animation()
            return

        now = time.perf_counter()
        if state["last_refresh"] and now - state["last_refresh"] < FRAME_INTERVAL_SEC:
            return
        t = motion_t(now)
        # Once we have reached the target, no more viewport refresh work is
        # needed. The wireframe and static direction arrow remain until leave/click.
        if t >= 1.0 and state["frame"] < 0:
            return
        state["last_refresh"] = now
        try:
            state["group"].transform = move_matrix(mark, t)
            if t >= 1.0:
                state["frame"] = -1
            else:
                state["frame"] += 1
            refresh()
        except Exception:
            stop_animation()

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            if action == "hoverMoveStart":
                start_animation(data.get("id"))
                return
            if action == "hoverMoveFrame":
                animation_frame(data.get("id"), data.get("t", 0.0))
                return
            if action == "hoverMoveEnd":
                if state["mid"] == data.get("id") or str(state["mid"]) == str(data.get("id")):
                    stop_animation()
                return

            if action in ("editManipulator", "accept", "reject", "tool"):
                stop_animation()

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        clear_groups()
        return result

    def stop(context):
        stop_animation(refresh_view=False)
        return old_stop(context)

    m.run = run
    m.stop = stop
