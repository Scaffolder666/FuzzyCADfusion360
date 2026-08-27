"""Crash-safe finishing for reopened proposal edits.

Fusion has hard-crashed when an active reopened card edit is closed through the
legacy UserInterface.terminateActiveCommand() path. Reopened Confirm, Accept,
and Reject therefore finish the native edit through Command.doExecute(True),
which lets the command own its normal Execute/Destroy lifecycle.

Confirm only closes the edit and leaves the proposal unresolved. For Accept or
Reject, the edit is closed first; only after Fusion has returned from doExecute do
we commit/remove the mark. This avoids deleting a proposal while its native
manipulator command is still alive.

The custom event carries only plain JSON data. No Fusion event/command wrapper is
retained across the palette -> main-thread boundary.
"""

import json

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
                "[FuzzyCAD SAFE FINISH] {} {}".format(event, detail))
        except Exception:
            pass

    def active_command():
        """Resolve the active Command from current inputs on demand."""
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

    def request_finish(reason="confirm", mid=None):
        payload = {"reason": str(reason or "confirm")}
        if mid is not None:
            try:
                payload["id"] = int(mid)
            except Exception:
                pass
        try:
            m._app.fireCustomEvent(EVENT_ID, json.dumps(payload))
            return True
        except Exception:
            try:
                trace("SAFE_FINISH_FIRE_EXCEPTION", m.traceback.format_exc())
            except Exception:
                pass
            return False

    def resolve_terminal(action, mid):
        """Resolve a card only after its native edit command is fully closed."""
        try:
            mid = int(mid)
        except Exception:
            trace("SAFE_TERMINAL_BAD_ID", "action={} id={}".format(action, mid))
            return False

        mark = None
        try:
            mark = m._find(mid)
        except Exception:
            pass
        if mark is None:
            trace("SAFE_TERMINAL_MARK_GONE", "action={} id={}".format(action, mid))
            return True

        trace("SAFE_TERMINAL_RESOLVE_BEGIN", "action={} id={} tool={}".format(
            action, mid, mark.get("tool")))
        try:
            if action == "accept":
                ok = True if mark.get("tool") == "note" else bool(m._accept(mark))
                if not ok:
                    trace("SAFE_TERMINAL_ACCEPT_FAILED", "id={}".format(mid))
                    return False
            elif action != "reject":
                return False

            # The edit command is already destroyed here, so _remove_mark's
            # persistence wrapper is allowed to save immediately.
            m._remove_mark(mid)

            try:
                cancel = getattr(m, "_animation_cancel", None)
                if cancel is not None:
                    cancel("terminal:" + action, refresh=False)
            except Exception:
                pass
            try:
                clear_reveal = getattr(m, "_visual_clear_revealed", None)
                if clear_reveal is not None:
                    clear_reveal(mid, hover_only=False)
            except Exception:
                pass

            # Re-enter the authoritative render pipeline with the mark absent.
            # This restores original opacity/material presentation and removes
            # comic/preview groups without retaining any stale native wrapper.
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
                    persist("safe-" + action)
            except Exception:
                pass
            try:
                if m._app and m._app.activeViewport:
                    m._app.activeViewport.refresh()
            except Exception:
                pass

            trace("SAFE_TERMINAL_RESOLVE_DONE", "action={} id={}".format(action, mid))
            return True
        except Exception:
            try:
                trace("SAFE_TERMINAL_EXCEPTION", m.traceback.format_exc())
            except Exception:
                trace("SAFE_TERMINAL_EXCEPTION", "traceback unavailable")
            return False

    class FinishReopen(adsk.core.CustomEventHandler):
        def notify(self, args):
            reason = "confirm"
            requested_mid = None
            try:
                raw = args.additionalInfo or ""
                data = json.loads(raw) if raw else {}
                reason = str(data.get("reason") or "confirm")
                requested_mid = data.get("id")
            except Exception:
                pass

            # The request may arrive after another command has already taken over.
            # Never execute a command we no longer own.
            if getattr(m, "_active_cmd", None) != "edit_existing":
                trace("SAFE_FINISH_STALE", "reason={} active_cmd={}".format(
                    reason, getattr(m, "_active_cmd", None)))
                return

            mid = getattr(m, "_active_edit_id", None)
            if requested_mid is not None:
                try:
                    if int(requested_mid) != int(mid):
                        trace("SAFE_FINISH_STALE_ID", "reason={} requested={} active={}".format(
                            reason, requested_mid, mid))
                        return
                except Exception:
                    pass

            cmd = active_command()
            if cmd is None:
                trace("SAFE_FINISH_NO_COMMAND", "reason={} active_edit={}".format(reason, mid))
                return

            trace("SAFE_FINISH_DOEXECUTE_BEGIN", "reason={} active_edit={}".format(reason, mid))
            try:
                ok = bool(cmd.doExecute(True))
                trace("SAFE_FINISH_DOEXECUTE_RETURN", "reason={} active_edit={} ok={}".format(
                    reason, mid, ok))
            except Exception:
                try:
                    trace("SAFE_FINISH_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    trace("SAFE_FINISH_EXCEPTION", "traceback unavailable")
                return

            if reason in ("accept", "reject"):
                # doExecute is synchronous in the tested Fusion lifecycle: Destroy
                # arrives before this return. Still guard against resolving while
                # the edit command claims ownership if a build behaves differently.
                if getattr(m, "_active_cmd", None) == "edit_existing":
                    trace("SAFE_TERMINAL_DEFERRED_NOT_CLOSED", "action={} id={}".format(reason, mid))
                    return
                resolve_terminal(reason, mid)

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            active = getattr(m, "_active_cmd", None) == "edit_existing"

            # Reopened Confirm: finish through the command's own OK lifecycle.
            if action == "confirm" and active:
                trace(
                    "RAIL_CONFIRM_SAFE",
                    "active_edit={} active_cmd=edit_existing".format(
                        getattr(m, "_active_edit_id", None)))
                request_finish("confirm", getattr(m, "_active_edit_id", None))
                return

            # Accept/Reject on the card currently being edited used to delegate
            # first and then fire the legacy terminateActiveCommand event. The log
            # shows Fusion dying exactly on that edge. Close safely first, then
            # resolve the plain mark after Destroy.
            if action in ("accept", "reject") and active:
                try:
                    tid = int(data.get("id"))
                    aid = int(getattr(m, "_active_edit_id", None))
                except Exception:
                    tid = aid = None
                if tid is not None and tid == aid:
                    trace("CARD_TERMINAL_SAFE", "action={} id={}".format(action, tid))
                    request_finish(action, tid)
                    return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._safe_finish_reopen = request_finish

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
