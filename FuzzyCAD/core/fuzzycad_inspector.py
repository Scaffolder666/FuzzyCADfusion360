"""FuzzyCAD Inspector: a fallback admin surface for what FuzzyCAD generated.

The runtime keeps its state in code -- the open marks, the ghosted bodies
(fuzzycad_opacity_runtime), and the ephemeral graphics registry owned by the
visual authority (fuzzycad_state_reconcile). This module exposes that in the
palette so it can be seen and repaired by hand:

  inspectorData   -> a snapshot: counts (open marks by type, ghosted bodies,
                     any stray graphics groups), where the state is stored, and a
                     compact per-mark list.
  repairViewport  -> manually run the visual authority once: sweep every stray
                     graphics group, restore every ghosted body to full opacity,
                     then redraw so only the legitimate ghosts/badges come back.

Per-mark Locate / Delete in the panel reuse the existing focus / reject actions,
so this module only adds the two read/repair actions above.
"""

import json


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD INSPECTOR] " + msg)
        except Exception:
            pass

    def canonical_mtype(mark):
        mt = mark.get("mtype", "need_input")
        if mark.get("tool") == "compare" or mt in ("alternative", "conflict"):
            return "conflict"
        if mt == "constraint":
            return "constraint"
        return "need_input"

    def ghost_count():
        try:
            return len(getattr(m, "_ghost_opacity_records", {}) or {})
        except Exception:
            return 0

    def stray_groups():
        """Ephemeral graphics groups that are not empty. When no FuzzyCAD command
        is running these should all be zero; anything here is leftover the Repair
        button (or the next reconcile) will sweep."""
        out = []
        for gid in list(getattr(m, "_EPHEMERAL_GROUPS", []) or []):
            try:
                grp = m._group(gid)
                cnt = int(getattr(grp, "count", 0) or 0)
                if cnt > 0:
                    out.append({"id": gid, "count": cnt})
            except Exception:
                continue
        return out

    def gather():
        marks = list(getattr(m, "_marks", None) or [])
        by_type = {"need_input": 0, "constraint": 0, "conflict": 0}
        items = []
        for mk in marks:
            if mk.get("status", "open") != "open":
                continue
            t = canonical_mtype(mk)
            by_type[t] = by_type.get(t, 0) + 1
            items.append({
                "id": mk.get("id"),
                "tool": mk.get("tool"),
                "mtype": t,
                "label": mk.get("label") or mk.get("title") or (mk.get("tool") or "mark"),
                "reference_lost": bool(mk.get("reference_lost")),
            })
        return {
            "counts": {
                "open": len(items),
                "need_input": by_type["need_input"],
                "constraint": by_type["constraint"],
                "conflict": by_type["conflict"],
                "ghosted": ghost_count(),
            },
            "stray": stray_groups(),
            "items": items,
            "storage": "Document attributes · group “FuzzyCAD”",
        }

    def repair():
        result = {"swept": False, "restored": 0}
        try:
            n = ghost_count()
            fn = getattr(m, "_restore_all_bodies", None)
            if fn:
                fn()
                result["restored"] = n
        except Exception:
            log("restore failed\n{}".format(m.traceback.format_exc()))
        try:
            fn = getattr(m, "_sweep_ephemeral", None)
            if fn:
                fn()
                result["swept"] = True
        except Exception:
            log("sweep failed\n{}".format(m.traceback.format_exc()))
        # Redraw so the legitimate ghosts/badges are re-applied from the marks.
        try:
            m._redraw_marks()
        except Exception:
            pass
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        log("REPAIR swept={} restored={}".format(result["swept"], result["restored"]))
        return result

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                act = e.action if e is not None else None
                if act == "inspectorData":
                    try:
                        e.returnData = json.dumps(gather())
                    except Exception:
                        e.returnData = json.dumps({"counts": {}, "items": [], "stray": []})
                    return
                if act == "repairViewport":
                    try:
                        e.returnData = json.dumps({"ok": True, "result": repair()})
                    except Exception:
                        e.returnData = json.dumps({"ok": False})
                    return
            except Exception:
                log("inspector action failed\n{}".format(m.traceback.format_exc()))
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
    log("INSPECTOR READY: inspectorData + repairViewport exposed to the palette")
