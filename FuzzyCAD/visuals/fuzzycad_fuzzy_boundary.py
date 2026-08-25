"""Experimental ghost replacement: a fuzzy (un-pinned) boundary.

The advisor's point: a half-transparent body is ambiguous -- transparency is
everywhere in CAD, so it doesn't read as "there's an open question here." This
moves the "unsettled" signal off transparency and onto a LINE GHOST: the
translucent, still-clickable body keeps a light fade, and its EDGES are redrawn as
a few offset copies -- each a hand-drawn (sketchy) line, like the pulled-out
proposal, in a grey that fades from light to near-black across the copies -- a
soft double image, "not pinned yet." The copies are CustomGraphics lines, so they
render but can never be selected or picked; only the real body responds to clicks.

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
HIDE_BODY       = False           # True -> hide the questioned body entirely, show only
                                  #         its sketchy ghost (drops the crisp CAD edges)
BODY_OPACITY    = 0.2             # the real body's fade when HIDE_BODY is False
COPIES_PER_BODY = 6               # how many offset wireframe copies = the ghosting
SCATTER         = 0.03            # copy offset, as a fraction of body size (bigger = more spread)
OVERSHOOT       = 0.05            # how far lines run PAST each corner (loosens sharp corners)
LINE_WEIGHT     = 1.0             # ghost line thickness (try 0.6 thin .. 2.5 bold)
GRAY_LIGHT      = 165             # lightest ghost copy (0=black .. 255=white)
GRAY_DARK       = 45              # darkest ghost copy — copies fade across this range
MAX_LINES       = 2400            # cost guard across all questioned bodies
# Each ghost line is drawn with the same hand-drawn "sketchy" wobble as the
# proposals (the pulled-out preview), via _visual_stroke's proposal role.
# ============================================================================


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_refresh_ghost = m._refresh_ghost

    m._FUZZY_BOUNDARY = FUZZY_ON
    GID = "FuzzyCAD_FuzzyBoundary"
    hidden = {}          # token -> body we set invisible, so we can restore it

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

    def gray_for(k):
        if COPIES_PER_BODY <= 1:
            g = GRAY_DARK
        else:
            t = k / float(COPIES_PER_BODY - 1)
            g = int(round(GRAY_LIGHT + (GRAY_DARK - GRAY_LIGHT) * t))
        g = max(0, min(255, g))
        return (g, g, g)

    def overshoot(poly, ext):
        """Run each edge a little PAST its two endpoints so corners cross/overshoot
        like a hand sketch instead of meeting at a precise point."""
        if ext <= 0 or len(poly) < 2:
            return poly

        def past(a, b):     # a point ext beyond a, along the direction away from b
            dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            return (a[0] + dx / d * ext, a[1] + dy / d * ext, a[2] + dz / d * ext)

        return [past(poly[0], poly[1])] + list(poly) + [past(poly[-1], poly[-2])]

    def stroke(grp, pts, rgb, seed, size):
        """One hand-drawn ghost line: the proposals' sketchy wobble, our grey."""
        vs = getattr(m, "_visual_stroke", None)
        if vs is not None:
            vs(grp, pts, "proposal_internal", seed, size=size, rgb=rgb,
               weight=LINE_WEIGHT, strokes=1)
        else:
            m._sketchy(grp, pts, rgb, max(0.01, size * 0.004), seed,
                       weight=LINE_WEIGHT, strokes=1)

    def draw_fuzzy():
        """Draw the questioned body's EDGES as a few offset copies, each a
        hand-drawn (sketchy) line in a grey that fades from light to near-black
        across the copies. Lines live in a CustomGraphics group, so they render
        but can never be clicked or picked; only the real body is selectable."""
        try:
            m._clear(GID)
        except Exception:
            pass
        if not getattr(m, "_FUZZY_BOUNDARY", True):
            return
        # Reconcile hide/fade here too, so a resolved body is restored on any redraw
        # (accept/reject, Inspector Repair) even if refresh_ghost didn't fire.
        try:
            apply_body_state()
        except Exception:
            pass
        bodies = questioned_bodies()
        if not bodies:
            return
        grp = m._group(GID)
        if grp is None:
            return
        drawn = 0
        for bi, b in enumerate(bodies):
            try:
                _, size = m._bbox_center_size(b)
            except Exception:
                size = 3.0
            step = max(0.02, min(float(size) * SCATTER, 0.80))  # copy offset ~ body size
            ext = max(0.0, min(float(size) * OVERSHOOT, 0.8))   # corner overshoot
            try:
                loops = m._sample_edges(b.edges)
            except Exception:
                loops = []
            if not loops:
                continue
            # Seeded per body so the scatter is stable across redraws (no flicker).
            rnd = random.Random(1234 + bi * 97)
            for k in range(COPIES_PER_BODY):
                if drawn >= MAX_LINES:
                    break
                rgb = gray_for(k)
                dx = rnd.uniform(-1, 1); dy = rnd.uniform(-1, 1); dz = rnd.uniform(-1, 1)
                dl = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
                mag = step * rnd.uniform(0.4, 1.1)
                ox, oy, oz = dx / dl * mag, dy / dl * mag, dz / dl * mag
                for j, poly in enumerate(loops):
                    if drawn >= MAX_LINES:
                        break
                    pts = overshoot([(q[0] + ox, q[1] + oy, q[2] + oz) for q in poly], ext)
                    try:
                        stroke(grp, pts, rgb, (bi * 911 + k * 131 + j) & 0xffff, size)
                        drawn += 1
                    except Exception:
                        pass
            if drawn >= MAX_LINES:
                break
        log("ghost lines drawn={} for {} body(ies)".format(drawn, len(bodies)))
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def body_tok(b):
        try:
            return b.entityToken
        except Exception:
            return id(b)

    def apply_body_state():
        """Hide the questioned bodies (so only the sketchy ghost shows, without the
        real body's crisp CAD edges) or fade them, and restore any body that is no
        longer questioned. Runs on every ghost refresh and every redraw, so a
        resolved body always comes back."""
        if not getattr(m, "_FUZZY_BOUNDARY", True):
            return
        want = questioned_bodies()
        want_keys = set()
        for b in want:
            tok = body_tok(b)
            want_keys.add(tok)
            try:
                if HIDE_BODY:
                    hidden[tok] = b
                    b.isVisible = False
                else:
                    b.opacity = BODY_OPACITY
            except Exception:
                pass
        for tok in list(hidden.keys()):
            if tok not in want_keys:
                b = hidden.pop(tok)
                try:
                    if b.isValid:
                        b.isVisible = True
                except Exception:
                    pass

    def restore_all_visibility():
        for tok in list(hidden.keys()):
            b = hidden.pop(tok)
            try:
                if b.isValid:
                    b.isVisible = True
            except Exception:
                pass

    m._fuzzy_restore_visibility = restore_all_visibility

    def refresh_ghost():
        old_refresh_ghost()
        apply_body_state()

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
        log("FUZZY BOUNDARY READY (experiment): {:.0%} real body + non-selectable sketchy "
            "grey line-ghost".format(BODY_OPACITY))
        return result

    m.run = run
