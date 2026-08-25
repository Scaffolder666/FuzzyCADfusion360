"""Keep degraded-reference flags synchronized with the rehydrated runtime maps."""

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
                alternatives = (geom or {}).get("alternatives") or []
                return valid(ent) and bool([b for b in alternatives if valid(b)])
            except Exception:
                return False
        return valid(body) and isinstance(geom, dict) and bool(geom)

    def can_relink(mark):
        if mark.get("tool") == "compare":
            return False
        if mark.get("tool") == "axis_rotate":
            geom = m._geom.get(mark.get("id"), {}) or {}
            return bool(geom.get("axis_origin") or mark.get("axis_origin")) and bool(
                geom.get("axis_dir") or mark.get("axis_dir"))
        return True

    def public(mark):
        # A previous degraded snapshot can contain reference_lost=True even when
        # Fusion successfully resolves the token on a later open. Clear that
        # sticky flag as soon as the current runtime maps prove the link healthy.
        if mark.get("reference_lost") and healthy(mark):
            mark.pop("reference_lost", None)
        out = old_public(mark)
        out["reference_lost"] = bool(mark.get("reference_lost"))
        out["can_relink"] = bool(out["reference_lost"] and can_relink(mark))
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
            if action in ("edit", "compare_choice"):
                mark = m._find(data.get("id"))
                if mark is not None and mark.get("reference_lost"):
                    return
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
