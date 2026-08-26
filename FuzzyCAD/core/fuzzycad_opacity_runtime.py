"""Apply source-body opacity from the central visual authority.

This module owns only Fusion body opacity bookkeeping. It does not decide which
state a tool is in. `fuzzycad_uncertainty_visual.py` supplies body-level targets:

- comic baseline -> source body almost hidden (paper fill is rendered above it)
- Fillet/Hole Editing -> source body at 0.50
- normal Editing / Resolved -> original Fusion opacity

Only entity tokens + original numeric opacity are retained long-term; BRepBody
wrappers are resolved fresh when a restore is required.
"""


def install(m):
    old_run = m.run
    old_stop = m.stop
    records = {}  # entity token -> original numeric opacity

    def body_token(body):
        try:
            return str(body.entityToken)
        except Exception:
            return None

    def resolve_body(tok):
        if not tok:
            return None
        try:
            design = m._design()
            if design is None:
                return None
            for ent in design.findEntityByToken(str(tok)):
                if isinstance(ent, m.adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def desired_targets():
        wanted = {}
        try:
            for tok, body, opacity in m._visual_opacity_subject_rows():
                if body is not None and tok and opacity is not None:
                    wanted[str(tok)] = (body, float(opacity))
            return wanted
        except Exception:
            pass

        # Install-time/backward fallback: old proposed-body ghost behavior.
        ghost_v = float(getattr(m, "GHOST_OPACITY", 0.5))
        for mark in list(getattr(m, "_marks", []) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            body = m._body.get(mark.get("id"))
            tok = body_token(body)
            if body is not None and tok:
                wanted[tok] = (body, ghost_v)
        return wanted

    def restore_token(tok):
        original = records.pop(tok, None)
        if original is None:
            return
        body = resolve_body(tok)
        if body is None:
            return
        try:
            if body.isValid:
                body.opacity = float(original)
        except Exception:
            try:
                body.opacity = float(original)
            except Exception:
                pass

    def capture_original(body, target):
        try:
            cur = float(body.opacity)
        except Exception:
            cur = 1.0

        # A document can be saved/reopened while an unresolved visual opacity is
        # applied. Do not capture that display-only value as the user's original.
        known_visual = [
            float(target),
            float(getattr(m, "GHOST_OPACITY", 0.5)),
            float(getattr(m, "_VISUAL_COMIC_SOURCE_OPACITY", 0.02)),
            float(getattr(m, "_VISUAL_SEMITRANSPARENT_SOURCE_OPACITY", 0.50)),
        ]
        if any(abs(cur - v) < 0.02 for v in known_visual):
            cur = 1.0
        return cur

    def refresh_ghost():
        wanted = desired_targets()

        for tok, (body, target) in wanted.items():
            if tok not in records:
                records[tok] = capture_original(body, target)
            try:
                body.opacity = float(target)
            except Exception:
                pass

        for tok in list(records.keys()):
            if tok not in wanted:
                restore_token(tok)

        try:
            m._ghosted = {tok: body for tok, (body, _target) in wanted.items()}
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
    m._sync_visual_opacity = refresh_ghost
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
