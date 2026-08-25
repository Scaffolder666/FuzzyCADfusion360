"""The single visual authority for FuzzyCAD: one reconcile pass that keeps the
3D viewport in sync with the actual open questions.

Appearance tokens (colours, stroke weights, wobble) live in
fuzzycad_visual_system; per-body ghost opacity is recorded by
fuzzycad_opacity_runtime. This module is the lifecycle authority that runs after
every redraw and, from the single source of truth (the open marks + the live
design), fixes the three ways the viewport used to drift:

1) Stuck ghost bodies. Ghosting writes BRepBody.opacity and is meant to be
   restored when a mark goes away. A rebuild (or a reopen of a document saved
   while a mark was open) can leave a body semi-transparent with no open question
   referencing it. reclaim_orphan_ghosts restores any such body to full opacity,
   using a window derived from the real ghost value.

2) Orphan interaction graphics. Previews, hover tints, and highlights are drawn
   into custom-graphics groups by many tools. When a command is interrupted the
   group is not always cleared, so a stray badge / hover glow / highlight lingers.
   Every such EPHEMERAL group is registered here and swept whenever no FuzzyCAD
   command is running -- decided from Fusion's activeCommand (ground truth), not
   our own flags, since a leaked flag is what used to strand the graphics.

3) Persistent groups are deliberately left alone: GROUP_MARKS (badges + open-mark
   proposals, redrawn from the marks), the silhouette overlay, and Compare's shown
   option (redrawn while its mark is open).

Runs only on discrete redraws (never per animation frame), so the allComponents
scan is not on any hot path. Loaded after the tool/visual layers so its redraw
wrapper is outermost and reconciles once everything else has drawn.
"""


def install(m):
    old_redraw = m._redraw_marks
    old_run = m.run

    # Reclaim window derived from the ACTUAL ghost opacity FuzzyCAD applies
    # (fuzzycad_opacity_runtime ghosts with GHOST_OPACITY, currently 0.5). The old
    # fixed 0.03..0.34 window predated the 0.5 ghost and silently excluded it, so a
    # body left ghosted after a reject/reopen was never reclaimed. The window now
    # tracks the ghost value with a small margin on each side; only bodies with no
    # open question (see desired_ghost_tokens) inside it are restored to full.
    ghost = float(getattr(m, "GHOST_OPACITY", 0.5))
    LO = 0.03
    HI = min(0.70, ghost + 0.10)

    # ---- ephemeral graphics registry --------------------------------------
    # Every FuzzyCAD custom-graphics group that is an interaction/preview overlay
    # and MUST be empty whenever no FuzzyCAD command is running. Persistent groups
    # are deliberately excluded: GROUP_MARKS (badges + open-mark proposals, kept in
    # sync by _redraw_marks), "FuzzyCAD_Silhouette" (a standing overlay owned by the
    # silhouette layer), and Compare's shown option (drawn on every _redraw_marks
    # while its mark is open). Listing the ephemeral groups here in one place lets
    # any tool draw into one without owning its cleanup -- the authority sweeps them.
    EPHEMERAL_GROUPS = [
        getattr(m, "GROUP_PREVIEW", "FuzzyCAD_Preview"),
        "FuzzyCAD_DepCheck",              # scale/extrude dependency check tint
        "FuzzyCAD_FollowHighlight",       # dependent-follow highlight
        "FuzzyCAD_HoverAnimation",        # move hover
        "FuzzyCAD_HoverDirectionArrow",   # move hover arrow
        "FuzzyCAD_OperationHover",        # operation hover
        "FuzzyCAD_CompareConnectorPreview",  # compare pick preview
    ]
    # Exposed so a new tool can register its own ephemeral group without editing
    # this module: m._EPHEMERAL_GROUPS.append("FuzzyCAD_MyPreview").
    m._EPHEMERAL_GROUPS = EPHEMERAL_GROUPS

    def fuzzy_command_running():
        """Ground truth from Fusion: is a FuzzyCAD command the active command?

        Preferred over our own _active_cmd / _pending flags because a leaked flag
        is exactly what used to strand preview graphics on screen. Every FuzzyCAD
        command id starts with 'FuzzyCAD_'; anything else (SelectCommand, a native
        Fusion tool, nothing) means no FuzzyCAD preview legitimately owns the
        ephemeral groups."""
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

    def reconcile():
        design = m._design()
        if design is None:
            return
        # When no FuzzyCAD command is running, no interaction/preview overlay is
        # legitimate -- sweep every ephemeral group so a stray badge, hover tint,
        # or highlight from an interrupted command cannot linger.
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
        # After hydration/startup, sweep once so a document reopened with stale
        # ghost opacity from a previous session comes back clean.
        try:
            reconcile()
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m.run = run
