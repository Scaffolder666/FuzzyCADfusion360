"""Left-side FuzzyCAD tool rail and lightweight multi-stage feedback.

Procedural progress (what to select/do next) belongs in the tool rail, while
actual design decisions continue to use the separate FuzzyCAD decision popup.
This keeps Axis Rotate and other staged tools legible without adding more Fusion
command-panel UI.

The stage card also exposes an optional Confirm action. Confirm ends the current
FuzzyCAD/Fusion command through the deferred main-thread termination path. When
Confirm closes a reopened card edit, this layer first forces the mark back into
the shared Proposed visual state so every tool returns to the same comic
baseline before Fusion delivers its later Destroy event.

This layer also owns toolbar-command lifecycle guards that do not change any
visual styling or geometry. Each input/preview/execute handler captures the
command session that created it and ignores late events after a newer command has
taken ownership. The existing Execute/Destroy rendering path remains intact; the
reopen finalizer below is an idempotent transition guard for the explicit Confirm
button only.
"""

import json


def install(m):
    adsk = m.adsk
    old_ensure_palettes = m._ensure_palettes
    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    CurrentFuzzyInputChanged = m.FuzzyInputChanged
    CurrentFuzzyPreview = m.FuzzyPreview
    CurrentFuzzyExecute = m.FuzzyExecute
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run

    # Which command/edit currently owns the contents of the left rail. This is
    # deliberately separate from viewport visuals. A new owner replaces the old
    # token before Fusion delivers the old command's late Destroy, so that stale
    # Destroy cannot clear the new rail.
    stage_state = {"owner": None}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD STAGE] " + msg)
        except Exception:
            pass

    def toolbar():
        try:
            return m._ui.palettes.itemById(m.TOOLBAR_ID) if m._ui else None
        except Exception:
            return None

    def send_stage(tool=None, steps=None, active=None, title=None):
        p = toolbar()
        if p is None:
            return
        payload = {
            "tool": tool,
            "title": title or "",
            "steps": steps or [],
            "active": active,
        }
        try:
            p.sendInfoToHTML("stage", json.dumps(payload))
        except Exception:
            pass

    def set_tool_stage(tool=None, steps=None, active=None, title=None):
        """External stage setter (e.g. card re-open).

        Calling this with a visible stage claims the rail for a fresh external
        owner. That makes a late Destroy from the toolbar command that preceded it
        harmless. Clearing the stage releases the owner. Payloads are unchanged.
        """
        if tool is None and not (steps or []):
            stage_state["owner"] = None
        else:
            stage_state["owner"] = object()
        send_stage(tool, steps, active, title)

    m._set_tool_stage = set_tool_stage

    def ensure_palettes():
        result = old_ensure_palettes()
        bar = toolbar()
        if bar is not None:
            try:
                bar.dockingState = m._dock_state("PaletteDockStateLeft")
            except Exception:
                try:
                    bar.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateLeft
                except Exception:
                    pass
            # Size hints are ignored by some Fusion builds after docking, but are
            # useful when the palette is first created or temporarily floating.
            try: bar.width = 190
            except Exception: pass
            try: bar.height = 640
            except Exception: pass
        return result

    m._ensure_palettes = ensure_palettes

    def sel_count(inputs, cid):
        try:
            it = inputs.itemById(cid)
            return it.selectionCount if it is not None else 0
        except Exception:
            return 0

    def stage_for(tool, inputs=None, cid=None, owner=None):
        if owner is not None:
            stage_state["owner"] = owner
        inputs = inputs or getattr(m, "_inputs", None)
        if tool == "axis_rotate":
            b = sel_count(inputs, "arb") > 0 if inputs else False
            a = sel_count(inputs, "ara") > 0 if inputs else False
            steps = [
                {"label": "Select body", "done": b},
                {"label": "Select rotation axis", "done": a, "hint": "circular edge"},
                {"label": "Drag to rotate", "done": False},
            ]
            active = 0 if not b else (1 if not a else 2)
            send_stage(tool, steps, active, "Axis Rotate")
            return

        if tool == "directional_scale":
            b = sel_count(inputs, "dsb") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select body", "done": b},
                {"label": "Choose direction, then drag", "done": False},
            ], 0 if not b else 1, "Scale X/Y/Z")
            return

        if tool == "transform":
            b = sel_count(inputs, "sel") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select body", "done": b},
                {"label": "Drag Move or Rotate", "done": False},
            ], 0 if not b else 1, "Move / Rotate")
            return

        if tool == "scale":
            b = sel_count(inputs, "sel") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select body", "done": b},
                {"label": "Drag corner handle", "done": False},
            ], 0 if not b else 1, "Scale All")
            return

        if tool == "extrude":
            b = sel_count(inputs, "sel") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select face", "done": b},
                {"label": "Drag depth", "done": False},
            ], 0 if not b else 1, "Extrude")
            return

        if tool == "fillet":
            b = sel_count(inputs, "sel") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select edge", "done": b},
                {"label": "Drag radius", "done": False},
            ], 0 if not b else 1, "Fillet")
            return

        if tool == "hole":
            b = sel_count(inputs, "sel") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select face", "done": b},
                {"label": "Set diameter & depth", "done": False, "hint": "both Need Input"},
            ], 0 if not b else 1, "Hole")
            return

        if tool == "rough":
            b = sel_count(inputs, "sel") > 0 if inputs else False
            send_stage(tool, [
                {"label": "Select the rough body", "done": b,
                 "hint": "whole shape is flagged — then Confirm"},
            ], 0, "Rough Shape")
            return

        send_stage(None, [], None, "")

    # ---- toolbar runtime ownership (no rendering policy changes) ----------
    def session_is_current(session):
        return session is None or session is getattr(m, "_cmd_session", None)

    class FuzzyInputChanged(CurrentFuzzyInputChanged):
        def __init__(self):
            super().__init__()
            self._fuzzy_session = getattr(m, "_cmd_session", None)

        def notify(self, args):
            if not session_is_current(self._fuzzy_session):
                return
            return super().notify(args)

    class FuzzyPreview(CurrentFuzzyPreview):
        def __init__(self):
            super().__init__()
            self._fuzzy_session = getattr(m, "_cmd_session", None)

        def notify(self, args):
            if not session_is_current(self._fuzzy_session):
                try:
                    args.isValidResult = True
                except Exception:
                    pass
                return
            return super().notify(args)

    class FuzzyExecute(CurrentFuzzyExecute):
        def __init__(self):
            super().__init__()
            self._fuzzy_session = getattr(m, "_cmd_session", None)

        def notify(self, args):
            if not session_is_current(self._fuzzy_session):
                return
            return super().notify(args)

    m.FuzzyInputChanged = FuzzyInputChanged
    m.FuzzyPreview = FuzzyPreview
    m.FuzzyExecute = FuzzyExecute

    # ---- left rail ownership ----------------------------------------------
    class StageInput(adsk.core.InputChangedEventHandler):
        def __init__(self, tool, owner):
            super().__init__(); self.tool = tool; self.owner = owner
        def notify(self, args):
            if stage_state.get("owner") is not self.owner:
                return
            try:
                stage_for(self.tool, args.inputs, args.input.id, self.owner)
            except Exception:
                log("stage input failed\n{}".format(m.traceback.format_exc()))

    class StageDestroy(adsk.core.CommandEventHandler):
        def __init__(self, owner):
            super().__init__(); self.owner = owner
        def notify(self, args):
            if stage_state.get("owner") is not self.owner:
                return
            stage_state["owner"] = None
            send_stage(None, [], None, "")

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            try:
                session = getattr(m, "_cmd_session", None)
                owner = session if session is not None else object()
                stage_for(self.cmd, args.command.commandInputs, owner=owner)
                h = StageInput(self.cmd, owner)
                args.command.inputChanged.add(h); m._handlers.append(h)
                d = StageDestroy(owner)
                args.command.destroy.add(d); m._handlers.append(d)
            except Exception:
                log("stage setup failed tool={}\n{}".format(self.cmd, m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    def force_reopened_proposed():
        """Make explicit Confirm an immediate Editing -> Proposed transition.

        The left-rail Confirm terminates Fusion's active command asynchronously.
        During a reopened card edit, waiting for that later Destroy left a race in
        which the badge survived but the source opacity/comic group could remain
        in the Editing presentation. Resolve the visual phase synchronously first.

        This is intentionally idempotent: the reopen command's normal Destroy will
        run afterward and perform its regular cleanup/redraw again.
        """
        if getattr(m, "_active_cmd", None) != "edit_existing":
            return False
        mid = getattr(m, "_active_edit_id", None)
        if mid is None:
            return False
        try:
            mid = int(mid)
        except Exception:
            pass

        mark = None
        try:
            mark = m._find(mid)
        except Exception:
            pass
        if mark is None or mark.get("status", "open") != "open":
            return False

        # _mark_phase() must see Proposed before any renderer is synchronized.
        # Clear a stale live owner too; reopened edits should normally use only
        # _active_edit_id, but this makes Confirm invariant across all tools.
        try:
            live = getattr(m, "_live", None)
            if isinstance(live, dict):
                for key, value in list(live.items()):
                    if value == mid:
                        live.pop(key, None)
        except Exception:
            pass
        m._active_edit_id = None

        try:
            cancel = getattr(m, "_animation_cancel", None)
            if cancel is not None:
                cancel("confirm", refresh=False)
        except Exception:
            pass
        try:
            clear_reveal = getattr(m, "_visual_clear_revealed", None)
            if clear_reveal is not None:
                clear_reveal(mid, hover_only=False)
        except Exception:
            pass
        try:
            m._clear(m.GROUP_PREVIEW)
        except Exception:
            pass

        # Redraw the card/badge/detail layer, then explicitly reconcile opacity
        # and comic graphics. Confirm is infrequent, so the small duplicate guard
        # cost is preferable to allowing a half-Editing/half-Proposed viewport.
        try:
            m._redraw_marks()
        except Exception:
            pass
        try:
            sync_opacity = getattr(m, "_sync_visual_opacity", None)
            if sync_opacity is not None:
                sync_opacity()
        except Exception:
            pass
        try:
            sync_comic = getattr(m, "_sync_comic_uncertainty", None)
            if sync_comic is not None:
                sync_comic()
        except Exception:
            pass
        try:
            m._send_state()
        except Exception:
            pass
        try:
            persist = getattr(m, "_persist_state", None)
            if persist is not None:
                persist("confirm-reopen-proposed")
        except Exception:
            pass
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass
        log("CONFIRM REOPEN -> PROPOSED mark={}".format(mid))
        return True

    m._force_reopened_proposed = force_reopened_proposed

    class StagePaletteHTMLHandler(adsk.core.HTMLEventHandler):
        """Adds a lightweight explicit finish path without changing tool switching.

        The existing LaunchHandler always terminates the active command before
        launching a requested tool. Firing that same custom event with an empty
        command id therefore means: terminate only, launch nothing.
        """
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
            except Exception:
                pass

            if action == "confirm":
                try:
                    force_reopened_proposed()
                except Exception:
                    pass
                try:
                    m._app.fireCustomEvent(m.LAUNCH_EVENT_ID, "")
                except Exception:
                    pass
                return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = StagePaletteHTMLHandler

    def run(context):
        result = old_run(context)
        try:
            ensure_palettes()
            stage_state["owner"] = None
            send_stage(None, [], None, "")
        except Exception:
            pass
        log("STAGE UI READY: left tool rail + session-safe lifecycle feedback")
        return result

    m.run = run
