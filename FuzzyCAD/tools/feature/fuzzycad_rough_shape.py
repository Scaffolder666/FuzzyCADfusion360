"""Rough Shape: flag a whole body as uncertain (open shape, not a parameter).

For cross-domain collaboration. A domain expert who knows roughly what a part
should be -- but not how to model it -- can drop in a crude placeholder (a box, a
lump) and mark the ENTIRE shape as a Need Input: "this is about right, someone
please make it real." The whole body then reads in the comic / cel-shaded
uncertainty style (fuzzycad_fuzzy_boundary already renders any questioned body
that way), and the panel card carries a free-text note saying what it is / what's
unresolved.

Unlike the other tools this is not a geometric operation: selecting the body IS
the mark. Accepting or rejecting just clears the flag; the body is left as-is for
someone else to refine.
"""


def install(m):
    adsk = m.adsk

    if "rough" not in m.COMMANDS:
        m.COMMANDS = tuple(m.COMMANDS) + ("rough",)
    m.CMD_ID["rough"] = "FuzzyCAD_RoughShape"
    m.CMD_LABEL["rough"] = "Rough Shape"
    m.CMD_FILTER["rough"] = "SolidBodies"
    m.CMD_HINT["rough"] = "Select a rough body to flag the whole shape as uncertain."
    m.CMD_CATS["rough"] = ("rough",)

    old_build_pending = m._build_pending
    old_category_raw = m._category_raw
    old_is_default = m._is_default
    old_fields = m._fields
    old_apply_edit = m._apply_edit
    old_summary = m._summary
    old_accept = m._accept
    CurrentInputChanged = m.FuzzyInputChanged

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD ROUGH] " + msg)
        except Exception:
            pass

    # ---- pending: just the selected body ----------------------------------
    def build_pending(cmd, ent):
        if cmd == "rough":
            if not isinstance(ent, adsk.fusion.BRepBody):
                return None
            center, size = m._bbox_center_size(ent)
            return {"geom": {"edges": m._sample_edges(ent.edges)},
                    "anchor": center, "size": size,
                    "entity": ent, "body": ent}
        return old_build_pending(cmd, ent)

    m._build_pending = build_pending

    # ---- keep FuzzyDestroy from treating a rough mark as an empty default ---
    def category_raw(cat):
        if cat == "rough":
            return {}
        return old_category_raw(cat)

    m._category_raw = category_raw

    def is_default(cat, op):
        if cat == "rough":
            return False        # a whole-shape flag is deliberate; never auto-remove
        return old_is_default(cat, op)

    m._is_default = is_default

    # ---- no proposal geometry to draw (the body itself carries the look) ----
    m._DRAW["rough"] = lambda group, mark, rgb, amp: None

    # ---- card: no note field; the comment thread + images carry the intent -
    def fields(mark):
        if mark.get("tool") == "rough":
            # A rough shape needs no constraint/note box of its own -- the size
            # dimensions are shown on the body, and discussion goes in the comment
            # thread. Return no editable fields so the card shows only comments +
            # image attach.
            return []
        return old_fields(mark)

    m._fields = fields

    def apply_edit(mark, key, value):
        if mark.get("tool") == "rough":
            if key == "note":
                mark["note"] = value
            return
        return old_apply_edit(mark, key, value)

    m._apply_edit = apply_edit

    def summary(mark):
        if mark.get("tool") == "rough":
            note = (mark.get("note") or "").strip()
            return "rough shape" + (" — " + note[:40] if note else "")
        return old_summary(mark)

    m._summary = summary

    # ---- create the mark on selection -------------------------------------
    def create_rough():
        if not m._pending or m._live.get("rough") is not None:
            return
        mid = m._next_id
        m._next_id = mid + 1
        mark = m._make_mark("rough", {"note": ""})
        mark["id"] = mid
        mark["mtype"] = "need_input"
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        m._marks.append(mark)
        m._live["rough"] = mid
        log("ROUGH mark created id={}".format(mid))
        try:
            m._refresh_ghost()
        except Exception:
            pass
        m._redraw_marks()
        m._send_state()

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)
            if getattr(m, "_active_cmd", None) != "rough" or not m._pending:
                return
            if cid == "sel":
                try:
                    create_rough()
                except Exception:
                    log("create failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    # ---- resolving just clears the flag; the body is left as-is ------------
    def accept(mark):
        if mark.get("tool") == "rough":
            log("ROUGH accepted (flag cleared) id={}".format(mark.get("id")))
            return True
        return old_accept(mark)

    m._accept = accept

    log("ROUGH SHAPE READY")
