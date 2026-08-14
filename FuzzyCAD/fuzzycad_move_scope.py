"""Prototype relationship-aware Move scope for FuzzyCAD.

This deliberately starts geometry-first rather than pretending we already have
semantic understanding.  When a body is selected for Move, nearby/touching
bodies in the same component are detected once, highlighted, and the user gets
one scope question: move only this body or move the highlighted set together.
The detected set is cached for the drag, so the expensive part does not run on
every manipulator frame.
"""

import math
import time


def install(m):
    adsk = m.adsk
    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    CurrentInputChanged = m.FuzzyInputChanged
    CurrentPreview = m.FuzzyPreview
    old_draw_move = m._DRAW.get("move")
    old_accept = m._accept
    old_summary = m._summary
    old_run = m.run

    HILITE_RGB = (225, 126, 38)
    CANDIDATE_RGB = (190, 190, 186)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD MOVE SCOPE] " + msg)
        except Exception:
            pass

    def bbox_gap(a, b):
        amn, amx = a.minPoint, a.maxPoint
        bmn, bmx = b.minPoint, b.maxPoint
        dx = max(0.0, bmn.x - amx.x, amn.x - bmx.x)
        dy = max(0.0, bmn.y - amx.y, amn.y - bmx.y)
        dz = max(0.0, bmn.z - amx.z, amn.z - bmx.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def body_token(body):
        try:
            return body.entityToken
        except Exception:
            return str(id(body))

    def detect_related(primary):
        """Return nearby/touching bodies in the same component, sorted by gap.

        This is intentionally conservative and cheap.  It is a first-pass
        relation signal for imported multi-body STEP models where Joint/Rigid
        metadata often does not exist.
        """
        t0 = time.perf_counter()
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
        pbb = primary.boundingBox
        rows = []
        ptok = body_token(primary)
        for i in range(bodies.count):
            try:
                b = bodies.item(i)
                if body_token(b) == ptok:
                    continue
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                # Do not silently couple a body that is already carrying another
                # unresolved FuzzyCAD question.
                try:
                    if m._body_locked(b):
                        continue
                except Exception:
                    pass
                gap = bbox_gap(pbb, b.boundingBox)
                if gap <= tol:
                    rows.append((gap, b))
            except Exception:
                continue
        rows.sort(key=lambda x: x[0])
        # Keep the first prototype visually readable and drag cost bounded.
        result = [b for _, b in rows[:8]]
        dt = (time.perf_counter() - t0) * 1000.0
        log("RELATION DETECT primary={} nearby={} tol_mm={:.2f} time_ms={:.2f}".format(
            getattr(primary, "name", "body"), len(result), tol * 10.0, dt))
        return result

    def scope_value():
        try:
            it = m._inputs.itemById("moveScope") if m._inputs else None
            selected = it.selectedItem if it else None
            if selected and selected.name.startswith("Together"):
                return "together"
        except Exception:
            pass
        return "only"

    def set_scope_ui(count):
        if m._inputs is None:
            return
        scope = m._inputs.itemById("moveScope")
        info = m._inputs.itemById("moveRelInfo")
        visible = count > 0
        if scope is not None:
            scope.isVisible = visible
        if info is not None:
            info.isVisible = visible
            if visible:
                info.formattedText = (
                    "<b>{}</b> nearby part{} highlighted. Choose the Move scope once; "
                    "the set stays cached while you drag.".format(
                        count, " is" if count == 1 else "s are"))

    def relation_data(mark=None):
        if mark is not None and mark.get("related_bodies") is not None:
            return mark.get("related_bodies", []), mark.get("move_scope", "only")
        if m._pending:
            return m._pending.get("related_bodies", []), m._pending.get("move_scope", scope_value())
        return [], "only"

    def add_body_graphic(group, body, matrix=None, color=HILITE_RGB, opacity=0.12):
        try:
            cg = group.addBRepBody(body)
            if matrix is not None:
                cg.transform = matrix
            cg.color = m._solid(color)
            cg.setOpacity(opacity, True)
            return cg
        except Exception:
            return None

    def question_text(group, anchor, count):
        if count <= 0 or getattr(m, "_active_cmd", None) != "transform":
            return
        try:
            s = m._pending.get("size", 3.0) if m._pending else 3.0
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            d = max(1.0, min(s * 0.42, 3.2))
            p = (anchor[0] + (0.72 * xx + 0.58 * yx) * d,
                 anchor[1] + (0.72 * xy + 0.58 * yy) * d,
                 anchor[2] + (0.72 * xz + 0.58 * yz) * d)
            m._sketchy(group, [tuple(anchor), p], HILITE_RGB, 0.0, 98111,
                       weight=2, strokes=1)
            cp = adsk.core.Point3D.create(*p)
            text = "Move {} highlighted part{} together?".format(
                count, "" if count == 1 else "s")
            t = group.addText(text, "Arial", max(0.45, min(s * 0.10, 0.85)),
                              m._label_transform(cp))
            t.color = m._solid(HILITE_RGB)
            m._apply_billboard(t, cp)
        except Exception:
            pass

    def draw_relation_selection():
        if not m._pending:
            return
        related = m._pending.get("related_bodies", [])
        if not related:
            return
        group = m._group(m.GROUP_PREVIEW)
        if group is None:
            return
        for body in related:
            add_body_graphic(group, body, color=HILITE_RGB, opacity=0.10)
        question_text(group, m._pending.get("anchor", [0, 0, 0]), len(related))
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def draw_move(group, mark, rgb, amp):
        if old_draw_move is not None:
            old_draw_move(group, mark, rgb, amp)
        related, scope = relation_data(mark)
        if not related:
            return

        matrix = m._op_matrix(mark)
        for body in related:
            # The original related bodies remain visible as a faint orange
            # relationship cue.  Together adds a transformed candidate copy.
            add_body_graphic(group, body, color=HILITE_RGB,
                             opacity=0.07 if scope == "together" else 0.12)
            if scope == "together":
                add_body_graphic(group, body, matrix=matrix,
                                 color=CANDIDATE_RGB, opacity=0.26)

        if getattr(m, "_active_cmd", None) == "transform":
            question_text(group, mark.get("anchor", [0, 0, 0]), len(related))

    m._DRAW["move"] = draw_move

    def attach_to_live_mark():
        if not m._pending:
            return None
        mid = m._live.get("move")
        mark = m._find(mid) if mid is not None else None
        if mark is None:
            return None
        mark["related_bodies"] = list(m._pending.get("related_bodies", []))
        mark["move_scope"] = m._pending.get("move_scope", scope_value())
        return mark

    def redraw_scope():
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        mark = attach_to_live_mark()
        if group is not None:
            if mark is not None:
                m._draw_one(group, mark)
            else:
                # Before a move amount exists, keep the related set visible.
                if m._pending:
                    related = m._pending.get("related_bodies", [])
                    for body in related:
                        add_body_graphic(group, body, color=HILITE_RGB, opacity=0.10)
                    if related:
                        question_text(group, m._pending.get("anchor", [0, 0, 0]), len(related))
        m._refresh_ghost()
        m._send_state()
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)

            if getattr(m, "_active_cmd", None) != "transform":
                return
            try:
                if cid == "sel":
                    if not m._pending:
                        set_scope_ui(0)
                        return
                    primary = m._pending.get("body")
                    related = detect_related(primary)
                    m._pending["related_bodies"] = related
                    m._pending["move_scope"] = "only"
                    set_scope_ui(len(related))
                    if related:
                        draw_relation_selection()
                    return

                if cid == "moveScope" and m._pending:
                    m._pending["move_scope"] = scope_value()
                    mark = attach_to_live_mark()
                    log("SCOPE={} related_count={}".format(
                        m._pending["move_scope"], len(m._pending.get("related_bodies", []))))
                    redraw_scope()
            except Exception:
                log("input failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    class FuzzyPreview(CurrentPreview):
        def notify(self, args):
            super().notify(args)
            try:
                if getattr(m, "_active_cmd", None) == "transform" and m._pending:
                    mark = attach_to_live_mark()
                    if mark is not None:
                        # The base preview already drew using pending scope. This
                        # second state push records the scope on the persistent card.
                        m._send_state()
            except Exception:
                log("preview attach failed: {}".format(m.traceback.format_exc()))

    m.FuzzyPreview = FuzzyPreview

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            if self.cmd != "transform":
                return
            try:
                inputs = args.command.commandInputs
                info = inputs.addTextBoxCommandInput(
                    "moveRelInfo", "", "", 2, True)
                info.isFullWidth = True
                info.isVisible = False
                scope = inputs.addRadioButtonGroupCommandInput(
                    "moveScope", "Move highlighted parts together?")
                scope.listItems.add("Only this", True)
                scope.listItems.add("Together", False)
                scope.isVisible = False
            except Exception:
                log("could not add move scope UI\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    def accept(mark):
        if mark.get("tool") != "move" or mark.get("move_scope") != "together":
            return old_accept(mark)
        primary = m._body.get(mark["id"])
        if primary is None:
            return False
        try:
            comp = primary.parentComponent
            coll = adsk.core.ObjectCollection.create()
            added = set()
            for body in [primary] + list(mark.get("related_bodies", [])):
                try:
                    tok = body_token(body)
                    if tok in added or body.parentComponent != comp:
                        continue
                    coll.add(body)
                    added.add(tok)
                    try:
                        body.opacity = 1.0
                    except Exception:
                        pass
                except Exception:
                    continue
            if coll.count < 1:
                return False
            comp.features.moveFeatures.add(
                comp.features.moveFeatures.createInput(coll, m._op_matrix(mark)))
            log("APPLY TOGETHER bodies={}".format(coll.count))
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't move the highlighted set:\n{}".format(
                m.traceback.format_exc()))
            return False

    m._accept = accept

    def summary(mark):
        text = old_summary(mark)
        if mark.get("tool") == "move" and mark.get("related_bodies"):
            if mark.get("move_scope") == "together":
                return text + " • {} highlighted together".format(
                    len(mark.get("related_bodies", [])) + 1)
            return text + " • only this"
        return text

    m._summary = summary

    def run(context):
        result = old_run(context)
        log("MOVE SCOPE READY: geometry-nearby set is detected once, highlighted, and cached")
        return result

    m.run = run
