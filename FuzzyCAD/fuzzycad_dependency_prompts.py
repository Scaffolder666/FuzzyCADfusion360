"""Soft dependency prompts for FuzzyCAD.

Design principle (from the project's collaboration model): the system makes a
possible coupling *visible*, but never makes the decision for the user. So this
module does NOT auto-change, auto-link, or auto-mark anything as uncertain. It
only asks a soft question -- "you changed a fitting/reaching dimension; have you
considered the neighbouring parts?" -- and tints those neighbours while that one
question is still open. The reviewer answers or dismisses it; the tint clears.

Timing differs by relation type, matching how each operation actually couples:

  * Move / Rotate (co-motion) -- handled elsewhere (fuzzycad_move_scope): the
    question is asked *before* the motion as a forced together/separate choice,
    because whether a neighbour travels along is a real fork.

  * Scale / Scale X-Y-Z (fit) and Extrude (reach) -- handled here: scaling a bore
    or extending a boss does not mean the mating part should change too, so there
    is no "do it together" fork. Instead, *after* the change is accepted, a new
    open follow-up card appears asking the reviewer to check fit / overlap. It
    pre-fills no geometry and can simply be dismissed.

Detection is geometry-first and conservative (bounding-box proximity within the
same component), the same cheap signal Move uses, run once at accept time -- not
per frame.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept
    old_redraw = m._redraw_marks

    DEP_GID = "FuzzyCAD_DepColor"
    # One consistent "relationship" colour across the app (same hue Move uses for
    # its nearby set), so a tinted body always reads as "the system is pointing at
    # this because of another change".
    DEP_RGB = (225, 126, 38)
    # Tools whose acceptance raises a *post-hoc* fit/reach question. Co-motion
    # tools (move/rotate) ask before the motion and are intentionally excluded.
    FIT_TOOLS = ("scale", "scale_axis")
    REACH_TOOLS = ("extrude",)
    DEP_TOOLS = FIT_TOOLS + REACH_TOOLS

    _followup_count = {"n": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD DEP] " + msg)
        except Exception:
            pass

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
        """Nearby/touching bodies in the same component, by bbox proximity.

        Deliberately the same cheap, conservative signal Move uses: a first-pass
        relation hint for imported multi-body STEP models that usually carry no
        joint metadata. Returns entityTokens (stable across the rebuild the
        accepted feature triggers) rather than live body proxies.
        """
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
        # cm: 0.5 mm minimum, up to 2 mm on larger objects.
        tol = max(0.05, min(float(size) * 0.02, 0.20))
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
                # Do not couple a body that already carries its own open question.
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

    def followup_text(mark, n):
        parts = "part" if n == 1 else "parts"
        if mark.get("tool") in REACH_TOOLS:
            return ("This extrude now reaches {} nearby {}. "
                    "Have you considered whether they overlap or interfere?").format(n, parts)
        return ("This scale changed a fitting dimension. "
                "Have you considered whether the {} highlighted {} still fit?").format(n, parts)

    def has_open_followup_for(source_token):
        for mk in m._marks:
            if (mk.get("followup") and mk.get("status", "open") == "open"
                    and mk.get("source_token") == source_token):
                return True
        return False

    def spawn_followup(mark, tokens):
        if not tokens:
            return
        primary = m._body.get(mark["id"])
        source_token = body_token(primary)
        if source_token and has_open_followup_for(source_token):
            return
        try:
            center, _ = m._bbox_center_size(primary)
        except Exception:
            center = list(mark.get("anchor", [0.0, 0.0, 0.0]))
        _followup_count["n"] += 1
        mid = m._next_id
        m._next_id = mid + 1
        reach = mark.get("tool") in REACH_TOOLS
        fmark = {
            "id": mid,
            "tool": "note",              # annotation-only: no geometry, no ghost
            "mtype": "constraint",       # amber "consider this" card
            "label": "Reach check" if reach else "Fit check",
            "num": _followup_count["n"],
            "status": "open",
            "comments": [],
            "anchor": list(center),
            "size": mark.get("size", 3.0),
            "text": followup_text(mark, len(tokens)),
            "followup": True,
            "source_token": source_token,
            "related_tokens": list(tokens),
        }
        m._marks.append(fmark)
        log("FOLLOWUP spawned id={} kind={} related={}".format(
            mid, "reach" if reach else "fit", len(tokens)))

    def accept(mark):
        # Detect BEFORE applying: the primary body proxy is still valid, and we
        # capture the neighbours by stable token so the post-rebuild tint works.
        tokens = []
        try:
            if mark.get("tool") in DEP_TOOLS:
                tokens = detect_related_tokens(m._body.get(mark["id"]))
        except Exception:
            log("detect failed\n{}".format(m.traceback.format_exc()))
        ok = old_accept(mark)
        if ok and tokens:
            try:
                spawn_followup(mark, tokens)
            except Exception:
                log("spawn failed\n{}".format(m.traceback.format_exc()))
        return ok

    m._accept = accept

    def paint_dependencies():
        try:
            m._clear(DEP_GID)
        except Exception:
            return
        open_followups = [mk for mk in m._marks
                          if mk.get("followup") and mk.get("status", "open") == "open"]
        if not open_followups:
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass
            return
        design = m._design()
        group = m._group(DEP_GID)
        if design is None or group is None:
            return
        seen = set()
        for mk in open_followups:
            for tok in mk.get("related_tokens", []):
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
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def redraw():
        old_redraw()
        try:
            paint_dependencies()
        except Exception:
            log("paint failed\n{}".format(m.traceback.format_exc()))

    m._redraw_marks = redraw

    log("DEPENDENCY PROMPTS READY: scale/extrude accept raises a soft fit/reach "
        "question and tints the affected neighbours until it is resolved")
