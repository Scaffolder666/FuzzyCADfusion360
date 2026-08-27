"""In-place Compare mark semantics and rendering.

This module owns only what happens *after* an in-place Compare Conflict mark
exists:

- hide the real alternatives while the conflict is unresolved;
- draw the currently shown alternative with proposal strokes;
- accept by keeping the selected alternative and deleting the other;
- restore hidden alternatives when the mark is resolved or the add-in stops.

Creation/selection is intentionally not implemented here. The authoritative
command flow is compare/fuzzycad_compare_selection_flow.py.
"""


def install(m):
    adsk = m.adsk
    old_stop = m.stop
    old_accept = m._accept
    old_draw = m._DRAW.get("compare")

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD COMPARE HERE] " + str(msg))
        except Exception:
            pass

    def resolve_body(design, tok):
        if design is None or not tok:
            return None
        try:
            for ent in design.findEntityByToken(tok):
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    # Tokens currently hidden by unresolved in-place comparisons. Keep tokens,
    # never long-lived BRepBody wrappers, so shutdown/reconcile resolves fresh.
    hidden = set()

    def shown_index(mark):
        selected = mark.get("selected")
        return selected if selected in (0, 1) else 0

    # ---- renderer ---------------------------------------------------------
    def draw_compare(group, mark, rgb, amp):
        if not mark.get("inplace"):
            if old_draw is not None:
                return old_draw(group, mark, rgb, amp)
            return

        design = m._design()
        alternatives = mark.get("alternatives") or []
        shown = shown_index(mark)
        if not (0 <= shown < len(alternatives)):
            return

        seed = mark["id"] * 700
        size = mark.get("size", 3.0)
        line_index = 0
        for tok in alternatives[shown].get("body_tokens", []):
            body = resolve_body(design, tok)
            if body is None:
                continue
            try:
                polylines = m._sample_edges(body.edges)
            except Exception:
                continue
            for poly in polylines:
                if len(poly) < 2:
                    continue
                try:
                    if hasattr(m, "_visual_stroke"):
                        m._visual_stroke(
                            group,
                            poly,
                            "proposal_outer",
                            seed + line_index,
                            size,
                        )
                    else:
                        m._sketchy(
                            group,
                            poly,
                            rgb,
                            amp,
                            seed + line_index,
                            weight=1,
                            strokes=2,
                        )
                    line_index += 1
                except Exception:
                    continue

    m._DRAW["compare"] = draw_compare

    # ---- unresolved real-body visibility --------------------------------
    def reconcile_visibility():
        design = m._design()
        if design is None:
            return

        want_hidden = set()
        for mark in list(getattr(m, "_marks", []) or []):
            if not (
                mark.get("tool") == "compare"
                and mark.get("inplace")
                and mark.get("status", "open") == "open"
            ):
                continue
            for alternative in (mark.get("alternatives") or [])[:2]:
                for tok in alternative.get("body_tokens", []):
                    if tok:
                        want_hidden.add(tok)

        for tok in want_hidden:
            body = resolve_body(design, tok)
            if body is None:
                continue
            try:
                body.isVisible = False
                hidden.add(tok)
            except Exception:
                pass

        for tok in list(hidden):
            if tok in want_hidden:
                continue
            body = resolve_body(design, tok)
            if body is not None:
                try:
                    body.isVisible = True
                except Exception:
                    pass
            hidden.discard(tok)

    # ---- terminal semantics ----------------------------------------------
    def accept(mark):
        if not (mark.get("tool") == "compare" and mark.get("inplace")):
            return old_accept(mark)

        choice = mark.get("selected")
        if choice not in (0, 1):
            try:
                m._ui.messageBox("Choose Option 1 or Option 2 first.")
            except Exception:
                pass
            return False

        winner = int(choice)
        loser = 1 - winner
        design = m._design()
        alternatives = mark.get("alternatives") or []
        deleted = 0

        if 0 <= loser < len(alternatives):
            for tok in alternatives[loser].get("body_tokens", []):
                hidden.discard(tok)
                body = resolve_body(design, tok)
                if body is None:
                    continue
                try:
                    body.deleteMe()
                    deleted += 1
                except Exception:
                    log("delete loser failed tok={}".format(tok))

        if 0 <= winner < len(alternatives):
            for tok in alternatives[winner].get("body_tokens", []):
                hidden.discard(tok)
                body = resolve_body(design, tok)
                if body is None:
                    continue
                try:
                    body.isVisible = True
                except Exception:
                    pass

        log("ACCEPT keep=Option {} deleted={}".format(winner + 1, deleted))
        return True

    m._accept = accept

    old_redraw = m._redraw_marks

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            reconcile_visibility()
        except Exception:
            log("visibility reconcile failed\n{}".format(m.traceback.format_exc()))
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m._redraw_marks = redraw

    def stop(context):
        # Resolve fresh wrappers before shutdown and restore every body this
        # module hid. Selection-command cleanup belongs to selection_flow.
        try:
            design = m._design()
            for tok in list(hidden):
                body = resolve_body(design, tok)
                if body is not None:
                    try:
                        body.isVisible = True
                    except Exception:
                        pass
            hidden.clear()
        except Exception:
            pass
        return old_stop(context)

    m.stop = stop
