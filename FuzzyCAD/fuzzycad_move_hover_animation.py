"""Hover animation for Move proposal cards.

A Move proposal already has a static destination candidate from
fuzzycad_unified_visuals.  When the user hovers its sidebar card, this patch
adds a second CustomGraphics BRep body that repeatedly travels from the original
position to the proposed position.  The sidebar supplies throttled frame values;
all Fusion API work stays inside palette callbacks on Fusion's main thread.
"""

HOVER_GROUP = "FuzzyCAD_HoverAnimation"
CANDIDATE_RGB = (190, 190, 186)
ANIM_OPACITY = 0.46


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {
        "mid": None,
        "graphic": None,
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
        had_animation = state["mid"] is not None or state["graphic"] is not None
        clear_group()
        state["mid"] = None
        state["graphic"] = None
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

    def start_animation(mid):
        try:
            mid = int(mid)
        except Exception:
            return

        mark = m._find(mid)
        if mark is None or mark.get("tool") != "move":
            stop_animation()
            return

        body = m._body.get(mid)
        if body is None:
            stop_animation()
            return
        try:
            if not body.isValid:
                stop_animation()
                return
        except Exception:
            pass

        stop_animation(refresh_view=False)
        group = m._group(HOVER_GROUP)
        if group is None:
            return

        try:
            graphic = group.addBRepBody(body)
            graphic.color = m._solid(CANDIDATE_RGB)
            graphic.setOpacity(ANIM_OPACITY, True)
            graphic.transform = move_matrix(mark, 0.0)
        except Exception as exc:
            clear_group()
            log("MOVE HOVER START failed mark={}: {}".format(mid, exc))
            return

        state["mid"] = mid
        state["graphic"] = graphic
        state["frame"] = 0
        vec_mm = [round(float(v) * 10.0, 3) for v in (mark.get("vec") or [0, 0, 0])]
        log("MOVE HOVER START mark={} target_mm={}".format(mid, vec_mm))
        refresh()

    def animation_frame(mid, t):
        try:
            mid = int(mid)
            t = max(0.0, min(1.0, float(t)))
        except Exception:
            return

        if state["mid"] != mid or state["graphic"] is None:
            return

        mark = m._find(mid)
        if mark is None or mark.get("tool") != "move":
            stop_animation()
            return

        try:
            state["graphic"].transform = move_matrix(mark, t)
            state["frame"] += 1
            # Keep logging useful without flooding TEXT COMMANDS at every tick.
            if state["frame"] == 1 or state["frame"] % 10 == 0:
                log("MOVE HOVER FRAME mark={} t={:.3f}".format(mid, t))
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

            # Any operation that can resolve/change the active proposal should
            # remove the transient animation immediately.
            if action in ("accept", "reject", "tool"):
                stop_animation()

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        clear_group()
        log("MOVE HOVER ANIMATION READY: hover a Move card")
        return result

    def stop(context):
        stop_animation(refresh_view=False)
        return old_stop(context)

    m.run = run
    m.stop = stop
