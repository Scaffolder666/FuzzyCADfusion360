"""Single viewport reconciliation pass for FuzzyCAD.

Lifecycle policy is not defined here. The central visual authority supplies both
comic visibility and source-body opacity targets. This module repairs drift and
clears stale ephemeral interaction graphics.

Opacity ownership is exact: reconciliation may restore a body only when the
opacity runtime has a captured FuzzyCAD original for it. It never infers that a
user-authored transparent body is stale from the numeric opacity alone.
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
        """Restore only bodies explicitly owned by FuzzyCAD opacity bookkeeping."""
        restore = getattr(m, "_restore_orphan_visual_body", None)
        if restore is None:
            return 0
        restored = 0
        for body in iter_live_bodies(design):
            try:
                if restore(body):
                    restored += 1
            except Exception:
                pass
        return restored

    def reconcile(full_scan=False):
        design = m._design()
        if design is None:
            return

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

        if full_scan:
            try:
                reclaim_orphan_visual_opacity(design)
            except Exception:
                pass

    m._reconcile_viewport = reconcile
    m._reclaim_orphan_visual_opacity = reclaim_orphan_visual_opacity

    def reclaim_on_resolve(reason):
        try:
            design = m._design()
            if design is not None:
                reclaim_orphan_visual_opacity(design)
        except Exception:
            pass

    old_accept = getattr(m, "_accept", None)
    if old_accept is not None:
        def accept(mark, *args, **kwargs):
            result = old_accept(mark, *args, **kwargs)
            reclaim_on_resolve("accept")
            return result
        m._accept = accept

    old_remove_mark = getattr(m, "_remove_mark", None)
    if old_remove_mark is not None:
        def remove_mark(mid, *args, **kwargs):
            result = old_remove_mark(mid, *args, **kwargs)
            reclaim_on_resolve("remove")
            return result
        m._remove_mark = remove_mark

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            reconcile(False)
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
            reconcile(True)
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m.run = run
