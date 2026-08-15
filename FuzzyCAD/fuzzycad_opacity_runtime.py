"""Preserve original Fusion body opacity without mutating the design during previews.

This deliberately keeps the opacity bookkeeping in Python memory only.  Writing
Design attributes from inputChanged/preview paths is avoided because those paths
run inside Fusion commands and should stay display-only.
"""


def install(m):
    old_run = m.run
    old_stop = m.stop
    records = {}  # token -> (body, original opacity)

    def body_token(body):
        try:
            return body.entityToken
        except Exception:
            return None

    def desired_bodies():
        wanted = {}
        for mark in list(getattr(m, "_marks", []) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            body = m._body.get(mark.get("id"))
            tok = body_token(body)
            if body is not None and tok:
                wanted[tok] = body
            if mark.get("tool") == "move" and mark.get("move_scope") == "together":
                for related in mark.get("related_bodies") or []:
                    rtok = body_token(related)
                    if related is not None and rtok:
                        wanted[rtok] = related
        return wanted

    def restore_token(tok):
        row = records.pop(tok, None)
        if row is None:
            return
        body, opacity = row
        try:
            valid = body.isValid
        except Exception:
            valid = True
        if valid:
            try:
                body.opacity = float(opacity)
            except Exception:
                pass

    def refresh_ghost():
        wanted = desired_bodies()
        for tok, body in wanted.items():
            if tok not in records:
                try:
                    records[tok] = (body, float(body.opacity))
                except Exception:
                    records[tok] = (body, 1.0)
            try:
                body.opacity = float(getattr(m, "GHOST_OPACITY", 0.5))
            except Exception:
                pass
        for tok in list(records.keys()):
            if tok not in wanted:
                restore_token(tok)
        try:
            m._ghosted = dict(wanted)
        except Exception:
            pass

    def restore_all_bodies():
        for tok in list(records.keys()):
            restore_token(tok)
        try:
            m._ghosted.clear()
        except Exception:
            m._ghosted = {}

    # Reassert after all older ghost wrappers.  No attributes, no feature/model
    # mutation: only BRepBody.opacity is touched and always restored to its exact
    # original runtime value.
    m._refresh_ghost = refresh_ghost
    m._restore_all_bodies = restore_all_bodies
    m._ghost_opacity_records = records

    def run(context):
        result = old_run(context)
        try:
            refresh_ghost()
        except Exception:
            pass
        return result

    def stop(context):
        try:
            restore_all_bodies()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
