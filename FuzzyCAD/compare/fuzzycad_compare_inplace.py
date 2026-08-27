"""In-place Compare for FuzzyCAD.

The existing Compare brings an alternative from elsewhere and aligns it to a
chosen target connection. But often the user has already built BOTH options at
the same spot and just wants to decide which to keep -- no target/placement
needed. This tool covers that case:

  * select the bodies of Alternative 1 and the bodies of Alternative 2
    (each can be several bodies), no target step;
  * it becomes a Conflict card with the same A / B toggle and "Confirm choice";
  * accepting keeps the chosen option in place and deletes the other's bodies.

The native Fusion command panel remains the primary selection surface. FuzzyCAD's
left rail only mirrors progress and tells the user what to select next. After the
first option is picked, focus advances to Option 2. Either Fusion's Done button or
the FuzzyCAD Confirm button creates the comparison.

It reuses the compare mark shape and the panel card. Loaded after the other
compare patches so its _accept / _DRAW["compare"] wrappers are outermost; both
delegate to the normal (target-aligned) Compare for non-in-place marks.
"""


def install(m):
    adsk = m.adsk
    CMD_HERE = "FuzzyCAD_CompareHere"
    FINISH_EVENT_ID = "FuzzyCADCompareHereFinish"
    m.CMD_ID["compare_here"] = CMD_HERE

    old_run = m.run
    old_stop = m.stop
    old_accept = m._accept
    old_draw = m._DRAW.get("compare")
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    CONF_RGB = (128, 90, 180)   # conflict purple (unresolved)
    KEEP_RGB = (46, 160, 90)    # the chosen option
    DROP_RGB = (200, 60, 50)    # the option that would be removed
    state = {
        "inputs": None,
        "finishing": False,
        "pending_finish": None,
    }

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
        # In-place uses the original Compare language: while unresolved BOTH real
        # bodies are hidden (reconcile_visibility) and the shown option is drawn
        # as hand-drawn sketchy edges -- so it reads as an uncertain proposal, not
        # a finished body. Accept then restores the chosen body solid.
        design = m._design()
        alts = mark.get("alternatives") or []
        shown = shown_index(mark)
        if not (0 <= shown < len(alts)):
            return
        seed = mark["id"] * 700
        size = mark.get("size", 3.0)
        idx = 0
        for tok in alts[shown].get("body_tokens", []):
            b = resolve_body(design, tok)
            if b is None:
                continue
            try:
                for poly in m._sample_edges(b.edges):
                    if len(poly) < 2:
                        continue
                    # Use the same wobbly "proposal" stroke as Move/Extrude so the
                    # unresolved option reads as a hand-drawn proposal. The compare
                    # role itself has no wobble, which is why passing its amp drew
                    # straight lines.
                    if hasattr(m, "_visual_stroke"):
                        m._visual_stroke(group, poly, "proposal_outer", seed + idx, size)
                    else:
                        m._sketchy(group, poly, rgb, amp, seed + idx, weight=1, strokes=2)
                    idx += 1
            except Exception:
                continue

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
                # While the comparison is open BOTH real bodies are hidden; the
                # shown option is represented by the sketchy edges drawn in
                # draw_compare.
                for alt in (mark.get("alternatives") or [])[:2]:
                    for tok in alt.get("body_tokens", []):
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
            winner = int(choice)
            loser = 1 - winner
            design = m._design()
            alts = mark.get("alternatives") or []
            deleted = 0
            try:
                if 0 <= loser < len(alts):
                    for tok in alts[loser].get("body_tokens", []):
                        b = resolve_body(design, tok)
                        hidden.discard(tok)
                        if b is not None:
                            try:
                                b.deleteMe()
                                deleted += 1
                            except Exception:
                                pass
            except Exception:
                log("delete loser failed\n{}".format(m.traceback.format_exc()))
            # Restore the chosen option to a solid, visible body (it was hidden
            # while the comparison was unresolved).
            try:
                if 0 <= winner < len(alts):
                    for tok in alts[winner].get("body_tokens", []):
                        hidden.discard(tok)
                        b = resolve_body(design, tok)
                        if b is not None:
                            try:
                                b.isVisible = True
                            except Exception:
                                pass
            except Exception:
                pass
            log("INPLACE accept keep=Option {} deleted={}".format(winner + 1, deleted))
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

    def set_focus(cid):
        """Advance Fusion's native selection input without replacing its UI."""
        inputs = state.get("inputs")
        if inputs is None:
            return
        for key in ("chere_a", "chere_b"):
            try:
                it = inputs.itemById(key)
                if it is not None:
                    it.hasFocus = (key == cid)
            except Exception:
                pass

    def stage():
        if not hasattr(m, "_set_tool_stage"):
            return
        a = count("chere_a") > 0
        b = count("chere_b") > 0
        active = 0 if not a else (1 if not b else 2)
        try:
            m._set_tool_stage("compare_here", [
                {"label": "Select Option 1 in Fusion", "done": a,
                 "hint": "click the first object/body in the viewport"},
                {"label": "Select Option 2 in Fusion", "done": b,
                 "hint": "after Option 1, Fusion moves to this selection"},
                {"label": "Finish the comparison", "done": False,
                 "hint": "click Done in Fusion, or Confirm here"},
            ], active, "Compare two options")
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

    def selected_tokens(cid):
        return [token(b) for b in bodies_from(cid) if token(b)]

    def request_finish():
        """Finish from the FuzzyCAD rail while preserving Fusion selections.

        Generic Confirm merely terminates the active Fusion command. Compare is
        different because the conflict mark is created on Execute. Capture the
        selected body tokens first and finish on a deferred main-thread event, so
        Confirm and Fusion's native Done have equivalent semantics.
        """
        if state.get("inputs") is None:
            return False
        if state.get("finishing"):
            return True

        a_toks = selected_tokens("chere_a")
        b_toks = selected_tokens("chere_b")
        if not a_toks:
            set_focus("chere_a")
            stage()
            return True
        if not b_toks:
            set_focus("chere_b")
            stage()
            return True

        state["pending_finish"] = {"a": list(a_toks), "b": list(b_toks)}
        state["finishing"] = True
        try:
            m._app.fireCustomEvent(FINISH_EVENT_ID, "done")
        except Exception:
            state["finishing"] = False
            state["pending_finish"] = None
        return True

    class Execute(adsk.core.CommandEventHandler):
        def notify(self, args):
            # A custom Confirm terminates the command and creates from captured
            # tokens in Finish; do not also create here if Fusion emits Execute.
            if state.get("finishing"):
                return
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
                cid = args.input.id
                stage()
                # The native Fusion command panel remains visible; this simply
                # advances its active selection box after the first object.
                if cid == "chere_a" and count("chere_a") > 0 and count("chere_b") == 0:
                    set_focus("chere_b")
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

    class Finish(adsk.core.CustomEventHandler):
        def notify(self, args):
            pending = state.get("pending_finish")
            state["pending_finish"] = None
            if not pending:
                state["finishing"] = False
                return
            try:
                # Close the modal Fusion command first. The selected tokens were
                # already captured, so Destroy is free to release CommandInputs.
                try:
                    m._ui.terminateActiveCommand()
                except Exception:
                    pass

                design = m._design()
                a = [resolve_body(design, tok) for tok in pending.get("a", [])]
                b = [resolve_body(design, tok) for tok in pending.get("b", [])]
                a = [body for body in a if body is not None]
                b = [body for body in b if body is not None]
                if a and b:
                    create_inplace_mark(a, b)
                    log("CONFIRM created in-place comparison")
            except Exception:
                log("confirm finish failed\n{}".format(m.traceback.format_exc()))
            finally:
                state["finishing"] = False

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
            if action == "confirm" and request_finish():
                return
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                state["pending_finish"] = None
                state["finishing"] = False
                cmd = args.command
                cmd.isRepeatable = False
                try:
                    cmd.okButtonText = "Done"
                    cmd.cancelButtonText = "Cancel"
                except Exception:
                    pass
                inputs = cmd.commandInputs
                a = inputs.addSelectionInput(
                    "chere_a", "1. Option 1", "Select the first object/body")
                b = inputs.addSelectionInput(
                    "chere_b", "2. Option 2", "Select the second object/body")
                for it in (a, b):
                    it.addSelectionFilter("SolidBodies")
                    it.setSelectionLimits(1, 0)   # at least one, unlimited
                    try:
                        it.isUseCurrentSelections = False
                    except Exception:
                        pass
                state["inputs"] = inputs
                set_focus("chere_a")
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
            m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(FINISH_EVENT_ID)
            h = Finish()
            evt.add(h)
            m._handlers.append(h)
        except Exception:
            log("finish event registration failed\n{}".format(m.traceback.format_exc()))
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
        try:
            m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
