"""Lightweight crash-survival tracing for the study/runtime build.

This is intentionally NOT a frame/performance logger. It records only coarse
lifecycle boundaries that are useful when Fusion exits before Python can report an
exception: card actions, reopen-command creation/activation/execute/destroy, and
basic mark/reference health. Every record is flushed immediately to a small temp
file so the tail normally survives a hard Fusion crash.

For reopened-card activation only, a short diagnostic window also traces the
render calls made before Fusion activates the native manipulator. The window is
armed at CommandCreated and closed by the activation observer, so drag/preview
frames remain completely outside this file logger.
"""

import os
import tempfile
import time


LOG_NAME = "fuzzycad_crash.log"
MAX_BYTES = 1024 * 1024
KEEP_BYTES = 256 * 1024
EDIT_CMD_ID = "FuzzyCAD_EditExistingProposal"
WATCH_ACTIONS = {"editManipulator", "confirm", "accept", "reject", "tool"}


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    path = os.path.join(tempfile.gettempdir(), LOG_NAME)
    state = {"observer": None, "activate_watch": None}
    m._crash_log_path = path

    def trim_once():
        try:
            if not os.path.exists(path) or os.path.getsize(path) <= MAX_BYTES:
                return
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - KEEP_BYTES))
                tail = fh.read()
            with open(path, "wb") as fh:
                fh.write(tail)
        except Exception:
            pass

    def write(event, detail=""):
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = "{} | {}".format(stamp, str(event))
            if detail:
                line += " | " + str(detail).replace("\n", "\\n")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        except Exception:
            pass

    def valid(obj):
        if obj is None:
            return False
        try:
            return bool(obj.isValid)
        except Exception:
            return True

    def token(obj):
        if obj is None:
            return None
        try:
            return str(obj.entityToken)
        except Exception:
            return None

    def mark_detail(mid):
        try:
            mid = int(mid)
        except Exception:
            return "id={} invalid-id".format(mid)
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if mark is None:
            return "id={} mark=missing".format(mid)
        try:
            phase = m._mark_phase(mark)
        except Exception:
            phase = "unknown"
        geom = (getattr(m, "_geom", {}) or {}).get(mid)
        body = (getattr(m, "_body", {}) or {}).get(mid)
        ent = (getattr(m, "_entity", {}) or {}).get(mid)
        try:
            geom_keys = sorted(str(k) for k in (geom or {}).keys())[:12]
        except Exception:
            geom_keys = []
        return (
            "id={id} tool={tool} phase={phase} status={status} mtype={mtype} "
            "geom={geom} geom_keys={keys} body={body} body_valid={body_valid} "
            "body_token={body_tok} entity={entity} entity_valid={entity_valid} "
            "entity_token={entity_tok} active_edit={active_edit} active_cmd={active_cmd}"
        ).format(
            id=mid,
            tool=mark.get("tool"),
            phase=phase,
            status=mark.get("status", "open"),
            mtype=mark.get("mtype", "need_input"),
            geom="yes" if geom is not None else "no",
            keys=",".join(geom_keys),
            body="yes" if body is not None else "no",
            body_valid=valid(body),
            body_tok=token(body),
            entity="yes" if ent is not None else "no",
            entity_valid=valid(ent),
            entity_tok=token(ent),
            active_edit=getattr(m, "_active_edit_id", None),
            active_cmd=getattr(m, "_active_cmd", None),
        )

    m._crash_trace = write
    m._crash_mark_detail = mark_detail

    # ---- activation-only render tracing ---------------------------------
    # These wrappers are silent unless a reopened command has just been created
    # and has not yet reached our activate observer. This gives us the exact last
    # completed render call if Fusion dies inside native activation, without
    # adding synchronous file I/O to normal drag/preview frames.
    old_clear = m._clear
    old_group = m._group
    old_draw_one = m._draw_one
    old_refresh_ghost = m._refresh_ghost

    def watch_mid():
        return state.get("activate_watch")

    def clear(gid):
        mid = watch_mid()
        if mid is not None:
            write("ACTIVATE_CLEAR_BEGIN", "id={} group={}".format(mid, gid))
        try:
            result = old_clear(gid)
        except Exception:
            if mid is not None:
                try:
                    write("ACTIVATE_CLEAR_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    pass
            raise
        if mid is not None:
            write("ACTIVATE_CLEAR_DONE", "id={} group={}".format(mid, gid))
        return result

    def group(gid):
        mid = watch_mid()
        if mid is not None:
            write("ACTIVATE_GROUP_BEGIN", "id={} group={}".format(mid, gid))
        try:
            result = old_group(gid)
        except Exception:
            if mid is not None:
                try:
                    write("ACTIVATE_GROUP_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    pass
            raise
        if mid is not None:
            write("ACTIVATE_GROUP_DONE", "id={} group={} found={}".format(
                mid, gid, result is not None))
        return result

    def draw_one(group_obj, mark):
        mid = watch_mid()
        mark_id = mark.get("id") if isinstance(mark, dict) else None
        if mid is not None:
            write("ACTIVATE_DRAW_ONE_BEGIN", "active={} mark={} tool={}".format(
                mid, mark_id, mark.get("tool") if isinstance(mark, dict) else None))
        try:
            result = old_draw_one(group_obj, mark)
        except Exception:
            if mid is not None:
                try:
                    write("ACTIVATE_DRAW_ONE_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    pass
            raise
        if mid is not None:
            write("ACTIVATE_DRAW_ONE_DONE", "active={} mark={}".format(mid, mark_id))
        return result

    def refresh_ghost():
        mid = watch_mid()
        if mid is not None:
            write("ACTIVATE_REFRESH_GHOST_BEGIN", "id={}".format(mid))
        try:
            result = old_refresh_ghost()
        except Exception:
            if mid is not None:
                try:
                    write("ACTIVATE_REFRESH_GHOST_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    pass
            raise
        if mid is not None:
            write("ACTIVATE_REFRESH_GHOST_DONE", "id={}".format(mid))
        return result

    m._clear = clear
    m._group = group
    m._draw_one = draw_one
    m._refresh_ghost = refresh_ghost

    class EditActivate(adsk.core.CommandEventHandler):
        def notify(self, args):
            mid = getattr(m, "_active_edit_id", None)
            write("EDIT_ACTIVATE", mark_detail(mid))
            # Reopen's own activate handler is registered before this observer in
            # the normal lifecycle. Reaching here therefore means its redraw,
            # active-preview draw, ghost sync, and viewport refresh all returned.
            if state.get("activate_watch") is not None:
                write("ACTIVATE_WATCH_COMPLETE", "id={}".format(state.get("activate_watch")))
                state["activate_watch"] = None

    class EditExecute(adsk.core.CommandEventHandler):
        def notify(self, args):
            write("EDIT_EXECUTE", mark_detail(getattr(m, "_active_edit_id", None)))

    class EditDestroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            state["activate_watch"] = None
            write(
                "EDIT_DESTROY",
                "active_edit={} active_cmd={}".format(
                    getattr(m, "_active_edit_id", None),
                    getattr(m, "_active_cmd", None)))

    class EditCommandObserver(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            mid = getattr(m, "_active_edit_id", None)
            state["activate_watch"] = mid
            write("EDIT_COMMAND_CREATED", mark_detail(mid))
            write("ACTIVATE_WATCH_ARMED", "id={}".format(mid))
            # Do not trace inputChanged/executePreview: those are hot drag paths.
            try:
                for handler, event in (
                    (EditActivate(), args.command.activate),
                    (EditExecute(), args.command.execute),
                    (EditDestroy(), args.command.destroy),
                ):
                    event.add(handler)
                    m._handlers.append(handler)
            except Exception as exc:
                write("TRACE_ATTACH_ERROR", repr(exc))

    def attach_edit_observer():
        try:
            cd = m._ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if cd is None:
                write("TRACE_OBSERVER_MISSING", EDIT_CMD_ID)
                return
            h = EditCommandObserver()
            cd.commandCreated.add(h)
            m._handlers.append(h)
            state["observer"] = h
            write("TRACE_OBSERVER_READY", EDIT_CMD_ID)
        except Exception as exc:
            write("TRACE_OBSERVER_ERROR", repr(exc))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            if action in WATCH_ACTIONS:
                if action in ("editManipulator", "accept", "reject"):
                    write("CARD_" + str(action).upper(), mark_detail(data.get("id")))
                elif action == "confirm":
                    write(
                        "RAIL_CONFIRM",
                        "active_edit={} active_cmd={}".format(
                            getattr(m, "_active_edit_id", None),
                            getattr(m, "_active_cmd", None)))
                elif action == "tool":
                    write("TOOL_LAUNCH", "tool={}".format(data.get("tool")))

            try:
                self._delegate.notify(args)
            except Exception:
                try:
                    write("PALETTE_EXCEPTION", m.traceback.format_exc())
                except Exception:
                    write("PALETTE_EXCEPTION", "traceback unavailable")
                raise

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        trim_once()
        write("SESSION_START", "marks={} log={}".format(
            len(getattr(m, "_marks", []) or []), path))
        attach_edit_observer()
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD CRASH TRACE] " + path)
        except Exception:
            pass
        return result

    def stop(context):
        state["activate_watch"] = None
        write("SESSION_STOP", "normal addon stop")
        return old_stop(context)

    m.run = run
    m.stop = stop
