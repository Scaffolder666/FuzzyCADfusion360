"""Connector-driven Compare workflow for FuzzyCAD.

Compare should feel like mating alternatives, not positioning their bounding boxes.
The user clicks a circular connection edge on the target, then the corresponding
connection edge on each alternative. Each click becomes a local coordinate frame.
Alternatives are previewed/applied with targetFrame * mateFlip * inverse(sourceFrame).
"""

import math

COMPARE_CMD_ID = "FuzzyCAD_Compare"
FINISH_EVENT_ID = "FuzzyCADCompareConnectorFinish"
GROUP_CONNECTOR = "FuzzyCAD_CompareConnectorPreview"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    old_accept = m._accept

    state = {"inputs": None, "created_id": None, "finishing": False}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD COMPARE CONNECTOR] " + msg)
        except Exception:
            pass

    def token(ent):
        if ent is None:
            return None
        try:
            return ent.entityToken
        except Exception:
            return None

    # ---- explicit 4x4 math -------------------------------------------------
    def matrix_to_list(mat):
        return [float(mat.getCell(r, c)) for r in range(4) for c in range(4)]

    def list_to_matrix(vals):
        mat = adsk.core.Matrix3D.create()
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return mat
        k = 0
        for r in range(4):
            for c in range(4):
                mat.setCell(r, c, float(vals[k])); k += 1
        return mat

    def mat_mul(a, b):
        return [sum(float(a[r * 4 + k]) * float(b[k * 4 + c]) for k in range(4))
                for r in range(4) for c in range(4)]

    def mat_inverse(vals):
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return None
        mat = list_to_matrix(vals)
        if not mat.invert():
            return None
        return matrix_to_list(mat)

    def placement_values(mark, alt_index):
        target = mark.get("target_frame")
        alts = mark.get("alternatives") or []
        if not isinstance(target, (list, tuple)) or len(target) != 16:
            return None
        if alt_index < 0 or alt_index >= len(alts):
            return None
        source = alts[alt_index].get("connector_frame")
        inv_source = mat_inverse(source)
        if inv_source is None:
            return None
        identity = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
        # Face-to-face mate by default: source normal ends opposite target normal.
        flip = [1,0,0,0, 0,-1,0,0, 0,0,-1,0, 0,0,0,1]
        local = flip if mark.get("oppose_normals", True) else identity
        return mat_mul(mat_mul(list(target), local), inv_source)

    def placement_matrix(mark, alt_index):
        vals = placement_values(mark, alt_index)
        return list_to_matrix(vals) if vals else None

    # ---- circular edge -> connection frame --------------------------------
    def circular_data(edge):
        if not isinstance(edge, adsk.fusion.BRepEdge):
            return None
        try:
            geo = edge.geometry
        except Exception:
            return None
        if not isinstance(geo, (adsk.core.Circle3D, adsk.core.Arc3D)):
            return None
        try:
            c = geo.center
            n = geo.normal.copy(); n.normalize()
            return geo, c, n, float(geo.radius)
        except Exception:
            return None

    def radial(center, normal, point):
        if point is None:
            return None
        try:
            v = adsk.core.Vector3D.create(
                point.x - center.x, point.y - center.y, point.z - center.z)
            npart = normal.copy(); npart.scaleBy(v.dotProduct(normal))
            v.subtract(npart)
            if v.length < 1e-7:
                return None
            v.normalize()
            return v
        except Exception:
            return None

    def fallback_x(edge, geo, center, normal):
        try:
            if isinstance(geo, adsk.core.Arc3D):
                x = geo.referenceVector.copy(); x.normalize(); return x
        except Exception:
            pass
        try:
            x = radial(center, normal, edge.startVertex.geometry)
            if x is not None:
                return x
        except Exception:
            pass
        helper = adsk.core.Vector3D.create(1, 0, 0)
        if abs(normal.dotProduct(helper)) > 0.88:
            helper = adsk.core.Vector3D.create(0, 1, 0)
        npart = normal.copy(); npart.scaleBy(helper.dotProduct(normal))
        helper.subtract(npart); helper.normalize()
        return helper

    def connector_from_selection(sel):
        if sel is None:
            return None
        try:
            edge = adsk.fusion.BRepEdge.cast(sel.entity)
        except Exception:
            edge = None
        data = circular_data(edge)
        if data is None:
            return None
        geo, center, zaxis, radius = data
        try:
            click = sel.point
        except Exception:
            click = None
        xaxis = radial(center, zaxis, click) or fallback_x(edge, geo, center, zaxis)
        try:
            yaxis = zaxis.crossProduct(xaxis); yaxis.normalize()
            xaxis = yaxis.crossProduct(zaxis); xaxis.normalize()
            frame = adsk.core.Matrix3D.create()
            frame.setWithCoordinateSystem(center, xaxis, yaxis, zaxis)
        except Exception:
            return None
        return {
            "edge": edge, "body": edge.body, "frame": matrix_to_list(frame),
            "origin": [center.x, center.y, center.z],
            "x": [xaxis.x, xaxis.y, xaxis.z],
            "y": [yaxis.x, yaxis.y, yaxis.z],
            "z": [zaxis.x, zaxis.y, zaxis.z], "radius": radius,
        }

    def selected_connector(cid):
        try:
            it = state["inputs"].itemById(cid)
            if it is None or it.selectionCount < 1:
                return None
            return connector_from_selection(it.selection(0))
        except Exception:
            return None

    # ---- card data ---------------------------------------------------------
    def body_size(body):
        try:
            _, size = m._bbox_center_size(body); return float(size)
        except Exception:
            bb = body.boundingBox
            return max(bb.maxPoint.x - bb.minPoint.x,
                       bb.maxPoint.y - bb.minPoint.y,
                       bb.maxPoint.z - bb.minPoint.z, 0.1)

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
        xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
        w = max(xmax - xmin, 1e-6); h = max(ymax - ymin, 1e-6)
        scale = min(88.0 / w, 58.0 / h)
        ox = 50.0 - (xmin + xmax) * 0.5 * scale
        oy = 35.0 - (ymin + ymax) * 0.5 * scale
        return [[[round(u * scale + ox, 2), round(v * scale + oy, 2)] for u, v in row]
                for row in projected]

    def create_mark(target_c, a_c, b_c):
        if not target_c or not a_c or not b_c:
            return None
        body_a, body_b = a_c["body"], b_c["body"]
        ta, tb = token(body_a), token(body_b)
        if ta and tb and ta == tb:
            return None
        mid = m._next_id; m._next_id += 1
        num = m._tool_count.get("compare", 0) + 1; m._tool_count["compare"] = num
        target_name = getattr(target_c["body"], "name", "Target")
        mark = {
            "id": mid, "tool": "compare", "mtype": "conflict",
            "label": "Compare alternatives", "anchor": list(target_c["origin"]),
            "size": max(body_size(body_a), body_size(body_b),
                        target_c.get("radius", 0.0) * 2.0, 1.0),
            "num": num, "status": "open", "comments": [], "selected": None,
            "oppose_normals": True,
            "target_token": token(target_c["edge"]),
            "target_label": "{} connection".format(target_name),
            "target_frame": list(target_c["frame"]),
            "target_connector_radius": target_c.get("radius"),
            "alternatives": [
                {"name": getattr(body_a, "name", "Alternative 1"), "token": ta,
                 "connector_token": token(a_c["edge"]),
                 "connector_frame": list(a_c["frame"]),
                 "connector_radius": a_c.get("radius"), "thumb": thumb_for_body(body_a)},
                {"name": getattr(body_b, "name", "Alternative 2"), "token": tb,
                 "connector_token": token(b_c["edge"]),
                 "connector_frame": list(b_c["frame"]),
                 "connector_radius": b_c.get("radius"), "thumb": thumb_for_body(body_b)},
            ],
        }
        m._marks.append(mark)
        m._geom[mid] = {"alternatives": [body_a, body_b]}
        m._entity[mid] = target_c["edge"]
        m._redraw_marks(); m._send_state()
        try:
            if getattr(m, "_persist_state", None):
                m._persist_state("compare-connectors-create")
        except Exception:
            pass
        try: m._focus_camera(mark["anchor"])
        except Exception: pass
        log("CREATED conflict={} target={} A={} B={}".format(
            mid, target_name, getattr(body_a, "name", "A"), getattr(body_b, "name", "B")))
        return mid

    # ---- rendering/application --------------------------------------------
    def resolve_body(tok):
        if not tok:
            return None
        try:
            for ent in m._design().findEntityByToken(tok):
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def alt_bodies(mark):
        g = m._geom.setdefault(mark.get("id"), {})
        bodies = g.get("alternatives")
        if bodies and len(bodies) >= 2 and all(bodies[:2]):
            return bodies
        bodies = [resolve_body(a.get("token")) for a in (mark.get("alternatives") or [])]
        if len(bodies) >= 2 and all(bodies[:2]):
            g["alternatives"] = bodies
        return bodies

    def conflict_style(name, rgb, opacity):
        try:
            st = m.VISUAL_TOKENS.get(name, {})
            return tuple(st.get("rgb", rgb)), float(st.get("opacity", opacity))
        except Exception:
            return rgb, opacity

    def add_placed(group, body, mark, index, rgb, opacity):
        matrix = placement_matrix(mark, index)
        if matrix is None:
            return
        try:
            mgr = adsk.fusion.TemporaryBRepManager.get()
            temp = mgr.copy(body)
            if temp is None or not mgr.transform(temp, matrix):
                return
            cg = group.addBRepBody(temp); cg.color = m._solid(rgb); cg.setOpacity(opacity, True)
        except Exception as exc:
            log("preview placement failed alt={}: {}".format(index, exc))

    def draw_target_connector(group, mark):
        vals = mark.get("target_frame")
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return
        o = [float(vals[3]), float(vals[7]), float(vals[11])]
        x = [float(vals[0]), float(vals[4]), float(vals[8])]
        y = [float(vals[1]), float(vals[5]), float(vals[9])]
        z = [float(vals[2]), float(vals[6]), float(vals[10])]
        r = float(mark.get("target_connector_radius") or max(mark.get("size", 3.0) * 0.08, 0.25))
        r = max(0.08, min(r, max(0.2, mark.get("size", 3.0) * 0.3)))
        def P(axis, scale): return tuple(o[i] + axis[i] * scale for i in range(3))
        try:
            m._visual_stroke(group, [P(z, -r*.55), P(z, r*.90)], "conflict_marker",
                             mark["id"]*75001, size=mark.get("size", 3.0))
            ring = []
            for i in range(33):
                a = math.pi*2*i/32.0
                ring.append(tuple(o[j] + (x[j]*math.cos(a)+y[j]*math.sin(a))*r for j in range(3)))
            m._visual_stroke(group, ring, "conflict_marker", mark["id"]*75002,
                             size=mark.get("size", 3.0))
        except Exception:
            pass

    def draw_compare(group, mark, rgb, amp):
        bodies = alt_bodies(mark)
        if len(bodies) < 2 or not all(bodies[:2]):
            draw_target_connector(group, mark); return
        sa = conflict_style("conflict_alt_a", (126,104,180), .18)
        sb = conflict_style("conflict_alt_b", (92,118,170), .18)
        ss = conflict_style("conflict_selected", (92,96,104), .42)
        choice = mark.get("selected")
        if choice in (0,1):
            add_placed(group, bodies[int(choice)], mark, int(choice), ss[0], ss[1])
        else:
            add_placed(group, bodies[0], mark, 0, sa[0], sa[1])
            add_placed(group, bodies[1], mark, 1, sb[0], sb[1])
        draw_target_connector(group, mark)

    m._DRAW["compare"] = draw_compare

    def accept(mark):
        if mark.get("tool") != "compare":
            return old_accept(mark)
        choice = mark.get("selected")
        if choice not in (0,1):
            try: m._ui.messageBox("Choose Alternative 1 or Alternative 2 first.")
            except Exception: pass
            return False
        bodies = alt_bodies(mark)
        if len(bodies) < 2 or bodies[int(choice)] is None:
            return False
        matrix = placement_matrix(mark, int(choice))
        if matrix is None:
            return False
        src = bodies[int(choice)]
        try:
            root = m._design().rootComponent
            placed = src.copyToComponent(root)
            if placed is None:
                return False
            coll = adsk.core.ObjectCollection.create(); coll.add(placed)
            moves = root.features.moveFeatures
            moves.add(moves.createInput(coll, matrix))
            try:
                placed.name = "{} (chosen)".format(mark["alternatives"][int(choice)].get("name", "Alternative"))
            except Exception:
                pass
            return True
        except Exception:
            try: m._ui.messageBox("FuzzyCAD couldn't place the chosen alternative:\n{}".format(m.traceback.format_exc()))
            except Exception: pass
            return False

    m._accept = accept

    # ---- selection feedback ------------------------------------------------
    def clear_preview():
        try: m._clear(GROUP_CONNECTOR)
        except Exception: pass

    def draw_selection_feedback():
        clear_preview()
        group = m._group(GROUP_CONNECTOR)
        if group is None:
            return
        for cid, seed in (("cmp_target",81001),("cmp_a",82001),("cmp_b",83001)):
            c = selected_connector(cid)
            if not c:
                continue
            o,x,y,z = c["origin"],c["x"],c["y"],c["z"]
            r = max(.12, min(float(c.get("radius") or .4), 1.2))
            def P(axis, scale): return tuple(o[j]+axis[j]*scale for j in range(3))
            try:
                m._visual_stroke(group, [P(z,-r*.35),P(z,r*.8)], "conflict_marker", seed,
                                 size=max(r*4,1.0))
                ring=[]
                for i in range(25):
                    a=math.pi*2*i/24.0
                    ring.append(tuple(o[j]+(x[j]*math.cos(a)+y[j]*math.sin(a))*r for j in range(3)))
                m._visual_stroke(group, ring, "conflict_marker", seed+1, size=max(r*4,1.0))
            except Exception:
                pass
        try: m._app.activeViewport.refresh()
        except Exception: pass

    # ---- safe staged command ----------------------------------------------
    def count(cid):
        try:
            it = state["inputs"].itemById(cid) if state.get("inputs") else None
            return it.selectionCount if it is not None else 0
        except Exception:
            return 0

    def set_focus(cid):
        if not state.get("inputs"):
            return
        for key in ("cmp_target","cmp_a","cmp_b"):
            try:
                it=state["inputs"].itemById(key)
                if it is not None: it.hasFocus=(key==cid)
            except Exception: pass

    def stage():
        if not hasattr(m, "_set_tool_stage"):
            return
        t=count("cmp_target")>0; a=count("cmp_a")>0; b=count("cmp_b")>0
        active=0 if not t else (1 if not a else (2 if not b else 3))
        try:
            m._set_tool_stage("compare", [
                {"label":"Click target connection","done":t,"hint":"circular edge"},
                {"label":"Click Alternative 1 connection","done":a,"hint":"circular edge"},
                {"label":"Click Alternative 2 connection","done":b,"hint":"circular edge"},
                {"label":"Compare in the card","done":False}], active, "Compare")
        except Exception: pass

    def reject_non_circular(cid):
        try:
            it=state["inputs"].itemById(cid); it.clearSelection(); it.hasFocus=True
        except Exception: pass
        try: m._ui.messageBox("Select a circular or arc edge at the connection point.")
        except Exception: pass

    def request_finish():
        if state.get("finishing"):
            return
        state["finishing"]=True
        try: m._app.fireCustomEvent(FINISH_EVENT_ID, "done")
        except Exception: state["finishing"]=False

    class InputChanged(adsk.core.InputChangedEventHandler):
        def notify(self,args):
            try:
                state["inputs"]=args.inputs; cid=args.input.id
                if cid not in ("cmp_target","cmp_a","cmp_b"):
                    return
                if count(cid) and selected_connector(cid) is None:
                    reject_non_circular(cid); draw_selection_feedback(); stage(); return
                draw_selection_feedback(); stage()
                if cid=="cmp_target" and count("cmp_target"):
                    set_focus("cmp_a"); stage(); return
                if cid=="cmp_a" and count("cmp_a"):
                    set_focus("cmp_b"); stage(); return
                if count("cmp_target") and count("cmp_a") and count("cmp_b") and state.get("created_id") is None:
                    mid=create_mark(selected_connector("cmp_target"), selected_connector("cmp_a"), selected_connector("cmp_b"))
                    if mid is None:
                        try: m._ui.messageBox("Compare needs three circular connection edges, and the two alternatives must be different bodies.")
                        except Exception: pass
                        return
                    state["created_id"]=mid; clear_preview(); stage(); request_finish()
            except Exception:
                log("input failed\n{}".format(m.traceback.format_exc()))
                try: m._ui.messageBox("FuzzyCAD Compare connection failed:\n{}".format(m.traceback.format_exc()))
                except Exception: pass

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self,args):
            state["inputs"]=None; state["created_id"]=None; state["finishing"]=False
            clear_preview()
            try:
                if hasattr(m,"_set_tool_stage"): m._set_tool_stage(None,[],None,"")
            except Exception: pass
            log("command closed cleanly")

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self,args):
            try:
                cmd=args.command; state["created_id"]=None; state["finishing"]=False
                cmd.isRepeatable=False
                try: cmd.isExecutedWhenPreEmpted=False
                except Exception: pass
                try: cmd.isOKButtonVisible=False; cmd.cancelButtonText="Cancel Compare"
                except Exception: pass
                inputs=cmd.commandInputs
                target=inputs.addSelectionInput("cmp_target","1. Target connection","Click a circular connection edge on the target")
                target.addSelectionFilter("Edges"); target.setSelectionLimits(1,1)
                a=inputs.addSelectionInput("cmp_a","2. Alternative 1 connection","Click its circular connection edge")
                a.addSelectionFilter("Edges"); a.setSelectionLimits(1,1)
                b=inputs.addSelectionInput("cmp_b","3. Alternative 2 connection","Click its circular connection edge")
                b.addSelectionFilter("Edges"); b.setSelectionLimits(1,1)
                for it in (target,a,b):
                    try: it.isUseCurrentSelections=False
                    except Exception: pass
                state["inputs"]=inputs; set_focus("cmp_target"); stage()
                ih=InputChanged(); cmd.inputChanged.add(ih); m._handlers.append(ih)
                dh=Destroy(); cmd.destroy.add(dh); m._handlers.append(dh)
                log("ACTIVE: target connector -> Alt 1 connector -> Alt 2 connector")
            except Exception:
                log("setup failed\n{}".format(m.traceback.format_exc()))
                try: m._ui.messageBox("FuzzyCAD Compare setup failed:\n{}".format(m.traceback.format_exc()))
                except Exception: pass

    class Finish(adsk.core.CustomEventHandler):
        def notify(self,args):
            try:
                if state.get("created_id") is not None: m._ui.terminateActiveCommand()
            except Exception: log("finish failed\n{}".format(m.traceback.format_exc()))
            finally: state["finishing"]=False

    def register_command():
        try:
            existing=m._ui.commandDefinitions.itemById(COMPARE_CMD_ID)
            if existing is not None:
                try: existing.deleteMe()
                except Exception: pass
            cd=m._ui.commandDefinitions.addButtonDefinition(COMPARE_CMD_ID,"Compare","Compare alternatives by matching connection edges","")
            h=Created(); cd.commandCreated.add(h); m._handlers.append(h)
            panel=m._ui.allToolbarPanels.itemById(m.PANEL_ID)
            if panel is not None:
                old=panel.controls.itemById(COMPARE_CMD_ID)
                if old is not None:
                    try: old.deleteMe()
                    except Exception: pass
                panel.controls.addCommand(cd)
        except Exception: log("registration failed\n{}".format(m.traceback.format_exc()))

    def run(context):
        result=old_run(context)
        try: m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception: pass
        try:
            evt=m._app.registerCustomEvent(FINISH_EVENT_ID)
            h=Finish(); evt.add(h); m._handlers.append(h)
        except Exception: log("finish event registration failed\n{}".format(m.traceback.format_exc()))
        register_command(); log("READY: connector-driven Compare registered")
        return result

    def stop(context):
        clear_preview()
        try: m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception: pass
        return old_stop(context)

    m.run=run
    m.stop=stop
