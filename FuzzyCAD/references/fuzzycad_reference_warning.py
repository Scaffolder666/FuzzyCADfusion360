"""Expose degraded geometry references without adding another Fusion command lifecycle.

The persistence layer already retains rows whose entity token cannot be resolved.
For the stability build, surface that state in the sidebar and block geometry-
changing actions. Recovery stays explicit: reject/recreate the question after the
model has changed. This avoids registering a Relink command/custom event in the
critical runtime path.
"""

import json


def install(m):
    adsk = m.adsk
    old_public = m._public
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    def valid(obj):
        if obj is None:
            return False
        try:
            return bool(obj.isValid)
        except Exception:
            return True

    def healthy(mark):
        mid = mark.get("id")
        tool = mark.get("tool")
        geom = m._geom.get(mid)
        ent = m._entity.get(mid)
        body = m._body.get(mid)
        if tool == "note":
            return valid(ent)
        if tool == "compare":
            try:
                alts = (geom or {}).get("alternatives") or []
                return valid(ent) and len([b for b in alts if valid(b)]) >= 2
            except Exception:
                return False
        return valid(body) and isinstance(geom, dict) and bool(geom)

    def public(mark):
        if mark.get("reference_lost") and healthy(mark):
            mark.pop("reference_lost", None)
        out = old_public(mark)
        out["reference_lost"] = bool(mark.get("reference_lost"))
        out["can_relink"] = False
        return out

    m._public = public

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

            if action in ("accept", "edit", "editManipulator", "compare_choice"):
                mark = m._find(data.get("id"))
                if mark is not None and mark.get("reference_lost"):
                    return
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
