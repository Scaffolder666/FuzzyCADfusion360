"""Stable Conflict/Compare implementation for FuzzyCAD.

This module intentionally owns the *only* Fusion command shell for Compare.
Older Compare experiments remain in the repository for reference but must not be
installed at runtime.  Keeping one command definition/handler chain avoids
re-registering and deleting the same Fusion command several times during add-in
startup.

Interaction
-----------
1. Click a planar target face or circular/arc target edge.
2. Click the connection face/edge on Alternative 1.
3. Click the connection face/edge on Alternative 2.
4. The command closes, then a Conflict card is created.

A planar face uses its centroid and outward normal as the connector.  A circular
edge uses its circle center and normal.  The click point defines the in-plane X
axis when possible.

Alternative scope is captured once when the conflict is created:
- assembly occurrence -> all BRep bodies in that occurrence;
- non-root component -> all BRep bodies in that component;
- shared BaseFeature -> all result bodies owned by that BaseFeature;
- otherwise -> clicked body only.

The viewport preview is edge-only.  It transforms cached sampled polylines and
never copies full temporary BReps during ordinary redraws.  This is deliberate:
large imported/derived STEP bodies made the earlier full-BRep Compare preview a
high-cost path inside Fusion's command/UI lifecycle.
"""

import math

COMPARE_CMD_ID = "FuzzyCAD_Compare"
FINISH_EVENT_ID = "FuzzyCADCompareStableFinish"
GROUP_PICK = "FuzzyCAD_CompareConnectorPreview"


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    old_accept = m._accept
    old_fields = m._fields
    old_summary = m._summary
    old_public = m._public
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    m.CMD_ID["compare"] = COMPARE_CMD_ID

    state = {
        "inputs": None,
        "pending": None,
        "finishing": False,
        "preview_cache": {},
        "thumb_cache": {},
    }

    def log(msg):
        # Always write to the app log (Text Commands) so Compare can be diagnosed
        # in the non-dev build where _debug is a no-op.
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMPARE STABLE] " + msg)
        except Exception:
            pass

    def token(ent):
        if ent is None:
            return None
        try:
            return ent.entityToken
        except Exception:
            return None

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

    def same_entity(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        ta, tb = token(a), token(b)
        return bool(ta and tb and ta == tb)

    # ---- matrix helpers ----------------------------------------------------
    def matrix_to_list(mat):
        return [float(mat.getCell(r, c)) for r in range(4) for c in range(4)]

    def list_to_matrix(vals):
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return None
        mat = adsk.core.Matrix3D.create()
        k = 0
        try:
            for r in range(4):
                for c in range(4):
                    mat.setCell(r, c, float(vals[k]))
                    k += 1
            return mat
        except Exception:
            return None

    def mat_mul(a, b):
        return [sum(float(a[r * 4 + k]) * float(b[k * 4 + c]) for k in range(4))
                for r in range(4) for c in range(4)]

    def placement_values(mark, index):
        try:
            target = list(mark.get("target_frame"))
            source = list((mark.get("alternatives") or [])[index].get("connector_frame"))
        except Exception:
            return None
        if len(target) != 16 or len(source) != 16:
            return None
        sm = list_to_matrix(source)
        if sm is None:
            return None
        try:
            if not sm.invert():
                return None
            inv_source = matrix_to_list(sm)
        except Exception:
            return None
        identity = [1, 0, 0, 0,
                    0, 1, 0, 0,
                    0, 0, 1, 0,
                    0, 0, 0, 1]
        # Face-to-face mate: source normal opposes target normal.
        flip = [1, 0, 0, 0,
                0, -1, 0, 0,
                0, 0, -1, 0,
                0, 0, 0, 1]
        local = flip if mark.get("oppose_normals", True) else identity
        return mat_mul(mat_mul(target, local), inv_source)

    def placement_matrix(mark, index):
        vals = placement_values(mark, index)
        return list_to_matrix(vals) if vals else None

    def transform_point(vals, p):
        x, y, z = p
        return (
            vals[0] * x + vals[1] * y + vals[2] * z + vals[3],
            vals[4] * x + vals[5] * y + vals[6] * z + vals[7],
            vals[8] * x + vals[9] * y + vals[10] * z + vals[11],
        )

    # ---- selection -> connector frame -------------------------------------
    def radial(center, normal, point):
        if point is None:
            return None
        try:
            v = adsk.core.Vector3D.create(
                point.x - center.x, point.y - center.y, point.z - center.z)
            npart = normal.copy()
            npart.scaleBy(v.dotProduct(normal))
            v.subtract(npart)
            if v.length < 1e-7:
                return None
            v.normalize()
            return v
        except Exception:
            return None

    def generic_x(normal):
        helper = adsk.core.Vector3D.create(1, 0, 0)
        try:
            if abs(normal.dotProduct(helper)) > 0.88:
                helper = adsk.core.Vector3D.create(0, 1, 0)
            npart = normal.copy()
            npart.scaleBy(helper.dotProduct(normal))
            helper.subtract(npart)
            helper.normalize()
            return helper
        except Exception:
            return adsk.core.Vector3D.create(1, 0, 0)

    def finish_frame(entity, body, center, zaxis, click, fallback=None,
                     radius=0.4, kind="connector"):
        try:
            zaxis.normalize()
            xaxis = radial(center, zaxis, click)
            if xaxis is None:
                xaxis = radial(center, zaxis, fallback)
            if xaxis is None:
                xaxis = generic_x(zaxis)
            yaxis = zaxis.crossProduct(xaxis)
            yaxis.normalize()
            xaxis = yaxis.crossProduct(zaxis)
            xaxis.normalize()
            frame = adsk.core.Matrix3D.create()
            frame.setWithCoordinateSystem(center, xaxis, yaxis, zaxis)
        except Exception:
            return None
        return {
            "entity": entity,
            "body": body,
            "kind": kind,
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
            normal = geo.normal.copy()
            normal.normalize()
            radius = float(geo.radius)
            click = sel.point
        except Exception:
            return None
        fallback = None
        try:
            fallback = edge.startVertex.geometry
        except Exception:
            pass
        return finish_frame(edge, edge.body, center, normal, click, fallback,
                            radius, "circular_edge")

    def face_radius(face, center, normal):
        best = 0.0
        try:
            for i in range(face.edges.count):
                pts = m._sample_edge(face.edges.item(i), 8)
                for xyz in pts:
                    p = adsk.core.Point3D.create(*xyz)
                    v = adsk.core.Vector3D.create(
                        p.x - center.x, p.y - center.y, p.z - center.z)
                    npart = normal.copy()
                    npart.scaleBy(v.dotProduct(normal))
                    v.subtract(npart)
                    best = max(best, float(v.length))
        except Exception:
            pass
        return best if best > 1e-7 else 0.4

    def face_connector(sel, face):
        try:
            geo = face.geometry
            if not isinstance(geo, adsk.core.Plane):
                return None
            center = face.centroid
            ok, normal = face.evaluator.getNormalAtPoint(center)
            if not ok or normal is None:
                normal = geo.normal.copy()
                if face.isParamReversed:
                    normal.scaleBy(-1.0)
            normal.normalize()
        except Exception:
            return None
        try:
            click = sel.point
        except Exception:
            click = None
        fallback = None
        try:
            if face.edges.count:
                e = face.edges.item(0)
                if e.startVertex is not None:
                    fallback = e.startVertex.geometry
        except Exception:
            pass
        return finish_frame(face, face.body, center, normal, click, fallback,
                            face_radius(face, center, normal), "planar_face")

    def connector_from_selection(sel):
        if sel is None:
            return None
        try:
            ent = sel.entity
        except Exception:
            return None
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

    # ---- alternative scope -------------------------------------------------
    def group_for_body(body):
        if body is None:
            return {"kind": "missing", "name": "Alternative", "bodies": []}

        try:
            occ = body.assemblyContext
        except Exception:
            occ = None
        if occ is not None:
            bodies = collection_items(getattr(occ, "bRepBodies", None))
            if bodies:
                return {"kind": "occurrence",
                        "name": getattr(occ, "name", "Alternative"),
                        "bodies": bodies}

        try:
            comp = body.parentComponent
            root = m._design().rootComponent
        except Exception:
            comp = None
            root = None
        if comp is not None and not same_entity(comp, root):
            bodies = collection_items(getattr(comp, "bRepBodies", None))
            if bodies:
                return {"kind": "component",
                        "name": getattr(comp, "name", "Alternative"),
                        "bodies": bodies}

        # Break Link on a Derived feature commonly leaves result bodies owned by
        # a BaseFeature.  Capture those as one option when Fusion exposes them.
        try:
            base = body.baseFeature
        except Exception:
            base = None
        if base is not None:
            bodies = collection_items(getattr(base, "bodies", None))
            if len(bodies) > 1:
                return {"kind": "base_feature",
                        "name": getattr(base, "name", "Derived alternative"),
                        "bodies": bodies}

        return {"kind": "body", "name": getattr(body, "name", "Alternative"),
                "bodies": [body]}

    def alt_bodies(mark, index):
        try:
            alt = (mark.get("alternatives") or [])[index]
        except Exception:
            return []
        toks = alt.get("body_tokens") or []
        bodies = [resolve_body(t) for t in toks]
        bodies = [b for b in bodies if b is not None]
        if bodies:
            return bodies
        clicked = resolve_body(alt.get("token"))
        return group_for_body(clicked)["bodies"] if clicked is not None else []

    # ---- lightweight edge preview -----------------------------------------
    def sample_group(bodies, max_edges=140):
        key = tuple(token(b) or str(id(b)) for b in bodies)
        cached = state["preview_cache"].get(key)
        if cached is not None:
            return cached
        rows = []
        remaining = int(max_edges)
        for body in bodies:
            if remaining <= 0:
                break
            try:
                take = min(int(body.edges.count), remaining)
                for i in range(take):
                    pts = m._sample_edge(body.edges.item(i), 6)
                    if len(pts) >= 2:
                        rows.append(pts)
                remaining -= take
            except Exception:
                pass
        state["preview_cache"][key] = rows
        return rows

    def style(name, rgb, opacity=1.0):
        try:
            st = m.VISUAL_TOKENS.get(name, {})
            return tuple(st.get("rgb", rgb)), float(st.get("opacity", opacity))
        except Exception:
            return rgb, opacity

    def draw_polyline(group, pts, rgb, seed, weight=1):
        try:
            m._sketchy(group, pts, rgb, 0.0, seed, weight=weight, strokes=1)
        except Exception:
            pass

    def draw_alt(group, mark, index, rgb, weight=1):
        vals = placement_values(mark, index)
        if vals is None:
            return
        bodies = alt_bodies(mark, index)
        if not bodies:
            return
        for j, poly in enumerate(sample_group(bodies)):
            try:
                moved = [transform_point(vals, p) for p in poly]
                draw_polyline(group, moved, rgb,
                              mark.get("id", 1) * 910003 + index * 4001 + j,
                              weight=weight)
            except Exception:
                pass

    def draw_target_marker(group, mark):
        vals = mark.get("target_frame")
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return
        o = [float(vals[3]), float(vals[7]), float(vals[11])]
        x = [float(vals[0]), float(vals[4]), float(vals[8])]
        y = [float(vals[1]), float(vals[5]), float(vals[9])]
        z = [float(vals[2]), float(vals[6]), float(vals[10])]
        size = float(mark.get("size", 3.0) or 3.0)
        r = float(mark.get("target_connector_radius") or max(size * 0.06, 0.2))
        r = max(0.08, min(r, max(0.2, size * 0.25)))

        def P(axis, scale):
            return tuple(o[i] + axis[i] * scale for i in range(3))

        try:
            if hasattr(m, "_visual_stroke"):
                m._visual_stroke(group, [P(z, -r * .35), P(z, r * .75)],
                                 "conflict_marker", mark["id"] * 920001,
                                 size=size)
                ring = []
                for i in range(25):
                    a = math.pi * 2.0 * i / 24.0
                    ring.append(tuple(o[j] +
                                      (x[j] * math.cos(a) + y[j] * math.sin(a)) * r
                                      for j in range(3)))
                m._visual_stroke(group, ring, "conflict_marker",
                                 mark["id"] * 920002, size=size)
        except Exception:
            pass

    def draw_compare(group, mark, rgb, amp):
        # No TemporaryBRepManager here.  Compare redraws are line-only.
        a_rgb, _ = style("conflict_alt_a", (126, 104, 180), .18)
        b_rgb, _ = style("conflict_alt_b", (92, 118, 170), .18)
        s_rgb, _ = style("conflict_selected", (72, 76, 80), .42)
        choice = mark.get("selected")
        if choice in (0, 1):
            draw_alt(group, mark, int(choice), s_rgb, weight=2)
        else:
            draw_alt(group, mark, 0, a_rgb, weight=1)
            draw_alt(group, mark, 1, b_rgb, weight=1)
        draw_target_marker(group, mark)

    m._DRAW["compare"] = draw_compare

    # ---- card serialization ------------------------------------------------
    def thumb_for_bodies(bodies):
        key = tuple(token(b) or str(id(b)) for b in bodies)
        cached = state["thumb_cache"].get(key)
        if cached is not None:
            return cached
        polylines = sample_group(bodies, 96)
        if not polylines:
            return []
        projected, xs, ys = [], [], []
        for poly in polylines:
            row = []
            for px, py, pz in poly:
                u = (px - py) * 0.8660254038
                v = (px + py) * 0.50 - pz
                row.append((u, v))
                xs.append(u)
                ys.append(v)
            projected.append(row)
        if not xs or not ys:
            return []
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        w = max(xmax - xmin, 1e-6)
        h = max(ymax - ymin, 1e-6)
        scale = min(88.0 / w, 58.0 / h)
        ox = 50.0 - (xmin + xmax) * .5 * scale
        oy = 35.0 - (ymin + ymax) * .5 * scale
        result = [[[round(u * scale + ox, 2), round(v * scale + oy, 2)]
                   for u, v in row] for row in projected]
        state["thumb_cache"][key] = result
        return result

    def fields(mark):
        if mark.get("tool") == "compare":
            return []
        return old_fields(mark)

    def summary(mark):
        if mark.get("tool") != "compare":
            return old_summary(mark)
        choice = mark.get("selected")
        if choice in (0, 1):
            try:
                return "selected {}".format(mark["alternatives"][int(choice)]["name"])
            except Exception:
                return "alternative selected"
        return "2 alternatives · unresolved"

    def public(mark):
        out = old_public(mark)
        if mark.get("tool") != "compare":
            return out
        out["mtype"] = "conflict"
        out["selected"] = mark.get("selected")
        out["target_label"] = mark.get("target_label", "Target")
        alts = []
        for i, alt in enumerate((mark.get("alternatives") or [])[:2]):
            row = dict(alt)
            bodies = alt_bodies(mark, i)
            row["body_count"] = len(bodies)
            row["thumb"] = thumb_for_bodies(bodies) if bodies else []
            alts.append(row)
        out["alternatives"] = alts
        return out

    m._fields = fields
    m._summary = summary
    m._public = public

    # ---- accept/commit -----------------------------------------------------
    def accept(mark):
        if mark.get("tool") != "compare":
            return old_accept(mark)
        choice = mark.get("selected")
        if choice not in (0, 1):
            try:
                m._ui.messageBox("Choose Alternative 1 or Alternative 2 first.")
            except Exception:
                pass
            return False
        idx = int(choice)
        bodies = alt_bodies(mark, idx)
        matrix = placement_matrix(mark, idx)
        if not bodies or matrix is None:
            return False
        root = m._design().rootComponent
        placed = []
        try:
            for body in bodies:
                cp = body.copyToComponent(root)
                if cp is None:
                    raise RuntimeError("Could not copy one body in the selected alternative")
                placed.append(cp)
            coll = adsk.core.ObjectCollection.create()
            for body in placed:
                coll.add(body)
            moves = root.features.moveFeatures
            moves.add(moves.createInput(coll, matrix))
            log("ACCEPT choice={} bodies={}".format(idx + 1, len(placed)))
            return True
        except Exception:
            for body in placed:
                try:
                    body.deleteMe()
                except Exception:
                    pass
            try:
                m._ui.messageBox("FuzzyCAD couldn't place this alternative:\n{}".format(
                    m.traceback.format_exc()))
            except Exception:
                pass
            return False

    m._accept = accept

    # ---- create conflict after Fusion command has closed -------------------
    def body_size(bodies):
        best = 1.0
        for body in bodies:
            try:
                _, size = m._bbox_center_size(body)
                best = max(best, float(size))
            except Exception:
                pass
        return best

    def make_alt(connector, ordinal, explicit_bodies=None):
        clicked = connector.get("body")
        explicit_bodies = [b for b in (explicit_bodies or []) if b is not None]
        if explicit_bodies:
            # The reviewer picked the exact bodies for this alternative -- use them
            # verbatim (plus the connector's own body). This handles alternatives
            # that are several loose bodies, not a clean component/occurrence.
            bodies = list(explicit_bodies)
            if clicked is not None and not any(same_entity(clicked, b) for b in bodies):
                bodies.insert(0, clicked)
            name = getattr(clicked, "name", "Alternative {}".format(ordinal))
            scope = "explicit"
        else:
            group = group_for_body(clicked)
            bodies = group["bodies"] or [clicked]
            bodies = [b for b in bodies if b is not None]
            name = group.get("name") or getattr(clicked, "name", "Alternative {}".format(ordinal))
            scope = group.get("kind", "body")
        return {
            "name": name,
            "token": token(clicked),
            "body_tokens": [token(b) for b in bodies if token(b)],
            "scope": scope,
            "connector_token": token(connector.get("entity")),
            "connector_frame": list(connector.get("frame") or []),
            "connector_radius": connector.get("radius"),
            "connector_kind": connector.get("kind"),
        }, bodies

    def create_mark(target_c, a_c, b_c, a_bodies=None, b_bodies=None):
        if not target_c or not a_c or not b_c:
            return None
        if not (a_bodies or b_bodies) and same_entity(a_c.get("body"), b_c.get("body")):
            return None
        alt_a, bodies_a = make_alt(a_c, 1, a_bodies)
        alt_b, bodies_b = make_alt(b_c, 2, b_bodies)
        mid = m._next_id
        m._next_id += 1
        num = m._tool_count.get("compare", 0) + 1
        m._tool_count["compare"] = num
        target_body = target_c.get("body")
        mark = {
            "id": mid,
            "tool": "compare",
            "mtype": "conflict",
            "label": "Compare alternatives",
            "anchor": list(target_c["origin"]),
            "size": max(body_size(bodies_a), body_size(bodies_b), 1.0),
            "num": num,
            "status": "open",
            "comments": [],
            "selected": None,
            "oppose_normals": True,
            "target_token": token(target_c.get("entity")),
            "target_label": "{} connection".format(
                getattr(target_body, "name", "Target")),
            "target_frame": list(target_c["frame"]),
            "target_connector_radius": target_c.get("radius"),
            "target_connector_kind": target_c.get("kind"),
            "alternatives": [alt_a, alt_b],
        }
        m._marks.append(mark)
        # Persistence only requires a resolvable clicked body for each alt; the
        # stable renderer uses the explicit body_tokens snapshot above.
        m._geom[mid] = {"alternatives": [a_c.get("body"), b_c.get("body")]}
        if target_c.get("entity") is not None:
            m._entity[mid] = target_c["entity"]
        try:
            m._redraw_marks()
        except Exception:
            log("redraw after create failed\n{}".format(m.traceback.format_exc()))
        try:
            m._send_state()
        except Exception:
            pass
        try:
            if getattr(m, "_persist_state", None):
                m._persist_state("compare-stable-create")
        except Exception:
            pass
        log("CREATED id={} A={}({}) B={}({})".format(
            mid, alt_a["scope"], len(bodies_a), alt_b["scope"], len(bodies_b)))
        return mid

    # ---- lightweight pick feedback ----------------------------------------
    def clear_pick_graphics():
        try:
            m._clear(GROUP_PICK)
        except Exception:
            pass

    def selected_connector(cid):
        try:
            it = state["inputs"].itemById(cid)
            if it is None or it.selectionCount < 1:
                return None
            return connector_from_selection(it.selection(0))
        except Exception:
            return None

    def count(cid):
        try:
            it = state["inputs"].itemById(cid) if state.get("inputs") else None
            return it.selectionCount if it is not None else 0
        except Exception:
            return 0

    def selected_bodies(cid):
        out = []
        try:
            it = state["inputs"].itemById(cid) if state.get("inputs") else None
            if it is None:
                return out
            for i in range(it.selectionCount):
                try:
                    b = adsk.fusion.BRepBody.cast(it.selection(i).entity)
                    if b is not None:
                        out.append(b)
                except Exception:
                    continue
        except Exception:
            pass
        return out

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
                {"label": "Click target connection", "done": t,
                 "hint": "planar face or circular edge"},
                {"label": "Click Alternative 1 connection", "done": a,
                 "hint": "planar face or circular edge"},
                {"label": "Click Alternative 2 connection", "done": b,
                 "hint": "planar face or circular edge"},
                {"label": "Compare in the card", "done": False},
            ], active, "Compare")
        except Exception:
            pass

    def reject(cid):
        try:
            it = state["inputs"].itemById(cid)
            it.clearSelection()
            it.hasFocus = True
        except Exception:
            pass
        try:
            m._ui.messageBox("Select a planar connection face or circular/arc edge.")
        except Exception:
            pass

    def request_finish():
        if state["finishing"]:
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
                if cid not in ("cmp_target", "cmp_a", "cmp_b"):
                    return
                if count(cid) and selected_connector(cid) is None:
                    reject(cid)
                    stage()
                    return
                stage()
                if cid == "cmp_target" and count(cid):
                    set_focus("cmp_a")
                    stage()
                    return
                if cid == "cmp_a" and count(cid):
                    set_focus("cmp_b")
                    stage()
                    return
                # No auto-finish: after the three connections the reviewer may add
                # extra bodies to each alternative's body list, then click the
                # "Create comparison" OK button.
            except Exception:
                log("input failed\n{}".format(m.traceback.format_exc()))

    class Destroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            state["inputs"] = None
            clear_pick_graphics()
            try:
                if hasattr(m, "_set_tool_stage"):
                    m._set_tool_stage(None, [], None, "")
            except Exception:
                pass
            log("command destroyed")

    class Finish(adsk.core.CustomEventHandler):
        def notify(self, args):
            pending = state.get("pending")
            state["pending"] = None
            log("FINISH fired pending={}".format("yes" if pending else "no"))
            try:
                # Close Fusion's modal command first.  Heavy sampling/redraw is
                # deliberately deferred until after the command has terminated.
                try:
                    m._ui.terminateActiveCommand()
                except Exception:
                    pass
                if pending and all(pending[:3]):
                    create_mark(pending[0], pending[1], pending[2],
                                pending[3] if len(pending) > 3 else None,
                                pending[4] if len(pending) > 4 else None)
            except Exception:
                log("finish failed\n{}".format(m.traceback.format_exc()))
            finally:
                state["finishing"] = False

    class Execute(adsk.core.CommandEventHandler):
        def notify(self, args):
            # OK ("Create comparison") pressed: capture the three connectors and
            # each alternative's (optional) explicit body list, then defer the
            # heavy create/redraw until after the command closes.
            try:
                tc = selected_connector("cmp_target")
                ac = selected_connector("cmp_a")
                bc = selected_connector("cmp_b")
                ab = selected_bodies("cmp_a_bodies")
                bb = selected_bodies("cmp_b_bodies")
                log("EXECUTE target={} a={} b={} a_bodies={} b_bodies={}".format(
                    tc is not None, ac is not None, bc is not None, len(ab), len(bb)))
                if tc and ac and bc:
                    state["pending"] = (tc, ac, bc, ab, bb)
                    request_finish()
                else:
                    log("EXECUTE aborted: a connector is missing/invalid")
            except Exception:
                log("execute failed\n{}".format(m.traceback.format_exc()))

    class Validate(adsk.core.ValidateInputsEventHandler):
        def notify(self, args):
            # Enable OK once all three connections have a selection. Keep it cheap
            # (no connector rebuild here) so OK does not flicker disabled.
            try:
                ins = args.inputs

                def picked(cid):
                    it = ins.itemById(cid)
                    return it is not None and it.selectionCount > 0

                args.areInputsValid = picked("cmp_target") and picked("cmp_a") and picked("cmp_b")
            except Exception:
                args.areInputsValid = False

    class Created(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                state["pending"] = None
                state["finishing"] = False
                cmd = args.command
                cmd.isRepeatable = False
                try:
                    cmd.isExecutedWhenPreEmpted = False
                except Exception:
                    pass
                try:
                    cmd.isOKButtonVisible = True
                    cmd.okButtonText = "Create comparison"
                    cmd.cancelButtonText = "Cancel Compare"
                except Exception:
                    pass
                inputs = cmd.commandInputs
                target = inputs.addSelectionInput(
                    "cmp_target", "1. Target connection",
                    "Click a planar face or circular edge")
                a = inputs.addSelectionInput(
                    "cmp_a", "2. Alternative 1 connection",
                    "Click its planar face or circular edge")
                b = inputs.addSelectionInput(
                    "cmp_b", "3. Alternative 2 connection",
                    "Click its planar face or circular edge")
                for it in (target, a, b):
                    it.addSelectionFilter("Faces")
                    it.addSelectionFilter("Edges")
                    it.setSelectionLimits(1, 1)
                    try:
                        it.isUseCurrentSelections = False
                    except Exception:
                        pass
                a_bodies = inputs.addSelectionInput(
                    "cmp_a_bodies", "   Alt 1 bodies (optional)",
                    "Add every body that makes up Alternative 1")
                b_bodies = inputs.addSelectionInput(
                    "cmp_b_bodies", "   Alt 2 bodies (optional)",
                    "Add every body that makes up Alternative 2")
                for it in (a_bodies, b_bodies):
                    it.addSelectionFilter("SolidBodies")
                    it.setSelectionLimits(0, 0)   # optional, unlimited
                    try:
                        it.isUseCurrentSelections = False
                    except Exception:
                        pass
                state["inputs"] = inputs
                set_focus("cmp_target")
                stage()
                ih = InputChanged()
                cmd.inputChanged.add(ih)
                m._handlers.append(ih)
                eh = Execute()
                cmd.execute.add(eh)
                m._handlers.append(eh)
                vh = Validate()
                cmd.validateInputs.add(vh)
                m._handlers.append(vh)
                dh = Destroy()
                cmd.destroy.add(dh)
                m._handlers.append(dh)
                log("ACTIVE single-shell Compare")
            except Exception:
                log("setup failed\n{}".format(m.traceback.format_exc()))
                try:
                    m._ui.messageBox("FuzzyCAD Compare setup failed:\n{}".format(
                        m.traceback.format_exc()))
                except Exception:
                    pass

    # ---- panel A/B switching ----------------------------------------------
    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass
            if action == "compare_choice":
                mark = m._find(data.get("id"))
                if mark and mark.get("tool") == "compare":
                    choice = data.get("choice")
                    mark["selected"] = int(choice) if choice in (0, 1, "0", "1") else None
                    m._redraw_marks()
                    m._send_state()
                    try:
                        if getattr(m, "_persist_state", None):
                            m._persist_state("compare-choice")
                    except Exception:
                        pass
                return
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    # ---- one command definition, one handler chain ------------------------
    def register_command():
        panel = m._ui.allToolbarPanels.itemById(m.PANEL_ID)
        # Remove any stale control first because controls reference command
        # definitions.  Do this once, not four times through nested patches.
        if panel is not None:
            try:
                ctrl = panel.controls.itemById(COMPARE_CMD_ID)
                if ctrl is not None:
                    ctrl.deleteMe()
            except Exception:
                pass
        try:
            existing = m._ui.commandDefinitions.itemById(COMPARE_CMD_ID)
            if existing is not None:
                existing.deleteMe()
        except Exception:
            pass
        cd = m._ui.commandDefinitions.addButtonDefinition(
            COMPARE_CMD_ID, "Compare",
            "Compare alternatives by matching connection faces or circular edges", "")
        h = Created()
        cd.commandCreated.add(h)
        m._handlers.append(h)
        if panel is not None:
            panel.controls.addCommand(cd)
        log("REGISTERED one Compare definition")

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
        log("READY: single-shell, edge-only Compare")
        return result

    def stop(context):
        clear_pick_graphics()
        try:
            m._app.unregisterCustomEvent(FINISH_EVENT_ID)
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
