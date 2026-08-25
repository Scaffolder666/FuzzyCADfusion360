"""Experimental ghost replacement: a fuzzy (un-pinned) boundary.

The advisor's point: a half-transparent body is ambiguous -- transparency is
everywhere in CAD, so it doesn't read as "there's an open question here." This
moves the "unsettled" signal off transparency and onto a shaky LINE GHOST: the
questioned body stays clearly present and clickable, and its EDGES are redrawn as
several faint, offset, jittered yellow copies -- a hand-shaky double image, "not
pinned yet." The copies are CustomGraphics lines, so they render but can never be
selected or picked; only the real body underneath responds to clicks.

Fully reversible:
  * set m._FUZZY_BOUNDARY = False  -> reverts to the classic 0.5 ghost, or
  * don't load this module in FuzzyCAD.py (one line).

Nothing else changes -- sketchy proposals, badges, colours all stay. This only
softens the ghost fade and adds one overlay group (FuzzyCAD_FuzzyBoundary), which
is redrawn from the open marks each _redraw_marks (mark-owned, not ephemeral).
"""

import math
import random

# ============================================================================
#  TUNABLE KNOBS  — edit these, then reload the add-in. (This is the top of the
#  file; there is nothing to hunt for inside the functions below.)
# ============================================================================
FUZZY_ON        = True            # False -> fall straight back to the plain ghost
BODY_OPACITY    = 0.5             # the real (clickable) body's fade, like the ghost
GHOST_RGB       = (240, 195, 45)  # colour of the ghost lines (yellow)
GHOST_ALPHA     = 110             # line faintness 0-255 (Fusion may clamp line alpha)
LINE_WEIGHT     = 1.0             # line thickness
COPIES_PER_BODY = 6               # how many offset wireframe copies = the ghosting
SCATTER         = 0.03            # copy offset, as a fraction of body size
JITTER          = 0.07            # per-point wobble, as a fraction of body size
                                  #   (LOWER = calmer; this is the "too shaky" dial)
MAX_LINES       = 1400            # cost guard across all questioned bodies
# ============================================================================


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_refresh_ghost = m._refresh_ghost

    m._FUZZY_BOUNDARY = FUZZY_ON
    GID = "FuzzyCAD_FuzzyBoundary"

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

    def faint_color():
        return adsk.fusion.CustomGraphicsSolidColorEffect.create(
            adsk.core.Color.create(GHOST_RGB[0], GHOST_RGB[1], GHOST_RGB[2], GHOST_ALPHA))

    def add_polyline(grp, pts, color):
        n = len(pts)
        if n < 2:
            return False
        flat = []
        for p in pts:
            flat.append(p[0]); flat.append(p[1]); flat.append(p[2])
        coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = grp.addLines(coords, list(range(n)), True)
        line.color = color
        try:
            line.weight = LINE_WEIGHT
        except Exception:
            pass
        return True

    def draw_fuzzy():
        """Draw the questioned body's EDGES as several offset, jittered copies --
        a shaky yellow line-ghost, not a translucent solid. Lines live in a
        CustomGraphics group, so they render but can never be clicked or picked;
        only the real body underneath is selectable."""
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
        col = faint_color()
        drawn = 0
        for bi, b in enumerate(bodies):
            try:
                _, size = m._bbox_center_size(b)
            except Exception:
                size = 3.0
            step = max(0.03, min(float(size) * SCATTER, 0.8))   # copy offset ~ body size
            jit = max(0.01, min(float(size) * JITTER, 0.6))     # per-point wobble
            try:
                loops = m._sample_edges(b.edges)
            except Exception:
                loops = []
            if not loops:
                continue
            # Seeded per body so the scatter/jitter is stable across redraws
            # (no flicker), but random enough to read as many shaky ghosts.
            rnd = random.Random(1234 + bi * 97)
            for _k in range(COPIES_PER_BODY):
                if drawn >= MAX_LINES:
                    break
                dx = rnd.uniform(-1, 1); dy = rnd.uniform(-1, 1); dz = rnd.uniform(-1, 1)
                dl = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
                mag = step * rnd.uniform(0.35, 1.15)
                ox, oy, oz = dx / dl * mag, dy / dl * mag, dz / dl * mag
                for poly in loops:
                    if drawn >= MAX_LINES:
                        break
                    pts = []
                    for q in poly:
                        pts.append((q[0] + ox + rnd.uniform(-jit, jit),
                                    q[1] + oy + rnd.uniform(-jit, jit),
                                    q[2] + oz + rnd.uniform(-jit, jit)))
                    if add_polyline(grp, pts, col):
                        drawn += 1
            if drawn >= MAX_LINES:
                break
        log("ghost lines drawn={} for {} body(ies)".format(drawn, len(bodies)))
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
                b.opacity = BODY_OPACITY
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
        log("FUZZY BOUNDARY READY (experiment): {:.0%} real body + non-selectable shaky "
            "yellow line-ghost".format(BODY_OPACITY))
        return result

    m.run = run
