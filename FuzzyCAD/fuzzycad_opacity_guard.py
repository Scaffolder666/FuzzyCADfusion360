"""Preserve native Fusion body opacity while FuzzyCAD shows open proposals.

FuzzyCAD temporarily fades source geometry, but that visualization must never
become a model-side display edit.  This patch records each body's exact original
opacity the first time it is ghosted, restores that exact value when the body is
no longer affected, and keeps a tiny Design attribute so an interrupted Fusion
session can recover the display state on the next run.
"""

import json

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "ghost_opacity_v1"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    # key -> {body, opacity, design, token}.  The design is kept with the record
    # so document switches can restore the old document even after another
    # design becomes active.
    records = {}
    last_saved = {}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD OPACITY] " + msg)
        except Exception:
            pass

    def active_design():
        try:
            return m._design()
        except Exception:
            return None

    def design_key(design):
        return str(id(design)) if design is not None else "none"

    def body_token(body):
        try:
            return body.entityToken
        except Exception:
            return None

    def key_for(design, token):
        return design_key(design) + ":" + str(token)

    def attr(design):
        if design is None:
            return None
        try:
            return design.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        except Exception:
            return None

    def read_registry(design):
        a = attr(design)
        if a is None:
            return {}
        try:
            data = json.loads(a.value or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write_registry(design, payload):
        if design is None:
            return
        dk = design_key(design)
        safe = {str(k): float(v) for k, v in payload.items()}
        text = json.dumps(safe, separators=(",", ":"), sort_keys=True)
        if last_saved.get(dk) == text:
            return
        try:
            if safe:
                design.attributes.add(ATTR_GROUP, ATTR_NAME, text)
            else:
                a = attr(design)
                if a is not None:
                    try:
                        a.deleteMe()
                    except Exception:
                        design.attributes.add(ATTR_GROUP, ATTR_NAME, "{}")
            last_saved[dk] = text
        except Exception:
            pass

    def registry_for_design(design):
        out = {}
        dk = design_key(design)
        prefix = dk + ":"
        for k, rec in records.items():
            if k.startswith(prefix):
                out[rec["token"]] = rec["opacity"]
        return out

    def resolve_body(design, token):
        if design is None or not token:
            return None
        try:
            for ent in design.findEntityByToken(token):
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def recover_crash_registry(design):
        saved = read_registry(design)
        if not saved:
            return 0
        restored = 0
        for token, opacity in saved.items():
            body = resolve_body(design, token)
            if body is None:
                continue
            try:
                body.opacity = float(opacity)
                restored += 1
            except Exception:
                pass
        write_registry(design, {})
        log("RECOVERED interrupted ghost opacity bodies={}".format(restored))
        return restored

    def target_bodies():
        """Return geometry-changing subjects that should currently look faded."""
        out = {}
        design = active_design()
        if design is None:
            return design, out
        for mark in list(getattr(m, "_marks", []) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            mid = mark.get("id")
            body = m._body.get(mid)
            tok = body_token(body)
            if body is not None and tok:
                out[tok] = body
            if mark.get("tool") == "move" and mark.get("move_scope") == "together":
                for related in mark.get("related_bodies") or []:
                    rtok = body_token(related)
                    if related is not None and rtok:
                        out[rtok] = related
        return design, out

    def remember(design, body):
        token = body_token(body)
        if not token:
            return None
        key = key_for(design, token)
        if key not in records:
            try:
                original = float(body.opacity)
            except Exception:
                original = 1.0
            records[key] = {
                "body": body,
                "opacity": original,
                "design": design,
                "token": token,
            }
        return key

    def restore_key(key):
        rec = records.pop(key, None)
        if rec is None:
            return False
        try:
            body = rec.get("body")
            if body is not None:
                try:
                    valid = body.isValid
                except Exception:
                    valid = True
                if valid:
                    body.opacity = float(rec.get("opacity", 1.0))
        except Exception:
            pass
        return True

    def refresh_ghost():
        design, wanted = target_bodies()
        dk = design_key(design)
        wanted_keys = set()
        for token, body in wanted.items():
            key = remember(design, body)
            if key:
                wanted_keys.add(key)
            try:
                body.opacity = float(getattr(m, "GHOST_OPACITY", 0.5))
            except Exception:
                pass

        # Restore records belonging to this design that are no longer affected.
        prefix = dk + ":"
        changed = False
        for key in list(records.keys()):
            if key.startswith(prefix) and key not in wanted_keys:
                changed = restore_key(key) or changed

        # Compatibility for code that only checks which tokens are ghosted.
        try:
            m._ghosted = dict(wanted)
        except Exception:
            pass

        payload = registry_for_design(design)
        write_registry(design, payload)
        return changed

    def restore_all_bodies():
        designs = {}
        for key, rec in list(records.items()):
            des = rec.get("design")
            if des is not None:
                designs[design_key(des)] = des
            restore_key(key)
        try:
            m._ghosted.clear()
        except Exception:
            m._ghosted = {}
        for des in designs.values():
            write_registry(des, {})

    m._refresh_ghost = refresh_ghost
    m._restore_all_bodies = restore_all_bodies
    m._ghost_opacity_records = records

    def run(context):
        # Restore an interrupted previous session before startup hygiene or
        # persistence has a chance to infer anything from the current opacity.
        recover_crash_registry(active_design())
        result = old_run(context)
        try:
            refresh_ghost()
        except Exception:
            pass
        log("READY: original body opacity is preserved exactly")
        return result

    def stop(context):
        try:
            restore_all_bodies()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
