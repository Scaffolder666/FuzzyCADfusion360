"""Prefer planar face centers as Compare connection locations.

This patch keeps the existing connector-based Compare rendering and placement,
but replaces the command shell so each connector can be chosen by clicking
 either:
- a planar BRep face: use BRepFace.centroid + outward face normal;
- a circular/arc BRep edge: use the circle center + curve normal.

For both cases the actual click location defines the in-plane X direction when
possible. The resulting mark schema matches fuzzycad_compare_connectors, so the
existing preview/card/accept logic continues to use
TargetFrame * mateFlip * inverse(SourceFrame).
"""

import math

COMPARE_CMD_ID = "FuzzyCAD_Compare"
FINISH_EVENT_ID = "FuzzyCADCompareFaceFinish"
GROUP_CONNECTOR = "FuzzyCAD_CompareConnectorPreview"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    state = {"inputs": None, "created_id": None, "finishing": False}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMPARE FACE] " + msg)
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
        return [float(mat.getCell(r, c)) for r in range(4) for c in range(4)]

    def radial(center, normal, point):
        if point is None:
            return None
        try:
            v = adsk.core.Vector3D.create(point.x-center.x, point.y-center.y, point.z-center.z)
            n_part = normal.copy(); n_part.scaleBy(v.dotProduct(normal)); v.subtract(n_part)
            if v.length < 1e-7:
                return None
            v.normalize(); return v
        except Exception:
            return None

    def generic_x(normal):
        helper = adsk.core.Vector3D.create(1, 0, 0)
        try:
            if abs(normal.dotProduct(helper)) > 0.88:
                helper = adsk.core.Vector3D.create(0, 1, 0)
            n_part = normal.copy(); n_part.scaleBy(helper.dotProduct(normal)); helper.subtract(n_part)
            helper.normalize(); return helper
        except Exception:
            return adsk.core.Vector3D.create(1, 0, 0)

    def finish_frame(entity, body, center, zaxis, click, fallback=None, radius=0.4, kind="connector"):
        try:
            zaxis.normalize()
            xaxis = radial(center, zaxis, click) or radial(center, zaxis, fallback) or generic_x(zaxis)
            yaxis = zaxis.crossProduct(xaxis); yaxis.normalize()
            xaxis = yaxis.crossProduct(zaxis); xaxis.normalize()
            frame = adsk.core.Matrix3D.create()
            frame.setWithCoordinateSystem(center, xaxis, yaxis, zaxis)
        except Exception:
            return None
        return {
            "entity": entity, "body": body, "kind": kind,
            "frame": matrix_to_list(frame),
            "origin": [center.x, center.y, center.z],
            "x": [xaxis.x, xaxis.y, xaxis.z],
            "y": [yaxis.x, yaxis.y, yaxis.z],
            "z": [zaxis.x, zaxis.y, zaxis.z],
            "radius": max(float(radius or 0.0), 0.08),
        }

    def edge_connector(sel, edge):
        try:
            geo = edge.geometry
            if not isinstance(geo, (adsk.core.Circle3D, adsk.core.Arc3D)):
                return None
            center = geo.center
            zaxis = geo.normal.copy(); zaxis.normalize()
            radius = float(geo.radius)
            click = sel.point
        except Exception:
            return None
        fallback = None
        try: fallback = edge.startVertex.geometry
        except Exception: pass
        return finish_frame(edge, edge.body, center, zaxis, click, fallback, radius, "circular_edge")

    def face_radius(face, center, normal):
        best = 0.0
        try:
            for i in range(face.edges.count):
                pts = m._sample_edge(face.edges.item(i), 12)
                for xyz in pts:
                    p = adsk.core.Point3D.create(*xyz)
                    v = adsk.core.Vector3D.create(p.x-center.x, p.y-center.y, p.z-center.z)
                    n_part = normal.copy(); n_part.scaleBy(v.dotProduct(normal)); v.subtract(n_part)
                    best = max(best, float(v.length))
        except Exception:
            pass
        if best > 1e-7:
            return best
        try:
            bb = face.boundingBox
            return max(bb.maxPoint.x-bb.minPoint.x,
                       bb.maxPoint.y-bb.minPoint.y,
                       bb.maxPoint.z-bb.minPoint.z, 0.2) * 0.5
        except Exception:
            return 0.4

    def face_fallback(face):
        try:
            for i in range(face.edges.count):
                e = face.edges.item(i)
                try:
                    if e.startVertex is not None:
                        return e.startVertex.geometry
                except Exception:
                    pass
                pts = m._sample_edge(e, 2)
                if pts:
                    return adsk.core.Point3D.create(*pts[0])
        except Exception:
            pass
        return None

    def face_connector(sel, face):
        try:
            geo = face.geometry
            if not isinstance(geo, adsk.core.Plane):
                return None
            center = face.centroid
            ok, zaxis = face.evaluator.getNormalAtPoint(center)
            if not ok or zaxis is None:
                zaxis = geo.normal.copy()
                if face.isParamReversed:
                    zaxis.scaleBy(-1.0)
            zaxis.normalize()
        except Exception:
            return None
        try: click = sel.point
        except Exception: click = None
        return finish_frame(face, face.body, center, zaxis, click,
                            face_fallback(face), face_radius(face, center, zaxis), "planar_face")

    def connector_from_selection(sel):
        if sel is None:
            return None
        try: ent = sel.entity
        except Exception: return None
        try:
            face = adsk.fusion.BRepFace.cast(ent)
            if face is not None:
                return face_connector(sel, face)
        except Exception:
            pass
        try:
            edge = adsk.fusion.BRepEdge.cast(ent)
            if edge is not None:
                return edge_connector(sel, edge)
        except Exception:
            pass
        return None

    def selected_connector(cid):
        try:
            it = state["inputs"].itemById(cid)
            if it is None or it.selectionCount < 1:
                return None
            return connector_from_selection(it.selection(0))
        except Exception:
            return None

    def body_size(body):
        try:
            _, size = m._bbox_center_size(body); return float(size)
        except Exception:
            bb = body.boundingBox
            return max(bb.maxPoint.x-bb.minPoint.x, bb.maxPoint.y-bb.minPoint.y,
                       bb.maxPoint.z-bb.minPoint.z, 0.1)

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
                u = (x-y)*0.8660254038; v = (x+y)*0.50-z
                row.append((u, v)); xs.append(u); ys.append(v)
            projected.append(row)
        xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
        w=max(xmax-xmin,1e-6); h=max(ymax-ymin,1e-6); scale=min(88.0/w,58.0/h)
        ox=50.0-(xmin+xmax)*0.5*scale; oy=35.0-(ymin+ymax)*0.5*scale
        return [[[round(u*scale+ox,2), round(v*scale+oy,2)] for u,v in row] for row in projected]

    def create_mark(target_c, a_c, b_c):
        if not target_c or not a_c or not b_c:
            return None
        body_a, body_b = a_c["body"], b_c["body"]
        ta, tb = token(body_a), token(body_b)
        if ta and tb and ta == tb:
            return None
        mid=m._next_id; m._next_id+=1
        num=m._tool_count.get("compare",0)+1; m._tool_count["compare"]=num
        target_name=getattr(target_c["body"],"name","Target")
        mark={
            "id":mid,"tool":"compare","mtype":"conflict","label":"Compare alternatives",
            "anchor":list(target_c["origin"]),
            "size":max(body_size(body_a),body_size(body_b),float(target_c.get("radius",0))*2,1.0),
            "num":num,"status":"open","comments":[],"selected":None,"oppose_normals":True,
            "target_token":token(target_c["entity"]),
            "target_label":"{} connection".format(target_name),
            "target_frame":list(target_c["frame"]),
            "target_connector_radius":target_c.get("radius"),
            "target_connector_kind":target_c.get("kind"),
            "alternatives":[
                {"name":getattr(body_a,"name","Alternative 1"),"token":ta,
                 "connector_token":token(a_c["entity"]),"connector_frame":list(a_c["frame"]),
                 "connector_radius":a_c.get("radius"),"connector_kind":a_c.get("kind"),
                 "thumb":thumb_for_body(body_a)},
                {"name":getattr(body_b,"name","Alternative 2"),"token":tb,
                 "connector_token":token(b_c["entity"]),"connector_frame":list(b_c["frame"]),
                 "connector_radius":b_c.get("radius"),"connector_kind":b_c.get("kind"),
                 "thumb":thumb_for_body(body_b)},
            ],
        }
        m._marks.append(mark); m._geom[mid]={"alternatives":[body_a,body_b]}; m._entity[mid]=target_c["entity"]
        m._redraw_marks(); m._send_state()
        try:
            if getattr(m,"_persist_state",None): m._persist_state("compare-face-create")
        except Exception: pass
        try: m._focus_camera(mark["anchor"])
        except Exception: pass
        log("CREATED conflict={} targetKind={} AKind={} BKind={}".format(mid,target_c.get("kind"),a_c.get("kind"),b_c.get("kind")))
        return mid

    def clear_preview():
        try: m._clear(GROUP_CONNECTOR)
        except Exception: pass

    def draw_feedback():
        clear_preview(); group=m._group(GROUP_CONNECTOR)
        if group is None: return
        for cid,seed in (("cmp_target",84001),("cmp_a",85001),("cmp_b",86001)):
            c=selected_connector(cid)
            if not c: continue
            o,x,y,z=c["origin"],c["x"],c["y"],c["z"]
            r=max(.12,min(float(c.get("radius") or .4),1.2))
            def P(axis,s): return tuple(o[j]+axis[j]*s for j in range(3))
            try:
                m._visual_stroke(group,[P(z,-r*.35),P(z,r*.8)],"conflict_marker",seed,size=max(r*4,1.0))
                ring=[]
                for i in range(25):
                    a=math.pi*2*i/24.0
                    ring.append(tuple(o[j]+(x[j]*math.cos(a)+y[j]*math.sin(a))*r for j in range(3)))
                m._visual_stroke(group,ring,"conflict_marker",seed+1,size=max(r*4,1.0))
            except Exception: pass
        try: m._app.activeViewport.refresh()
        except Exception: pass

    def count(cid):
        try:
            it=state["inputs"].itemById(cid) if state.get("inputs") else None
            return it.selectionCount if it else 0
        except Exception: return 0

    def set_focus(cid):
        if not state.get("inputs"): return
        for key in ("cmp_target","cmp_a","cmp_b"):
            try:
                it=state["inputs"].itemById(key)
                if it: it.hasFocus=(key==cid)
            except Exception: pass

    def stage():
        if not hasattr(m,"_set_tool_stage"): return
        t=count("cmp_target")>0; a=count("cmp_a")>0; b=count("cmp_b")>0
        active=0 if not t else (1 if not a else (2 if not b else 3))
        try:
            m._set_tool_stage("compare",[
                {"label":"Click target connection","done":t,"hint":"planar face or circular edge"},
                {"label":"Click Alternative 1 connection","done":a,"hint":"planar face or circular edge"},
                {"label":"Click Alternative 2 connection","done":b,"hint":"planar face or circular edge"},
                {"label":"Compare in the card","done":False}],active,"Compare")
        except Exception: pass

    def reject(cid):
        try:
            it=state["inputs"].itemById(cid); it.clearSelection(); it.hasFocus=True
        except Exception: pass
        try: m._ui.messageBox("Select a planar connection face or a circular/arc edge.")
        except Exception: pass

    def request_finish():
        if state.get("finishing"): return
        state["finishing"]=True
        try: m._app.fireCustomEvent(FINISH_EVENT_ID,"done")
        except Exception: state["finishing"]=False

    class InputChanged(adsk.core.InputChangedEventHandler):
        def notify(self,args):
            try:
                state["inputs"]=args.inputs; cid=args.input.id
                if cid not in ("cmp_target","cmp_a","cmp_b"): return
                if count(cid) and selected_connector(cid) is None:
                    reject(cid); draw_feedback(); stage(); return
                draw_feedback(); stage()
                if cid=="cmp_target" and count(cid): set_focus("cmp_a"); stage(); return
                if cid=="cmp_a" and count(cid): set_focus("cmp_b"); stage(); return
                if count("cmp_target") and count("cmp_a") and count("cmp_b") and state.get("created_id") is None:
                    mid=create_mark(selected_connector("cmp_target"),selected_connector("cmp_a"),selected_connector("cmp_b"))
                    if mid is None:
                        m._ui.messageBox("Compare needs three valid connection selections, and the alternatives must be different bodies.")
                        return
                    state["created_id"]=mid; clear_preview(); stage(); request_finish()
            except Exception:
                log("input failed\n{}".format(m.traceback.format_exc()))
                try: m._ui.messageBox("FuzzyCAD Compare connection failed:\n{}".format(m.traceback.format_exc()))
                except Exception: pass

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self,args):
            state["inputs"]=None; state["created_id"]=None; state["finishing"]=False; clear_preview()
            try:
                if hasattr(m,"_set_tool_stage"): m._set_tool_stage(None,[],None,"")
            except Exception: pass

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self,args):
            try:
                cmd=args.command; state["created_id"]=None; state["finishing"]=False; cmd.isRepeatable=False
                try: cmd.isExecutedWhenPreEmpted=False
                except Exception: pass
                try: cmd.isOKButtonVisible=False; cmd.cancelButtonText="Cancel Compare"
                except Exception: pass
                inputs=cmd.commandInputs
                target=inputs.addSelectionInput("cmp_target","1. Target connection","Click a planar face or circular edge")
                a=inputs.addSelectionInput("cmp_a","2. Alternative 1 connection","Click its planar face or circular edge")
                b=inputs.addSelectionInput("cmp_b","3. Alternative 2 connection","Click its planar face or circular edge")
                for it in (target,a,b):
                    it.addSelectionFilter("Faces"); it.addSelectionFilter("Edges"); it.setSelectionLimits(1,1)
                    try: it.isUseCurrentSelections=False
                    except Exception: pass
                state["inputs"]=inputs; set_focus("cmp_target"); stage()
                ih=InputChanged(); cmd.inputChanged.add(ih); m._handlers.append(ih)
                dh=Destroy(); cmd.destroy.add(dh); m._handlers.append(dh)
                log("ACTIVE: face/edge target -> Alt 1 -> Alt 2")
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
            cd=m._ui.commandDefinitions.addButtonDefinition(COMPARE_CMD_ID,"Compare","Compare alternatives by matching planar faces or circular edges","")
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
            evt=m._app.registerCustomEvent(FINISH_EVENT_ID); h=Finish(); evt.add(h); m._handlers.append(h)
        except Exception: log("finish event registration failed\n{}".format(m.traceback.format_exc()))
        register_command(); log("READY: planar-face / circular-edge Compare registered")
        return result

    def stop(context):
        clear_preview()
        try: m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception: pass
        return old_stop(context)

    m.run=run; m.stop=stop
