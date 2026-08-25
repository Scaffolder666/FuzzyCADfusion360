"""Experimental ghost replacement: a fuzzy (un-pinned) boundary.

The advisor's point: a half-transparent body is ambiguous -- transparency is
everywhere in CAD, so it doesn't read as "there's an open question here." This
moves the "unsettled" signal off transparency and onto the BOUNDARY: the
questioned body stays clearly present (only a light fade, so the sketchy proposal
still reads through it), and its edges are redrawn as an offset hand-drawn blur --
"the true edge isn't pinned yet."

Fully reversible:
  * set m._FUZZY_BOUNDARY = False  -> reverts to the classic 0.5 ghost, or
  * don't load this module in FuzzyCAD.py (one line).

Nothing else changes -- sketchy proposals, badges, colours all stay. This only
softens the ghost fade and adds one overlay group (FuzzyCAD_FuzzyBoundary), which
is redrawn from the open marks each _redraw_marks (mark-owned, not ephemeral).
"""


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_refresh_ghost = m._refresh_ghost

    # Flip to False to fall straight back to the plain ghost with no other change.
    m._FUZZY_BOUNDARY = True

    GID = "FuzzyCAD_FuzzyBoundary"
    SOFT_OPACITY = 0.7           # present, but the proposal still reads through
    EDGE_RGB = (118, 124, 132)   # cool graphite -- not the orange operation cue
    PASSES = 2                   # overlapping jittered passes = the blur
    MAX_EDGES = 220              # cost guard across all questioned bodies

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD FUZZY] " + msg)
        except Exception:
            pass

    def questioned_bodies():
        """Bodies of open, non-note marks -- the same set opacity_runtime ghosts."""
        out, seen = [], set()
        for mark in list(getattr(m, "_marks", None) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            b = m._body.get(mark.get("id"))
            if b is None:
                continue
            try:
                tok = b.entityToken
            except Exception:
                tok = None
            key = tok or id(b)
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
        return out

    def sample_edge(edge, n=10):
        try:
            ev = edge.evaluator
            ok, sp, ep = ev.getParameterExtents()
            if not ok:
                return []
            pts = []
            for i in range(n + 1):
                t = sp + (ep - sp) * i / n
                ok2, p = ev.getPointAtParameter(t)
                if ok2:
                    pts.append((p.x, p.y, p.z))
            return pts
        except Exception:
            return []

    def draw_fuzzy():
        try:
            m._clear(GID)
        except Exception:
            pass
        if not getattr(m, "_FUZZY_BOUNDARY", True):
            return
        bodies = questioned_bodies()
        if not bodies:
            return
        grp = m._group(GID)
        if grp is None:
            return
        drawn = 0
        for b in bodies:
            try:
                _, size = m._bbox_center_size(b)
            except Exception:
                size = 3.0
            amp = max(0.03, min(float(size) * 0.015, 0.20))   # blur scales with body
            try:
                edges = b.edges
                count = edges.count
            except Exception:
                continue
            for i in range(count):
                if drawn >= MAX_EDGES:
                    log("edge cap hit ({}) -- boundary drawn partially".format(MAX_EDGES))
                    break
                pts = sample_edge(edges.item(i))
                if len(pts) < 2:
                    continue
                for s in range(PASSES):
                    try:
                        m._sketchy(grp, pts, EDGE_RGB, amp, (i * 7 + s * 131) & 0xffff,
                                   weight=1, strokes=1)
                    except Exception:
                        pass
                drawn += 1
            if drawn >= MAX_EDGES:
                break
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def refresh_ghost():
        old_refresh_ghost()
        if not getattr(m, "_FUZZY_BOUNDARY", True):
            return
        # Soften the fade the base ghost applied: keep the body clearly present so
        # the boundary blur -- not the transparency -- is what reads as uncertain.
        for b in questioned_bodies():
            try:
                b.opacity = SOFT_OPACITY
            except Exception:
                pass

    m._refresh_ghost = refresh_ghost

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            draw_fuzzy()
        except Exception:
            log("fuzzy draw failed\n{}".format(m.traceback.format_exc()))
        return result

    m._redraw_marks = redraw

    old_run = m.run

    def run(context):
        result = old_run(context)
        log("FUZZY BOUNDARY READY (experiment): soft {:.0%} body + offset hand-drawn edges".format(
            SOFT_OPACITY))
        return result

    m.run = run
