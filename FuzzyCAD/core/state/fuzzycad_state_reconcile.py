"""Single viewport reconciliation pass for FuzzyCAD.

Lifecycle policy is not defined here. The central visual authority supplies both
comic visibility and source-body opacity targets. This module only repairs drift
and clears stale ephemeral interaction graphics.

Ordinary redraw is intentionally targeted: it re-applies authoritative visual
state but does NOT scan every body in every component. The full-document orphan
opacity sweep is a recovery operation reserved for startup and explicit Repair.
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

    # Any body faded below this is treated as leftover FuzzyCAD display state
    # during a full recovery. A resolved comic/rough body can persist in the saved
    # document at COMIC_SOURCE_OPACITY (~0.02) -- it then reads as a see-through
    # wireframe "frame" even without the add-in loaded, because body opacity is a
    # saved property. Matching only the exact known values missed bodies whose
    # reloaded opacity drifted or was written by an older build, so recovery now
    # restores ANY orphan (no open mark wants it faded) that is not solid.
    ORPHAN_FADE_CEILING = 0.95

    def reclaim_orphan_visual_opacity(design):
        """Full-document recovery for stale FuzzyCAD display opacity.

        This is intentionally NOT part of ordinary redraw. It walks every body in
        every component, so it belongs to startup recovery and Inspector Repair.
        Fillet/Hole Editing remain in desired_visual_tokens, so their intentional
        0.50 source body is never reclaimed.
        """
        want = desired_visual_tokens()
        restore = getattr(m, "_restore_orphan_visual_body", None)
        restored = 0
        for body in iter_live_bodies(design):
            btok = tok(body)
            if btok in want:
                continue
            try:
                op = float(body.opacity)
            except Exception:
                continue
            # No open mark wants this body faded. If it is not solid, it is
            # leftover FuzzyCAD display state -- restore it to opaque.
            if op >= ORPHAN_FADE_CEILING:
                continue
            if restore is not None:
                try:
                    if restore(body):
                        restored += 1
                        continue
                except Exception:
                    pass
            try:
                body.opacity = 1.0
                restored += 1
            except Exception:
                pass
        return restored

    def reconcile(full_scan=False):
        design = m._design()
        if design is None:
            return

        # Token-based crash recovery and authoritative opacity sync are bounded by
        # FuzzyCAD-owned records/marks, so they are safe on an ordinary redraw.
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

    # Resolving a mark (Accept/Reject/Delete) is a discrete, non-drag event. After
    # the mark's status flips, its subject body is no longer in desired_visual_
    # tokens(), so a leftover comic/semi-transparent opacity must be reverted to
    # the body's real appearance. The runtime opacity record can be gone here (a
    # reopened/crash-recovered document starts with empty records and the mark's
    # live body handle may not be resolvable), so instead of trusting a single
    # cached token we run the bounded orphan reclaim over the live bodies. This is
    # what makes "reject/accept a Rough Shape leaves the body a see-through frame"
    # actually restore the body's colour instead of stranding it at ~0.02 opacity.
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
            # Startup is the right place for the expensive legacy/orphan sweep.
            reconcile(True)
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m.run = run
