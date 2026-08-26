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

    # Move + rotate handles for the transform command. Hidden until the reviewer
    # has answered the scope question, so a related change is a deliberate choice.
    MANIP_IDS = ("mX", "mY", "mZ", "rX", "rY", "rZ", "sc")

    def scope_value():
        """'together', 'only', or None when the reviewer has not chosen yet."""
        try:
            it = m._inputs.itemById("moveScope") if m._inputs else None
            selected = it.selectedItem if it else None
            if selected:
                if selected.name.startswith("Move together"):
                    return "together"
                if selected.name.startswith("Only"):
                    return "only"
        except Exception:
            pass
        return None

    def hide_manipulators():
        if m._inputs is None:
            return
        for cid in MANIP_IDS:
            it = m._inputs.itemById(cid)
            if it is not None:
                try:
                    it.isVisible = False
                    it.isEnabled = False
                except Exception:
                    pass

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
                    "<b>{}</b> nearby part{} highlighted in orange. Choose whether they move "
                    "with this one — the handles appear once you pick.".format(
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
            add_body_graphic(group, body, color=HILITE_RGB, opacity=0.45)
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

        # Followers get NO ghost/candidate overlay here. The silhouette +
        # translucent copies read as noise; the set was already shown (in orange)
        # at selection time, and Accept still carries them along. They simply stay
        # as their real selves during the drag and while the proposal is open.
        if getattr(m, "_active_cmd", None) == "transform":
            question_text(group, mark.get("anchor", [0, 0, 0]), len(related))

    m._DRAW["move"] = draw_move

    def ask_scope(count):
        """One modal question, styled like the accept dialog the reviewer liked.
        Yes -> carry the touching neighbours together; No -> move only this part."""
        try:
            res = m._ui.messageBox(
                "{} adjacent part{} touching this one (highlighted in orange).\n\n"
                "Move or rotate them together with this part?".format(
                    count, " is" if count == 1 else "s are"),
                "FuzzyCAD — move scope",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType)
            return "together" if res == adsk.core.DialogResults.DialogYes else "only"
        except Exception:
            return "only"

    def stamp_scope():
        """Copy the selection-time scope snapshot onto whichever transform mark is
        live -- BOTH move and rotate, since the transform command produces either.
        Called on every transform inputChanged (the drag that creates/updates the
        mark arrives as inputChanged, not executePreview, in this build), so the
        mark reliably carries move_scope + the token snapshots by accept. Without
        this the mark reached accept unstamped, defaulting to an empty all-token
        snapshot -- which made accept auto-carry every touching neighbour and moved
        parts together even when 'Only this part' was chosen."""
        if not m._pending or not m._pending.get("scope_asked_for"):
            return
        rel_b = list(m._pending.get("related_bodies", []))
        rel_t = list(m._pending.get("related_tokens", []))
        all_t = list(m._pending.get("all_tokens", []))
        scope = m._pending.get("move_scope") or scope_value() or "only"
        for key in ("move", "rotate"):
            mid = m._live.get(key)
            mark = m._find(mid) if mid is not None else None
            if mark is not None:
                mark["related_bodies"] = rel_b
                mark["related_tokens"] = rel_t
                mark["all_tokens_at_mark"] = all_t
                mark["move_scope"] = scope

    def attach_to_live_mark():
        stamp_scope()
        if not m._pending:
            return None
        mid = m._live.get("move")
        return m._find(mid) if mid is not None else None

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
                        add_body_graphic(group, body, color=HILITE_RGB, opacity=0.45)
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
                    ptok = body_token(primary) if primary is not None else None
                    # Ask the scope question exactly once per selected body. The
                    # native scope radio stays hidden -- super() already placed the
                    # manipulator, so after the reviewer answers the dialog the
                    # handles are live immediately (select -> ask -> drag).
                    if m._pending.get("scope_asked_for") == ptok:
                        return
                    set_scope_ui(0)
                    # Snapshot with the SAME detector accept uses, so "built on top
                    # since" can be measured against this set later.
                    detect = getattr(m, "_follow_detect_dependents", None)
                    try:
                        related = detect(primary) if detect else detect_related(primary)
                    except Exception:
                        related = detect_related(primary)
                    m._pending["related_bodies"] = list(related)
                    m._pending["related_tokens"] = [body_token(b) for b in related]
                    snap = getattr(m, "_follow_all_tokens", None)
                    m._pending["all_tokens"] = list(snap()) if snap else []
                    m._pending["scope_asked_for"] = ptok
                    m._pending["scope_chosen"] = True
                    if related:
                        # Paint the touching set orange so it is visible behind the
                        # modal dialog, then ask.
                        grp = m._group(m.GROUP_PREVIEW)
                        if grp is not None:
                            for body in related:
                                add_body_graphic(grp, body, color=HILITE_RGB, opacity=0.45)
                        try:
                            m._app.activeViewport.refresh()
                        except Exception:
                            pass
                        m._pending["move_scope"] = ask_scope(len(related))
                        log("SCOPE picked={} for {} touching part(s)".format(
                            m._pending["move_scope"], len(related)))
                        m._clear(m.GROUP_PREVIEW)
                        draw_relation_selection()
                    else:
                        m._pending["move_scope"] = "only"
                    return
                # Any other transform input == a manipulator drag, which creates /
                # updates the live move|rotate mark. Stamp the scope snapshot onto it
                # now, because executePreview does not fire on drag in this build.
                stamp_scope()
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
                    "moveScope", "Move scope — pick before dragging")
                scope.listItems.add("Choose…", True)
                scope.listItems.add("Only this part", False)
                scope.listItems.add("Move together", False)
                scope.isVisible = False
            except Exception:
                log("could not add move scope UI\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    # Carrying the related/dependent set for Move & Rotate is now owned entirely by
    # fuzzycad_dependent_follow (loaded outermost): it honours the scope the reviewer
    # picked at selection and auto-carries anything built on top since. Keeping a
    # second "together" mover here would double-apply the transform, so this wrapper
    # just delegates -- the scope snapshot it records (below) is what drives it.
    def accept(mark):
        return old_accept(mark)

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
