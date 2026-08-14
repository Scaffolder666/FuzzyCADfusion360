"""End-to-end numeric tracing for FuzzyCAD manipulators.

Logs every executePreview callback and follows the numeric value through four
stages:
  HANDLE       value currently reported by Fusion's manipulator inputs
  MARK         value stored in the FuzzyCAD proposal/card
  ACCEPT       value present when the user clicks Accept
  APPLY        exact value handed to _accept when the real Fusion feature is built

This is deliberately noisy development instrumentation.  It is intended to make
any handle/card/apply divergence obvious in TEXT COMMANDS.
"""

import json
import math


def install(m):
    adsk = m.adsk
    OriginalFuzzyCommandCreated = m.FuzzyCommandCreated
    OriginalPaletteHTMLHandler = m.PaletteHTMLHandler
    original_accept = m._accept

    state = {
        "preview_seq": 0,
        "last_handle_by_mark": {},
        "accept_by_mark": {},
    }

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
            app.log("[FuzzyCAD VALUE] " + msg)
        except Exception:
            pass

    def roundn(v, n=4):
        try:
            return round(float(v), n)
        except Exception:
            return None

    def mark_value(mark):
        """Canonical, user-readable value for a proposal mark."""
        if not mark:
            return None
        tool = mark.get("tool")
        if tool == "move":
            vec = mark.get("vec") or [0.0, 0.0, 0.0]
            return {"tool": tool, "mm": [roundn(x * 10.0) for x in vec]}
        if tool == "rotate":
            rot = mark.get("rot") or [0.0, 0.0, 0.0]
            return {"tool": tool, "deg": [roundn(x) for x in rot]}
        if tool == "scale":
            return {"tool": tool, "factor": roundn(mark.get("factor", 1.0), 6)}
        if tool == "extrude":
            return {"tool": tool, "mm": roundn(mark.get("amount", 0.0) * 10.0)}
        if tool == "fillet":
            return {"tool": tool, "radius_mm": roundn(mark.get("amount", 0.0) * 10.0)}
        if tool == "note":
            return {"tool": tool, "text": mark.get("text", "")}
        return {"tool": tool}

    def handle_value(tool):
        """Read the current native Fusion CommandInput values, not the mark."""
        try:
            if tool == "move":
                return {"tool": tool, "mm": [
                    roundn(m._val("mX") * 10.0),
                    roundn(m._val("mY") * 10.0),
                    roundn(m._val("mZ") * 10.0),
                ]}
            if tool == "rotate":
                return {"tool": tool, "deg": [
                    roundn(math.degrees(m._val("rX"))),
                    roundn(math.degrees(m._val("rY"))),
                    roundn(math.degrees(m._val("rZ"))),
                ]}
            if tool == "scale":
                length = m._pending.get("scale_len", 1.0) if m._pending else 1.0
                f = 1.0 + m._val("sc") / (length if length > 1e-6 else 1.0)
                return {"tool": tool, "factor": roundn(max(0.05, f), 6)}
            if tool == "extrude":
                return {"tool": tool, "mm": roundn(m._val("d") * 10.0)}
            if tool == "fillet":
                return {"tool": tool, "radius_mm": roundn(m._val("d") * 10.0)}
        except Exception as exc:
            return {"tool": tool, "error": str(exc)}
        return {"tool": tool}

    def comparable(v):
        if not isinstance(v, dict):
            return None
        tool = v.get("tool")
        if tool == "move":
            return tuple(v.get("mm") or [])
        if tool == "rotate":
            return tuple(v.get("deg") or [])
        if tool == "scale":
            return (v.get("factor"),)
        if tool == "extrude":
            return (v.get("mm"),)
        if tool == "fillet":
            return (v.get("radius_mm"),)
        return None

    def values_match(a, b, tol=0.002):
        aa, bb = comparable(a), comparable(b)
        if aa is None or bb is None or len(aa) != len(bb):
            return None
        try:
            return all(abs(float(x) - float(y)) <= tol for x, y in zip(aa, bb))
        except Exception:
            return False

    def status(a, b):
        match = values_match(a, b)
        if match is None:
            return "N/A"
        return "MATCH" if match else "!!! MISMATCH !!!"

    def active_live_marks():
        out = []
        try:
            for cat, mid in dict(m._live).items():
                mark = m._find(mid)
                if mark is not None:
                    out.append((cat, mid, mark))
        except Exception:
            pass
        return out

    class EveryPreviewValue(adsk.core.CommandEventHandler):
        def __init__(self, command_tool):
            super().__init__()
            self.command_tool = command_tool

        def notify(self, args):
            try:
                state["preview_seq"] += 1
                seq = state["preview_seq"]
                lives = active_live_marks()

                # Log every callback, even when Fusion reports the same value twice.
                if not lives:
                    cats = m.CMD_CATS.get(self.command_tool, ())
                    vals = {cat: handle_value(cat) for cat in cats}
                    log("VALUE PREVIEW #{:05d} command={} no-live-mark HANDLE={}".format(
                        seq, self.command_tool, vals))
                    return

                for cat, mid, mark in lives:
                    hv = handle_value(cat)
                    mv = mark_value(mark)
                    state["last_handle_by_mark"][mid] = hv
                    log("VALUE PREVIEW #{:05d} mark={} tool={} HANDLE={} MARK={} => {}".format(
                        seq, mid, cat, hv, mv, status(hv, mv)))
            except Exception as exc:
                log("VALUE PREVIEW logger failed: {}".format(exc))

    class FuzzyCommandCreated(OriginalFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            try:
                h = EveryPreviewValue(self.cmd)
                args.command.executePreview.add(h)
                m._handlers.append(h)
            except Exception as exc:
                log("could not attach every-preview value logger for {}: {}".format(
                    self.cmd, exc))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = OriginalPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            mid = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
                mid = data.get("id")

                if action == "edit" and mid is not None:
                    mark = m._find(mid)
                    log("VALUE CARD EDIT mark={} BEFORE={} data={}".format(
                        mid, mark_value(mark), data))

                if action == "accept" and mid is not None:
                    mark = m._find(mid)
                    av = mark_value(mark)
                    hv = state["last_handle_by_mark"].get(mid)
                    # Read the native input one last time if this is still the live mark.
                    try:
                        if any(v == mid for v in m._live.values()) and mark is not None:
                            hv = handle_value(mark.get("tool"))
                    except Exception:
                        pass
                    state["accept_by_mark"][mid] = {
                        "handle": hv,
                        "accept": av,
                    }
                    log("VALUE ACCEPT mark={} HANDLE_LAST={} ACCEPT_MARK={} => {}".format(
                        mid, hv, av, status(hv, av)))
            except Exception as exc:
                log("VALUE palette pre-log failed: {}".format(exc))

            self._delegate.notify(args)

            try:
                if action == "edit" and mid is not None:
                    mark = m._find(mid)
                    log("VALUE CARD EDIT mark={} AFTER={}".format(mid, mark_value(mark)))
            except Exception:
                pass

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def traced_accept(mark):
        mid = mark.get("id") if mark else None
        apply_value = mark_value(mark)
        rec = state["accept_by_mark"].get(mid, {})
        hv = rec.get("handle")
        av = rec.get("accept")

        log("VALUE APPLY REQUEST mark={} HANDLE_LAST={} ACCEPT_MARK={} APPLY_VALUE={} | handle->apply={} accept->apply={}".format(
            mid, hv, av, apply_value, status(hv, apply_value), status(av, apply_value)))

        try:
            before = m._debug_model_snapshot() if hasattr(m, "_debug_model_snapshot") else None
        except Exception:
            before = None

        ok = original_accept(mark)

        try:
            after = m._debug_model_snapshot() if hasattr(m, "_debug_model_snapshot") else None
        except Exception:
            after = None

        # APPLY_VALUE is the exact numeric payload consumed by the real Fusion
        # feature construction in _accept.  The model snapshots verify that the
        # geometry changed and later transaction logging verifies persistence.
        log("VALUE APPLY RESULT mark={} ok={} APPLIED_VALUE={} | handle->applied={} accept->applied={} model_before={} model_after={}".format(
            mid, ok, apply_value, status(hv, apply_value), status(av, apply_value),
            before, after))
        return ok

    m._accept = traced_accept
