"""Apply source-body opacity from the central visual authority.

This module owns only Fusion body opacity bookkeeping. It does not decide which
state a tool is in. `fuzzycad_uncertainty_visual.py` supplies body-level targets:

- comic baseline -> source body almost hidden (paper fill is rendered above it)
- Fillet/Hole Editing -> source body at 0.50
- normal Editing / Resolved -> original Fusion opacity

The original opacity is also mirrored into a small design attribute while an
override is active. That gives us a recovery path if Fusion/the add-in exits
before the normal stop callback restores the body. No Fusion native wrapper is
stored long-term: runtime and persisted records contain only entity tokens and
numeric opacity values.
"""

import json

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "visual_opacity_originals_v1"


def install(m):
    old_run = m.run
    old_stop = m.stop
    old_remove_mark = m._remove_mark
    records = {}  # entity token -> original numeric opacity
    crash_records = {}  # persisted token -> original numeric opacity
    crash_loaded = [False]
    last_targets = [None]  # pure-Python phase signature; avoids per-drag comic sync
    applied = {}  # entity token -> opacity we last WROTE (skip redundant per-frame writes)

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

    def target_signature(wanted):
        """Pure-data signature for transitions that can change comic visibility."""
        try:
            return tuple(sorted(
                (str(tok), round(float(target), 4))
                for tok, (_body, target) in wanted.items()))
        except Exception:
            return ()

    def visual_values(extra=None):
        vals = [
            float(getattr(m, "GHOST_OPACITY", 0.5)),
            float(getattr(m, "_VISUAL_COMIC_SOURCE_OPACITY", 0.02)),
            float(getattr(m, "_VISUAL_SEMITRANSPARENT_SOURCE_OPACITY", 0.50)),
        ]
        if extra is not None:
            try:
                vals.append(float(extra))
            except Exception:
                pass
        return vals

    def capture_original(tok, body, target):
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

        # Backward recovery for documents produced before persistent originals
        # existed. A saved display-only opacity should not become the new original.
        if any(abs(cur - v) < 0.02 for v in visual_values(target)):
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
            # Keep the persisted record. The body may become resolvable after a
            # document/feature rebuild or on the next add-in start.
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
        """Restore one body that no longer has any authoritative opacity target."""
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
        if original is not None:
            try:
                body.opacity = float(original)
                crash_records.pop(tok, None)
                save_crash_records()
                return True
            except Exception:
                return False

        # Legacy safety net: old builds did not persist the true original. Only
        # touch values that match a FuzzyCAD display override closely.
        try:
            cur = float(body.opacity)
        except Exception:
            return False
        if any(abs(cur - v) < 0.025 for v in visual_values()):
            try:
                body.opacity = 1.0
                return True
            except Exception:
                pass
        return False

    def recover_crash_records():
        """Recover stale opacity left by an interrupted previous session.

        Open unresolved marks keep their saved original in `records` and receive
        the current authoritative target again. Orphaned tokens are restored now.
        """
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
            # Only WRITE body.opacity when it actually changes. Re-writing the same
            # value every executePreview frame during a native manipulator drag is a
            # Fusion hard-crash (each write pokes the display like a mid-drag
            # refresh). Unchanged targets -> no write -> the drag stays stable.
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

        # Comic CustomGraphics are persistent per body. Synchronize only when the
        # body-level target actually changes, not on every manipulator frame.
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

    # Resolution is a terminal visual transition. Restore opacity immediately
    # after the mark disappears, before the heavier full viewport redraw. Sending
    # state here also lets Reject disappear from the panel immediately instead of
    # waiting for sketch/comic reconstruction to finish.
    def remove_mark(mid):
        result = old_remove_mark(mid)
        try:
            refresh_ghost()
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
