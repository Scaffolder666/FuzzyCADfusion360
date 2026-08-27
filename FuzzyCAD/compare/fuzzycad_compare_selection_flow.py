"""Rail-first selection flow for in-place Compare.

This module replaces only the Fusion command shell used by ``compare_here``.
The existing in-place Compare renderer/accept logic remains authoritative once a
Conflict mark exists.

Interaction contract:
1. Click the first body in the viewport.
2. FuzzyCAD advances selection focus to the second body automatically.
3. Click the second body.
4. Click FuzzyCAD Confirm. Fusion's native Done remains only a fallback.

The left rail is the primary instruction surface. The native Fusion command
panel may remain visible, but the user does not need to interact with it.
"""


def install(m):
    adsk = m.adsk
    CMD_ID = "FuzzyCAD_CompareHere"
    old_run = m.run
    old_stop = m.stop
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    state = {
        "inputs": None,
        "pending": None,
        "active": False,
        "stage_note": None,
    }

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD COMPARE FLOW] " + str(msg))
        except Exception:
            pass

    def token(body):
        try:
            return str(body.entityToken)
        except Exception:
            return None

    def resolve_body(tok):
        if not tok:
            return None
        try:
            design = m._design()
            if design is None:
                return None
            for ent in design.findEntityByToken(str(tok)):
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def selection_input(cid):
        try:
            ins = state.get("inputs")
            return ins.itemById(cid) if ins is not None else None
        except Exception:
            return None

    def body_from(cid):
        it = selection_input(cid)
        if it is None:
            return None
        try:
            if it.selectionCount < 1:
                return None
            return adsk.fusion.BRepBody.cast(it.selection(0).entity)
        except Exception:
            return None

    def clear_selection(cid):
        try:
            it = selection_input(cid)
            if it is not None:
                it.clearSelection()
        except Exception:
            pass

    def set_focus(cid):
        """Move viewport selection ownership to one of the two hidden steps."""
        for key in ("cmpflow_a", "cmpflow_b"):
            try:
                it = selection_input(key)
                if it is not None:
                    it.hasFocus = (key == cid)
            except Exception:
                pass

    def selected_name(body, fallback):
        if body is None:
            return fallback
        try:
            name = str(body.name or "").strip()
            return name if name else fallback
        except Exception:
            return fallback

    def selections_distinct(a=None, b=None):
        a = a or body_from("cmpflow_a")
        b = b or body_from("cmpflow_b")
        if a is None or b is None:
            return True
        ta, tb = token(a), token(b)
        return not (ta and tb and ta == tb)

    def stage():
        setter = getattr(m, "_set_tool_stage", None)
        if setter is None:
            return
        a = body_from("cmpflow_a")
        b = body_from("cmpflow_b")
        have_a = a is not None
        have_b = b is not None
        active = 0 if not have_a else (1 if not have_b else 2)

        if not have_a:
            title = "Compare · first object"
        elif not have_b:
            title = "Compare · second object"
        else:
            title = "Compare · ready"

        a_hint = (selected_name(a, "First object") + " selected") if have_a else \
            "Click the first body directly in the viewport"
        if have_b:
            b_hint = selected_name(b, "Second object") + " selected"
        elif have_a:
            b_hint = state.get("stage_note") or "Now click the second body in the viewport"
        else:
            b_hint = "This step activates automatically after the first object"

        ready_hint = "Click Confirm below to create the Conflict card" if (have_a and have_b) else \
            "Select both objects first"

        try:
            setter("compare_here", [
                {"label": "Select first object" if not have_a else "First object selected",
                 "done": have_a, "hint": a_hint},
                {"label": "Select second object" if not have_b else "Second object selected",
                 "done": have_b, "hint": b_hint},
                {"label": "Create comparison", "done": False, "hint": ready_hint},
            ], active, title)
        except Exception:
            pass

    def create_mark(a_body, b_body):
        ta, tb = token(a_body), token(b_body)
        if not ta or not tb or ta == tb:
            return None
        try:
            center, size = m._bbox_center_size(a_body)
        except Exception:
            center, size = [0.0, 0.0, 0.0], 3.0

        mid = m._next_id
        m._next_id += 1
        num = m._tool_count.get("compare", 0) + 1
        m._tool_count["compare"] = num
        mark = {
            "id": mid,
            "tool": "compare",
            "mtype": "conflict",
            "label": "Compare in place",
            "anchor": list(center),
            "size": float(size),
            "num": num,
            "status": "open",
            "comments": [],
            "selected": None,
            "inplace": True,
            "target_label": "in place",
            "alternatives": [
                {"name": selected_name(a_body, "Option 1"), "body_tokens": [ta]},
                {"name": selected_name(b_body, "Option 2"), "body_tokens": [tb]},
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
            persist = getattr(m, "_persist_state", None)
            if persist is not None:
                persist("compare-rail-create")
        except Exception:
            pass
        log("CREATED mark={} A={} B={}".format(mid, ta, tb))
        return mid

    def capture_pending():
        a = body_from("cmpflow_a")
        b = body_from("cmpflow_b")
        if a is None:
            state["stage_note"] = None
            set_focus("cmpflow_a")
            stage()
            return "wait"
        if b is None:
            state["stage_note"] = "Now click the second body in the viewport"
            set_focus("cmpflow_b")
            stage()
            return "wait"
        if not selections_distinct(a, b):
            clear_selection("cmpflow_b")
            state["stage_note"] = "Choose a different body for the second object"
            set_focus("cmpflow_b")
            stage()
            return "wait"

        state["stage_note"] = None
        state["pending"] = {"a": token(a), "b": token(b)}
        return "ready"

    def finish_pending():
        pending = state.get("pending")
        state["pending"] = None
        if not pending:
            return False
        a = resolve_body(pending.get("a"))
        b = resolve_body(pending.get("b"))
        if a is None or b is None or not selections_distinct(a, b):
            log("pending selection could not be resolved")
            return False
        return create_mark(a, b) is not None

    class InputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                state["inputs"] = args.inputs
                cid = args.input.id
                state["stage_note"] = None
                # Each option accepts exactly one body. Once Option 1 is picked,
                # explicitly hand viewport selection to Option 2 before updating
                # the rail so the next click goes to the second object.
                if cid == "cmpflow_a" and body_from("cmpflow_a") is not None:
                    if body_from("cmpflow_b") is None:
                        set_focus("cmpflow_b")
                elif cid == "cmpflow_b" and not selections_distinct():
                    clear_selection("cmpflow_b")
                    state["stage_note"] = "Choose a different body for the second object"
                    set_focus("cmpflow_b")
                stage()
            except Exception:
                log("inputChanged failed\n{}".format(m.traceback.format_exc()))

    class Validate(adsk.core.ValidateInputsEventHandler):
        def notify(self, args):
            try:
                a = body_from("cmpflow_a")
                b = body_from("cmpflow_b")
                args.areInputsValid = bool(a is not None and b is not None and selections_distinct(a, b))
            except Exception:
                args.areInputsValid = False

    class Execute(adsk.core.CommandEventHandler):
        def notify(self, args):
            # Native Fusion Done is a fallback. Capture only; Destroy runs after
            # Execute and creates the mark when the modal command has closed.
            try:
                capture_pending()
            except Exception:
                log("execute capture failed\n{}".format(m.traceback.format_exc()))

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            pending = state.get("pending")
            state["inputs"] = None
            state["active"] = False
            state["stage_note"] = None
            try:
                setter = getattr(m, "_set_tool_stage", None)
                if setter is not None:
                    setter(None, [], None, "")
            except Exception:
                pass
            if pending:
                try:
                    finish_pending()
                except Exception:
                    log("destroy finish failed\n{}".format(m.traceback.format_exc()))
            else:
                state["pending"] = None

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                state["pending"] = None
                state["stage_note"] = None
                state["active"] = True
                cmd = args.command
                cmd.isRepeatable = False
                try:
                    cmd.isExecutedWhenPreEmpted = False
                except Exception:
                    pass
                try:
                    cmd.okButtonText = "Done"
                    cmd.cancelButtonText = "Cancel"
                except Exception:
                    pass

                inputs = cmd.commandInputs
                a = inputs.addSelectionInput(
                    "cmpflow_a", "1. First object", "Select the first body")
                b = inputs.addSelectionInput(
                    "cmpflow_b", "2. Second object", "Select the second body")
                for it in (a, b):
                    it.addSelectionFilter("SolidBodies")
                    # One body per option makes the viewport flow deterministic:
                    # first click -> focus advances -> second click -> Confirm.
                    it.setSelectionLimits(1, 1)
                    try:
                        it.isUseCurrentSelections = False
                    except Exception:
                        pass

                state["inputs"] = inputs
                set_focus("cmpflow_a")
                stage()

                for handler, event in (
                    (InputChanged(), cmd.inputChanged),
                    (Validate(), cmd.validateInputs),
                    (Execute(), cmd.execute),
                    (Destroy(), cmd.destroy),
                ):
                    event.add(handler)
                    m._handlers.append(handler)
                log("ACTIVE rail-first Compare")
            except Exception:
                state["active"] = False
                log("setup failed\n{}".format(m.traceback.format_exc()))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
            except Exception:
                pass

            if action == "confirm" and state.get("active") and state.get("inputs") is not None:
                result = capture_pending()
                if result != "ready":
                    return
                # Delegate the ready Confirm to the existing generic stage handler.
                # It performs the normal terminate-only command close. Our Destroy
                # then resolves the captured tokens and creates the Conflict mark.
                self._delegate.notify(args)
                return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def register_command():
        panel = m._ui.allToolbarPanels.itemById(m.PANEL_ID)
        if panel is not None:
            try:
                ctrl = panel.controls.itemById(CMD_ID)
                if ctrl is not None:
                    ctrl.deleteMe()
            except Exception:
                pass
        try:
            old = m._ui.commandDefinitions.itemById(CMD_ID)
            if old is not None:
                old.deleteMe()
        except Exception:
            pass

        cd = m._ui.commandDefinitions.addButtonDefinition(
            CMD_ID,
            "Compare",
            "Select two existing bodies in the viewport, then Confirm",
            "")
        h = Created()
        cd.commandCreated.add(h)
        m._handlers.append(h)
        if panel is not None:
            panel.controls.addCommand(cd)
        m.CMD_ID["compare_here"] = CMD_ID
        log("REGISTERED rail-first Compare command")

    def run(context):
        result = old_run(context)
        try:
            register_command()
        except Exception:
            log("register failed\n{}".format(m.traceback.format_exc()))
        return result

    def stop(context):
        state["inputs"] = None
        state["pending"] = None
        state["active"] = False
        return old_stop(context)

    m.run = run
    m.stop = stop
