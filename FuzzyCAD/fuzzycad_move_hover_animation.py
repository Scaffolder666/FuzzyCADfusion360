"""Hover animation for Move proposal cards.

The animation is intentionally cheap: build the primary/related BRep graphics
once when hover starts, then animate the CustomGraphicsGroup transform.  Fusion
therefore receives one transform update per frame even when a Together proposal
contains several bodies.
"""

HOVER_GROUP = "FuzzyCAD_HoverAnimation"
CANDIDATE_RGB = (190, 190, 186)
PRIMARY_OPACITY = 0.46
RELATED_OPACITY = 0.36


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {
        "mid": None,
        "group": None,
        "body_count": 0,
        "frame": 0,
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
        state["mid"] = None
        state["group"] = None
        state["body_count"] = 0
        state["frame"] = 0
        if had_animation:
            log("MOVE HOVER END")
        if refresh_view and had_animation:
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

    def animation_bodies(mark, primary):
        bodies = [primary]
        if mark.get("move_scope") == "together":
            bodies.extend(mark.get("related_bodies") or [])
        out = []
        seen = set()
        for body in bodies:
            if not valid_body(body):
                continue
            try:
                key = body.entityToken
            except Exception:
                key = str(id(body))
            if key in seen:
                continue
            seen.add(key)
            out.append(body)
        return out

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

        bodies = animation_bodies(mark, primary)
        if not bodies:
            return

        try:
            effect = m._solid(CANDIDATE_RGB)
            for i, body in enumerate(bodies):
                cg = group.addBRepBody(body)
                cg.color = effect
                cg.setOpacity(PRIMARY_OPACITY if i == 0 else RELATED_OPACITY, True)
            # All bodies share one group transform. This is substantially cheaper
            # than rebuilding or moving each BRep graphic every animation frame.
            group.transform = move_matrix(mark, 0.0)
        except Exception as exc:
            clear_group()
            log("MOVE HOVER START failed mark={}: {}".format(mid, exc))
            return

        state["mid"] = mid
        state["group"] = group
        state["body_count"] = len(bodies)
        state["frame"] = 0
        vec_mm = [round(float(v) * 10.0, 3) for v in (mark.get("vec") or [0, 0, 0])]
        log("MOVE HOVER START mark={} target_mm={} bodies={} scope={}".format(
            mid, vec_mm, len(bodies), mark.get("move_scope", "only")))
        refresh()

    def animation_frame(mid, t):
        try:
            mid = int(mid)
            t = max(0.0, min(1.0, float(t)))
        except Exception:
            return

        if state["mid"] != mid or state["group"] is None:
            return

        mark = m._find(mid)
        if mark is None or mark.get("tool") != "move":
            stop_animation()
            return

        try:
            state["group"].transform = move_matrix(mark, t)
            state["frame"] += 1
            if state["frame"] == 1 or state["frame"] % 10 == 0:
                log("MOVE HOVER FRAME mark={} t={:.3f} bodies={}".format(
                    mid, t, state["body_count"]))
            refresh()
        except Exception as exc:
            log("MOVE HOVER FRAME failed mark={}: {}".format(mid, exc))
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

            if action in ("accept", "reject", "tool"):
                stop_animation()

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        clear_group()
        log("MOVE HOVER ANIMATION READY: one group transform for Selected + Together bodies")
        return result

    def stop(context):
        stop_animation(refresh_view=False)
        return old_stop(context)

    m.run = run
    m.stop = stop
