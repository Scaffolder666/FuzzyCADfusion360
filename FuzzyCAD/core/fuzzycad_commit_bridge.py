"""Commit FuzzyCAD proposals in their own Fusion command transaction.

Why this exists:
A proposal is created while a long-running FuzzyCAD tool command is active.
Accept used to call _accept directly from the HTML palette. Fusion therefore
placed the real Move/Rotate/Scale/Extrude/Fillet feature inside the still-active
tool command transaction. The model looked correct immediately after Accept,
but terminating that tool later aborted its transaction and rolled the feature
back.

The bridge makes Accept asynchronous:
  palette Accept -> fire FuzzyCAD_Commit custom event
  -> LaunchHandler terminates the preview/tool command (safe: it only owns
     proposal UI/custom graphics)
  -> start a tiny no-input FuzzyCAD_Commit command
  -> apply the real geometry from that command's execute event
  -> remove the mark and let Fusion finish/commit that transaction normally.
"""

import json
import time

COMMIT_CMD_ID = "FuzzyCAD_Commit"
COMMIT_CMD_NAME = "FuzzyCAD Commit"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    OriginalPaletteHTMLHandler = m.PaletteHTMLHandler

    m._pending_commit_id = None

    def dbg(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            app = m._app or adsk.core.Application.get()
            app.log("[FuzzyCAD COMMIT] " + msg)
        except Exception:
            pass

    def hide_live_inputs():
        """Immediately make a palette Accept feel resolved while the bridge
        switches commands. The old command will be terminated by LaunchHandler
        a moment later, but the user should not be able to keep dragging it."""
        inputs = getattr(m, "_inputs", None)
        if inputs is None:
            return
        for cid in ("mX", "mY", "mZ", "rX", "rY", "rZ", "sc", "d"):
            try:
                it = inputs.itemById(cid)
                if it is not None:
                    it.isVisible = False
                    it.isEnabled = False
            except Exception:
                pass

    def study_values(mark):
        """Return the same compact, human-readable values used by the study log."""
        if not mark or mark.get("tool") == "compare":
            return {}
        out = {}
        try:
            fields = m._fields(mark) or []
        except Exception:
            fields = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key", "") or "")
            if not key:
                continue
            value = field.get("value")
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                value = str(value)
            out[key] = {
                "label": str(field.get("label", key)),
                "value": value,
                "unit": str(field.get("unit", "") or ""),
            }
        return out

    def log_study_accept(mark):
        """Record Accept only after the dedicated Fusion commit succeeds.

        Palette Accept is asynchronous, so the outer study logger cannot know
        during the HTML callback whether the commit later succeeded. The commit
        command is the authoritative success point; append the study event here.
        """
        try:
            study = getattr(m, "_study_logger_state", None)
            if not isinstance(study, dict) or not study.get("active"):
                return
            mid = mark.get("id")
            # Defensive de-duplication in case another future logger layer also
            # records this successful commit.
            for row in reversed(study.get("events") or []):
                if row.get("event") == "proposal_accepted" and row.get("mark_id") == mid:
                    return
            started = study.get("started_mono")
            elapsed = 0.0 if started is None else max(0.0, time.monotonic() - started)
            study.setdefault("events", []).append({
                "event": "proposal_accepted",
                "elapsed_sec": round(elapsed, 3),
                "mark_id": mid,
                "tool": mark.get("tool"),
                "type": mark.get("mtype", "need_input"),
                "values": study_values(mark),
            })
            try:
                study.get("last_values", {}).pop(int(mid), None)
            except Exception:
                pass
            try:
                study.get("pending", {}).pop(int(mid), None)
            except Exception:
                pass
        except Exception:
            # Study instrumentation must never affect the commit path.
            pass

    class CommitExecute(adsk.core.CommandEventHandler):
        def notify(self, args):
            mid = getattr(m, "_pending_commit_id", None)
            if mid is None:
                dbg("COMMIT EXECUTE with no pending mark")
                return

            mark = m._find(mid)
            if mark is None:
                dbg("COMMIT EXECUTE mark={} missing; nothing to apply".format(mid))
                m._pending_commit_id = None
                return

            tool = mark.get("tool")
            dbg("COMMIT EXECUTE BEGIN mark={} tool={}".format(mid, tool))
            try:
                ok = True if tool == "note" else m._accept(mark)
                if not ok:
                    dbg("COMMIT EXECUTE FAILED mark={} tool={} (mark kept open)".format(mid, tool))
                    return

                # The geometry mutation above happened inside THIS command's
                # execute event, so Fusion owns it in this command transaction.
                # Study logging belongs here too: this is the first point at which
                # we know the asynchronous Accept actually succeeded.
                log_study_accept(mark)
                m._remove_mark(mid)
                try:
                    m._clear(m.GROUP_PREVIEW)
                except Exception:
                    pass
                m._redraw_marks()
                m._send_state()
                dbg("COMMIT EXECUTE SUCCESS mark={} tool={} (dedicated transaction)".format(mid, tool))
            except Exception:
                dbg("COMMIT EXECUTE EXCEPTION mark={} tool={}\n{}".format(
                    mid, tool, m.traceback.format_exc()))
            finally:
                # If apply failed, the mark remains in _marks and can be edited
                # or accepted again; only clear the one-shot bridge request.
                m._pending_commit_id = None

    class CommitCreated(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                cmd = args.command
                # No command inputs. With isAutoExecute=True Fusion immediately
                # fires execute and then completes the command transaction.
                cmd.isAutoExecute = True
                h = CommitExecute()
                cmd.execute.add(h)
                m._handlers.append(h)
                dbg("COMMIT COMMAND CREATED pending_mark={}".format(
                    getattr(m, "_pending_commit_id", None)))
            except Exception:
                dbg("COMMIT COMMAND CREATE FAILED\n{}".format(m.traceback.format_exc()))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = OriginalPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            if action != "accept":
                self._delegate.notify(args)
                return

            mid = data.get("id")
            mark = m._find(mid) if mid is not None else None
            if mark is None:
                dbg("PALETTE ACCEPT ignored: mark={} missing".format(mid))
                return

            if getattr(m, "_pending_commit_id", None) is not None:
                dbg("PALETTE ACCEPT ignored: commit already pending for mark={}".format(
                    m._pending_commit_id))
                return

            m._pending_commit_id = mid
            hide_live_inputs()
            try:
                m._clear(m.GROUP_PREVIEW)
                m._app.activeViewport.refresh()
            except Exception:
                pass

            dbg("PALETTE ACCEPT QUEUED mark={} tool={} -> {}".format(
                mid, mark.get("tool"), COMMIT_CMD_ID))
            try:
                # Use the add-in's existing custom-event bridge so command
                # switching happens on Fusion's main thread. LaunchHandler first
                # terminates the preview tool, then executes FuzzyCAD_Commit.
                m._app.fireCustomEvent(m.LAUNCH_EVENT_ID, COMMIT_CMD_ID)
            except Exception:
                m._pending_commit_id = None
                dbg("PALETTE ACCEPT could not queue commit\n{}".format(
                    m.traceback.format_exc()))

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def ensure_commit_definition():
        ui = m._ui
        if ui is None:
            return
        cd = ui.commandDefinitions.itemById(COMMIT_CMD_ID)
        if cd is not None:
            try:
                cd.deleteMe()
            except Exception:
                pass
        cd = ui.commandDefinitions.addButtonDefinition(
            COMMIT_CMD_ID, COMMIT_CMD_NAME,
            "Internal command used to commit a FuzzyCAD proposal", "")
        h = CommitCreated()
        cd.commandCreated.add(h)
        m._handlers.append(h)

    def run(context):
        result = old_run(context)
        try:
            ensure_commit_definition()
            dbg("dedicated commit transaction bridge ready")
        except Exception:
            dbg("commit bridge startup failed\n{}".format(m.traceback.format_exc()))
        return result

    def stop(context):
        try:
            if m._ui:
                cd = m._ui.commandDefinitions.itemById(COMMIT_CMD_ID)
                if cd is not None:
                    cd.deleteMe()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop