"""Single viewport reconciliation pass for FuzzyCAD.

Lifecycle policy is not defined here. The central visual authority supplies both
comic visibility and source-body opacity targets. This module repairs drift and
clears stale ephemeral interaction graphics.

Opacity ownership is exact: reconciliation may restore a body only when the
opacity runtime has a captured FuzzyCAD original for it. It never infers that a
user-authored transparent body is stale from the numeric opacity alone.

In-place Compare has one terminal invariant enforced here at the outer resolve
boundary: Confirm choice succeeds only when the selected subject remains and the
unselected subject is actually gone from the Fusion design. This is verified by
re-resolving the loser after deletion instead of trusting the delete call alone.
"""


def install(m):
    old_redraw = m._redraw_marks
    old_run = m.run

    EPHEMERAL_GROUPS = [
        getattr(m, "GROUP_PREVIEW", "FuzzyCAD_Preview"),
        "FuzzyCAD_DepCheck",
        "FuzzyCAD_FollowHighlight",
        "FuzzyCAD_HoverAnimation",
        "FuzzyCAD_HoverDirectionArrow",
        "FuzzyCAD_OperationHover",
        "FuzzyCAD_CompareConnectorPreview",
    ]
    m._EPHEMERAL_GROUPS = EPHEMERAL_GROUPS

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log(
                "[FuzzyCAD RECONCILE] " + str(msg))
        except Exception:
            pass

    def fuzzy_command_running():
        try:
            active = m._ui.activeCommand or ""
        except Exception:
            active = ""
        return isinstance(active, str) and active.startswith("FuzzyCAD_")

    def sweep_ephemeral():
        for gid in EPHEMERAL_GROUPS:
            try:
                m._clear(gid)
            except Exception:
                pass

    m._sweep_ephemeral = sweep_ephemeral

    def iter_live_bodies(design):
        try:
            comps = design.allComponents
        except Exception:
            return
        for i in range(comps.count):
            try:
                bodies = comps.item(i).bRepBodies
            except Exception:
                continue
            for j in range(bodies.count):
                try:
                    yield bodies.item(j)
                except Exception:
                    continue

    def reclaim_orphan_visual_opacity(design):
        """Restore only bodies explicitly owned by FuzzyCAD opacity bookkeeping."""
        restore = getattr(m, "_restore_orphan_visual_body", None)
        if restore is None:
            return 0
        restored = 0
        for body in iter_live_bodies(design):
            try:
                if restore(body):
                    restored += 1
            except Exception:
                pass
        return restored

    def reconcile(full_scan=False):
        design = m._design()
        if design is None:
            return

        try:
            recover = getattr(m, "_recover_visual_opacity", None)
            if recover is not None:
                recover()
        except Exception:
            pass
        try:
            sync = getattr(m, "_sync_visual_opacity", None)
            if sync is not None:
                sync()
        except Exception:
            pass

        if not fuzzy_command_running():
            sweep_ephemeral()

        if full_scan:
            try:
                reclaim_orphan_visual_opacity(design)
            except Exception:
                pass

    m._reconcile_viewport = reconcile
    m._reclaim_orphan_visual_opacity = reclaim_orphan_visual_opacity

    def reclaim_on_resolve(reason):
        try:
            design = m._design()
            if design is not None:
                reclaim_orphan_visual_opacity(design)
        except Exception:
            pass

    # ---- final in-place Compare terminal invariant -----------------------
    # The earlier Compare modules own creation, preview, and visibility. This
    # outer layer owns only the final postcondition: after Confirm choice, the
    # winner exists and the loser does not.

    def token(obj):
        if obj is None:
            return None
        try:
            return str(obj.entityToken)
        except Exception:
            return None

    def valid(obj):
        if obj is None:
            return False
        try:
            return bool(obj.isValid)
        except Exception:
            return True

    def same_entity(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        try:
            return bool(a == b)
        except Exception:
            return False

    def occurrence_path(occ):
        try:
            value = str(occ.fullPathName or "").strip()
            return value or None
        except Exception:
            return None

    def resolve_by_token(tok, caster):
        if not tok:
            return None
        try:
            design = m._design()
            if design is None:
                return None
            for ent in design.findEntityByToken(str(tok)):
                obj = caster(ent)
                if obj is not None and valid(obj):
                    return obj
        except Exception:
            pass
        return None

    def resolve_occurrence(alt):
        occ = resolve_by_token(
            alt.get("occurrence_token"), m.adsk.fusion.Occurrence.cast)
        if occ is not None:
            return occ
        path = alt.get("occurrence_path")
        if not path:
            return None
        try:
            design = m._design()
            all_occ = design.rootComponent.allOccurrences
            for i in range(all_occ.count):
                candidate = all_occ.item(i)
                if (candidate is not None and valid(candidate)
                        and occurrence_path(candidate) == str(path)):
                    return candidate
        except Exception:
            pass
        return None

    def native_body(body):
        if body is None:
            return None
        try:
            native = body.nativeObject
            return native if native is not None else body
        except Exception:
            return body

    def resolve_body(alt):
        for btok in alt.get("body_tokens") or []:
            body = resolve_by_token(btok, m.adsk.fusion.BRepBody.cast)
            if body is not None:
                return body

        # Entity tokens can be rebound differently in an assembly. Fall back to
        # exact occurrence + native-body identity captured when Compare was made.
        occ = resolve_occurrence(alt)
        native_tok = alt.get("native_body_token")
        target_native = resolve_by_token(native_tok, m.adsk.fusion.BRepBody.cast)
        if occ is None or not native_tok:
            return None
        try:
            bodies = occ.bRepBodies
            for i in range(bodies.count):
                candidate = bodies.item(i)
                if candidate is None or not valid(candidate):
                    continue
                candidate_native = native_body(candidate)
                if target_native is not None and same_entity(candidate_native, target_native):
                    return candidate
                if token(candidate_native) == str(native_tok):
                    return candidate
        except Exception:
            pass
        return None

    def compare_subject(alt):
        if not isinstance(alt, dict):
            return None
        if alt.get("subject_kind") == "occurrence":
            occ = resolve_occurrence(alt)
            if occ is not None:
                return {"kind": "occurrence", "object": occ, "alt": alt}
        body = resolve_body(alt)
        if body is not None:
            return {"kind": "body", "object": body, "alt": alt}
        return None

    def restore_compare_subject(subject):
        if subject is None:
            return False
        obj = subject.get("object")
        alt = subject.get("alt") or {}
        if obj is None or not valid(obj):
            return False
        try:
            if subject.get("kind") == "occurrence":
                obj.isLightBulbOn = bool(alt.get("occurrence_light_bulb", True))
            else:
                obj.isLightBulbOn = bool(alt.get("body_light_bulb", True))
            return True
        except Exception:
            return False

    def compare_subject_exists(alt):
        if not isinstance(alt, dict):
            return False
        if alt.get("subject_kind") == "occurrence":
            return resolve_occurrence(alt) is not None
        return resolve_body(alt) is not None

    def delete_compare_subject(subject):
        if subject is None:
            return False, "unselected subject could not be resolved"
        obj = subject.get("object")
        alt = subject.get("alt") or {}
        if obj is None or not valid(obj):
            return not compare_subject_exists(alt), "already absent"

        # Autodesk explicitly marks derived bodies/occurrences as non-deletable.
        try:
            if bool(getattr(obj, "isDerived", False)):
                return False, "unselected subject is derived and cannot be deleted"
        except Exception:
            pass

        target = obj
        if subject.get("kind") == "body":
            # Delete the native body definition, not an assembly proxy wrapper.
            target = native_body(obj)
            try:
                if bool(getattr(target, "isDerived", False)):
                    return False, "unselected body is derived and cannot be deleted"
            except Exception:
                pass

        try:
            result = target.deleteMe()
            log("COMPARE_DELETE_CALL kind={} result={}".format(
                subject.get("kind"), result))
        except Exception as exc:
            log("COMPARE_DELETE_EXCEPTION {}".format(exc))
            return False, "deleteMe raised an exception"

        # The return value is advisory here; the actual invariant is whether the
        # same subject can still be resolved in the design after the call.
        if compare_subject_exists(alt):
            return False, "unselected subject still exists after deleteMe"
        return True, subject.get("kind") or "subject"

    def accept_inplace_compare(mark):
        alts = (mark.get("alternatives") or [])[:2]
        choice = mark.get("selected")
        if choice not in (0, 1) or len(alts) < 2:
            try:
                m._ui.messageBox("Choose Option 1 or Option 2 first.")
            except Exception:
                pass
            return False

        winner = int(choice)
        loser = 1 - winner
        winner_subject = compare_subject(alts[winner])
        loser_subject = compare_subject(alts[loser])
        if winner_subject is None or loser_subject is None:
            try:
                m._ui.messageBox(
                    "FuzzyCAD couldn't resolve both comparison options. "
                    "The comparison is still unresolved.")
            except Exception:
                pass
            return False

        restore_compare_subject(winner_subject)
        ok, detail = delete_compare_subject(loser_subject)
        if not ok:
            log("COMPARE_CONFIRM_FAILED id={} choice={} reason={}".format(
                mark.get("id"), winner + 1, detail))
            try:
                m._ui.messageBox(
                    "FuzzyCAD couldn't remove the unselected comparison option. "
                    "The comparison is still unresolved.\n\n{}".format(detail))
            except Exception:
                pass
            return False

        # Re-resolve the winner after destructive topology changes and restore its
        # original light-bulb state once more. The caller removes the Conflict mark
        # only after this function returns True.
        winner_subject = compare_subject(alts[winner])
        if winner_subject is None:
            log("COMPARE_CONFIRM_FAILED id={} winner disappeared".format(mark.get("id")))
            return False
        restore_compare_subject(winner_subject)

        # Hard postcondition: loser absent, winner present.
        if compare_subject_exists(alts[loser]):
            log("COMPARE_CONFIRM_FAILED id={} loser survived postcondition".format(
                mark.get("id")))
            return False

        log("COMPARE_CONFIRM_DONE id={} keep={} removed={}".format(
            mark.get("id"), winner + 1, detail))
        return True

    old_accept = getattr(m, "_accept", None)
    if old_accept is not None:
        def accept(mark, *args, **kwargs):
            is_inplace_compare = bool(
                isinstance(mark, dict)
                and mark.get("tool") == "compare"
                and mark.get("inplace")
                and len(mark.get("alternatives") or []) >= 2)
            if is_inplace_compare:
                result = accept_inplace_compare(mark)
            else:
                result = old_accept(mark, *args, **kwargs)
            reclaim_on_resolve("accept")
            return result
        m._accept = accept

    old_remove_mark = getattr(m, "_remove_mark", None)
    if old_remove_mark is not None:
        def remove_mark(mid, *args, **kwargs):
            result = old_remove_mark(mid, *args, **kwargs)
            reclaim_on_resolve("remove")
            return result
        m._remove_mark = remove_mark

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            reconcile(False)
        except Exception:
            pass
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m._redraw_marks = redraw

    def run(context):
        result = old_run(context)
        try:
            reconcile(True)
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return result

    m.run = run
