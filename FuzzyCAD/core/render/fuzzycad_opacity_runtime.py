"""Apply source-body opacity from the central visual authority.

This module owns only Fusion body opacity bookkeeping. It does not decide which
state a tool is in. `fuzzycad_uncertainty_visual.py` supplies body-level targets.

A critical ownership rule applies: FuzzyCAD restores opacity only when it has an
explicit record of the value it changed. A user's own Fusion Opacity Control is
never inferred to be stale merely because it happens to equal a FuzzyCAD display
value such as 0.50.
"""

import json

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "visual_opacity_originals_v1"


def install(m):
    old_run = m.run
    old_stop = m.stop
    old_remove_mark = m._remove_mark
    records = {}  # entity-token lookup handle -> captured original numeric opacity
    crash_records = {}  # persisted lookup handle -> original numeric opacity
    crash_loaded = [False]
    last_targets = [None]
    applied = {}  # lookup handle -> opacity we last wrote

    def design():
        try:
            return m._design()
        except Exception:
            return None

    def body_token(body):
        try:
            return str(body.entityToken)
        except Exception:
            return None

    def resolve_body(tok):
        if not tok:
            return None
        try:
            des = design()
            if des is None:
                return None
            for ent in des.findEntityByToken(str(tok)):
                if isinstance(ent, m.adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def load_crash_records():
        if crash_loaded[0]:
            return crash_records
        crash_loaded[0] = True
        crash_records.clear()
        des = design()
        if des is None:
            return crash_records
        try:
            attr = des.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
            if attr is None:
                return crash_records
            payload = json.loads(attr.value or "{}")
            if isinstance(payload, dict):
                for tok, value in payload.items():
                    try:
                        crash_records[str(tok)] = float(value)
                    except Exception:
                        pass
        except Exception:
            pass
        return crash_records

    def save_crash_records():
        des = design()
        if des is None:
            return
        try:
            attr = des.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        except Exception:
            attr = None
        if crash_records:
            try:
                text = json.dumps(crash_records, separators=(",", ":"), sort_keys=True)
                des.attributes.add(ATTR_GROUP, ATTR_NAME, text)
            except Exception:
                pass
        elif attr is not None:
            try:
                attr.deleteMe()
            except Exception:
                try:
                    des.attributes.add(ATTR_GROUP, ATTR_NAME, "{}")
                except Exception:
                    pass

    def desired_targets():
        wanted = {}
        try:
            for tok, body, opacity in m._visual_opacity_subject_rows():
                if body is not None and tok and opacity is not None:
                    wanted[str(tok)] = (body, float(opacity))
            return wanted
        except Exception:
            pass

        ghost_v = float(getattr(m, "GHOST_OPACITY", 0.5))
        for mark in list(getattr(m, "_marks", []) or []):
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            body = m._body.get(mark.get("id"))
            tok = body_token(body)
            if body is not None and tok:
                wanted[tok] = (body, ghost_v)
        return wanted

    def target_signature(wanted):
        try:
            return tuple(sorted(
                (str(tok), round(float(target), 4))
                for tok, (_body, target) in wanted.items()))
        except Exception:
            return ()

    def capture_original(tok, body, target):
        """Capture exactly what Fusion says the user's body opacity is now.

        Never infer an original value from the numeric opacity. A legitimate user
        setting can be 0.50, 0.16, or any other value that FuzzyCAD also uses.
        """
        load_crash_records()
        if tok in crash_records:
            try:
                return float(crash_records[tok])
            except Exception:
                pass
        try:
            cur = float(body.opacity)
        except Exception:
            cur = 1.0
        crash_records[str(tok)] = float(cur)
        save_crash_records()
        return float(cur)

    def restore_token(tok):
        load_crash_records()
        applied.pop(tok, None)
        original = records.pop(tok, None)
        if original is None:
            original = crash_records.get(tok)
        if original is None:
            return False
        body = resolve_body(tok)
        if body is None:
            return False
        ok = False
        try:
            if body.isValid:
                body.opacity = float(original)
                ok = True
        except Exception:
            try:
                body.opacity = float(original)
                ok = True
            except Exception:
                pass
        if ok:
            crash_records.pop(tok, None)
            save_crash_records()
        return ok

    def restore_orphan_body(body):
        """Restore only a body for which FuzzyCAD captured an original opacity."""
        tok = body_token(body)
        if not tok:
            return False
        if tok in desired_targets():
            return False

        load_crash_records()
        applied.pop(tok, None)
        original = records.pop(tok, None)
        if original is None:
            original = crash_records.get(tok)
        if original is None:
            # Ownership boundary: no FuzzyCAD record means do not touch the user's
            # native Opacity Control, regardless of its numeric value.
            return False
        try:
            body.opacity = float(original)
            crash_records.pop(tok, None)
            save_crash_records()
            return True
        except Exception:
            return False

    def recover_crash_records():
        load_crash_records()
        wanted = desired_targets()
        changed = False
        for tok, original in list(crash_records.items()):
            if tok in wanted:
                records[tok] = float(original)
                continue
            body = resolve_body(tok)
            if body is None:
                continue
            try:
                body.opacity = float(original)
                crash_records.pop(tok, None)
                records.pop(tok, None)
                applied.pop(tok, None)
                changed = True
            except Exception:
                pass
        if changed:
            save_crash_records()

    def refresh_ghost():
        wanted = desired_targets()
        signature = target_signature(wanted)
        phase_changed = signature != last_targets[0]
        load_crash_records()

        for tok, (body, target) in wanted.items():
            if tok not in records:
                records[tok] = capture_original(tok, body, target)
            target = float(target)
            if applied.get(tok) == target:
                continue
            try:
                body.opacity = target
                applied[tok] = target
            except Exception:
                pass

        for tok in list(records.keys()):
            if tok not in wanted:
                restore_token(tok)
                applied.pop(tok, None)

        try:
            m._ghosted = {tok: body for tok, (body, _target) in wanted.items()}
        except Exception:
            pass

        last_targets[0] = signature

        if phase_changed:
            try:
                sync = getattr(m, "_sync_comic_uncertainty", None)
                if sync is not None:
                    sync()
            except Exception:
                pass

    def restore_all_bodies():
        load_crash_records()
        tokens = set(records.keys()) | set(crash_records.keys())
        for tok in list(tokens):
            restore_token(tok)
        applied.clear()
        last_targets[0] = None
        try:
            m._ghosted.clear()
        except Exception:
            m._ghosted = {}

    m._refresh_ghost = refresh_ghost
    m._sync_visual_opacity = refresh_ghost
    m._restore_all_bodies = restore_all_bodies
    m._restore_orphan_visual_body = restore_orphan_body
    m._recover_visual_opacity = recover_crash_records
    m._ghost_opacity_records = records

    def remove_mark(mid):
        body = None
        try:
            body = m._body.get(mid)
        except Exception:
            body = None

        result = old_remove_mark(mid)
        try:
            refresh_ghost()
        except Exception:
            pass

        # If refresh did not already restore it, try the exact ownership record.
        # No record means no write; never force an arbitrary transparent body solid.
        if body is not None:
            try:
                restore_orphan_body(body)
            except Exception:
                pass

        try:
            m._send_state()
        except Exception:
            pass
        return result

    m._remove_mark = remove_mark

    def run(context):
        result = old_run(context)
        try:
            recover_crash_records()
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
