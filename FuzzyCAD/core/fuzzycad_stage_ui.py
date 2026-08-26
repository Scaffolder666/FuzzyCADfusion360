"""Left-side FuzzyCAD tool rail and lightweight multi-stage feedback.

Procedural progress (what to select/do next) belongs in the tool rail, while
actual design decisions continue to use the separate FuzzyCAD decision popup.
This keeps Axis Rotate and other staged tools legible without adding more Fusion
command-panel UI.

The stage card also exposes an optional Confirm action. Confirm simply ends the
currently active FuzzyCAD/Fusion command through the same deferred main-thread
termination path used when switching tools. Switching directly to another tool
still works exactly as before and remains the fastest path.
"""

import json


def install(m):
    adsk = m.adsk
    old_ensure_palettes = m._ensure_palettes
    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run

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

    m._set_tool_stage = send_stage

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

    def stage_for(tool, inputs=None, cid=None):
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

    class StageInput(adsk.core.InputChangedEventHandler):
        def __init__(self, tool):
            super().__init__(); self.tool = tool
        def notify(self, args):
            try:
                stage_for(self.tool, args.inputs, args.input.id)
            except Exception:
                log("stage input failed\n{}".format(m.traceback.format_exc()))

    class StageDestroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            send_stage(None, [], None, "")

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            try:
                stage_for(self.cmd, args.command.commandInputs)
                h = StageInput(self.cmd)
                args.command.inputChanged.add(h); m._handlers.append(h)
                d = StageDestroy()
                args.command.destroy.add(d); m._handlers.append(d)
            except Exception:
                log("stage setup failed tool={}\n{}".format(self.cmd, m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

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
            send_stage(None, [], None, "")
        except Exception:
            pass
        log("STAGE UI READY: left tool rail + procedural progress feedback")
        return result

    m.run = run
