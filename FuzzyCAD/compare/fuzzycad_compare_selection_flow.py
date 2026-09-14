"""Rail-first selection flow for in-place Compare.

This module replaces only the Fusion command shell used by ``compare_here``.
The existing in-place Compare renderer remains authoritative once a Conflict
mark exists, but this shell owns the exact assembly identity captured at pick
time. That matters because a body selected in an assembly is a proxy: after the
native command closes, re-binding only its body entityToken can lose the
occurrence context and make visibility changes target the body definition rather
than the occurrence the user actually clicked.

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
    old_accept = m._accept
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    state = {
        "inputs": None,
        "pending": None,
        "active": False,
        "stage_note": None,
    }

    # Exact visibility ownership for in-place Compare. These caches contain only
    # plain Python values; native Fusion wrappers are always resolved fresh.
    hidden_occurrences = {}
    hidden_bodies = {}

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD COMPARE FLOW] " + str(msg))
        except Exception:
            pass

    def trace(event, detail=""):
        try:
            fn = getattr(m, "_crash_trace", None)
            if fn is not None:
                fn(event, detail)
                return
        except Exception:
            pass
        log("{} {}".format(event, detail))

    def token(obj):
        try:
            return str(obj.entityToken)
        except Exception:
            return None

    def occurrence_path(occ):
        try:
            value = str(occ.fullPathName or "").strip()
            return value or None
        except Exception:
            return None

    def occurrence_key_from_values(tok=None, path=None):
        if path:
            return "path:" + str(path)
        return ("token:" + str(tok)) if tok else None

    def occurrence_key_from_alt(alt):
        return occurrence_key_from_values(
            alt.get("occurrence_token"), alt.get("occurrence_path"))

    def resolve_occurrence(tok=None, path=None):
        design = m._design()
        if design is None:
            return None
        if tok:
            try:
                for ent in design.findEntityByToken(str(tok)):
                    occ = adsk.fusion.Occurrence.cast(ent)
                    if occ is not None:
                        return occ
            except Exception:
                pass
        if path:
            try:
                all_occ = design.rootComponent.allOccurrences
                for i in range(all_occ.count):
                    occ = all_occ.item(i)
                    if occ is not None and occurrence_path(occ) == str(path):
                        return occ
            except Exception:
                pass
        return None

    def resolve_body(tok):
        if not tok:
            return None
        try:
            design = m._design()
            if design is None:
                return None
            for ent in design.findEntityByToken(str(tok)):
                body = adsk.fusion.BRepBody.cast(ent)
                if body is not None:
                    return body
        except Exception:
            pass
        return None

    def collection_items(coll):
        out = []
        if coll is None:
            return out
        try:
            for i in range(coll.count):
                item = coll.item(i)
                if item is not None:
                    out.append(item)
        except Exception:
            pass
        return out

    def native_body(body):
        if body is None:
            return None
        try:
            native = body.nativeObject
            return native if native is not None else body
        except Exception:
            return body

    def same_entity(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        try:
            if a == b:
                return True
        except Exception:
            pass
        ta, tb = token(a), token(b)
        return bool(ta and tb and ta == tb)

    def selected_name(body, fallback):
        if body is None:
            return fallback
        try:
            name = str(body.name or "").strip()
            return name if name else fallback
        except Exception:
            return fallback

    def describe_body(body, fallback):
        if body is None:
            return None
        btok = token(body)
        if not btok:
            return None
        try:
            center, size = m._bbox_center_size(body)
        except Exception:
            center, size = [0.0, 0.0, 0.0], 3.0
        desc = {
            "body_token": btok,
            "native_body_token": token(native_body(body)),
            "name": selected_name(body, fallback),
            "anchor": list(center),
            "size": float(size),
        }
        try:
            desc["body_light_bulb"] = bool(body.isLightBulbOn)
        except Exception:
            desc["body_light_bulb"] = True
        try:
            occ = adsk.fusion.Occurrence.cast(body.assemblyContext)
        except Exception:
            occ = None
        if occ is not None:
            desc["occurrence_token"] = token(occ)
            desc["occurrence_path"] = occurrence_path(occ)
            try:
                desc["occurrence_light_bulb"] = bool(occ.isLightBulbOn)
            except Exception:
                desc["occurrence_light_bulb"] = True
        return desc

    def body_matches_native(body, native_tok):
        if body is None or not native_tok:
            return False
        target = resolve_body(native_tok)
        candidate = native_body(body)
        if target is not None and same_entity(candidate, target):
            return True
        return bool(token(candidate) and token(candidate) == str(native_tok))

    def resolve_body_for_alt(alt):
        occ = resolve_occurrence(
            alt.get("occurrence_token"), alt.get("occurrence_path"))
        native_tok = alt.get("native_body_token")
        if occ is not None and native_tok:
            for body in collection_items(getattr(occ, "bRepBodies", None)):
                if body_matches_native(body, native_tok):
                    return body
        for btok in alt.get("body_tokens") or []:
            body = resolve_body(btok)
            if body is not None:
                return body
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
        for key in ("cmpflow_a", "cmpflow_b"):
            try:
                it = selection_input(key)
                if it is not None:
                    it.hasFocus = (key == cid)
            except Exception:
                pass

    def selections_distinct(a=None, b=None):
        a = a or body_from("cmpflow_a")
        b = b or body_from("cmpflow_b")
        if a is None or b is None:
            return True
        if same_entity(a, b):
            return False
        na, nb = native_body(a), native_body(b)
        if same_entity(na, nb):
            try:
                oa = adsk.fusion.Occurrence.cast(a.assemblyContext)
                ob = adsk.fusion.Occurrence.cast(b.assemblyContext)
            except Exception:
                oa = ob = None
            if oa is None and ob is None:
                return False
            if oa is not None and ob is not None:
                ka = occurrence_key_from_values(token(oa), occurrence_path(oa))
                kb = occurrence_key_from_values(token(ob), occurrence_path(ob))
                return ka != kb
        return True

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

    def alt_from_desc(desc, fallback, force_body=False):
        occ_key = occurrence_key_from_values(
            desc.get("occurrence_token"), desc.get("occurrence_path"))
        subject_kind = "body" if force_body or not occ_key else "occurrence"
        return {
            "name": desc.get("name") or fallback,
            "body_tokens": [desc.get("body_token")],
            "native_body_token": desc.get("native_body_token"),
            "subject_kind": subject_kind,
            "occurrence_token": desc.get("occurrence_token"),
            "occurrence_path": desc.get("occurrence_path"),
            "body_light_bulb": desc.get("body_light_bulb", True),
            "occurrence_light_bulb": desc.get("occurrence_light_bulb", True),
        }

    def create_mark(a_desc, b_desc):
        if not a_desc or not b_desc:
            return None
        ta, tb = a_desc.get("body_token"), b_desc.get("body_token")
        if not ta or not tb:
            return None
        ka = occurrence_key_from_values(
            a_desc.get("occurrence_token"), a_desc.get("occurrence_path"))
        kb = occurrence_key_from_values(
            b_desc.get("occurrence_token"), b_desc.get("occurrence_path"))
        same_occurrence = bool(ka and kb and ka == kb)
        mid = m._next_id
        m._next_id += 1
        num = m._tool_count.get("compare", 0) + 1
        m._tool_count["compare"] = num
        mark = {
            "id": mid,
            "tool": "compare",
            "mtype": "conflict",
            "label": "Compare in place",
            "anchor": list(a_desc.get("anchor") or [0.0, 0.0, 0.0]),
            "size": float(a_desc.get("size") or 3.0),
            "num": num,
            "status": "open",
            "comments": [],
            "selected": None,
            "inplace": True,
            "target_label": "in place",
            "alternatives": [
                alt_from_desc(a_desc, "Option 1", force_body=same_occurrence),
                alt_from_desc(b_desc, "Option 2", force_body=same_occurrence),
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
        log("CREATED mark={} A={}({}) B={}({})".format(
            mid, ta, mark["alternatives"][0].get("subject_kind"),
            tb, mark["alternatives"][1].get("subject_kind")))
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
        a_desc = describe_body(a, "Option 1")
        b_desc = describe_body(b, "Option 2")
        if not a_desc or not b_desc:
            return "wait"
        state["stage_note"] = None
        state["pending"] = {"a": a_desc, "b": b_desc}
        return "ready"

    def finish_pending():
        pending = state.get("pending")
        state["pending"] = None
        if not pending:
            return False
        return create_mark(pending.get("a"), pending.get("b")) is not None

    def body_visibility_key(alt, body=None):
        occ_key = occurrence_key_from_alt(alt)
        native_tok = alt.get("native_body_token")
        btok = (alt.get("body_tokens") or [None])[0]
        if occ_key and native_tok:
            return "{}|native:{}".format(occ_key, native_tok)
        return "body:" + str(btok or token(body) or "missing")

    def subject_for_alt(alt):
        if not isinstance(alt, dict):
            return None
        if alt.get("subject_kind") == "occurrence":
            occ = resolve_occurrence(
                alt.get("occurrence_token"), alt.get("occurrence_path"))
            if occ is not None:
                return {"kind": "occurrence", "occurrence": occ,
                        "key": occurrence_key_from_alt(alt), "alt": alt}
        body = resolve_body_for_alt(alt)
        if body is not None:
            return {"kind": "body", "body": body,
                    "key": body_visibility_key(alt, body), "alt": alt}
        return None

    def hide_body_subject(subject):
        body = subject.get("body")
        key = subject.get("key")
        alt = subject.get("alt") or {}
        if body is None or not key:
            return False
        if key not in hidden_bodies:
            try:
                original = bool(body.isLightBulbOn)
            except Exception:
                original = bool(alt.get("body_light_bulb", True))
            hidden_bodies[key] = {
                "body_token": (alt.get("body_tokens") or [None])[0],
                "native_body_token": alt.get("native_body_token"),
                "occurrence_token": alt.get("occurrence_token"),
                "occurrence_path": alt.get("occurrence_path"),
                "original": original,
            }
        try:
            body.isLightBulbOn = False
        except Exception:
            trace("COMPARE_HIDE_BODY_SET_FAILED", "key={}".format(key))
            return False
        try:
            visible = bool(body.isVisible)
        except Exception:
            visible = False
        trace("COMPARE_HIDE_BODY", "key={} visible_after={}".format(key, visible))
        return not visible

    def hide_occurrence_subject(subject):
        occ = subject.get("occurrence")
        key = subject.get("key")
        alt = subject.get("alt") or {}
        if occ is None or not key:
            return []
        if key not in hidden_occurrences:
            try:
                original = bool(occ.isLightBulbOn)
            except Exception:
                original = bool(alt.get("occurrence_light_bulb", True))
            hidden_occurrences[key] = {
                "token": alt.get("occurrence_token"),
                "path": alt.get("occurrence_path"),
                "original": original,
            }
        try:
            occ.isLightBulbOn = False
        except Exception:
            trace("COMPARE_HIDE_OCC_SET_FAILED", "key={}".format(key))
        try:
            visible = bool(occ.isVisible)
        except Exception:
            visible = False
        trace("COMPARE_HIDE_OCC", "key={} visible_after={}".format(key, visible))
        if not visible:
            return []
        fallback = []
        for body in collection_items(getattr(occ, "bRepBodies", None)):
            body_alt = {
                "body_tokens": [token(body)],
                "native_body_token": token(native_body(body)),
                "occurrence_token": alt.get("occurrence_token"),
                "occurrence_path": alt.get("occurrence_path"),
                "body_light_bulb": True,
            }
            bsub = {"kind": "body", "body": body,
                    "key": body_visibility_key(body_alt, body), "alt": body_alt}
            hide_body_subject(bsub)
            fallback.append(bsub["key"])
        return fallback

    def restore_occurrence_key(key, row):
        occ = resolve_occurrence(row.get("token"), row.get("path"))
        if occ is not None:
            try:
                occ.isLightBulbOn = bool(row.get("original", True))
            except Exception:
                pass
        hidden_occurrences.pop(key, None)

    def resolve_hidden_body(row):
        alt = {
            "body_tokens": [row.get("body_token")],
            "native_body_token": row.get("native_body_token"),
            "occurrence_token": row.get("occurrence_token"),
            "occurrence_path": row.get("occurrence_path"),
        }
        return resolve_body_for_alt(alt)

    def restore_body_key(key, row):
        body = resolve_hidden_body(row)
        if body is not None:
            try:
                body.isLightBulbOn = bool(row.get("original", True))
            except Exception:
                pass
        hidden_bodies.pop(key, None)

    def reconcile_exact_visibility():
        want_occurrences = {}
        want_bodies = {}
        for mark in list(getattr(m, "_marks", []) or []):
            if not (mark.get("tool") == "compare" and mark.get("inplace")
                    and mark.get("status", "open") == "open"):
                continue
            for alt in (mark.get("alternatives") or [])[:2]:
                subject = subject_for_alt(alt)
                if subject is None:
                    continue
                key = subject.get("key")
                if not key:
                    continue
                if subject.get("kind") == "occurrence":
                    want_occurrences[key] = subject
                else:
                    want_bodies[key] = subject
        for key, subject in want_occurrences.items():
            fallback_keys = hide_occurrence_subject(subject)
            for bkey in fallback_keys:
                if bkey in hidden_bodies:
                    want_bodies[bkey] = None
        for key, subject in want_bodies.items():
            if subject is not None:
                hide_body_subject(subject)
        for key, row in list(hidden_occurrences.items()):
            if key not in want_occurrences:
                restore_occurrence_key(key, row)
        for key, row in list(hidden_bodies.items()):
            if key not in want_bodies:
                restore_body_key(key, row)

    try:
        overlays = getattr(m, "_persistent_overlays", None)
        if overlays is None:
            overlays = []
            m._persistent_overlays = overlays
        kept = []
        for fn in overlays:
            if (getattr(fn, "__name__", "") == "reconcile_visibility"
                    and "compare_inplace" in str(getattr(fn, "__module__", ""))):
                continue
            kept.append(fn)
        overlays[:] = kept
        if reconcile_exact_visibility not in overlays:
            overlays.append(reconcile_exact_visibility)
    except Exception:
        pass

    def restore_subject_now(subject):
        if subject is None:
            return
        alt = subject.get("alt") or {}
        if subject.get("kind") == "occurrence":
            row = hidden_occurrences.get(subject.get("key")) or {}
            value = row.get("original", alt.get("occurrence_light_bulb", True))
            try:
                subject.get("occurrence").isLightBulbOn = bool(value)
            except Exception:
                pass
            return
        row = hidden_bodies.get(subject.get("key")) or {}
        value = row.get("original", alt.get("body_light_bulb", True))
        try:
            subject.get("body").isLightBulbOn = bool(value)
        except Exception:
            pass

    def delete_subject(subject):
        if subject is None:
            return False, "comparison subject could not be resolved"
        if subject.get("kind") == "occurrence":
            occ = subject.get("occurrence")
            if occ is None:
                return False, "occurrence could not be resolved"
            try:
                if bool(getattr(occ, "isDerived", False)):
                    return False, "derived occurrence cannot be deleted"
            except Exception:
                pass
            try:
                result = occ.deleteMe()
                return (result is not False), "occurrence"
            except Exception:
                return False, "occurrence delete failed"
        body = subject.get("body")
        if body is None:
            return False, "body could not be resolved"
        try:
            if bool(getattr(body, "isDerived", False)):
                return False, "derived body cannot be deleted"
        except Exception:
            pass
        try:
            result = body.deleteMe()
            return (result is not False), "body"
        except Exception:
            return False, "body delete failed"

    def accept(mark):
        alts = (mark.get("alternatives") or [])[:2] if isinstance(mark, dict) else []
        exact = bool(mark and mark.get("tool") == "compare" and mark.get("inplace")
                     and len(alts) >= 2
                     and any(alt.get("subject_kind") for alt in alts
                             if isinstance(alt, dict)))
        if not exact:
            return old_accept(mark)
        choice = mark.get("selected")
        if choice not in (0, 1):
            try:
                m._ui.messageBox("Choose Option 1 or Option 2 first.")
            except Exception:
                pass
            return False
        subjects = [subject_for_alt(alt) for alt in alts]
        if subjects[0] is None or subjects[1] is None:
            return False
        winner = int(choice)
        loser = 1 - winner
        winner_subject = subjects[winner]
        loser_subject = subjects[loser]
        trace("COMPARE_ACCEPT_EXACT_BEGIN", "id={} choice={} winner={} loser={}".format(
            mark.get("id"), winner + 1,
            winner_subject.get("kind"), loser_subject.get("kind")))
        restore_subject_now(winner_subject)
        ok, detail = delete_subject(loser_subject)
        if not ok:
            trace("COMPARE_ACCEPT_EXACT_FAILED", "id={} reason={}".format(
                mark.get("id"), detail))
            try:
                reconcile_exact_visibility()
            except Exception:
                pass
            try:
                m._ui.messageBox(
                    "FuzzyCAD couldn't remove the unselected comparison option. "
                    "The comparison is still unresolved.")
            except Exception:
                pass
            return False
        restore_subject_now(winner_subject)
        trace("COMPARE_ACCEPT_EXACT_DONE", "id={} keep={} removed={}".format(
            mark.get("id"), winner + 1, detail))
        return True

    m._accept = accept

    class InputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                state["inputs"] = args.inputs
                cid = args.input.id
                state["stage_note"] = None
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
                args.areInputsValid = bool(
                    a is not None and b is not None and selections_distinct(a, b))
            except Exception:
                args.areInputsValid = False

    class Execute(adsk.core.CommandEventHandler):
        def notify(self, args):
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
            CMD_ID, "Compare",
            "Select two existing bodies in the viewport, then Confirm", "")
        h = Created()
        cd.commandCreated.add(h)
        m._handlers.append(h)
        if panel is not None:
            panel.controls.addCommand(cd)
        m.CMD_ID["compare_here"] = CMD_ID
        log("REGISTERED rail-first Compare command")

    def restore_all_exact_visibility():
        for key, row in list(hidden_occurrences.items()):
            restore_occurrence_key(key, row)
        for key, row in list(hidden_bodies.items()):
            restore_body_key(key, row)

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
        try:
            restore_all_exact_visibility()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
