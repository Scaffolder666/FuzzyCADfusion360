"""Crash-safe left-rail Confirm for reopened proposal edits.

The legacy rail used a custom event whose handler called
UserInterface.terminateActiveCommand(). That path has hard-crashed Fusion during
reopened card edits. For edit_existing only, intercept Confirm before the legacy
handler and defer a Command.doExecute(True) call to the main thread instead.
Fusion documents doExecute as the programmatic equivalent of clicking the
command dialog's OK button, so the command's own Execute/Destroy lifecycle stays
in control.

All other Confirm flows delegate unchanged; this patch is intentionally narrow.
"""

EVENT_ID = "FuzzyCADSafeReopenConfirm"


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    def trace(event, detail=""):
        try:
            fn = getattr(m, "_crash_trace", None)
            if fn is not None:
                fn(event, detail)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD SAFE CONFIRM] {} {}".format(event, detail))
        except Exception:
            pass

    def active_command():
        """Resolve the active Command from the current CommandInputs on demand."""
        try:
            inputs = getattr(m, "_inputs", None)
            if inputs is None:
                return None
            cmd = inputs.command
            if cmd is None:
                return None
            try:
                if not bool(cmd.isValid):
                    return None
            except Exception:
                pass
            return cmd
        except Exception:
            return None

    class FinishReopen(adsk.core.CustomEventHandler):
        def notify(self, args):
            # The request may arrive after another command has already taken over.
            # Never execute a command we no longer own.
            if getattr(m, "_active_cmd", None) != "edit_existing":
                trace("SAFE_CONFIRM_STALE", "active_cmd={}".format(
                    getattr(m, "_active_cmd", None)))
                return

            mid = getattr(m, "_active_edit_id", None)
            cmd = active_command()
            if cmd is None:
                trace("SAFE_CONFIRM_NO_COMMAND", "active_edit={}".format(mid))
                return

            trace("SAFE_CONFIRM_DOEXECUTE_BEGIN", "active_edit={}".format(mid))
            try:
                ok = bool(cmd.doExecute(True))
                trace("SAFE_CONFIRM_DOEXECUTE_RETURN", "active_edit={} ok={}".format(mid, ok))
            except Exception:
                try:
                    trace("SAFE_CONFIRM_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    trace("SAFE_CONFIRM_EXCEPTION", "traceback unavailable")

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
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

            # Only replace the risky reopen-edit Confirm path. New-tool Confirm,
            # Compare, Rough, etc. keep their existing behavior for now.
            if action == "confirm" and getattr(m, "_active_cmd", None) == "edit_existing":
                trace(
                    "RAIL_CONFIRM_SAFE",
                    "active_edit={} active_cmd=edit_existing".format(
                        getattr(m, "_active_edit_id", None)))
                try:
                    m._app.fireCustomEvent(EVENT_ID, "")
                except Exception:
                    try:
                        trace("SAFE_CONFIRM_FIRE_EXCEPTION", m.traceback.format_exc())
                    except Exception:
                        pass
                return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        try:
            m._app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(EVENT_ID)
            h = FinishReopen()
            evt.add(h)
            m._handlers.append(h)
            trace("SAFE_CONFIRM_READY", EVENT_ID)
        except Exception:
            try:
                trace("SAFE_CONFIRM_REGISTER_EXCEPTION", m.traceback.format_exc())
            except Exception:
                pass
        return result

    def stop(context):
        try:
            m._app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
