"""Single viewport reconciliation pass for FuzzyCAD.

Lifecycle policy is not defined here. The central visual authority supplies both
comic visibility and source-body opacity targets. This module only repairs drift
and clears stale ephemeral interaction graphics.
"""


def install(m):
    old_redraw = m._redraw_marks
    old_run = m.run

    ghost = float(getattr(m, "GHOST_OPACITY", 0.5))
    LO = 0.015
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

    def desired_visual_tokens():
        """Bodies intentionally using a display-only non-original opacity."""
        try:
            return set(str(t) for t, _b, _op in m._visual_opacity_subject_rows() if t)
        except Exception:
            pass
        try:
            rows, _retained = m._visual_comic_subject_rows()
            return set(str(t) for t, _b in rows if t)
        except Exception:
            return set()

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

    def reclaim_orphan_visual_opacity(design):
        """Repair stale display opacity left by a dead/rebuilt graphics session.

        Fillet/Hole Editing are included in desired_visual_tokens, so their 0.50
        source body is never mistaken for an orphan ghost.
        """
        want = desired_visual_tokens()
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

        # Apply the authoritative target first. This makes Confirm -> Proposed an
        # immediate presentation switch on the same redraw that changes phase.
        try:
            sync = getattr(m, "_sync_visual_opacity", None)
            if sync is not None:
                sync()
        except Exception:
            pass

        if not fuzzy_command_running():
            sweep_ephemeral()
        try:
            reclaim_orphan_visual_opacity(design)
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
