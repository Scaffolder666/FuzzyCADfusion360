"""In-place Compare mark semantics and rendering.

This module owns only what happens *after* an in-place Compare Conflict mark
exists:

- hide the real alternatives while the conflict is unresolved;
- draw the selected alternative as the primary proposal while keeping the other
  alternative visible as a translucent comparison reference;
- accept by keeping the selected alternative and removing the other;
- restore hidden alternatives when the mark is resolved or the add-in stops.

Creation/selection is intentionally not implemented here. The authoritative
command flow is compare/fuzzycad_compare_selection_flow.py.

Important assembly rule
-----------------------
A body selected in the root assembly is commonly a proxy inside an Occurrence,
especially for imported/inserted STEP parts. In that case the *Occurrence* is the
comparison subject. Hiding/deleting the BRepBody definition directly can affect
other assembly instances and can leave the kept option hidden after topology or
entity-token changes. Therefore this module operates on Occurrence visibility and
Occurrence.deleteMe() whenever the selected body has an assemblyContext. Root
bodies keep the original body-level behavior.
"""


def install(m):
    adsk = m.adsk
    old_stop = m.stop
    old_accept = m._accept
    old_draw = m._DRAW.get("compare")

    MAX_DRAW_EDGES = 180
    UNSELECTED_RGB = (156, 158, 156)
    UNSELECTED_OPACITY = 0.20

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD COMPARE HERE] " + str(msg))
        except Exception:
            pass

    def trace(event, detail=""):
        try:
            fn = getattr(m, "_crash_trace", None)
            if fn is not None:
                fn(event, detail)
        except Exception:
            pass

    def token(obj):
        if obj is None:
            return None
        try:
            return str(obj.entityToken)
        except Exception:
            return None

    def resolve_body(design, tok):
        if design is None or not tok:
            return None
        try:
            for ent in design.findEntityByToken(str(tok)):
                body = adsk.fusion.BRepBody.cast(ent)
                if body is not None:
                    return body
        except Exception:
            pass
        return None

    def resolve_occurrence(design, tok):
        if design is None or not tok:
            return None
        try:
            for ent in design.findEntityByToken(str(tok)):
                occ = adsk.fusion.Occurrence.cast(ent)
                if occ is not None:
                    return occ
        except Exception:
            pass
        return None

    def collection_items(coll):
        rows = []
        if coll is None:
            return rows
        try:
            for i in range(coll.count):
                item = coll.item(i)
                if item is not None:
                    rows.append(item)
        except Exception:
            pass
        return rows

    def occurrence_for_body(body):
        if body is None:
            return None
        try:
            occ = body.assemblyContext
            return adsk.fusion.Occurrence.cast(occ) if occ is not None else None
        except Exception:
            return None

    def occurrence_path(occ):
        try:
            value = str(occ.fullPathName or "").strip()
            return value or None
        except Exception:
            return None

    def occurrence_key(occ):
        path = occurrence_path(occ)
        if path:
            return "path:" + path
        tok = token(occ)
        return ("token:" + tok) if tok else None

    def find_occurrence(design, tok=None, path=None):
        occ = resolve_occurrence(design, tok)
        if occ is not None:
            return occ
        if design is None or not path:
            return None
        try:
            all_occ = design.rootComponent.allOccurrences
            for i in range(all_occ.count):
                candidate = all_occ.item(i)
                if candidate is not None and occurrence_path(candidate) == path:
                    return candidate
        except Exception:
            pass
        return None

    def body_subject(alternative):
        design = m._design()
        toks = [str(t) for t in (alternative.get("body_tokens") or []) if t]
        bodies = [resolve_body(design, t) for t in toks]
        bodies = [b for b in bodies if b is not None]
        return {
            "kind": "body",
            "bodies": bodies,
            "body_tokens": toks,
            "occurrence": None,
            "occurrence_token": None,
            "occurrence_path": None,
            "key": None,
        }

    def subject_for_alternative(alternative, allow_occurrence=True):
        subject = body_subject(alternative)
        if not allow_occurrence or not subject["bodies"]:
            return subject

        occ = occurrence_for_body(subject["bodies"][0])
        if occ is None:
            return subject

        occ_tok = token(occ)
        occ_path = occurrence_path(occ)
        key = occurrence_key(occ)
        if not key:
            return subject

        occ_bodies = collection_items(getattr(occ, "bRepBodies", None))
        if not occ_bodies:
            occ_bodies = subject["bodies"]
        return {
            "kind": "occurrence",
            "bodies": occ_bodies,
            "body_tokens": subject["body_tokens"],
            "occurrence": occ,
            "occurrence_token": occ_tok,
            "occurrence_path": occ_path,
            "key": key,
        }

    def subjects_for_mark(mark):
        alternatives = (mark.get("alternatives") or [])[:2]
        subjects = [subject_for_alternative(alt) for alt in alternatives]

        # If two selected bodies happen to live inside the same occurrence, the
        # user is comparing bodies within that component, not the component with
        # itself. Fall back to body-level semantics for that special case.
        if len(subjects) == 2:
            a, b = subjects
            if (a.get("kind") == "occurrence" and b.get("kind") == "occurrence"
                    and a.get("key") and a.get("key") == b.get("key")):
                subjects = [body_subject(alternatives[0]), body_subject(alternatives[1])]
        return subjects

    # Store only pure-Python visibility state. Never retain Occurrence/BRepBody
    # wrappers across events.
    hidden_occurrences = {}  # stable key -> {token, path, original}
    hidden_bodies = {}       # body token -> {token, original}

    def remember_hide_occurrence(subject):
        occ = subject.get("occurrence")
        key = subject.get("key")
        if occ is None or not key:
            return
        if key not in hidden_occurrences:
            try:
                original = bool(occ.isLightBulbOn)
            except Exception:
                original = True
            hidden_occurrences[key] = {
                "token": subject.get("occurrence_token"),
                "path": subject.get("occurrence_path"),
                "original": original,
            }
        try:
            occ.isLightBulbOn = False
        except Exception:
            pass

    def remember_hide_body(body, tok):
        if body is None or not tok:
            return
        if tok not in hidden_bodies:
            try:
                original = bool(body.isLightBulbOn)
            except Exception:
                original = True
            hidden_bodies[tok] = {"token": tok, "original": original}
        try:
            body.isLightBulbOn = False
        except Exception:
            pass

    def restore_occurrence_row(key, row):
        design = m._design()
        occ = find_occurrence(design, row.get("token"), row.get("path"))
        if occ is not None:
            try:
                occ.isLightBulbOn = bool(row.get("original", True))
            except Exception:
                pass
        hidden_occurrences.pop(key, None)

    def restore_body_row(tok, row):
        body = resolve_body(m._design(), row.get("token") or tok)
        if body is not None:
            try:
                body.isLightBulbOn = bool(row.get("original", True))
            except Exception:
                pass
        hidden_bodies.pop(tok, None)

    def restore_subject_now(subject):
        """Restore a kept subject before any destructive loser operation."""
        if subject.get("kind") == "occurrence":
            key = subject.get("key")
            row = hidden_occurrences.get(key) or {}
            occ = subject.get("occurrence")
            if occ is not None:
                try:
                    occ.isLightBulbOn = bool(row.get("original", True))
                except Exception:
                    pass
            return

        for body, tok in zip(subject.get("bodies") or [], subject.get("body_tokens") or []):
            row = hidden_bodies.get(tok) or {}
            try:
                body.isLightBulbOn = bool(row.get("original", True))
            except Exception:
                pass

    # ---- renderer ---------------------------------------------------------
    def draw_subject_edges(group, mark, subject, seed_offset=0):
        seed = mark["id"] * 700 + seed_offset
        size = mark.get("size", 3.0)
        line_index = 0
        remaining = MAX_DRAW_EDGES

        for body in subject.get("bodies") or []:
            if remaining <= 0:
                break
            try:
                edge_count = min(int(body.edges.count), remaining)
                for i in range(edge_count):
                    poly = m._sample_edge(body.edges.item(i))
                    if len(poly) < 2:
                        continue
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
                            (150, 150, 150),
                            0.0,
                            seed + line_index,
                            weight=1,
                            strokes=2,
                        )
                    line_index += 1
                remaining -= edge_count
            except Exception:
                continue

    def draw_subject_translucent(group, subject):
        """Draw the non-selected option without mutating the real body's opacity."""
        rgb = UNSELECTED_RGB
        opacity = UNSELECTED_OPACITY
        try:
            st = getattr(m, "VISUAL_TOKENS", {}).get("conflict_unselected", {})
            rgb = tuple(st.get("rgb", rgb))
            opacity = float(st.get("opacity", opacity))
        except Exception:
            pass

        for body in subject.get("bodies") or []:
            try:
                cg = group.addBRepBody(body)
                if cg is None:
                    continue
                cg.color = m._solid(rgb)
                cg.setOpacity(opacity, True)
            except Exception:
                continue

    def draw_compare(group, mark, rgb, amp):
        if not mark.get("inplace"):
            if old_draw is not None:
                return old_draw(group, mark, rgb, amp)
            return

        subjects = subjects_for_mark(mark)
        selected = mark.get("selected")
        if len(subjects) < 1:
            return

        if selected in (0, 1) and int(selected) < len(subjects):
            primary = int(selected)
            secondary = 1 - primary
            draw_subject_edges(group, mark, subjects[primary], seed_offset=0)
            if 0 <= secondary < len(subjects):
                draw_subject_translucent(group, subjects[secondary])
            return

        # Before a choice is made, preserve the compact unresolved baseline used
        # by the current UI: show Option 1 as the proposal subject. Choosing either
        # card option then adds the other one back as a translucent comparison.
        draw_subject_edges(group, mark, subjects[0], seed_offset=0)

    m._DRAW["compare"] = draw_compare

    # ---- unresolved real-subject visibility ------------------------------
    def reconcile_visibility():
        want_occurrences = {}
        want_bodies = {}

        for mark in list(getattr(m, "_marks", []) or []):
            if not (
                mark.get("tool") == "compare"
                and mark.get("inplace")
                and mark.get("status", "open") == "open"
            ):
                continue

            for subject in subjects_for_mark(mark):
                if subject.get("kind") == "occurrence":
                    key = subject.get("key")
                    if key:
                        want_occurrences[key] = subject
                else:
                    for body, tok in zip(
                            subject.get("bodies") or [],
                            subject.get("body_tokens") or []):
                        if body is not None and tok:
                            want_bodies[tok] = body

        for subject in want_occurrences.values():
            remember_hide_occurrence(subject)
        for tok, body in want_bodies.items():
            remember_hide_body(body, tok)

        for key, row in list(hidden_occurrences.items()):
            if key not in want_occurrences:
                restore_occurrence_row(key, row)
        for tok, row in list(hidden_bodies.items()):
            if tok not in want_bodies:
                restore_body_row(tok, row)

    # ---- terminal semantics ----------------------------------------------
    def delete_subject(subject):
        if subject.get("kind") == "occurrence":
            occ = subject.get("occurrence")
            if occ is None:
                return False, "occurrence could not be resolved"
            try:
                if bool(getattr(occ, "isDerived", False)):
                    return False, "derived occurrence cannot be deleted"
            except Exception:
                pass
            try:
                result = occ.deleteMe()
                return (result is not False), "occurrence"
            except Exception:
                return False, "occurrence delete failed"

        bodies = list(subject.get("bodies") or [])
        if not bodies:
            return False, "body could not be resolved"
        deleted = 0
        for body in bodies:
            try:
                if bool(getattr(body, "isDerived", False)):
                    return False, "derived body cannot be deleted"
            except Exception:
                pass
            try:
                result = body.deleteMe()
                if result is not False:
                    deleted += 1
            except Exception:
                return False, "body delete failed"
        return deleted == len(bodies), "{} body(s)".format(deleted)

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

        subjects = subjects_for_mark(mark)
        if len(subjects) < 2:
            return False

        winner = int(choice)
        loser = 1 - winner
        winner_subject = subjects[winner]
        loser_subject = subjects[loser]

        trace(
            "COMPARE_ACCEPT_BEGIN",
            "id={} choice={} winner_kind={} loser_kind={} winner_path={} loser_path={}".format(
                mark.get("id"),
                winner + 1,
                winner_subject.get("kind"),
                loser_subject.get("kind"),
                winner_subject.get("occurrence_path"),
                loser_subject.get("occurrence_path"),
            ),
        )

        # Critical ordering for imported STEP/assembly subjects: restore the kept
        # occurrence before deleting the loser. Deleting a body/component can
        # invalidate other entity references; the winner must not depend on a
        # later token re-resolution just to become visible again.
        restore_subject_now(winner_subject)

        ok, detail = delete_subject(loser_subject)
        if not ok:
            trace("COMPARE_ACCEPT_FAILED", "id={} reason={}".format(mark.get("id"), detail))
            log("ACCEPT failed: " + detail)
            try:
                reconcile_visibility()
            except Exception:
                pass
            try:
                m._ui.messageBox(
                    "FuzzyCAD couldn't remove the unselected comparison option. "
                    "The comparison is still unresolved."
                )
            except Exception:
                pass
            return False

        # Re-assert winner visibility through the already-resolved local subject.
        # No long-lived native wrapper is retained after this synchronous event.
        restore_subject_now(winner_subject)
        trace(
            "COMPARE_ACCEPT_DONE",
            "id={} keep={} removed={}".format(mark.get("id"), winner + 1, detail),
        )
        log("ACCEPT keep=Option {} removed={}".format(winner + 1, detail))
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
        # Restore every browser visibility state this module changed. Resolve
        # fresh wrappers; the dictionaries above contain pure-Python values only.
        try:
            for key, row in list(hidden_occurrences.items()):
                restore_occurrence_row(key, row)
            for tok, row in list(hidden_bodies.items()):
                restore_body_row(tok, row)
        except Exception:
            pass
        return old_stop(context)

    m.stop = stop
