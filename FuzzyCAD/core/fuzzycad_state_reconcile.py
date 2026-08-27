"""Single viewport reconciliation pass for FuzzyCAD.

Lifecycle policy is not defined here. The central visual authority supplies both
comic visibility and source-body opacity targets. This module only repairs drift
and clears stale ephemeral interaction graphics.
"""


def install(m):
    old_redraw = m._redraw_marks
    old_run = m.run

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

    def known_visual_opacity(value):
        try:
            op = float(value)
        except Exception:
            return False
        vals = [
            float(getattr(m, "GHOST_OPACITY", 0.5)),
            float(getattr(m, "_VISUAL_COMIC_SOURCE_OPACITY", 0.02)),
            float(getattr(m, "_VISUAL_SEMITRANSPARENT_SOURCE_OPACITY", 0.50)),
        ]
        return any(abs(op - v) < 0.025 for v in vals)

    def reclaim_orphan_visual_opacity(design):
        """Repair stale display opacity left by a dead/rebuilt graphics session.

        Fillet/Hole Editing remain in desired_visual_tokens, so their intentional
        0.50 source body is never reclaimed. Once a mark is accepted/rejected (or
        an interrupted edit no longer owns the body), both the comic opacity and
        the 0.50 Editing opacity are eligible for restoration.
        """
        want = desired_visual_tokens()
        restore = getattr(m, "_restore_orphan_visual_body", None)
        for body in iter_live_bodies(design):
            btok = tok(body)
            if btok in want:
                continue
            try:
                op = float(body.opacity)
            except Exception:
                continue
            # The opacity runtime has the persisted/original value when available.
            # Call it for any known FuzzyCAD visual opacity; legacy fallback there
            # restores to 1.0 only when no original record exists.
            if known_visual_opacity(op):
                if restore is not None:
                    try:
                        if restore(body):
                            continue
                    except Exception:
                        pass
                try:
                    body.opacity = 1.0
                except Exception:
                    pass

    def reconcile():
        design = m._design()
        if design is None:
            return

        # First recover any original opacity records left by a previous interrupted
        # session, then apply today's authoritative visual target.
        try:
            recover = getattr(m, "_recover_visual_opacity", None)
            if recover is not None:
                recover()
        except Exception:
            pass
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
