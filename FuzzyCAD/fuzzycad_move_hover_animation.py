"""Lightweight hover animation for Move proposal cards.

Hover replay only needs to communicate movement tendency.  It therefore avoids
adding the full Fusion BRep to CustomGraphics.  The primary body is represented
by a sparse subset of the already-sampled proposal edge polylines; Together
bodies use very cheap bounding-box wireframes.  The graphics are built once and
then one group transform is updated at a deliberately slow cadence.

The browser may send hoverMoveFrame more frequently, but this layer throttles
Fusion viewport refreshes and computes its own slower forward/return motion.
"""

import math
import time

HOVER_GROUP = "FuzzyCAD_HoverAnimation"
ANIM_RGB = (108, 112, 116)
MAX_PRIMARY_POLYS = 26
MAX_POINTS_PER_POLY = 14
FRAME_INTERVAL_SEC = 0.12       # ~8.3 viewport refreshes/sec, formerly ~16.7
FORWARD_SEC = 1.75
HOLD_SEC = 0.28
RETURN_SEC = 1.10
REST_SEC = 0.30


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {
        "mid": None,
        "group": None,
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

    def clear_group():
        try:
            m._clear(HOVER_GROUP)
        except Exception:
            pass

    def stop_animation(refresh_view=True):
        had_animation = state["mid"] is not None or state["group"] is not None
        clear_group()
        state.update({
            "mid": None,
            "group": None,
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

    def sparse_primary(mark, body):
        polys = list((m._geom.get(mark.get("id"), {}) or {}).get("edges") or [])
        if not polys:
            return bbox_polys(body)
        step = max(1, int(math.ceil(len(polys) / float(MAX_PRIMARY_POLYS))))
        chosen = polys[::step][:MAX_PRIMARY_POLYS]
        return [decimate_poly(poly) for poly in chosen if poly and len(poly) >= 2]

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

    def add_polyline(group, poly, seed):
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
            line.weight = 1.0
            return True
        except Exception:
            return False

    def animation_polys(mark, primary):
        rows = [(poly, 0) for poly in sparse_primary(mark, primary)]
        if mark.get("move_scope") == "together":
            # Related bodies only need to indicate that they travel with the
            # selected body. Bounding boxes are much cheaper than full BReps or
            # sampling every related edge and remain sufficient for tendency.
            for ridx, body in enumerate(mark.get("related_bodies") or []):
                for poly in bbox_polys(body):
                    rows.append((poly, (ridx + 1) * 100))
        return rows

    def eased(t):
        t = max(0.0, min(1.0, float(t)))
        return t * t * (3.0 - 2.0 * t)

    def motion_t(now):
        total = FORWARD_SEC + HOLD_SEC + RETURN_SEC + REST_SEC
        elapsed = max(0.0, now - state["started"]) % total
        if elapsed < FORWARD_SEC:
            return eased(elapsed / FORWARD_SEC)
        elapsed -= FORWARD_SEC
        if elapsed < HOLD_SEC:
            return 1.0
        elapsed -= HOLD_SEC
        if elapsed < RETURN_SEC:
            return 1.0 - eased(elapsed / RETURN_SEC)
        return 0.0

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
        for idx, (poly, offset) in enumerate(animation_polys(mark, primary)):
            if add_polyline(group, poly, mid * 51001 + offset + idx):
                count += 1
        if count < 1:
            clear_group()
            return

        now = time.perf_counter()
        try:
            group.transform = move_matrix(mark, 0.0)
        except Exception:
            clear_group()
            return

        state.update({
            "mid": mid,
            "group": group,
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
        state["last_refresh"] = now
        try:
            state["group"].transform = move_matrix(mark, motion_t(now))
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

            # Clicking a card enters editManipulator through the delegated
            # handler. The browser sends hoverMoveEnd first; these actions are a
            # second guard so replay never competes with editing/apply/reject.
            if action in ("editManipulator", "accept", "reject", "tool"):
                stop_animation()

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        clear_group()
        return result

    def stop(context):
        stop_animation(refresh_view=False)
        return old_stop(context)

    m.run = run
    m.stop = stop
