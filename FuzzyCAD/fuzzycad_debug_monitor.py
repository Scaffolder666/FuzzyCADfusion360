"""Live debug monitor for FuzzyCAD development.

Shows Fusion's TEXT COMMANDS palette automatically and logs both Fusion command
lifecycle events and FuzzyCAD's internal palette/command state.  The goal is to
make "Apply worked, then the model changed back when I picked another tool"
observable instead of guessing whether the rollback came from Fusion's command
transaction, a late preview, or FuzzyCAD state cleanup.
"""

import json
import math
import time

PREFIX = "[FuzzyCAD DEBUG]"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    OriginalPaletteHTMLHandler = m.PaletteHTMLHandler
    OriginalFuzzyCommandCreated = m.FuzzyCommandCreated
    OriginalLaunchHandler = m.LaunchHandler

    state = {
        "global_handlers_attached": False,
        "last_apply": None,
        "preview_values": {},
    }

    def now():
        try:
            return time.strftime("%H:%M:%S") + ".{:03d}".format(int((time.time() % 1) * 1000))
        except Exception:
            return "--:--:--.---"

    def log(message):
        line = "{} {} {}".format(PREFIX, now(), message)
        try:
            app = m._app or adsk.core.Application.get()
            app.log(line)
        except Exception:
            try:
                print(line)
            except Exception:
                pass

    m._debug = log

    def _round(v, digits=5):
        try:
            return round(float(v), digits)
        except Exception:
            return None

    def _bbox_tuple(box):
        try:
            mn, mx = box.minPoint, box.maxPoint
            return tuple(_round(x, 4) for x in
                         (mn.x, mn.y, mn.z, mx.x, mx.y, mx.z))
        except Exception:
            return None

    def model_snapshot():
        """A cheap signature that changes for move/rotate/scale/extrude/fillet.

        Timeline count catches parametric feature commits/rollbacks.  Body count,
        volume and aggregate bounding-box coordinates also work for direct-model
        documents and transforms where volume alone is unchanged.
        """
        out = {
            "timeline": None,
            "bodies": 0,
            "volume": 0.0,
            "bbox_sum": [0.0] * 6,
        }
        try:
            design = m._design()
            if design is None:
                return out
            try:
                out["timeline"] = design.timeline.count
            except Exception:
                pass

            bodies = []
            seen = set()

            def add_collection(coll):
                try:
                    for i in range(coll.count):
                        b = coll.item(i)
                        try:
                            key = b.entityToken
                        except Exception:
                            key = "obj:{}".format(id(b))
                        if key in seen:
                            continue
                        seen.add(key)
                        bodies.append(b)
                except Exception:
                    pass

            try:
                root = design.rootComponent
                add_collection(root.bRepBodies)
                occs = root.allOccurrences
                for i in range(occs.count):
                    try:
                        add_collection(occs.item(i).bRepBodies)
                    except Exception:
                        pass
            except Exception:
                pass

            out["bodies"] = len(bodies)
            for body in bodies:
                try:
                    out["volume"] += float(body.volume)
                except Exception:
                    try:
                        out["volume"] += float(body.physicalProperties.volume)
                    except Exception:
                        pass
                bt = _bbox_tuple(getattr(body, "boundingBox", None))
                if bt:
                    for i, value in enumerate(bt):
                        if value is not None:
                            out["bbox_sum"][i] += value

            out["volume"] = _round(out["volume"], 6)
            out["bbox_sum"] = tuple(_round(v, 4) for v in out["bbox_sum"])
        except Exception:
            pass
        return out

    m._debug_model_snapshot = model_snapshot

    def snap_text(s=None):
        s = s or model_snapshot()
        return "timeline={} bodies={} volume={} bbox={}".format(
            s.get("timeline"), s.get("bodies"), s.get("volume"), s.get("bbox_sum"))

    def internal_text():
        try:
            live = dict(m._live)
        except Exception:
            live = {}
        try:
            active = m._active_cmd
        except Exception:
            active = None
        try:
            pending = m._pending is not None
        except Exception:
            pending = False
        try:
            marks = [x.get("id") for x in m._marks]
        except Exception:
            marks = []
        return "active={} pending={} live={} marks={}".format(active, pending, live, marks)

    def same_snapshot(a, b):
        if not a or not b:
            return False
        return (a.get("timeline") == b.get("timeline") and
                a.get("bodies") == b.get("bodies") and
                a.get("volume") == b.get("volume") and
                a.get("bbox_sum") == b.get("bbox_sum"))

    def inspect_rollback(where):
        rec = state.get("last_apply")
        if not rec:
            return
        current = model_snapshot()
        after = rec.get("after")
        before = rec.get("before")
        if same_snapshot(current, after):
            log("{} | last applied model STILL PRESENT | mark={} tool={} | {}".format(
                where, rec.get("mark"), rec.get("tool"), snap_text(current)))
        elif same_snapshot(current, before) and not same_snapshot(before, after):
            log("!!! ROLLBACK DETECTED at {} | mark={} tool={} | model returned exactly to BEFORE-APPLY state | {}".format(
                where, rec.get("mark"), rec.get("tool"), snap_text(current)))
        else:
            log("{} | model differs from both before/after apply | mark={} tool={} | {}".format(
                where, rec.get("mark"), rec.get("tool"), snap_text(current)))

    class GlobalCommandHandler(adsk.core.ApplicationCommandEventHandler):
        def __init__(self, phase):
            super().__init__()
            self.phase = phase

        def notify(self, args):
            try:
                cid = getattr(args, "commandId", "?")
                log("FUSION COMMAND {} id={} | {} | {}".format(
                    self.phase, cid, internal_text(), snap_text()))
                if self.phase == "TERMINATED":
                    inspect_rollback("COMMAND TERMINATED {}".format(cid))
            except Exception as exc:
                log("global command logger failed: {}".format(exc))

    def attach_global_handlers():
        if state["global_handlers_attached"]:
            return
        ui = m._ui
        if ui is None:
            return
        try:
            h = GlobalCommandHandler("STARTING")
            ui.commandStarting.add(h)
            m._handlers.append(h)
        except Exception as exc:
            log("could not attach commandStarting: {}".format(exc))
        try:
            h = GlobalCommandHandler("TERMINATED")
            ui.commandTerminated.add(h)
            m._handlers.append(h)
        except Exception as exc:
            log("could not attach commandTerminated: {}".format(exc))
        state["global_handlers_attached"] = True

    def show_text_commands():
        try:
            p = m._ui.palettes.itemById("TextCommands")
            if p is not None:
                p.isVisible = True
        except Exception as exc:
            log("could not show TEXT COMMANDS: {}".format(exc))

    class LifecycleHandler(adsk.core.CommandEventHandler):
        def __init__(self, tool, phase):
            super().__init__()
            self.tool = tool
            self.phase = phase

        def _values(self):
            values = {}
            try:
                for cat in m.CMD_CATS.get(self.tool, ()):
                    try:
                        op = m._category_raw(cat)
                        clean = {}
                        for key, val in op.items():
                            if isinstance(val, (list, tuple)):
                                clean[key] = [round(float(x), 4) for x in val]
                            else:
                                clean[key] = round(float(val), 4)
                        values[cat] = clean
                    except Exception:
                        pass
            except Exception:
                pass
            return values

        def notify(self, args):
            try:
                if self.phase == "PREVIEW":
                    values = self._values()
                    key = self.tool
                    encoded = repr(values)
                    if state["preview_values"].get(key) == encoded:
                        return
                    state["preview_values"][key] = encoded
                    log("FUZZYCAD {} PREVIEW values={} | {} | {}".format(
                        self.tool, values, internal_text(), snap_text()))
                    return
                log("FUZZYCAD {} {} | {} | {}".format(
                    self.tool, self.phase, internal_text(), snap_text()))
                if self.phase in ("DEACTIVATE", "DESTROY"):
                    inspect_rollback("FUZZYCAD {} {}".format(self.tool, self.phase))
            except Exception as exc:
                log("lifecycle logger failed {} {}: {}".format(self.tool, self.phase, exc))

    class FuzzyCommandCreated(OriginalFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            try:
                command = args.command
                log("FUZZYCAD {} CREATED | {} | {}".format(
                    self.cmd, internal_text(), snap_text()))
                for event_name, phase in (
                    ("activate", "ACTIVATE"),
                    ("executePreview", "PREVIEW"),
                    ("execute", "EXECUTE"),
                    ("deactivate", "DEACTIVATE"),
                    ("destroy", "DESTROY"),
                ):
                    try:
                        handler = LifecycleHandler(self.cmd, phase)
                        getattr(command, event_name).add(handler)
                        m._handlers.append(handler)
                    except Exception as exc:
                        log("could not attach {} logger for {}: {}".format(
                            phase, self.cmd, exc))
            except Exception as exc:
                log("command-created logger failed: {}".format(exc))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    class LaunchHandler(OriginalLaunchHandler):
        def notify(self, args):
            try:
                log("TOOL LAUNCH requested={} BEFORE legacy terminate/execute | {} | {}".format(
                    getattr(args, "additionalInfo", "?"), internal_text(), snap_text()))
                inspect_rollback("BEFORE TOOL LAUNCH")
            except Exception:
                pass
            super().notify(args)
            try:
                log("TOOL LAUNCH returned requested={} | {} | {}".format(
                    getattr(args, "additionalInfo", "?"), internal_text(), snap_text()))
            except Exception:
                pass

    m.LaunchHandler = LaunchHandler

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = OriginalPaletteHTMLHandler()

        def notify(self, args):
            action = "?"
            data = {}
            before = None
            mark_tool = None
            mid = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
                mid = data.get("id")
                mark = m._find(mid) if mid is not None else None
                mark_tool = mark.get("tool") if mark else None
                if action in ("accept", "reject", "tool", "edit"):
                    before = model_snapshot()
                    log("PALETTE {} data={} BEFORE | {} | {}".format(
                        action.upper(), data, internal_text(), snap_text(before)))
            except Exception as exc:
                log("palette pre-log failed: {}".format(exc))

            self._delegate.notify(args)

            try:
                if action in ("accept", "reject", "tool", "edit"):
                    after = model_snapshot()
                    log("PALETTE {} data={} AFTER | {} | {}".format(
                        action.upper(), data, internal_text(), snap_text(after)))
                    if action == "accept" and mid is not None and m._find(mid) is None:
                        state["last_apply"] = {
                            "mark": mid,
                            "tool": mark_tool,
                            "before": before,
                            "after": after,
                        }
                        if same_snapshot(before, after):
                            log("!!! ACCEPT removed mark={} tool={} but MODEL SIGNATURE DID NOT CHANGE".format(
                                mid, mark_tool))
                        else:
                            log("ACCEPT COMMITTED mark={} tool={} | before=[{}] after=[{}]".format(
                                mid, mark_tool, snap_text(before), snap_text(after)))
            except Exception as exc:
                log("palette post-log failed: {}".format(exc))

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        try:
            show_text_commands()
            attach_global_handlers()
            log("============================================================")
            log("DEBUG MONITOR ON | {} | {}".format(internal_text(), snap_text()))
            log("Repro recipe: Fuzzy tool -> drag -> Accept -> click another tool. Watch for ROLLBACK DETECTED.")
            log("============================================================")
        except Exception as exc:
            log("debug monitor startup failed: {}".format(exc))
        return result

    def stop(context):
        try:
            log("DEBUG MONITOR STOP | {} | {}".format(internal_text(), snap_text()))
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
