"""Move-card hover replay.

Hover keeps the real proposed sketch visible, adds a separate thin wireframe copy
that travels from the current position to the proposal, and keeps an orange
movement arrow on screen. The committed body never moves.
"""

import math
import time

HOVER_GROUP = "FuzzyCAD_HoverAnimation"
HOVER_ARROW_GROUP = "FuzzyCAD_HoverDirectionArrow"
ANIM_RGB = (68, 72, 76)
ANIM_WEIGHT = 1.25
ARROW_RGB = (225, 126, 38)
MAX_PRIMARY_POLYS = 30
MAX_RELATED_POLYS = 12
MAX_POINTS_PER_POLY = 14
FRAME_INTERVAL_SEC = 0.12
FORWARD_SEC = 1.90


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {
        "mid": None,
        "group": None,
        "arrow_group": None,
        "frame": 0,
        "started": 0.0,
        "last_refresh": 0.0,
    }

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
        had = state["mid"] is not None or state["group"] is not None or state["arrow_group"] is not None
        clear_groups()
        state.update({
            "mid": None,
            "group": None,
            "arrow_group": None,
            "frame": 0,
            "started": 0.0,
            "last_refresh": 0.0,
        })
        if had and refresh_view:
            refresh()

    def valid_body(body):
        if body is None:
            return False
        try:
            return bool(body.isValid)
        except Exception:
            return True

    def move_matrix(mark, t):
        vec = mark.get("vec") or [0.0, 0.0, 0.0]
        mat = adsk.core.Matrix3D.create()
        mat.translation = adsk.core.Vector3D.create(
            float(vec[0]) * t, float(vec[1]) * t, float(vec[2]) * t)
        return mat

    def decimate(poly):
        if not poly or len(poly) < 2:
            return []
        if len(poly) <= MAX_POINTS_PER_POLY:
            return list(poly)
        step = max(1, int(math.ceil((len(poly) - 1) / float(MAX_POINTS_PER_POLY - 1))))
        out = list(poly[::step])
        if out[-1] != poly[-1]:
            out.append(poly[-1])
        return out[:MAX_POINTS_PER_POLY]

    def poly_length(poly):
        total = 0.0
        for i in range(1, len(poly)):
            a, b = poly[i - 1], poly[i]
            total += math.sqrt(sum((float(b[j]) - float(a[j])) ** 2 for j in range(3)))
        return total

    def choose_longest(polys, limit):
        rows = [decimate(p) for p in polys if p and len(p) >= 2]
        rows = [p for p in rows if len(p) >= 2]
        rows.sort(key=poly_length, reverse=True)
        return rows[:limit]

    def primary_polys(mark):
        rows = list((m._geom.get(mark.get("id"), {}) or {}).get("edges") or [])
        return choose_longest(rows, MAX_PRIMARY_POLYS)

    def related_polys(body):
        """Use recognizable body edges, not bbox proxy sticks."""
        if not valid_body(body):
            return []
        rows = []
        try:
            edges = body.edges
            count = int(edges.count)
            if count < 1:
                return []
            step = max(1, int(math.ceil(count / float(MAX_RELATED_POLYS))))
            for i in range(0, count, step):
                if len(rows) >= MAX_RELATED_POLYS:
                    break
                try:
                    poly = m._sample_edge(edges.item(i), n=6)
                    if poly and len(poly) >= 2:
                        rows.append(poly)
                except Exception:
                    pass
        except Exception:
            pass
        return choose_longest(rows, MAX_RELATED_POLYS)

    def animation_polys(mark, primary):
        rows = list(primary_polys(mark))
        if not rows:
            try:
                rows = related_polys(primary)
            except Exception:
                rows = []
        if mark.get("move_scope") == "together":
            for body in mark.get("related_bodies") or []:
                rows.extend(related_polys(body))
        return rows

    def add_polyline(group, poly):
        pts = decimate(poly)
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
            try:
                line.depthPriority = 7
            except Exception:
                pass
            return True
        except Exception:
            return False

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

    def normalized(v):
        n = math.sqrt(sum(float(x) * float(x) for x in v))
        if n < 1e-9:
            return None
        return tuple(float(x) / n for x in v)

    def add_arrow(mark, primary):
        vec = tuple(float(x) for x in (mark.get("vec") or [0.0, 0.0, 0.0]))
        direction = normalized(vec)
        if direction is None:
            return None
        distance = math.sqrt(sum(x * x for x in vec))
        if distance < 1e-6:
            return None

        group = m._group(HOVER_ARROW_GROUP)
        if group is None:
            return None

        start = body_center(primary)
        end = tuple(start[i] + vec[i] for i in range(3))
        try:
            m._sketchy(group, [start, end], ARROW_RGB, 0.0,
                       mark.get("id", 1) * 77001, weight=2, strokes=1)

            # Screen-facing V arrowhead.
            right, up = m._camera_xy()
            side = up
            dot = abs(sum(direction[i] * side[i] for i in range(3)))
            if dot > 0.88:
                side = right
            head = max(0.22, min(float(mark.get("size", 3.0) or 3.0) * 0.10, 0.75))
            base = tuple(end[i] - direction[i] * head for i in range(3))
            w = head * 0.48
            p1 = tuple(base[i] + side[i] * w for i in range(3))
            p2 = tuple(base[i] - side[i] * w for i in range(3))
            m._sketchy(group, [p1, end, p2], ARROW_RGB, 0.0,
                       mark.get("id", 1) * 77002, weight=2, strokes=1)
        except Exception:
            pass
        return group

    def eased(t):
        t = max(0.0, min(1.0, float(t)))
        return t * t * (3.0 - 2.0 * t)

    def motion_t(now):
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

        stop_animation(False)
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

        arrow_group = add_arrow(mark, primary)
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
        if t >= 1.0 and state["frame"] < 0:
            return
        state["last_refresh"] = now
        try:
            state["group"].transform = move_matrix(mark, t)
            state["frame"] = -1 if t >= 1.0 else state["frame"] + 1
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
                if str(state["mid"]) == str(data.get("id")):
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
        stop_animation(False)
        return old_stop(context)

    m.run = run
    m.stop = stop
