"""Soft dependency prompts for FuzzyCAD, shown in the left tool rail.

Design principle: the system makes a possible coupling *visible* but never makes
the decision for the user. So this module does NOT auto-change, auto-link, or
mark anything as uncertain. After a Scale or Extrude is accepted, it detects the
nearby parts that might be affected and raises one soft nudge -- "you changed a
fitting/reaching dimension; have you considered these parts?" -- as a compact
banner at the top of the FuzzyCAD tool rail, and tints those parts orange while
the nudge is open. The reviewer dismisses it (or acts on their own); nothing is
applied automatically.

Timing matches how each operation couples: Move/Rotate ask *before* the motion
(a real together/separate fork, handled in fuzzycad_move_scope). Scale (fit) and
Extrude (reach) ask *afterward*, because scaling a bore or extending a boss does
not imply the mating part should change too -- there is no fork, only a check.

The prompt is intentionally runtime-only: it is an ephemeral "did you consider"
nudge, not a saved question, so it is never written to the document.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept
    old_redraw = m._redraw_marks
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    DEP_GID = "FuzzyCAD_DepColor"
    DEP_RGB = (225, 126, 38)          # same relationship hue Move uses
    FIT_TOOLS = ("scale", "scale_axis")
    REACH_TOOLS = ("extrude",)
    DEP_TOOLS = FIT_TOOLS + REACH_TOOLS

    # Runtime-only list of active nudges: {id, kind, text, related_tokens, source_token}
    PROMPTS = []
    seq = {"n": 0}

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

    # ---- left-rail banner --------------------------------------------------
    def toolbar():
        try:
            return m._ui.palettes.itemById(m.TOOLBAR_ID) if m._ui else None
        except Exception:
            return None

    def send_banner():
        p = toolbar()
        if p is None:
            return
        import json
        payload = {"prompts": [
            {"id": pr["id"], "text": pr["text"], "kind": pr["kind"]}
            for pr in PROMPTS]}
        try:
            p.sendInfoToHTML("depPrompt", json.dumps(payload))
        except Exception:
            pass

    # ---- related-body tint -------------------------------------------------
    def paint_dependencies():
        try:
            m._clear(DEP_GID)
        except Exception:
            return
        if not PROMPTS:
            return
        design = m._design()
        group = m._group(DEP_GID)
        if design is None or group is None:
            return
        seen = set()
        for pr in PROMPTS:
            for tok in pr.get("related_tokens", []):
                if tok in seen:
                    continue
                seen.add(tok)
                body = resolve_body(design, tok)
                if body is None:
                    continue
                try:
                    cg = group.addBRepBody(body)
                    cg.color = m._solid(DEP_RGB)
                    cg.setOpacity(0.45, True)
                except Exception:
                    continue

    def refresh():
        send_banner()
        try:
            paint_dependencies()
        except Exception:
            pass
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def has_prompt_for(source_token):
        return any(pr.get("source_token") == source_token for pr in PROMPTS)

    def add_prompt(tool, tokens, source_token):
        if not tokens:
            return
        if source_token and has_prompt_for(source_token):
            return
        seq["n"] += 1
        kind = "reach" if tool in REACH_TOOLS else "fit"
        PROMPTS.append({
            "id": seq["n"],
            "kind": kind,
            "text": followup_text(tool, len(tokens)),
            "related_tokens": list(tokens),
            "source_token": source_token,
        })
        log("PROMPT+ id={} kind={} related={}".format(seq["n"], kind, len(tokens)))

    def dismiss(pid):
        n = len(PROMPTS)
        if pid is None:
            PROMPTS[:] = []
        else:
            PROMPTS[:] = [pr for pr in PROMPTS if pr.get("id") != pid]
        if len(PROMPTS) != n:
            refresh()

    def reset():
        if PROMPTS:
            PROMPTS[:] = []
        try:
            m._clear(DEP_GID)
        except Exception:
            pass
        send_banner()

    m._reset_dependency_prompts = reset
    m._dependency_prompts = PROMPTS

    # ---- hooks -------------------------------------------------------------
    def accept(mark):
        tokens = []
        try:
            if mark.get("tool") in DEP_TOOLS:
                primary = m._body.get(mark["id"])
                tokens = detect_related_tokens(primary)
                source_token = body_token(primary)
        except Exception:
            tokens = []
            source_token = None
        ok = old_accept(mark)
        if ok and tokens:
            try:
                add_prompt(mark.get("tool"), tokens, source_token)
                refresh()
            except Exception:
                log("add prompt failed\n{}".format(m.traceback.format_exc()))
        return ok

    m._accept = accept

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            paint_dependencies()
        except Exception:
            log("paint failed\n{}".format(m.traceback.format_exc()))
        return result

    m._redraw_marks = redraw

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                if e is not None and e.action == "dismissDep":
                    data = json.loads(e.data) if e.data else {}
                    dismiss(data.get("id"))
                    try: e.returnData = json.dumps({"ok": True})
                    except Exception: pass
                    return
            except Exception:
                log("dismissDep failed\n{}".format(m.traceback.format_exc()))
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    log("DEPENDENCY PROMPTS READY: scale/extrude accept raises a left-rail nudge "
        "and tints the affected neighbours until dismissed")
