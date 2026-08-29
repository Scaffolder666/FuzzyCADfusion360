"""Always-on flow trace plus opt-in lightweight study logging.

The flow trace records palette actions and toolbar command lifecycle EDGES to the
shared crash log for manual risk sweeps. It remains edge-only: no per-frame
executePreview, inputChanged, animation, hover, or camera logging.

The study logger is separate in behavior even though this outermost runtime hook
hosts it: it is OFF by default and adds no study work until the user presses
Start Logging in the sidebar. While active it keeps a small in-memory event list,
coalesces proposal value edits to their final before/after values, and exports one
timestamped JSON file when the user presses Stop Logging.
"""

import copy
import json
import os
import tempfile
import time
from datetime import datetime


def install(m):
    adsk = m.adsk
    CurrentCreated = m.FuzzyCommandCreated
    CurrentPalette = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop
    old_redraw = m._redraw_marks

    # ------------------------------------------------------------------
    # Existing crash / risk-sweep flow trace
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Opt-in study log
    # ------------------------------------------------------------------
    study = {
        "active": False,
        "started_at": None,
        "started_mono": None,
        "events": [],
        "last_values": {},
        "pending": {},
        "last_filename": None,
    }
    m._study_logger_state = study

    def study_elapsed():
        if study["started_mono"] is None:
            return 0.0
        return max(0.0, time.monotonic() - study["started_mono"])

    def study_iso_now():
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def study_safe(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [study_safe(v) for v in value]
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                converted = study_safe(item)
                if converted is not None or item is None:
                    out[str(key)] = converted
            return out
        return None

    def study_find(mid):
        try:
            return m._find(mid)
        except Exception:
            return None

    def study_values(mark):
        """Compact, human-readable proposal values; no sampled geometry."""
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
            out[key] = {
                "label": str(field.get("label", key)),
                "value": study_safe(field.get("value")),
                "unit": str(field.get("unit", "") or ""),
            }
        return out

    def study_meta(mark):
        return {
            "mark_id": mark.get("id") if mark else None,
            "tool": mark.get("tool") if mark else None,
            "type": mark.get("mtype", "need_input") if mark else None,
        }

    def study_add(name, mark=None, payload=None, at=None):
        if not study["active"]:
            return
        row = {
            "event": str(name),
            "elapsed_sec": round(
                study_elapsed() if at is None else float(at), 3),
        }
        if mark:
            row.update(study_meta(mark))
        if payload:
            row.update({str(k): study_safe(v) for k, v in payload.items()})
        study["events"].append(row)

    def study_baseline():
        study["last_values"].clear()
        study["pending"].clear()
        for mark in list(getattr(m, "_marks", None) or []):
            try:
                study["last_values"][int(mark["id"])] = copy.deepcopy(
                    study_values(mark))
            except Exception:
                pass

    def study_observe():
        """Called only at persistent redraws; never on drag/animation frames."""
        if not study["active"]:
            return
        for mark in list(getattr(m, "_marks", None) or []):
            try:
                mid = int(mark["id"])
            except Exception:
                continue

            current = copy.deepcopy(study_values(mark))
            if mid not in study["last_values"]:
                study["last_values"][mid] = current
                study_add("proposal_created", mark, {"values": current})
                continue

            previous = study["last_values"][mid]
            if previous == current:
                continue

            pending = study["pending"].get(mid)
            if pending is None:
                study["pending"][mid] = {
                    "meta": study_meta(mark),
                    "before": copy.deepcopy(previous),
                    "after": copy.deepcopy(current),
                    "elapsed_sec": study_elapsed(),
                }
            else:
                pending["after"] = copy.deepcopy(current)
                pending["elapsed_sec"] = study_elapsed()
                pending["meta"] = study_meta(mark)
            study["last_values"][mid] = current

    def study_flush(mid=None):
        try:
            keys = list(study["pending"]) if mid is None else [int(mid)]
        except Exception:
            keys = []
        for key in keys:
            pending = study["pending"].pop(key, None)
            if not pending or pending["before"] == pending["after"]:
                continue
            row = {
                "event": "proposal_value_changed",
                "elapsed_sec": round(float(pending["elapsed_sec"]), 3),
                "before": pending["before"],
                "after": pending["after"],
            }
            row.update(pending["meta"])
            study["events"].append(row)

    def study_start():
        if study["active"]:
            return {
                "ok": True,
                "active": True,
                "elapsed_sec": round(study_elapsed(), 3),
            }
        study["active"] = True
        study["started_at"] = study_iso_now()
        study["started_mono"] = time.monotonic()
        study["events"] = []
        study["last_filename"] = None
        study_baseline()
        study_add("start_logging", at=0.0)
        return {"ok": True, "active": True, "elapsed_sec": 0.0}

    def study_export_path(filename):
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, "Downloads") if home else None,
            home if home and home != "~" else None,
            tempfile.gettempdir(),
        ]
        for directory in candidates:
            try:
                if not directory or not os.path.isdir(directory):
                    continue
                if not os.access(directory, os.W_OK):
                    continue
                path = os.path.join(directory, filename)
                if not os.path.exists(path):
                    return path
                stem, ext = os.path.splitext(filename)
                seq = 2
                while os.path.exists(os.path.join(
                        directory, "{}_{}{}".format(stem, seq, ext))):
                    seq += 1
                return os.path.join(
                    directory, "{}_{}{}".format(stem, seq, ext))
            except Exception:
                pass
        raise IOError("No writable export folder found.")

    def study_stop(reason="user"):
        if not study["active"]:
            return {
                "ok": False,
                "active": False,
                "error": "Study logging is not active.",
            }

        study_observe()
        study_flush()
        duration = study_elapsed()
        study_add(
            "stop_logging",
            payload={"duration_sec": round(duration, 3)},
            at=duration,
        )
        ended_at = study_iso_now()

        payload = {
            "schema": 1,
            "started_at": study["started_at"],
            "ended_at": ended_at,
            "duration_sec": round(duration, 3),
            "events": sorted(
                study["events"],
                key=lambda event: event.get("elapsed_sec", 0.0),
            ),
        }

        filename = "fuzzycad_{}.json".format(
            datetime.now().astimezone().strftime("%Y%m%d_%H%M%S"))
        path = study_export_path(filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        study["active"] = False
        study["last_filename"] = os.path.basename(path)
        return {
            "ok": True,
            "active": False,
            "filename": study["last_filename"],
            "directory": os.path.basename(os.path.dirname(path)),
            "duration_sec": round(duration, 3),
            "reason": reason,
        }

    def study_status():
        return {
            "ok": True,
            "active": bool(study["active"]),
            "elapsed_sec": round(study_elapsed(), 3)
            if study["active"] else 0.0,
            "filename": study["last_filename"],
        }

    # ------------------------------------------------------------------
    # Toolbar lifecycle tracing
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Palette actions: trace everything; study-log only selected outcomes
    # ------------------------------------------------------------------
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
                e, data = None, {}

            action = e.action if e is not None else None

            if action in ("studyLogStart", "studyLogStop", "studyLogStatus"):
                try:
                    if action == "studyLogStart":
                        result = study_start()
                    elif action == "studyLogStop":
                        result = study_stop()
                    else:
                        result = study_status()
                    e.returnData = json.dumps(result)
                except Exception as exc:
                    try:
                        e.returnData = json.dumps({
                            "ok": False,
                            "active": bool(study["active"]),
                            "error": str(exc),
                        })
                    except Exception:
                        pass
                return

            if not study["active"]:
                self._delegate.notify(args)
                return

            mid = data.get("id")
            before = study_find(mid) if mid is not None else None
            before_values = copy.deepcopy(study_values(before))
            before_comments = list(before.get("comments") or []) if before else []
            before_images = list(before.get("images") or []) if before else []
            before_selected = before.get("selected") if before else None

            # Accept/Reject removes the mark. Flush its final coalesced value
            # change before delegating.
            if action in ("accept", "reject") and mid is not None:
                study_flush(mid)

            self._delegate.notify(args)
            after = study_find(mid) if mid is not None else None

            if action == "comment" and before and after:
                comments = list(after.get("comments") or [])
                for comment in comments[len(before_comments):]:
                    text = (
                        comment.get("text", "")
                        if isinstance(comment, dict)
                        else str(comment)
                    )
                    if text:
                        study_add("comment_added", after, {"text": text})

            elif action in ("attachImageNode", "attachImageFace") and before and after:
                images = list(after.get("images") or [])
                for image in images[len(before_images):]:
                    mode = image.get("mode") if isinstance(image, dict) else None
                    study_add("image_attached", after, {"mode": mode})

            elif action == "compare_choice" and after:
                selected = after.get("selected")
                if selected != before_selected:
                    study_add(
                        "alternative_selected",
                        after,
                        {"selected": selected},
                    )

            elif action == "accept" and before and after is None:
                study_add(
                    "proposal_accepted",
                    before,
                    {"values": before_values},
                )
                try:
                    study["last_values"].pop(int(mid), None)
                except Exception:
                    pass

            elif action == "reject" and before and after is None:
                study_add(
                    "proposal_rejected",
                    before,
                    {"values": before_values},
                )
                try:
                    study["last_values"].pop(int(mid), None)
                except Exception:
                    pass

    m.PaletteHTMLHandler = PaletteHTMLHandler

    # A persistent redraw is already a discrete state transition in the runtime
    # architecture. While study logging is active, use it only to notice new
    # proposals or update the in-memory final-value diff.
    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        if study["active"]:
            try:
                study_observe()
            except Exception:
                pass
        return result

    m._redraw_marks = redraw

    def run(context):
        result = old_run(context)
        try:
            trace("FLOW_TRACE_READY", "manual-sweep logging on")
        except Exception:
            pass
        return result

    m.run = run

    def stop(context):
        # If Fusion/add-in closes while a session is still active, save what was
        # collected so the study data is not silently lost.
        if study["active"]:
            try:
                study_stop("addin_stop")
            except Exception:
                pass
        return old_stop(context)

    m.stop = stop
