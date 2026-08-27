"""Always-on flow trace for the manual risk sweep.

Logs every palette action and every toolbar command lifecycle EDGE to the shared
crash log (`m._crash_trace` -> %TEMP%/fuzzycad_crash.log), so running each command
once by hand produces one readable timeline we can review together.

Edge-only by design: nothing here runs on a per-frame executePreview /
inputChanged / animation path, so it adds no drag-time file I/O and cannot cause
the very lag/instability we are hunting. It is installed OUTERMOST (last) so it
sees every palette action before any inner handler can consume it, and pairs with
the traces already emitted by fuzzycad_safe_confirm.py and the reopen edit module.
"""

import json


def install(m):
    adsk = m.adsk
    CurrentCreated = m.FuzzyCommandCreated
    CurrentPalette = m.PaletteHTMLHandler
    old_run = m.run

    def trace(event, detail=""):
        fn = getattr(m, "_crash_trace", None)
        if fn is not None:
            try:
                fn(event, detail)
                return
            except Exception:
                pass
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD FLOW] {} {}".format(event, detail))
        except Exception:
            pass

    m._flow_trace = trace

    def compact(data):
        try:
            keys = ("id", "tool", "key", "value", "kind")
            return " ".join(
                "{}={}".format(k, data[k]) for k in keys if k in data)
        except Exception:
            return ""

    class TraceExecute(adsk.core.CommandEventHandler):
        def __init__(self, tool):
            super().__init__()
            self.tool = tool

        def notify(self, args):
            trace("TOOL_OK", "tool={} active_cmd={}".format(
                self.tool, getattr(m, "_active_cmd", None)))

    class TraceDestroy(adsk.core.CommandEventHandler):
        def __init__(self, tool):
            super().__init__()
            self.tool = tool

        def notify(self, args):
            trace("TOOL_CLOSE", "tool={} active_cmd={} active_edit={}".format(
                self.tool, getattr(m, "_active_cmd", None),
                getattr(m, "_active_edit_id", None)))

    class FuzzyCommandCreated(CurrentCreated):
        def notify(self, args):
            trace("TOOL_OPEN", "tool={}".format(getattr(self, "cmd", "?")))
            super().notify(args)
            # Attach trace-only Execute/Destroy handlers AFTER the real ones, so
            # they log the resulting state once the real handlers have run.
            try:
                tool = getattr(self, "cmd", "?")
                te = TraceExecute(tool)
                args.command.execute.add(te)
                m._handlers.append(te)
                td = TraceDestroy(tool)
                args.command.destroy.add(td)
                m._handlers.append(td)
            except Exception:
                pass

    m.FuzzyCommandCreated = FuzzyCommandCreated

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPalette()

        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                data = {}
                try:
                    data = json.loads(e.data) if e.data else {}
                except Exception:
                    data = {}
                trace("ACTION", "{} {}".format(e.action, compact(data)))
            except Exception:
                pass
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        try:
            trace("FLOW_TRACE_READY", "manual-sweep logging on")
        except Exception:
            pass
        return result

    m.run = run
