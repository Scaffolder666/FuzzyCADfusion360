"""Persist FuzzyCAD collaboration state inside the Fusion design.

The document stores one compact JSON attribute. Geometry references are stored
as Fusion entity tokens and are resolved again with Design.findEntityByToken
when the add-in opens the file. CustomGraphics are visualization only.

Persistence is deliberately defensive:
- never overwrite a non-empty saved snapshot from an unhydrated empty runtime;
- keep the previous snapshot in a backup attribute before each write;
- restore cards even when a geometry token can no longer be resolved, so a
  collaboration decision does not silently disappear from the sidebar.
"""

import json

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "uncertainty_state_v1"
BACKUP_NAME = "uncertainty_state_v1_backup"
SCHEMA_VERSION = 1


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    old_remove_mark = m._remove_mark
    old_apply_edit = m._apply_edit
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    CurrentLaunchHandler = m.LaunchHandler
    CurrentNoteDestroy = m.NoteDestroy

    state = {
        "loading": False,
        "saving": False,
        "hydrated": False,
        "load_failed": False,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD STORE] " + msg)
        except Exception:
            pass

    def design():
        try:
            return m._design()
        except Exception:
            return None

    def attribute(name):
        des = design()
        if des is None:
            return None
        try:
            return des.attributes.itemByName(ATTR_GROUP, name)
        except Exception:
            return None

    def snapshot(name=ATTR_NAME):
        attr = attribute(name)
        if attr is None:
            return None, {}, []
        try:
            text = attr.value or ""
        except Exception:
            text = ""
        try:
            payload = json.loads(text or "{}")
            rows = payload.get("marks") or []
            if not isinstance(rows, list):
                rows = []
            return text, payload, rows
        except Exception:
            return text, None, None

    def entity_token(ent):
        if ent is None:
            return None
        try:
            return ent.entityToken
        except Exception:
            return None

    def json_safe(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            out = []
            for v in value:
                sv = json_safe(v)
                if sv is not None or v is None:
                    out.append(sv)
            return out
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                # Live Fusion objects are reconstructed from tokens below.
                if k in ("related_bodies", "candidate_body"):
                    continue
                sv = json_safe(v)
                if sv is not None or v is None:
                    out[str(k)] = sv
            return out
        return None

    def serialize_mark(mark):
        mid = mark.get("id")
        related = []
        for body in mark.get("related_bodies", []) or []:
            tok = entity_token(body)
            if tok:
                related.append(tok)
        return {
            "mark": json_safe(mark),
            "entity_token": entity_token(m._entity.get(mid)),
            "body_token": entity_token(m._body.get(mid)),
            "related_tokens": related,
        }

    def save_state(reason="state"):
        if state["loading"] or state["saving"]:
            return False
        des = design()
        if des is None:
            return False

        old_text, old_payload, old_rows = snapshot(ATTR_NAME)
        old_count = len(old_rows) if isinstance(old_rows, list) else 0

        # Critical safety rule. If startup/rehydration failed and runtime is
        # empty, lifecycle events such as tool-switch/stop must not erase a
        # non-empty document snapshot.
        if old_count > 0 and not state["hydrated"] and len(m._marks) == 0:
            log("SAVE BLOCKED reason={} saved_marks={} runtime=0 hydration_not_confirmed".format(
                reason, old_count))
            return False

        state["saving"] = True
        try:
            payload = {
                "schema": SCHEMA_VERSION,
                "marks": [serialize_mark(mark) for mark in m._marks],
                "meta": {"reason": str(reason)},
            }
            text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

            # Keep one previous snapshot. This makes future persistence failures
            # recoverable without depending on CustomGraphics leftovers.
            if old_text and old_text != text:
                try:
                    des.attributes.add(ATTR_GROUP, BACKUP_NAME, old_text)
                except Exception:
                    pass

            des.attributes.add(ATTR_GROUP, ATTR_NAME, text)
            state["hydrated"] = True
            state["load_failed"] = False
            log("SAVE reason={} marks={} bytes={} backup_old_marks={}".format(
                reason, len(m._marks), len(text.encode("utf-8")), old_count))
            return True
        except Exception:
            log("SAVE failed reason={}\n{}".format(reason, m.traceback.format_exc()))
            return False
        finally:
            state["saving"] = False

    def resolve(tok, cls=None):
        if not tok:
            return None
        des = design()
        if des is None:
            return None
        try:
            matches = des.findEntityByToken(tok)
            for ent in matches:
                if cls is None or isinstance(ent, cls):
                    return ent
        except Exception:
            pass
        return None

    def reconstruct_geom(mark, ent, body):
        tool = mark.get("tool")
        if tool == "note":
            return {}
        if tool == "compare":
            alternatives = []
            for alt in mark.get("alternatives") or []:
                if not isinstance(alt, dict):
                    continue
                b = resolve(alt.get("token"), adsk.fusion.BRepBody)
                if b is not None:
                    alternatives.append(b)
            return {"alternatives": alternatives} if len(alternatives) >= 2 else None
        if tool in ("move", "rotate", "scale", "scale_axis", "axis_rotate"):
            if body is None:
                return None
            geom = {"edges": m._sample_edges(body.edges)}
            if tool == "axis_rotate":
                geom["axis_origin"] = list(mark.get("axis_origin", mark.get("anchor", [0, 0, 0])))
                geom["axis_dir"] = list(mark.get("axis_dir", [0, 0, 1]))
            return geom
        if tool in ("extrude", "fillet") and ent is not None:
            try:
                pending = m._build_pending(tool, ent)
                return pending.get("geom") if pending else None
            except Exception:
                return None
        return {"edges": m._sample_edges(body.edges)} if body is not None else {}

    def load_payload(payload, source_name):
        rows = payload.get("marks") or [] if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []

        state["loading"] = True
        state["hydrated"] = False
        state["load_failed"] = False
        try:
            try: m._restore_all_bodies()
            except Exception: pass
            m._marks[:] = []
            m._geom.clear(); m._entity.clear(); m._body.clear()
            m._tool_count.clear()

            max_id = 0
            loaded = 0
            degraded = 0
            skipped = 0
            for row in rows:
                if not isinstance(row, dict):
                    skipped += 1
                    continue
                mark = row.get("mark") or {}
                if not isinstance(mark, dict) or "tool" not in mark:
                    skipped += 1
                    continue
                try:
                    mid = int(mark.get("id"))
                except Exception:
                    skipped += 1
                    continue
                mark["id"] = mid
                max_id = max(max_id, mid)

                tool = mark.get("tool")
                body = resolve(row.get("body_token"), adsk.fusion.BRepBody)
                ent = resolve(row.get("entity_token"))
                if tool == "compare" and ent is None:
                    ent = resolve(mark.get("target_token"))
                if body is None and isinstance(ent, (adsk.fusion.BRepBody, adsk.fusion.BRepFace, adsk.fusion.BRepEdge)):
                    try:
                        body = ent if isinstance(ent, adsk.fusion.BRepBody) else ent.body
                    except Exception:
                        pass

                geom = reconstruct_geom(mark, ent, body)
                if geom is None:
                    # Do not drop the collaboration decision just because Fusion
                    # can no longer resolve its geometry token. Keep the card and
                    # flag it as degraded; the viewport simply has no proposal
                    # geometry for this row.
                    geom = {}
                    mark["reference_lost"] = True
                    degraded += 1

                if tool not in ("note", "compare") and body is None:
                    mark["reference_lost"] = True
                    degraded += 1

                related = []
                for tok in row.get("related_tokens") or []:
                    rb = resolve(tok, adsk.fusion.BRepBody)
                    if rb is not None:
                        related.append(rb)
                if related:
                    mark["related_bodies"] = related

                m._marks.append(mark)
                m._geom[mid] = geom
                if ent is not None:
                    m._entity[mid] = ent
                if body is not None:
                    m._body[mid] = body
                try:
                    n = int(mark.get("num", 1) or 1)
                except Exception:
                    n = 1
                m._tool_count[tool] = max(m._tool_count.get(tool, 0), n)
                loaded += 1

            m._next_id = max(max_id + 1, 1)

            for mark in list(m._marks):
                if mark.get("tool") in ("extrude", "fillet") and not mark.get("reference_lost"):
                    try: m._compute_real(mark)
                    except Exception: pass

            try: m._redraw_marks()
            except Exception: pass
            try: m._send_state()
            except Exception: pass

            # Valid rows are considered hydrated even when geometry references
            # are degraded, because the decision/card itself has been recovered.
            state["hydrated"] = (loaded > 0 or len(rows) == 0)
            state["load_failed"] = bool(rows and loaded == 0)
            log("LOAD source={} restored={} degraded={} skipped={} next_id={}".format(
                source_name, loaded, degraded, skipped, m._next_id))
            return loaded
        except Exception:
            state["load_failed"] = True
            state["hydrated"] = False
            log("LOAD failed source={}\n{}".format(source_name, m.traceback.format_exc()))
            return 0
        finally:
            state["loading"] = False

    def load_state():
        state["hydrated"] = False
        state["load_failed"] = False
        text, payload, rows = snapshot(ATTR_NAME)
        if text is None:
            # A genuinely new/no-state document is safely hydrated as empty.
            state["hydrated"] = True
            try: m._send_state()
            except Exception: pass
            log("LOAD no saved state")
            return 0

        if payload is None:
            # Only use the backup when the primary snapshot is corrupt. Do not
            # resurrect a valid intentionally-empty primary snapshot.
            btext, bpayload, brows = snapshot(BACKUP_NAME)
            if bpayload is not None:
                log("LOAD primary JSON invalid; trying backup")
                return load_payload(bpayload, "backup")
            state["load_failed"] = True
            log("LOAD invalid JSON and no valid backup")
            return 0

        return load_payload(payload, "primary")

    # Save at discrete collaboration events, never on every drag frame.
    def remove_mark(mid):
        old_remove_mark(mid)
        if getattr(m, "_active_cmd", None) is None:
            save_state("remove")
    m._remove_mark = remove_mark

    def apply_edit(mark, key, value):
        result = old_apply_edit(mark, key, value)
        save_state("edit")
        return result
    m._apply_edit = apply_edit

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__(); self._delegate = CurrentPaletteHTMLHandler()
        def notify(self, args):
            action = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
            except Exception:
                pass
            self._delegate.notify(args)
            if action in ("comment", "reject"):
                save_state("palette-" + str(action))
    m.PaletteHTMLHandler = PaletteHTMLHandler

    class LaunchHandler(adsk.core.CustomEventHandler):
        def __init__(self):
            super().__init__(); self._delegate = CurrentLaunchHandler()
        def notify(self, args):
            save_state("tool-switch")
            self._delegate.notify(args)
    m.LaunchHandler = LaunchHandler

    class NoteDestroy(adsk.core.CommandEventHandler):
        def __init__(self):
            super().__init__(); self._delegate = CurrentNoteDestroy()
        def notify(self, args):
            self._delegate.notify(args)
            save_state("note-done")
    m.NoteDestroy = NoteDestroy

    m._persist_state = save_state
    m._reload_persisted_state = load_state
    m._persistence_health = lambda: dict(state)

    def run(context):
        result = old_run(context)
        load_state()
        log("PERSISTENCE READY: guarded snapshot + backup + degraded-card recovery")
        return result

    def stop(context):
        save_state("add-in-stop")
        return old_stop(context)

    m.run = run
    m.stop = stop
