"""Experimental ghost replacement: a fuzzy (un-pinned) boundary.

The advisor's point: a half-transparent body is ambiguous -- transparency is
everywhere in CAD, so it doesn't read as "there's an open question here." This
moves the "unsettled" signal off transparency and onto a DOUBLE IMAGE: the
questioned body stays clearly present and clickable, and a couple of faint,
OFFSET copies of it are drawn as ghost doubles -- "not pinned yet." The copies
are CustomGraphics, so they render but can never be selected or picked; only the
real body underneath responds to clicks.

Fully reversible:
  * set m._FUZZY_BOUNDARY = False  -> reverts to the classic 0.5 ghost, or
  * don't load this module in FuzzyCAD.py (one line).

Nothing else changes -- sketchy proposals, badges, colours all stay. This only
softens the ghost fade and adds one overlay group (FuzzyCAD_FuzzyBoundary), which
is redrawn from the open marks each _redraw_marks (mark-owned, not ephemeral).
"""

import math
import random


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_refresh_ghost = m._refresh_ghost

    # Flip to False to fall straight back to the plain ghost with no other change.
    m._FUZZY_BOUNDARY = True

    GID = "FuzzyCAD_FuzzyBoundary"
    SOFT_OPACITY = 0.85          # the real (clickable) body stays clearly present
    GHOST_RGB = (240, 195, 45)   # yellow afterimage
    GHOST_OPACITY = 0.12         # each copy very faint; many of them layer up
    COPIES_PER_BODY = 6          # more doubles, scattered
    ROT_MAX_DEG = 2.5            # tiny random rotation per copy = the jitter
    MAX_COPIES = 18              # cost guard across all questioned bodies

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

    def draw_fuzzy():
        """Draw non-selectable, offset copies of each questioned body -- 'ghost
        doubles'. They live in a CustomGraphics group, so they render but can never
        be clicked or picked; only the real body underneath is selectable."""
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
        try:
            tmp = adsk.fusion.TemporaryBRepManager.get()
        except Exception:
            return
        made = 0
        for bi, b in enumerate(bodies):
            try:
                center, size = m._bbox_center_size(b)
            except Exception:
                center, size = [0.0, 0.0, 0.0], 3.0
            step = max(0.05, min(float(size) * 0.03, 0.6))   # scatter scales with body
            ctr = adsk.core.Point3D.create(center[0], center[1], center[2])
            # Seeded per body so the scatter is stable across redraws (no flicker),
            # but random enough to read as jitter rather than a clean offset.
            rnd = random.Random(1234 + bi * 97)
            for _k in range(COPIES_PER_BODY):
                if made >= MAX_COPIES:
                    break
                try:
                    dup = tmp.copy(b)                        # temp BRep copy
                    if dup is None:
                        continue
                    # random scatter direction + magnitude
                    dirx = rnd.uniform(-1, 1); diry = rnd.uniform(-1, 1); dirz = rnd.uniform(-1, 1)
                    dl = math.sqrt(dirx * dirx + diry * diry + dirz * dirz) or 1.0
                    mag = step * rnd.uniform(0.35, 1.15)
                    ox, oy, oz = dirx / dl * mag, diry / dl * mag, dirz / dl * mag
                    # tiny random rotation about the body centre = the shimmer
                    ang = math.radians(rnd.uniform(-ROT_MAX_DEG, ROT_MAX_DEG))
                    ax = adsk.core.Vector3D.create(rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))
                    try:
                        ax.normalize()
                    except Exception:
                        ax = adsk.core.Vector3D.create(0, 0, 1)
                    mtx = adsk.core.Matrix3D.create()
                    mtx.setToRotation(ang, ax, ctr)
                    t = mtx.translation
                    mtx.translation = adsk.core.Vector3D.create(t.x + ox, t.y + oy, t.z + oz)
                    tmp.transform(dup, mtx)
                    cg = grp.addBRepBody(dup)               # CustomGraphics = not selectable
                    cg.color = m._solid(GHOST_RGB)
                    cg.setOpacity(GHOST_OPACITY, True)
                    made += 1
                except Exception:
                    pass
            if made >= MAX_COPIES:
                break
        log("ghost doubles drawn={} for {} body(ies)".format(made, len(bodies)))
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
        log("FUZZY BOUNDARY READY (experiment): {:.0%} real body + non-selectable offset "
            "ghost doubles".format(SOFT_OPACITY))
        return result

    m.run = run
