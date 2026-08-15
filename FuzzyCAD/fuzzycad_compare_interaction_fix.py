"""Safer staged interaction for FuzzyCAD Compare.

The first Compare prototype left a modal Fusion command active with three
selection inputs and no explicit stage/focus lifecycle.  This patch replaces
only that command shell.  It keeps the existing Compare rendering/card/apply
logic, but makes selection sequential and guarantees that the command closes
after the conflict card is created.
"""

COMPARE_CMD_ID = "FuzzyCAD_Compare"
FINISH_EVENT_ID = "FuzzyCADCompareFinish"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    state = {
        "command": None,
        "inputs": None,
        "created_id": None,
        "finishing": False,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMPARE SAFE] " + msg)
        except Exception:
            pass

    def token(ent):
        if ent is None:
            return None
        try:
            return ent.entityToken
        except Exception:
            return None

    def matrix_to_list(mat):
        out = []
        for r in range(4):
            for c in range(4):
                try:
                    out.append(float(mat.getCell(r, c)))
                except Exception:
                    out.append(1.0 if r == c else 0.0)
        return out

    def point_frame(p):
        mat = adsk.core.Matrix3D.create()
        mat.translation = adsk.core.Vector3D.create(float(p.x), float(p.y), float(p.z))
        return mat

    def target_frame(ent):
        try:
            if isinstance(ent, adsk.fusion.JointOrigin):
                mat = ent.transform
                if mat is None:
                    return None, None, None
                mat = mat.copy()
                return mat, [mat.getCell(0, 3), mat.getCell(1, 3), mat.getCell(2, 3)], getattr(ent, "name", "Joint Origin")
        except Exception:
            pass
        try:
            if isinstance(ent, adsk.fusion.BRepVertex):
                p = ent.geometry
                return point_frame(p), [p.x, p.y, p.z], "Vertex"
        except Exception:
            pass
        try:
            if isinstance(ent, adsk.fusion.ConstructionPoint):
                p = ent.geometry
                return point_frame(p), [p.x, p.y, p.z], getattr(ent, "name", "Construction Point")
        except Exception:
            pass
        return None, None, None

    def body_center_size(body):
        try:
            center, size = m._bbox_center_size(body)
            return list(center), float(size)
        except Exception:
            bb = body.boundingBox
            c = [(bb.minPoint.x + bb.maxPoint.x) * 0.5,
                 (bb.minPoint.y + bb.maxPoint.y) * 0.5,
                 (bb.minPoint.z + bb.maxPoint.z) * 0.5]
            s = max(bb.maxPoint.x - bb.minPoint.x,
                    bb.maxPoint.y - bb.minPoint.y,
                    bb.maxPoint.z - bb.minPoint.z, 0.1)
            return c, s

    def thumb_for_body(body):
        polylines = []
        try:
            for i in range(min(int(body.edges.count), 72)):
                pts = m._sample_edge(body.edges.item(i), 7)
                if len(pts) >= 2:
                    polylines.append(pts)
        except Exception:
            return []
        if not polylines:
            return []
        projected, xs, ys = [], [], []
        for poly in polylines:
            row = []
            for x, y, z in poly:
                u = (x - y) * 0.8660254038
                v = (x + y) * 0.50 - z
                row.append((u, v)); xs.append(u); ys.append(v)
            projected.append(row)
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        w = max(xmax - xmin, 1e-6)
        h = max(ymax - ymin, 1e-6)
        scale = min(88.0 / w, 58.0 / h)
        ox = 50.0 - (xmin + xmax) * 0.5 * scale
        oy = 35.0 - (ymin + ymax) * 0.5 * scale
        return [[[round(u * scale + ox, 2), round(v * scale + oy, 2)] for u, v in row]
                for row in projected]

    def create_mark(target, body_a, body_b):
        if target is None or body_a is None or body_b is None:
            return None
        ta, tb = token(body_a), token(body_b)
        if ta and tb and ta == tb:
            return None
        frame, origin, target_label = target_frame(target)
        if frame is None or origin is None:
            return None

        mid = m._next_id
        m._next_id += 1
        num = m._tool_count.get("compare", 0) + 1
        m._tool_count["compare"] = num
        _, sa = body_center_size(body_a)
        _, sb = body_center_size(body_b)
        mark = {
            "id": mid,
            "tool": "compare",
            "mtype": "conflict",
            "label": "Compare alternatives",
            "anchor": list(origin),
            "size": max(sa, sb, 1.0),
            "num": num,
            "status": "open",
            "comments": [],
            "selected": None,
            "target_token": token(target),
            "target_label": target_label,
            "target_frame": matrix_to_list(frame),
            "alternatives": [
                {"name": getattr(body_a, "name", "Alternative 1"),
                 "token": ta, "thumb": thumb_for_body(body_a)},
                {"name": getattr(body_b, "name", "Alternative 2"),
                 "token": tb, "thumb": thumb_for_body(body_b)},
            ],
        }
        m._marks.append(mark)
        m._geom[mid] = {"alternatives": [body_a, body_b]}
        m._entity[mid] = target
        m._redraw_marks()
        m._send_state()
        try:
            if getattr(m, "_persist_state", None):
                m._persist_state("compare-create")
        except Exception:
            pass
        log("created conflict={} target={} A={} B={}".format(
            mid, target_label, getattr(body_a, "name", "A"), getattr(body_b, "name", "B")))
        return mid

    def count(cid):
        try:
            it = state["inputs"].itemById(cid) if state.get("inputs") else None
            return it.selectionCount if it is not None else 0
        except Exception:
            return 0

    def set_focus(cid):
        if not state.get("inputs"):
            return
        for key in ("cmp_target", "cmp_a", "cmp_b"):
            try:
                it = state["inputs"].itemById(key)
                if it is not None:
                    it.hasFocus = (key == cid)
            except Exception:
                pass

    def stage():
        if not hasattr(m, "_set_tool_stage"):
            return
        t = count("cmp_target") > 0
        a = count("cmp_a") > 0
        b = count("cmp_b") > 0
        active = 0 if not t else (1 if not a else (2 if not b else 3))
        try:
            m._set_tool_stage("compare", [
                {"label": "Select target location", "done": t, "hint": "Joint Origin, vertex, or construction point"},
                {"label": "Select Alternative 1", "done": a, "hint": "solid body"},
                {"label": "Select Alternative 2", "done": b, "hint": "solid body"},
                {"label": "Compare in the card", "done": False},
            ], active, "Compare")
        except Exception:
            pass

    def request_finish():
        if state.get("finishing"):
            return
        state["finishing"] = True
        try:
            m._app.fireCustomEvent(FINISH_EVENT_ID, "done")
        except Exception:
            state["finishing"] = False

    class InputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                state["inputs"] = args.inputs
                cid = args.input.id
                stage()
                if cid == "cmp_target" and count("cmp_target"):
                    set_focus("cmp_a")
                    stage()
                    return
                if cid == "cmp_a" and count("cmp_a"):
                    set_focus("cmp_b")
                    stage()
                    return
                if count("cmp_target") and count("cmp_a") and count("cmp_b") and state.get("created_id") is None:
                    target = state["inputs"].itemById("cmp_target").selection(0).entity
                    body_a = adsk.fusion.BRepBody.cast(state["inputs"].itemById("cmp_a").selection(0).entity)
                    body_b = adsk.fusion.BRepBody.cast(state["inputs"].itemById("cmp_b").selection(0).entity)
                    mid = create_mark(target, body_a, body_b)
                    if mid is None:
                        try:
                            m._ui.messageBox("Compare needs a valid target and two different solid bodies.")
                        except Exception:
                            pass
                        return
                    state["created_id"] = mid
                    stage()
                    request_finish()
            except Exception:
                log("input failed\n{}".format(m.traceback.format_exc()))
                try:
                    m._ui.messageBox("FuzzyCAD Compare selection failed:\n{}".format(m.traceback.format_exc()))
                except Exception:
                    pass

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            state["command"] = None
            state["inputs"] = None
            state["created_id"] = None
            state["finishing"] = False
            try:
                if hasattr(m, "_set_tool_stage"):
                    m._set_tool_stage(None, [], None, "")
            except Exception:
                pass
            log("command closed cleanly")

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                cmd = args.command
                state["command"] = cmd
                state["created_id"] = None
                state["finishing"] = False
                cmd.isRepeatable = False
                # If another Fusion command pre-empts Compare, discard this input
                # session; never execute a half-complete Compare transaction.
                try:
                    cmd.isExecutedWhenPreEmpted = False
                except Exception:
                    pass
                try:
                    cmd.isOKButtonVisible = False
                    cmd.cancelButtonText = "Cancel Compare"
                except Exception:
                    pass

                inputs = cmd.commandInputs
                target = inputs.addSelectionInput(
                    "cmp_target", "1. Target location",
                    "Select a Joint Origin, vertex, or construction point")
                for filt in ("JointOrigins", "Vertices", "ConstructionPoints"):
                    if not target.addSelectionFilter(filt):
                        log("selection filter unavailable: {}".format(filt))
                target.setSelectionLimits(1, 1)

                a = inputs.addSelectionInput(
                    "cmp_a", "2. Alternative 1", "Select the first solid body")
                a.addSelectionFilter("SolidBodies"); a.setSelectionLimits(1, 1)
                b = inputs.addSelectionInput(
                    "cmp_b", "3. Alternative 2", "Select the second solid body")
                b.addSelectionFilter("SolidBodies"); b.setSelectionLimits(1, 1)

                for it in (target, a, b):
                    try:
                        it.isUseCurrentSelections = False
                    except Exception:
                        pass
                state["inputs"] = inputs
                set_focus("cmp_target")
                stage()

                ih = InputChanged(); cmd.inputChanged.add(ih); m._handlers.append(ih)
                dh = Destroy(); cmd.destroy.add(dh); m._handlers.append(dh)
                log("ACTIVE: staged target -> Alternative 1 -> Alternative 2")
            except Exception:
                log("setup failed\n{}".format(m.traceback.format_exc()))
                try:
                    m._ui.messageBox("FuzzyCAD Compare setup failed:\n{}".format(m.traceback.format_exc()))
                except Exception:
                    pass

    class Finish(adsk.core.CustomEventHandler):
        def notify(self, args):
            try:
                # This runs after the selection callback returns to Fusion's main
                # event loop, avoiding termination from inside inputChanged.
                if state.get("created_id") is not None:
                    m._ui.terminateActiveCommand()
            except Exception:
                log("finish failed\n{}".format(m.traceback.format_exc()))
            finally:
                state["finishing"] = False

    def register_command():
        try:
            existing = m._ui.commandDefinitions.itemById(COMPARE_CMD_ID)
            if existing is not None:
                try:
                    existing.deleteMe()
                except Exception:
                    pass
            cd = m._ui.commandDefinitions.addButtonDefinition(
                COMPARE_CMD_ID, "Compare", "Compare two alternatives at one target slot", "")
            h = Created(); cd.commandCreated.add(h); m._handlers.append(h)
            panel = m._ui.allToolbarPanels.itemById(m.PANEL_ID)
            if panel is not None:
                old = panel.controls.itemById(COMPARE_CMD_ID)
                if old is not None:
                    try: old.deleteMe()
                    except Exception: pass
                panel.controls.addCommand(cd)
        except Exception:
            log("registration failed\n{}".format(m.traceback.format_exc()))

    def run(context):
        result = old_run(context)
        try:
            m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(FINISH_EVENT_ID)
            h = Finish(); evt.add(h); m._handlers.append(h)
        except Exception:
            log("finish event registration failed\n{}".format(m.traceback.format_exc()))
        register_command()
        log("READY: safe staged Compare command registered")
        return result

    def stop(context):
        try:
            m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
