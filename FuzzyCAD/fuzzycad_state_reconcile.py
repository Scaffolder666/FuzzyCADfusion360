"""Authoritative state reconciliation for FuzzyCAD 3D visuals.

Two failure modes made the viewport drift out of sync with the actual open
questions, especially after accepting/rejecting cards and after reopening a
saved document:

1) Stuck ghost bodies. Ghosting is applied by writing BRepBody.opacity and is
   meant to be restored when a mark goes away. The restore path holds the body
   proxy captured when the ghost was applied, but accepting a proposal rebuilds
   the timeline, which invalidates those proxies (and reassigns entity tokens).
   The restore is then silently skipped and the body stays semi-transparent for
   the rest of the session even though no open question references it.

2) Orphan previews. The extrude/fillet/move manipulators draw into the PREVIEW
   graphics group. Rejecting the card removes the mark but does not terminate an
   already-idle preview, so a stray dimension ruler or badge sprite can linger.

Both are fixed here by reconciling from the single source of truth -- the open
marks -- against the *live* bodies in the design, rather than trusting captured
proxies. This runs only on discrete redraws (never per animation frame), so the
allComponents scan is not on any hot path.

Loaded last so its redraw wrapper is outermost and reconciles after every other
visual layer has drawn.
"""


def install(m):
    old_redraw = m._redraw_marks
    old_run = m.run

    ghost = float(getattr(m, "GHOST_OPACITY", 0.16))
    # Reclaim window around the values FuzzyCAD ghosts with (0.08 / 0.16). Kept
    # tight so an opacity a user deliberately set (e.g. 0.5) is never touched.
    LO, HI = 0.03, 0.34

    def tok(body):
        try:
            return body.entityToken
        except Exception:
            return None

    def desired_ghost_tokens():
        """Tokens of bodies that SHOULD be ghosted right now: the body of every
        open, non-note proposal, plus the related set of a move-together group.
        Mirrors fuzzycad_opacity_runtime.desired_bodies so we never reclaim a
        body that legitimately still carries an open question."""
        want = set()
        for mark in list(getattr(m, "_marks", None) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            t = tok(m._body.get(mark.get("id")))
            if t:
                want.add(t)
            if mark.get("tool") == "move" and mark.get("move_scope") == "together":
                for related in mark.get("related_bodies") or []:
                    rt = tok(related)
                    if rt:
                        want.add(rt)
        return want

    def iter_live_bodies(design):
        try:
            comps = design.allComponents
        except Exception:
            return
        for i in range(comps.count):
            try:
                bodies = comps.item(i).bRepBodies
            except Exception:
                continue
            for j in range(bodies.count):
                try:
                    yield bodies.item(j)
                except Exception:
                    continue

    def reclaim_orphan_ghosts(design):
        want = desired_ghost_tokens()
        for body in iter_live_bodies(design):
            try:
                op = float(body.opacity)
            except Exception:
                continue
            if LO <= op <= HI and tok(body) not in want:
                try:
                    body.opacity = 1.0
                except Exception:
                    pass

    def is_idle():
        # No FuzzyCAD command or pending selection/drag is in flight, so nothing
        # legitimately owns the PREVIEW group.
        return (getattr(m, "_active_cmd", None) is None
                and not getattr(m, "_pending", None))

    def reconcile():
        design = m._design()
        if design is None:
            return
        if is_idle():
            try:
                m._clear(m.GROUP_PREVIEW)
            except Exception:
                pass
        try:
            reclaim_orphan_ghosts(design)
        except Exception:
            pass

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            reconcile()
        except Exception:
            pass
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m._redraw_marks = redraw

    def run(context):
        result = old_run(context)
        # After hydration/startup, sweep once so a document reopened with stale
        # ghost opacity from a previous session comes back clean.
        try:
            reconcile()
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m.run = run
