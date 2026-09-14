"""Apply source-body opacity from the central visual authority.

This module owns only Fusion body opacity bookkeeping. It does not decide which
state a tool is in. `fuzzycad_uncertainty_visual.py` supplies body-level claims.

Opacity ownership is body-scoped, not mark-scoped. Several unresolved decisions
may legitimately share one body. FuzzyCAD captures that body's original Fusion
opacity once, keeps the record while ANY uncertainty claim remains, and restores
the original only after the last claim disappears. Accept/Reject order therefore
does not change the original value.

A claim can temporarily request no opacity override (for example a clean Editing
view). In that case the body is restored to its captured original for display,
but the ownership record is retained until the final claim is gone.

FuzzyCAD never infers ownership from a numeric opacity value. A user's own Fusion
Opacity Control is touched only when FuzzyCAD has an explicit captured original.
"""

import json

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "visual_opacity_originals_v1"


def install(m):
    old_run = m.run
    old_stop = m.stop
    old_remove_mark = m._remove_mark

    # Lookup handles are persistence handles only, never identity comparisons.
    records = {}       # saved token -> captured original numeric opacity
    crash_records = {} # persisted saved token -> captured original numeric opacity
    crash_loaded = [False]
    last_targets = [None]
    applied = {}       # saved token -> opacity FuzzyCAD last wrote

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
                body = m.adsk.fusion.BRepBody.cast(ent)
                if body is not None:
                    return body
        except Exception:
            pass
        return None

    def same_entity(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        try:
            return bool(a == b)
        except Exception:
            return False

    def native_body(body):
        if body is None:
            return None
        try:
            native = body.nativeObject
            return native if native is not None else body
        except Exception:
            return body

    def body_occurrence(body):
        try:
            return m.adsk.fusion.Occurrence.cast(body.assemblyContext)
        except Exception:
            return None

    def same_body_instance(a, b):
        """Compare live body identity while preserving occurrence context."""
        if same_entity(a, b):
            return True
        if a is None or b is None:
            return False
        if not same_entity(native_body(a), native_body(b)):
            return False
        oa, ob = body_occurrence(a), body_occurrence(b)
        if oa is None and ob is None:
            return True
        if oa is None or ob is None:
            return False
        return same_entity(oa, ob)

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

    def owner_token_for_body(body, preferred=None):
        """Reuse an existing ownership handle for the same resolved body instance."""
        load_crash_records()
        if preferred and (preferred in records or preferred in crash_records):
            return preferred
        for tok in list(records.keys()) + [t for t in crash_records.keys() if t not in records]:
            if same_body_instance(resolve_body(tok), body):
                return tok
        return preferred or body_token(body)

    def visual_claims():
        """Return one merged claim per live body instance.

        The visual authority normally already aggregates marks by body. We merge
        once more by resolved Fusion identity so two wrappers/tokens for the same
        occurrence cannot create two opacity owners.
        """
        groups = []
        try:
            states = list(m._visual_body_states() or [])
        except Exception:
            states = []

        if states:
            for row in states:
                body = row.get("body")
                if body is None:
                    continue
                group = None
                for existing in groups:
                    if same_body_instance(existing["body"], body):
                        group = existing
                        break
                if group is None:
                    group = {
                        "body": body,
                        "tokens": [],
                        "mark_ids": [],
                        "wants_comic": False,
                        "suppress_comic": False,
                        "editing_opacities": [],
                        "fallback_targets": [],
                    }
                    groups.append(group)
                tok = row.get("token") or body_token(body)
                if tok and tok not in group["tokens"]:
                    group["tokens"].append(str(tok))
                for mid in row.get("mark_ids") or []:
                    if mid not in group["mark_ids"]:
                        group["mark_ids"].append(mid)
                if row.get("wants_comic") or row.get("comic_visible"):
                    group["wants_comic"] = True
                if row.get("suppress_comic"):
                    group["suppress_comic"] = True
                editing_opacity = row.get("editing_opacity")
                if editing_opacity is not None:
                    try:
                        group["editing_opacities"].append(float(editing_opacity))
                    except Exception:
                        pass
                target = row.get("source_opacity")
                if target is not None:
                    try:
                        group["fallback_targets"].append(float(target))
                    except Exception:
                        pass
        else:
            # Compatibility fallback for older visual authority implementations.
            try:
                for tok, body, opacity in m._visual_opacity_subject_rows():
                    if body is None:
                        continue
                    groups.append({
                        "body": body,
                        "tokens": [str(tok)] if tok else [],
                        "mark_ids": [],
                        "wants_comic": False,
                        "suppress_comic": False,
                        "editing_opacities": [],
                        "fallback_targets": [float(opacity)] if opacity is not None else [],
                    })
            except Exception:
                pass

        claims = {}
        comic_opacity = float(getattr(m, "_VISUAL_COMIC_SOURCE_OPACITY", 0.02))
        for group in groups:
            # Preserve the visual authority's precedence: a clean Editing claim
            # suppresses Proposed comic. If that editing claim explicitly asks for
            # opacity (Hole/Fillet), use it. Otherwise show the captured original.
            if group["suppress_comic"]:
                target = (max(group["editing_opacities"])
                          if group["editing_opacities"] else None)
            elif group["wants_comic"]:
                target = comic_opacity
            elif group["editing_opacities"]:
                target = max(group["editing_opacities"])
            elif group["fallback_targets"]:
                # Fallback rows have no phase metadata. Use the least invasive
                # explicit target deterministically instead of traversal order.
                target = max(group["fallback_targets"])
            else:
                target = None

            preferred = group["tokens"][0] if group["tokens"] else body_token(group["body"])
            key = owner_token_for_body(group["body"], preferred)
            if not key:
                continue
            claims[str(key)] = {
                "body": group["body"],
                "target": target,
                "mark_ids": list(group["mark_ids"]),
            }
        return claims

    def desired_targets():
        return {tok: (row["body"], float(row["target"]))
                for tok, row in visual_claims().items()
                if row.get("target") is not None}

    def target_signature(claims):
        try:
            return tuple(sorted(
                (str(tok),
                 None if row.get("target") is None else round(float(row.get("target")), 4),
                 tuple(sorted(str(mid) for mid in row.get("mark_ids") or [])))
                for tok, row in claims.items()))
        except Exception:
            return ()

    def capture_original(tok, body):
        """Capture exactly what Fusion says the user's body opacity is now."""
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

    def original_for(tok):
        load_crash_records()
        if tok in records:
            return records.get(tok)
        return crash_records.get(tok)

    def restore_owned(tok, body=None, drop_owner=False):
        """Show the captured original, optionally releasing FuzzyCAD ownership."""
        original = original_for(tok)
        if original is None:
            return False
        if body is None:
            body = resolve_body(tok)
        if body is None:
            return False
        try:
            body.opacity = float(original)
        except Exception:
            return False
        applied.pop(tok, None)
        if drop_owner:
            records.pop(tok, None)
            crash_records.pop(tok, None)
            save_crash_records()
        return True

    def body_has_claim(body, claims=None):
        claims = claims if claims is not None else visual_claims()
        for row in claims.values():
            if same_body_instance(row.get("body"), body):
                return True
        return False

    def restore_orphan_body(body):
        """Restore only after the LAST uncertainty claim on this body disappears."""
        if body is None:
            return False
        claims = visual_claims()
        if body_has_claim(body, claims):
            # The body is still owned by at least one uncertainty. A clean Editing
            # claim may display the original opacity, but ownership must stay alive.
            return False
        tok = owner_token_for_body(body, body_token(body))
        if not tok or original_for(tok) is None:
            return False
        return restore_owned(tok, body, drop_owner=True)

    def recover_crash_records():
        load_crash_records()
        claims = visual_claims()
        changed = False
        for tok, original in list(crash_records.items()):
            body = resolve_body(tok)
            if body is None:
                continue
            claim = None
            for key, row in claims.items():
                if same_body_instance(row.get("body"), body):
                    claim = (key, row)
                    break
            if claim is not None:
                key, row = claim
                # Re-key a persisted old lookup handle onto the claim owner while
                # preserving the one true original value.
                records[key] = float(original)
                if key != tok:
                    crash_records[key] = float(original)
                    crash_records.pop(tok, None)
                    changed = True
                if row.get("target") is None:
                    try:
                        body.opacity = float(original)
                    except Exception:
                        pass
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
        claims = visual_claims()
        signature = target_signature(claims)
        phase_changed = signature != last_targets[0]
        load_crash_records()

        # Apply or temporarily release the visual override for every active claim.
        for tok, row in claims.items():
            body = row.get("body")
            target = row.get("target")
            if target is None:
                # Keep the captured original record while another uncertainty still
                # owns the body. Only the display override is temporarily released.
                if tok in records or tok in crash_records:
                    restore_owned(tok, body, drop_owner=False)
                continue

            if tok not in records:
                records[tok] = capture_original(tok, body)
            target = float(target)
            if applied.get(tok) == target:
                continue
            try:
                body.opacity = target
                applied[tok] = target
            except Exception:
                pass

        # Ownership ends only when no active uncertainty claim remains on the body.
        owned_tokens = set(records.keys()) | set(crash_records.keys())
        for tok in list(owned_tokens):
            body = resolve_body(tok)
            if body is not None and body_has_claim(body, claims):
                continue
            restore_owned(tok, body, drop_owner=True)

        try:
            m._ghosted = {tok: row["body"] for tok, row in claims.items()
                          if row.get("target") is not None}
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
            restore_owned(tok, resolve_body(tok), drop_owner=True)
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
    m._visual_opacity_claims = visual_claims

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

        # refresh_ghost owns the normal stacked-claim path. This is only a bounded
        # recovery attempt for a body whose last mark disappeared unexpectedly.
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
