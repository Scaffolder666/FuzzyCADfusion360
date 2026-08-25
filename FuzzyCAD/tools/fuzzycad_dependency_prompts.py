"""Dependency check for FuzzyCAD, shown as a messageBox at accept.

Design principle: the system makes a possible coupling *visible* but never makes
the decision for the user. So this module does NOT auto-change, auto-link, or
mark anything. After a Scale or Extrude is accepted, it detects the nearby parts
that might be affected, highlights them orange, and raises one modal nudge --
"you changed a fitting/reaching dimension; do these parts still fit / any
overlap?" -- which the reviewer simply acknowledges. Nothing is applied here.

Timing matches how each operation couples: Move/Rotate and Scale ask a real
together/attached fork *before* the change (fuzzycad_move_scope /
fuzzycad_scale_scope). This afterward check is a different, awareness-only
question -- "now that it changed, is the fit still OK?" -- so it is a plain OK
acknowledgement, not another fork. It runs for Scale (fit) and Extrude (reach).

Loaded AFTER fuzzycad_dependent_follow so its _accept wrapper is outermost: the
messageBox appears only once the whole operation (the scale/extrude itself plus
any dependent parts that followed) has finished, not in the middle of it.

The nudge is intentionally runtime-only: an ephemeral "did you consider" check,
never written to the document.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept

    CHECK_GID = "FuzzyCAD_DepCheck"
    DEP_RGB = (225, 126, 38)          # same relationship hue Move/Scale use
    FIT_TOOLS = ("scale", "scale_axis")
    REACH_TOOLS = ("extrude",)
    DEP_TOOLS = FIT_TOOLS + REACH_TOOLS

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD DEP] " + msg)
        except Exception:
            pass

    # ---- geometry-first relation detection --------------------------------
    def body_token(body):
        try:
            return body.entityToken
        except Exception:
            return None

    def bbox_gap(a, b):
        amn, amx = a.minPoint, a.maxPoint
        bmn, bmx = b.minPoint, b.maxPoint
        dx = max(0.0, bmn.x - amx.x, amn.x - bmx.x)
        dy = max(0.0, bmn.y - amx.y, amn.y - bmx.y)
        dz = max(0.0, bmn.z - amx.z, amn.z - bmx.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def detect_related_tokens(primary):
        if primary is None:
            return []
        try:
            comp = primary.parentComponent
            bodies = comp.bRepBodies
        except Exception:
            return []
        try:
            _, size = m._bbox_center_size(primary)
        except Exception:
            size = 3.0
        tol = max(0.05, min(float(size) * 0.02, 0.20))  # 0.5mm .. 2mm
        try:
            pbb = primary.boundingBox
        except Exception:
            return []
        ptok = body_token(primary)
        rows = []
        for i in range(bodies.count):
            try:
                b = bodies.item(i)
                tok = body_token(b)
                if tok is None or tok == ptok:
                    continue
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                try:
                    if m._body_locked(b):
                        continue
                except Exception:
                    pass
                gap = bbox_gap(pbb, b.boundingBox)
                if gap <= tol:
                    rows.append((gap, tok))
            except Exception:
                continue
        rows.sort(key=lambda r: r[0])
        return [tok for _, tok in rows[:8]]

    def resolve_body(design, token):
        if design is None or not token:
            return None
        try:
            ents = design.findEntityByToken(token)
        except Exception:
            return None
        try:
            for e in ents:
                if isinstance(e, adsk.fusion.BRepBody):
                    return e
        except Exception:
            pass
        return None

    def followup_text(tool, n):
        parts = "part" if n == 1 else "parts"
        if tool in REACH_TOOLS:
            return "Extrude reaches {} nearby {} — checked for overlap?".format(n, parts)
        return "Scale changed a fit — do the {} highlighted {} still fit?".format(n, parts)

    # ---- transient highlight + modal nudge ---------------------------------
    def tint(tokens, on):
        try:
            m._clear(CHECK_GID)
        except Exception:
            pass
        if not on:
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass
            return
        design = m._design()
        group = m._group(CHECK_GID)
        if design is None or group is None:
            return
        for tok in tokens:
            body = resolve_body(design, tok)
            if body is None:
                continue
            try:
                cg = group.addBRepBody(body)
                cg.color = m._solid(DEP_RGB)
                cg.setOpacity(0.45, True)
            except Exception:
                continue
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def show_check(tool, tokens):
        """Highlight the nearby parts, then raise a plain OK acknowledgement."""
        tint(tokens, True)
        try:
            m._ui.messageBox(
                followup_text(tool, len(tokens)) + "\n\n"
                "Nothing was changed automatically — this is just a check.",
                "FuzzyCAD — dependency check",
                adsk.core.MessageBoxButtonTypes.OKButtonType,
                adsk.core.MessageBoxIconTypes.WarningIconType)
        except Exception:
            log("dependency messageBox failed\n{}".format(m.traceback.format_exc()))
        tint(tokens, False)
        log("CHECK shown tool={} related={}".format(tool, len(tokens)))

    def reset():
        try:
            m._clear(CHECK_GID)
        except Exception:
            pass

    # Kept for callers (e.g. Clear all) that used to flush the old rail banner.
    m._reset_dependency_prompts = reset
    m._dependency_prompts = []          # nothing persistent any more

    # ---- hook --------------------------------------------------------------
    def accept(mark):
        tool = mark.get("tool")
        tokens = []
        try:
            if tool in DEP_TOOLS:
                primary = m._body.get(mark["id"])
                tokens = detect_related_tokens(primary)   # neighbours before the op
        except Exception:
            tokens = []
        ok = old_accept(mark)     # the scale/extrude AND any dependent follow finish
        if ok and tokens:
            try:
                show_check(tool, tokens)
            except Exception:
                log("dependency check failed\n{}".format(m.traceback.format_exc()))
        return ok

    m._accept = accept

    log("DEPENDENCY CHECK READY: scale/extrude accept raises an OK messageBox "
        "and highlights the affected neighbours while it is open")
