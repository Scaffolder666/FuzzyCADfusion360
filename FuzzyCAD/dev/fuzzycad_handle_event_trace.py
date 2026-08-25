"""Direct native manipulator event tracing for FuzzyCAD.

The earlier value tracer attached an additional executePreview listener from a
CommandCreated wrapper.  That is useful for end-to-end ACCEPT/APPLY comparison,
but it is too indirect for proving that every native arrow movement is reaching
Fusion.  This patch wraps the actual FuzzyInputChanged and FuzzyPreview handler
classes that FuzzyCAD installs on each command.

For every native manipulator input event we log:
  - which native CommandInput changed (mX/mY/mZ/rX/rY/rZ/sc/d)
  - its raw/user-readable handle value
  - the live proposal mark value after FuzzyCAD synchronizes it
We also log every executePreview callback as a second independent signal.
"""

import math

MANIP_IDS = {"mX", "mY", "mZ", "rX", "rY", "rZ", "sc", "d"}


def install(m):
    adsk = m.adsk
    CurrentInputChanged = m.FuzzyInputChanged
    CurrentPreview = m.FuzzyPreview
    old_run = m.run

    state = {"input_seq": 0, "preview_seq": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            app = m._app or adsk.core.Application.get()
            app.log("[FuzzyCAD HANDLE] " + msg)
        except Exception:
            pass

    def rnd(v, n=4):
        try:
            return round(float(v), n)
        except Exception:
            return None

    def mark_value(mark):
        if not mark:
            return None
        tool = mark.get("tool")
        if tool == "move":
            v = mark.get("vec") or [0.0, 0.0, 0.0]
            return {"mm": [rnd(x * 10.0) for x in v]}
        if tool == "rotate":
            v = mark.get("rot") or [0.0, 0.0, 0.0]
            return {"deg": [rnd(x) for x in v]}
        if tool == "scale":
            return {"factor": rnd(mark.get("factor", 1.0), 6)}
        if tool == "extrude":
            return {"mm": rnd(mark.get("amount", 0.0) * 10.0)}
        if tool == "fillet":
            return {"radius_mm": rnd(mark.get("amount", 0.0) * 10.0)}
        return None

    def handle_for(tool):
        """Read native CommandInput values directly from Fusion."""
        try:
            if tool == "move":
                return {"mm": [
                    rnd(m._val("mX") * 10.0),
                    rnd(m._val("mY") * 10.0),
                    rnd(m._val("mZ") * 10.0),
                ]}
            if tool == "rotate":
                return {"deg": [
                    rnd(math.degrees(m._val("rX"))),
                    rnd(math.degrees(m._val("rY"))),
                    rnd(math.degrees(m._val("rZ"))),
                ]}
            if tool == "scale":
                length = m._pending.get("scale_len", 1.0) if m._pending else 1.0
                if length <= 1e-6:
                    length = 1.0
                return {"factor": rnd(max(0.05, 1.0 + m._val("sc") / length), 6)}
            if tool == "extrude":
                return {"mm": rnd(m._val("d") * 10.0)}
            if tool == "fillet":
                return {"radius_mm": rnd(m._val("d") * 10.0)}
        except Exception as exc:
            return {"error": str(exc)}
        return None

    def live_rows():
        rows = []
        try:
            for tool, mid in dict(m._live).items():
                mark = m._find(mid)
                rows.append({
                    "tool": tool,
                    "mark": mid,
                    "handle": handle_for(tool),
                    "mark_value": mark_value(mark),
                })
        except Exception:
            pass
        return rows

    def all_command_handles():
        rows = {}
        try:
            for tool in m.CMD_CATS.get(m._active_cmd, ()):
                rows[tool] = handle_for(tool)
        except Exception:
            pass
        return rows

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass

            # Let the real FuzzyCAD handler synchronize the proposal first, then
            # inspect both sides of the mapping in the same callback.
            super().notify(args)

            if cid not in MANIP_IDS:
                return
            try:
                state["input_seq"] += 1
                log("HANDLE INPUT #{:05d} input={} active={} HANDLE={} LIVE={}".format(
                    state["input_seq"], cid, getattr(m, "_active_cmd", None),
                    all_command_handles(), live_rows()))
            except Exception as exc:
                log("HANDLE INPUT logger failed input={}: {}".format(cid, exc))

    class FuzzyPreview(CurrentPreview):
        def notify(self, args):
            # The real preview is run first so MARK reflects exactly what the
            # viewport/card just consumed for this callback.
            super().notify(args)
            try:
                state["preview_seq"] += 1
                log("HANDLE PREVIEW #{:05d} active={} HANDLE={} LIVE={}".format(
                    state["preview_seq"], getattr(m, "_active_cmd", None),
                    all_command_handles(), live_rows()))
            except Exception as exc:
                log("HANDLE PREVIEW logger failed: {}".format(exc))

    # FuzzyCommandCreated looks these names up from the module at command-create
    # time, so replacing them here guarantees these are the handlers actually
    # registered with Fusion, rather than relying on an extra side listener.
    m.FuzzyInputChanged = FuzzyInputChanged
    m.FuzzyPreview = FuzzyPreview

    def run(context):
        result = old_run(context)
        log("HANDLE EVENT TRACE READY: native inputChanged + executePreview are instrumented")
        return result

    m.run = run
