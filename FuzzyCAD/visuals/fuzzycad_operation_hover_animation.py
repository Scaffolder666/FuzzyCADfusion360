"""Lightweight card-hover replay for non-Move transformation proposals.

Rotate, Axis Rotate, Scale, directional Scale, and Extrude get small replay
animations without rebuilding BReps.  Source edge/face polylines are created
once when hover starts; animation frames only update one CustomGraphicsGroup
transform.  Fillet is intentionally excluded because morphing a real fillet
would require repeated kernel work and is better communicated by its local
orange surface highlight.
"""

import math

GROUP_ID = "FuzzyCAD_OperationHover"
SUPPORTED = ("rotate", "scale", "scale_axis", "axis_rotate", "extrude")


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {"mid": None, "tool": None, "group": None, "frames": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD OP ANIM] " + msg)
        except Exception:
            pass

    def clear_group():
        try: m._clear(GROUP_ID)
        except Exception: pass

    def refresh():
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass

    def stop_animation(do_refresh=True):
        had = state["group"] is not None
        clear_group()
        state.update({"mid": None, "tool": None, "group": None, "frames": 0})
        if had and do_refresh:
            refresh()

    def scale_matrix(anchor, factors):
        fx, fy, fz = factors
        a = list(anchor or [0.0, 0.0, 0.0])
        mat = adsk.core.Matrix3D.create()
        mat.setCell(0, 0, fx); mat.setCell(1, 1, fy); mat.setCell(2, 2, fz)
        mat.translation = adsk.core.Vector3D.create(
            a[0] * (1.0 - fx), a[1] * (1.0 - fy), a[2] * (1.0 - fz))
        return mat

    def partial_matrix(mark, t):
        tool = mark.get("tool")
        t = max(0.0, min(1.0, float(t)))
        if tool == "rotate":
            rot = list(mark.get("rot") or [0.0, 0.0, 0.0])
            idx = max(range(3), key=lambda i: abs(rot[i]))
            angle = math.radians(float(rot[idx]) * t)
            axis = m._axis_unit("XYZ"[idx])
            mat = adsk.core.Matrix3D.create()
            mat.setToRotation(angle, adsk.core.Vector3D.create(*axis),
                              adsk.core.Point3D.create(*(mark.get("anchor") or [0, 0, 0])))
            return mat
        if tool == "axis_rotate":
            g = m._geom.get(mark.get("id"), {})
            origin = g.get("axis_origin") or mark.get("axis_origin") or [0, 0, 0]
            direction = g.get("axis_dir") or mark.get("axis_dir") or [0, 0, 1]
            mat = adsk.core.Matrix3D.create()
            mat.setToRotation(math.radians(float(mark.get("angle", 0.0)) * t),
                              adsk.core.Vector3D.create(*direction),
                              adsk.core.Point3D.create(*origin))
            return mat
        if tool == "scale":
            target = max(0.05, float(mark.get("factor", 1.0)))
            f = 1.0 + (target - 1.0) * t
            return scale_matrix(mark.get("anchor"), (f, f, f))
        if tool == "scale_axis":
            target = max(0.05, float(mark.get("factor", 1.0)))
            f = 1.0 + (target - 1.0) * t
            axis = mark.get("axis", "X")
            factors = (f, 1.0, 1.0) if axis == "X" else ((1.0, f, 1.0) if axis == "Y" else (1.0, 1.0, f))
            base = mark.get("base_anchor") or mark.get("anchor")
            return scale_matrix(base, factors)
        if tool == "extrude":
            g = m._geom.get(mark.get("id"), {})
            d = g.get("normal") or [0.0, 0.0, 1.0]
            amount = float(mark.get("amount", 0.0)) * t
            mat = adsk.core.Matrix3D.create()
            mat.translation = adsk.core.Vector3D.create(d[0] * amount, d[1] * amount, d[2] * amount)
            return mat
        return adsk.core.Matrix3D.create()

    def source_polys(mark):
        tool = mark.get("tool")
        g = m._geom.get(mark.get("id"), {})
        if tool == "extrude":
            return list(g.get("loops") or [])
        return list(g.get("edges") or [])

    def add_polys(group, polys, mark):
        # One-pixel single strokes keep hover replay cheap and separate from the
        # persistent two-stroke proposal rendering.
        count = 0
        for i, poly in enumerate(polys):
            if not poly or len(poly) < 2:
                continue
            try:
                m._sketchy(group, poly, (82, 86, 90), 0.0,
                           mark.get("id", 1) * 51001 + i,
                           weight=1, strokes=1)
                count += 1
            except Exception:
                pass
        return count

    def start_animation(mid):
        try: mid = int(mid)
        except Exception: return
        mark = m._find(mid)
        if mark is None or mark.get("tool") not in SUPPORTED:
            stop_animation(); return

        stop_animation(False)
        group = m._group(GROUP_ID)
        if group is None:
            return
        polys = source_polys(mark)
        count = add_polys(group, polys, mark)
        if count < 1:
            clear_group(); return
        try: group.transform = partial_matrix(mark, 0.0)
        except Exception: pass
        state.update({"mid": mid, "tool": mark.get("tool"), "group": group, "frames": 0})
        log("START mark={} tool={} polylines={}".format(mid, mark.get("tool"), count))
        refresh()

    def animation_frame(mid, t):
        try: mid = int(mid); t = float(t)
        except Exception: return
        if state["mid"] != mid or state["group"] is None:
            return
        mark = m._find(mid)
        if mark is None:
            stop_animation(); return
        try:
            state["group"].transform = partial_matrix(mark, t)
            state["frames"] += 1
            # Keep the UI smooth without doing any geometry reconstruction.
            refresh()
        except Exception as exc:
            log("FRAME failed mark={} {}".format(mid, exc))
            stop_animation()

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__(); self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None; data = {}
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            if action == "hoverOpStart":
                start_animation(data.get("id")); return
            if action == "hoverOpFrame":
                animation_frame(data.get("id"), data.get("t", 0.0)); return
            if action == "hoverOpEnd":
                if str(state["mid"]) == str(data.get("id")):
                    stop_animation()
                return
            if action in ("accept", "reject", "tool"):
                stop_animation(False)

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        clear_group()
        log("OPERATION HOVER READY: rotate / scale / axis rotate / extrude use transform-only replay")
        return result

    def stop(context):
        stop_animation(False)
        return old_stop(context)

    m.run = run
    m.stop = stop
