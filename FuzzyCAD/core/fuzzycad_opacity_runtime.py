"""Preserve original Fusion body opacity for uncertainty visuals.

This module is runtime bookkeeping only; it no longer decides which marks should
look uncertain. The central visual authority supplies the body-level comic state.
That keeps opacity synchronized with the same Proposed/Editing/Resolved policy as
fill and sketch boundary while preserving exact original opacity for restoration.
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

        # Central authority: only inactive Proposed geometry receives the comic
        # body treatment. Editing bodies are restored to their original opacity.
        try:
            rows, _retained = m._visual_comic_subject_rows()
            for tok, body in rows:
                if body is not None and tok:
                    wanted[str(tok)] = body
            return wanted
        except Exception:
            pass

        # Install-time/backward-compatible fallback.
        for mark in list(getattr(m, "_marks", []) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            body = m._body.get(mark.get("id"))
            tok = body_token(body)
            if body is not None and tok:
                wanted[str(tok)] = body
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
        ghost_v = float(getattr(m, "GHOST_OPACITY", 0.5))
        for tok, body in wanted.items():
            if tok not in records:
                try:
                    cur = float(body.opacity)
                except Exception:
                    cur = 1.0
                if abs(cur - ghost_v) < 0.02:
                    cur = 1.0
                records[tok] = (body, cur)
            try:
                body.opacity = ghost_v
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
