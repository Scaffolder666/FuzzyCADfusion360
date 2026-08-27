"""Crash-safe finishing for reopened proposal edits.

Fusion has hard-crashed when an active reopened card edit is closed through the
legacy UserInterface.terminateActiveCommand() path. Reopened Confirm, Accept,
and Reject therefore finish the native edit through Command.doExecute(True),
which lets the command own its normal Execute/Destroy lifecycle.

Confirm only closes the edit and leaves the proposal unresolved. For Accept or
Reject, the edit is closed first; only after Fusion has returned from doExecute do
we commit/remove the mark. This avoids deleting a proposal while its native
manipulator command is still alive.

Before finishing, this owner also clears temporary replay/reveal state. The
reopened Confirm palette action is intercepted here and therefore does not reach
older palette wrappers; without this explicit cleanup, the mark could return to
Proposed while still carrying the persistent-detail reveal flag, which looked
like leftover Editing lines over the comic baseline.

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

    def sync_fillet_exact(reason, mid=None):
        """Reconcile the cached translucent Fillet candidate with mark lifecycle.

        The exact Fillet solid lives in its own runtime graphics group. The new
        central persistent renderer intentionally bypasses the old redraw-wrapper
        chain, so terminal card actions must explicitly let the Fillet layer hide
        or delete that group. This function never rebuilds the kernel candidate;
        it only reconciles the already-cached graphics against current phase/marks.
        """
        try:
            fn = getattr(m, "_sync_fillet_solids", None)
            if fn is None:
                return False
            fn()
            trace("FILLET_EXACT_SYNC", "reason={} id={}".format(reason, mid))
            return True
        except Exception:
            try:
                trace("FILLET_EXACT_SYNC_EXCEPTION", m.traceback.format_exc())
            except Exception:
                pass
            return False

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

    def prepare_visual_finish(reason, mid):
        """Remove ephemeral Editing-only state before the native command closes.

        safe_confirm is the outer palette owner for a reopened Confirm, so the
        intercepted action never reaches progressive_visibility's normal Confirm
        cleanup. Do that cleanup here, before Destroy redraws the mark as Proposed.
        """
        try:
            cancel = getattr(m, "_animation_cancel", None)
            if cancel is not None:
                cancel("safe-finish:" + str(reason), refresh=False)
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
        trace(
            "SAFE_FINISH_VISUAL_PREP",
            "reason={} active_edit={}".format(reason, mid))

    def verify_confirm_visual(mid):
        """Validate the Proposed baseline after Fusion has fully closed the command."""
        # Destroy normally clears GROUP_PREVIEW, but clear it once more after
        # doExecute returns so a late native preview cannot survive the handoff.
        try:
            m._clear(m.GROUP_PREVIEW)
        except Exception:
            pass

        # Fillet's translucent exact BRep is an editing-only layer. Reconcile it
        # after ownership is gone so Confirm immediately hides it in Proposed.
        try:
            mark = m._find(mid) if mid is not None else None
        except Exception:
            mark = None
        if mark is not None and mark.get("tool") == "fillet":
            sync_fillet_exact("confirm", mid)

        repaired = False
        try:
            repair = getattr(m, "_repair_comic_integrity", None)
            if repair is not None:
                repaired = bool(repair("confirm-post-command"))
        except Exception:
            pass
        trace(
            "SAFE_FINISH_VISUAL_VERIFY",
            "active_edit={} repaired={}".format(mid, repaired))

    def close_active_edit_sync(reason="switch"):
        """Close an active reopened proposal edit synchronously via doExecute(True).

        This is the crash-safe replacement for UserInterface.terminateActiveCommand()
        when the active command is the native manipulator edit (`edit_existing`) --
        terminating that hard-crashes Fusion (ARCHITECTURE.md §5). Safe to call from
        inside a CustomEventHandler (the same context this module's doExecute runs in);
        do NOT call it directly from an HTML event (defer through request_finish there).

        Returns True if an edit was active and closed; False if no edit was active
        (the caller should then fall back to its normal path).
        """
        if getattr(m, "_active_cmd", None) != "edit_existing":
            return False
        cmd = active_command()
        if cmd is None:
            return False
        mid = getattr(m, "_active_edit_id", None)
        trace("CLOSE_EDIT_SYNC_BEGIN", "reason={} active_edit={}".format(reason, mid))
        prepare_visual_finish(reason, mid)
        try:
            cmd.doExecute(True)
        except Exception:
            try:
                trace("CLOSE_EDIT_SYNC_EXCEPTION", m.traceback.format_exc())
            except Exception:
                trace("CLOSE_EDIT_SYNC_EXCEPTION", "traceback unavailable")
            return False
        if str(reason) == "confirm":
            verify_confirm_visual(mid)
        trace("CLOSE_EDIT_SYNC_DONE", "reason={} active_cmd={}".format(
            reason, getattr(m, "_active_cmd", None)))
        return True

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

        is_fillet = mark.get("tool") == "fillet"
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

            # The exact Fillet candidate is a separate runtime group; deleting the
            # mark must delete that group too, otherwise rolling back the committed
            # Fusion feature reveals a faint translucent filleted body in place.
            if is_fillet:
                sync_fillet_exact(action, mid)

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

            prepare_visual_finish(reason, mid)
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

            if reason == "confirm":
                # This runs after Destroy returned, catching graphics Fusion may
                # drop at the command-teardown boundary without another global redraw.
                verify_confirm_visual(mid)

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

            # Normal (already-Proposed) Fillet terminal actions pass through the
            # legacy resolver. Remember the tool before delegation, then reconcile
            # its separate exact-candidate group after the mark has been removed.
            terminal_fillet_id = None
            if action in ("accept", "reject"):
                try:
                    tid = int(data.get("id"))
                    mark = m._find(tid)
                    if mark is not None and mark.get("tool") == "fillet":
                        terminal_fillet_id = tid
                except Exception:
                    terminal_fillet_id = None

            self._delegate.notify(args)

            if terminal_fillet_id is not None:
                sync_fillet_exact(action, terminal_fillet_id)
                try:
                    if m._app and m._app.activeViewport:
                        m._app.activeViewport.refresh()
                except Exception:
                    pass

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._safe_finish_reopen = request_finish
    m._close_active_edit_sync = close_active_edit_sync

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
