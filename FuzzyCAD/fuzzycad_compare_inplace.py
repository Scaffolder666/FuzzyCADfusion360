"""In-place Compare for FuzzyCAD.

The existing Compare brings an alternative from elsewhere and aligns it to a
chosen target connection. But often the user has already built BOTH options at
the same spot and just wants to decide which to keep -- no target/placement
needed. This tool covers that case:

  * select the bodies of Alternative 1 and the bodies of Alternative 2
    (each can be several bodies), no target step;
  * it becomes a Conflict card with the same A / B toggle and "Confirm choice";
  * accepting keeps the chosen option in place and deletes the other's bodies.

It reuses the compare mark shape and the panel card. Loaded after the other
compare patches so its _accept / _DRAW["compare"] wrappers are outermost; both
delegate to the normal (target-aligned) Compare for non-in-place marks.
"""


def install(m):
    adsk = m.adsk
    CMD_HERE = "FuzzyCAD_CompareHere"
    m.CMD_ID["compare_here"] = CMD_HERE

    old_run = m.run
    old_stop = m.stop
    old_accept = m._accept
    old_draw = m._DRAW.get("compare")

    CONF_RGB = (128, 90, 180)   # conflict purple (unresolved)
    KEEP_RGB = (46, 160, 90)    # the chosen option
    DROP_RGB = (200, 60, 50)    # the option that would be removed
    state = {"inputs": None}

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMPARE HERE] " + msg)
        except Exception:
            pass

    def token(e):
        try:
            return e.entityToken
        except Exception:
            return None

    def resolve_body(design, tok):
        if design is None or not tok:
            return None
        try:
            for e in design.findEntityByToken(tok):
                if isinstance(e, adsk.fusion.BRepBody):
                    return e
        except Exception:
            pass
        return None

    # Tokens currently hidden by an in-place comparison, so they can be restored.
    hidden = set()

    # ---- draw: nothing extra; the option is a real body toggled visible ----
    def draw_compare(group, mark, rgb, amp):
        if not mark.get("inplace"):
            if old_draw is not None:
                return old_draw(group, mark, rgb, amp)
            return
        # In-place uses the original Compare language: only one option is shown,
        # the other is hidden. Visibility is toggled in reconcile_visibility on
        # every redraw; here there is nothing to draw (the conflict badge is
        # still added by _draw_one).
        return

    m._DRAW["compare"] = draw_compare

    def shown_index(mark):
        sel = mark.get("selected")
        return sel if sel in (0, 1) else 0     # default to Option 1 before a pick

    def reconcile_visibility():
        """Show only the chosen option of each open in-place comparison; hide the
        other. Restore anything previously hidden that no longer should be."""
        design = m._design()
        if design is None:
            return
        want_hidden = set()
        for mark in list(getattr(m, "_marks", []) or []):
            if (mark.get("tool") == "compare" and mark.get("inplace")
                    and mark.get("status", "open") == "open"):
                alts = mark.get("alternatives") or []
                loser = 1 - shown_index(mark)
                if 0 <= loser < len(alts):
                    for tok in alts[loser].get("body_tokens", []):
                        want_hidden.add(tok)
        for tok in want_hidden:
            b = resolve_body(design, tok)
            if b is not None:
                try:
                    b.isVisible = False
                    hidden.add(tok)
                except Exception:
                    pass
        for tok in list(hidden):
            if tok not in want_hidden:
                b = resolve_body(design, tok)
                if b is not None:
                    try:
                        b.isVisible = True
                    except Exception:
                        pass
                hidden.discard(tok)

    # ---- accept: keep the chosen option, delete the other ------------------
    def accept(mark):
        if mark.get("tool") == "compare" and mark.get("inplace"):
            choice = mark.get("selected")
            if choice not in (0, 1):
                try:
                    m._ui.messageBox("Choose Option 1 or Option 2 first.")
                except Exception:
                    pass
                return False
            loser = 1 - int(choice)
            design = m._design()
            deleted = 0
            try:
                for tok in (mark.get("alternatives") or [])[loser].get("body_tokens", []):
                    b = resolve_body(design, tok)
                    if b is not None:
                        try:
                            b.deleteMe()
                            deleted += 1
                        except Exception:
                            pass
            except Exception:
                log("delete loser failed\n{}".format(m.traceback.format_exc()))
            log("INPLACE accept keep=Option {} deleted={}".format(int(choice) + 1, deleted))
            return True
        return old_accept(mark)

    m._accept = accept

    old_redraw = m._redraw_marks

    def redraw(*a, **k):
        r = old_redraw(*a, **k)
        try:
            reconcile_visibility()
        except Exception:
            log("visibility reconcile failed\n{}".format(m.traceback.format_exc()))
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        return r

    m._redraw_marks = redraw

    # ---- create the in-place conflict --------------------------------------
    def bodies_from(cid):
        out = []
        try:
            it = state["inputs"].itemById(cid) if state.get("inputs") else None
            if it is None:
                return out
            for i in range(it.selectionCount):
                b = adsk.fusion.BRepBody.cast(it.selection(i).entity)
                if b is not None:
                    out.append(b)
        except Exception:
            pass
        return out

    def count(cid):
        try:
            it = state["inputs"].itemById(cid) if state.get("inputs") else None
            return it.selectionCount if it is not None else 0
        except Exception:
            return 0

    def stage():
        if not hasattr(m, "_set_tool_stage"):
            return
        a = count("chere_a") > 0
        b = count("chere_b") > 0
        active = 0 if not a else (1 if not b else 2)
        try:
            m._set_tool_stage("compare_here", [
                {"label": "Select Option 1 bodies", "done": a,
                 "hint": "every body of the first option"},
                {"label": "Select Option 2 bodies", "done": b,
                 "hint": "every body of the second option"},
                {"label": "Create, then pick in the card", "done": False},
            ], active, "Compare here")
        except Exception:
            pass

    def create_inplace_mark(a_bodies, b_bodies):
        a_toks = [token(b) for b in a_bodies if token(b)]
        b_toks = [token(b) for b in b_bodies if token(b)]
        if not a_toks or not b_toks:
            return None
        try:
            center, size = m._bbox_center_size(a_bodies[0])
        except Exception:
            center, size = [0.0, 0.0, 0.0], 3.0
        mid = m._next_id
        m._next_id += 1
        num = m._tool_count.get("compare", 0) + 1
        m._tool_count["compare"] = num
        mark = {
            "id": mid, "tool": "compare", "mtype": "conflict",
            "label": "Compare in place", "anchor": list(center), "size": size,
            "num": num, "status": "open", "comments": [], "selected": None,
            "inplace": True,
            "target_label": "in place",
            "alternatives": [
                {"name": "Option 1", "body_tokens": a_toks},
                {"name": "Option 2", "body_tokens": b_toks},
            ],
        }
        m._marks.append(mark)
        m._geom[mid] = {"inplace": True}
        try:
            m._redraw_marks()
        except Exception:
            pass
        try:
            m._send_state()
        except Exception:
            pass
        try:
            if getattr(m, "_persist_state", None):
                m._persist_state("compare-inplace-create")
        except Exception:
            pass
        log("CREATED in-place id={} A={} B={}".format(mid, len(a_toks), len(b_toks)))
        return mid

    class Execute(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                a = bodies_from("chere_a")
                b = bodies_from("chere_b")
                if a and b:
                    create_inplace_mark(a, b)
            except Exception:
                log("execute failed\n{}".format(m.traceback.format_exc()))

    class Validate(adsk.core.ValidateInputsEventHandler):
        def notify(self, args):
            try:
                ins = args.inputs
                a = ins.itemById("chere_a")
                b = ins.itemById("chere_b")
                args.areInputsValid = bool(a and b and a.selectionCount > 0 and b.selectionCount > 0)
            except Exception:
                args.areInputsValid = False

    class InputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                state["inputs"] = args.inputs
                stage()
            except Exception:
                pass

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            state["inputs"] = None
            try:
                if hasattr(m, "_set_tool_stage"):
                    m._set_tool_stage(None, [], None, "")
            except Exception:
                pass

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                cmd = args.command
                cmd.isRepeatable = False
                try:
                    cmd.okButtonText = "Create comparison"
                    cmd.cancelButtonText = "Cancel"
                except Exception:
                    pass
                inputs = cmd.commandInputs
                a = inputs.addSelectionInput(
                    "chere_a", "1. Option 1 bodies", "Select every body of the first option")
                b = inputs.addSelectionInput(
                    "chere_b", "2. Option 2 bodies", "Select every body of the second option")
                for it in (a, b):
                    it.addSelectionFilter("SolidBodies")
                    it.setSelectionLimits(1, 0)   # at least one, unlimited
                    try:
                        it.isUseCurrentSelections = False
                    except Exception:
                        pass
                state["inputs"] = inputs
                for handler, event in (
                    (InputChanged(), cmd.inputChanged),
                    (Execute(), cmd.execute),
                    (Validate(), cmd.validateInputs),
                    (Destroy(), cmd.destroy),
                ):
                    event.add(handler)
                    m._handlers.append(handler)
                stage()
                log("ACTIVE in-place Compare")
            except Exception:
                log("setup failed\n{}".format(m.traceback.format_exc()))

    def register_command():
        panel = m._ui.allToolbarPanels.itemById(m.PANEL_ID)
        if panel is not None:
            try:
                ctrl = panel.controls.itemById(CMD_HERE)
                if ctrl is not None:
                    ctrl.deleteMe()
            except Exception:
                pass
        try:
            existing = m._ui.commandDefinitions.itemById(CMD_HERE)
            if existing is not None:
                existing.deleteMe()
        except Exception:
            pass
        cd = m._ui.commandDefinitions.addButtonDefinition(
            CMD_HERE, "Compare here",
            "Compare two options already built at the same place; keep one, drop the other", "")
        h = Created()
        cd.commandCreated.add(h)
        m._handlers.append(h)
        if panel is not None:
            panel.controls.addCommand(cd)

    def run(context):
        result = old_run(context)
        try:
            register_command()
        except Exception:
            log("command registration failed\n{}".format(m.traceback.format_exc()))
        log("READY: in-place Compare")
        return result

    def stop(context):
        # Restore any option hidden by an in-place comparison before shutdown.
        try:
            design = m._design()
            for tok in list(hidden):
                b = resolve_body(design, tok)
                if b is not None:
                    try:
                        b.isVisible = True
                    except Exception:
                        pass
            hidden.clear()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
