"""Expose degraded geometry references without another Fusion command lifecycle.

Persistence and shared-subject rebasing keep collaboration cards even when a
face/edge token can no longer be resolved. Surface that state in the sidebar and
block geometry-changing actions. Automatic same-body topology relinking happens
inside `core/fuzzycad_subject_decisions.py`; if that conservative relink cannot
find an unambiguous match, recovery stays explicit: reject/recreate the question.
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
        if tool in ("extrude", "hole"):
            # These decisions depend on a specific face, not merely on the host
            # body. A healthy body plus stale cached geometry must not silently
            # clear `reference_lost` after topology changed.
            try:
                return (valid(body) and valid(ent)
                        and adsk.fusion.BRepFace.cast(ent) is not None
                        and isinstance(geom, dict) and bool(geom))
            except Exception:
                return False
        if tool == "fillet":
            try:
                return (valid(body) and valid(ent)
                        and adsk.fusion.BRepEdge.cast(ent) is not None
                        and isinstance(geom, dict) and bool(geom))
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
