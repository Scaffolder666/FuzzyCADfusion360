"""Trace whether the sidebar card being accepted is the same proposal as the
currently active manipulator.

This is development-only instrumentation.  It does not block accepting an older
card; it makes that identity mismatch explicit in TEXT COMMANDS so a value/UX
problem is not mistaken for a geometry or transaction bug.
"""

import json
import math


def install(m):
    adsk = m.adsk
    OriginalPaletteHTMLHandler = m.PaletteHTMLHandler

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD IDENTITY] " + msg)
        except Exception:
            pass

    def mark_value(mark):
        if not mark:
            return None
        tool = mark.get("tool")
        if tool == "move":
            return {"tool": tool, "mm": [round(float(x) * 10.0, 4) for x in (mark.get("vec") or [0, 0, 0])]}
        if tool == "rotate":
            return {"tool": tool, "deg": [round(float(x), 4) for x in (mark.get("rot") or [0, 0, 0])]}
        if tool == "scale":
            return {"tool": tool, "factor": round(float(mark.get("factor", 1.0)), 6)}
        if tool == "extrude":
            return {"tool": tool, "mm": round(float(mark.get("amount", 0.0)) * 10.0, 4)}
        if tool == "fillet":
            return {"tool": tool, "radius_mm": round(float(mark.get("amount", 0.0)) * 10.0, 4)}
        return {"tool": tool}

    def handle_value(cat):
        try:
            if cat == "move":
                return {"tool": cat, "mm": [round(m._val("mX") * 10.0, 4), round(m._val("mY") * 10.0, 4), round(m._val("mZ") * 10.0, 4)]}
            if cat == "rotate":
                return {"tool": cat, "deg": [round(math.degrees(m._val("rX")), 4), round(math.degrees(m._val("rY")), 4), round(math.degrees(m._val("rZ")), 4)]}
            if cat == "scale":
                length = m._pending.get("scale_len", 1.0) if m._pending else 1.0
                f = 1.0 + m._val("sc") / (length if length > 1e-6 else 1.0)
                return {"tool": cat, "factor": round(max(0.05, f), 6)}
            if cat == "extrude":
                return {"tool": cat, "mm": round(m._val("d") * 10.0, 4)}
            if cat == "fillet":
                return {"tool": cat, "radius_mm": round(m._val("d") * 10.0, 4)}
        except Exception as exc:
            return {"tool": cat, "error": str(exc)}
        return {"tool": cat}

    def live_snapshot():
        out = {}
        try:
            for cat, mid in dict(m._live).items():
                mark = m._find(mid)
                out[cat] = {
                    "mark_id": mid,
                    "handle": handle_value(cat),
                    "mark": mark_value(mark),
                }
        except Exception:
            pass
        return out

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = OriginalPaletteHTMLHandler()

        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
                if action == "accept":
                    clicked = data.get("id")
                    mark = m._find(clicked) if clicked is not None else None
                    live = live_snapshot()
                    live_ids = [v.get("mark_id") for v in live.values()]
                    same = clicked in live_ids if live_ids else None
                    status = "SAME ACTIVE PROPOSAL" if same else ("NO ACTIVE MANIPULATOR" if same is None else "!!! ACCEPT TARGET != CURRENT MANIPULATOR !!!")
                    log("ACCEPT IDENTITY clicked_mark={} clicked_value={} live={} => {}".format(
                        clicked, mark_value(mark), live, status))
            except Exception as exc:
                log("ACCEPT IDENTITY logger failed: {}".format(exc))
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
