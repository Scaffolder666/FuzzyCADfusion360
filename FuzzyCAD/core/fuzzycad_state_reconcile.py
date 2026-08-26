"""Single viewport reconciliation pass for FuzzyCAD.

Lifecycle policy is not defined here. The central uncertainty visual authority
provides the body-level comic state; this module only repairs drift: orphan body
opacity and stale ephemeral interaction graphics.
"""


def install(m):
    old_redraw = m._redraw_marks
    old_run = m.run

    ghost = float(getattr(m, "GHOST_OPACITY", 0.5))
    LO = 0.03
    HI = min(0.70, ghost + 0.10)

    EPHEMERAL_GROUPS = [
        getattr(m, "GROUP_PREVIEW", "FuzzyCAD_Preview"),
        "FuzzyCAD_DepCheck",
        "FuzzyCAD_FollowHighlight",
        "FuzzyCAD_HoverAnimation",
        "FuzzyCAD_HoverDirectionArrow",
        "FuzzyCAD_OperationHover",
        "FuzzyCAD_CompareConnectorPreview",
    ]
    m._EPHEMERAL_GROUPS = EPHEMERAL_GROUPS

    def fuzzy_command_running():
        try:
            active = m._ui.activeCommand or ""
        except Exception:
            active = ""
        return isinstance(active, str) and active.startswith("FuzzyCAD_")

    def sweep_ephemeral():
        for gid in EPHEMERAL_GROUPS:
            try:
                m._clear(gid)
            except Exception:
                pass

    m._sweep_ephemeral = sweep_ephemeral

    def tok(body):
        try:
            return str(body.entityToken)
        except Exception:
            return None

    def desired_ghost_tokens():
        """Bodies that the central visual policy says are currently comic/proposed."""
        try:
            rows, _retained = m._visual_comic_subject_rows()
            return set(str(t) for t, _b in rows if t)
        except Exception:
            pass

        # Backward/install fallback.
        want = set()
        for mark in list(getattr(m, "_marks", None) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            t = tok(m._body.get(mark.get("id")))
            if t:
                want.add(t)
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

    def reconcile():
        design = m._design()
        if design is None:
            return
        if not fuzzy_command_running():
            sweep_ephemeral()
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
        try:
            reconcile()
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m.run = run
