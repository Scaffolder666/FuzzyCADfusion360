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

    # ---- draw: tint each option so the choice reads in place ---------------
    def draw_compare(group, mark, rgb, amp):
        if not mark.get("inplace"):
            if old_draw is not None:
                return old_draw(group, mark, rgb, amp)
            return
        design = m._design()
        sel = mark.get("selected")
        for i, alt in enumerate((mark.get("alternatives") or [])[:2]):
            if sel is None:
                col, op = CONF_RGB, 0.30
            elif i == sel:
                col, op = KEEP_RGB, 0.45      # kept option
            else:
                col, op = DROP_RGB, 0.20      # would be removed
            for tok in alt.get("body_tokens", []):
                b = resolve_body(design, tok)
                if b is None:
                    continue
                try:
                    cg = group.addBRepBody(b)
                    cg.color = m._solid(col)
                    cg.setOpacity(op, True)
                except Exception:
                    continue
        # The conflict badge is drawn by _draw_one after this, so it is not
        # repeated here.

    m._DRAW["compare"] = draw_compare

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

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            state["inputs"] = None

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
                    (Execute(), cmd.execute),
                    (Validate(), cmd.validateInputs),
                    (Destroy(), cmd.destroy),
                ):
                    event.add(handler)
                    m._handlers.append(handler)
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

    m.run = run
