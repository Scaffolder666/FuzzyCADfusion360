"""Persist FuzzyCAD collaboration state inside the Fusion design.

The document stores one compact JSON attribute. Geometry references are stored
as Fusion entity tokens and are resolved again with Design.findEntityByToken
when the add-in opens the file. CustomGraphics are always rebuilt from that
state; they are visualization, not the source of truth.
"""

import json

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "uncertainty_state_v1"
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

    state = {"loading": False, "saving": False}

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
            return
        des = design()
        if des is None:
            return
        state["saving"] = True
        try:
            payload = {
                "schema": SCHEMA_VERSION,
                "marks": [serialize_mark(mark) for mark in m._marks],
            }
            text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            des.attributes.add(ATTR_GROUP, ATTR_NAME, text)
            log("SAVE reason={} marks={} bytes={}".format(reason, len(m._marks), len(text.encode("utf-8"))))
        except Exception:
            log("SAVE failed reason={}\n{}".format(reason, m.traceback.format_exc()))
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

    def load_state():
        des = design()
        if des is None:
            return
        try:
            attr = des.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        except Exception:
            attr = None
        if attr is None:
            log("LOAD no saved state")
            return

        try:
            payload = json.loads(attr.value or "{}")
        except Exception:
            log("LOAD invalid JSON")
            return

        rows = payload.get("marks") or []
        state["loading"] = True
        try:
            try: m._restore_all_bodies()
            except Exception: pass
            m._marks[:] = []
            m._geom.clear(); m._entity.clear(); m._body.clear()
            m._tool_count.clear()

            max_id = 0
            loaded = 0
            skipped = 0
            for row in rows:
                mark = row.get("mark") or {}
                if not isinstance(mark, dict) or "tool" not in mark:
                    skipped += 1; continue
                try:
                    mid = int(mark.get("id"))
                except Exception:
                    skipped += 1; continue
                mark["id"] = mid
                max_id = max(max_id, mid)

                tool = mark.get("tool")
                body = resolve(row.get("body_token"), adsk.fusion.BRepBody)
                ent = resolve(row.get("entity_token"))
                if tool == "compare" and ent is None:
                    ent = resolve(mark.get("target_token"))
                if body is None and isinstance(ent, (adsk.fusion.BRepBody, adsk.fusion.BRepFace, adsk.fusion.BRepEdge)):
                    try: body = ent if isinstance(ent, adsk.fusion.BRepBody) else ent.body
                    except Exception: pass

                # Geometry-changing marks need their referenced body/entity. A
                # note is anchor-only; Compare owns two body tokens in the mark.
                if tool not in ("note", "compare") and body is None:
                    skipped += 1; continue

                geom = reconstruct_geom(mark, ent, body)
                if geom is None:
                    skipped += 1; continue

                related = []
                for tok in row.get("related_tokens") or []:
                    rb = resolve(tok, adsk.fusion.BRepBody)
                    if rb is not None:
                        related.append(rb)
                if related:
                    mark["related_bodies"] = related

                m._marks.append(mark)
                m._geom[mid] = geom
                if ent is not None: m._entity[mid] = ent
                if body is not None: m._body[mid] = body
                n = int(mark.get("num", 1) or 1)
                m._tool_count[tool] = max(m._tool_count.get(tool, 0), n)
                loaded += 1

            m._next_id = max(max_id + 1, 1)

            for mark in list(m._marks):
                if mark.get("tool") in ("extrude", "fillet"):
                    try: m._compute_real(mark)
                    except Exception: pass

            m._redraw_marks()
            m._send_state()
            log("LOAD restored={} skipped={} next_id={}".format(loaded, skipped, m._next_id))
        except Exception:
            log("LOAD failed\n{}".format(m.traceback.format_exc()))
        finally:
            state["loading"] = False

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

    def run(context):
        result = old_run(context)
        load_state()
        log("PERSISTENCE READY: Design.attributes JSON + entity-token rehydration")
        return result

    def stop(context):
        save_state("add-in-stop")
        return old_stop(context)

    m.run = run
    m.stop = stop
