"""Compare two BRep alternatives at one shared conflict slot.

The closest Fusion analogue to an Onshape Mate Connector is a Joint Origin.
Compare therefore accepts a Joint Origin as the preferred target frame.  For a
lighter-weight workflow, a BRep vertex or Construction Point can also be used;
those fall back to a world-axis frame at the selected point.

Interaction:
  1. Select target slot (Joint Origin recommended).
  2. Select Alternative 1 body.
  3. Select Alternative 2 body.
  4. A Conflict card appears. With no choice, both alternatives are shown as a
     translucent unresolved overlay at the slot. Clicking an alternative in the
     card switches the viewport preview. Accept copies the chosen source body
     into the root component and moves the copy into the slot.

Alternative thumbnails are generated locally from sampled BRep edges using a
fixed isometric projection. They are compact vector previews, so Compare does
not need to isolate the model or take disruptive viewport screenshots.
"""

import math

COMPARE_CMD_ID = "FuzzyCAD_Compare"


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
        "live_id": None,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMPARE] " + msg)
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

    def matrix_from_list(vals):
        mat = adsk.core.Matrix3D.create()
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return mat
        k = 0
        for r in range(4):
            for c in range(4):
                try:
                    mat.setCell(r, c, float(vals[k]))
                except Exception:
                    pass
                k += 1
        return mat

    def point_frame(p):
        mat = adsk.core.Matrix3D.create()
        mat.translation = adsk.core.Vector3D.create(float(p.x), float(p.y), float(p.z))
        return mat

    def target_frame(ent):
        """Return (Matrix3D, origin, label) for a selected slot entity."""
        if isinstance(ent, adsk.fusion.JointOrigin):
            try:
                mat = ent.transform.copy()
            except Exception:
                mat = ent.transform
            try:
                origin = [mat.getCell(0, 3), mat.getCell(1, 3), mat.getCell(2, 3)]
            except Exception:
                t = mat.translation
                origin = [t.x, t.y, t.z]
            return mat, origin, getattr(ent, "name", "Joint Origin")

        if isinstance(ent, adsk.fusion.BRepVertex):
            p = ent.geometry
            return point_frame(p), [p.x, p.y, p.z], "Vertex"

        if isinstance(ent, adsk.fusion.ConstructionPoint):
            p = ent.geometry
            return point_frame(p), [p.x, p.y, p.z], getattr(ent, "name", "Construction Point")

        # Defensive fallback for other point-like API objects.
        try:
            p = ent.worldGeometry
            return point_frame(p), [p.x, p.y, p.z], "Point"
        except Exception:
            pass
        try:
            p = ent.geometry
            if hasattr(p, "x") and hasattr(p, "y") and hasattr(p, "z"):
                return point_frame(p), [p.x, p.y, p.z], "Point"
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

    def placement_matrix(mark, body):
        """Map the source body's bbox center/world axes to the target slot frame."""
        target = matrix_from_list(mark.get("target_frame"))
        center, _ = body_center_size(body)
        # p' = R*(p-c) + t, where target stores R,t.
        rx = [target.getCell(0, i) for i in range(3)]
        ry = [target.getCell(1, i) for i in range(3)]
        rz = [target.getCell(2, i) for i in range(3)]
        tx = target.getCell(0, 3) - sum(rx[i] * center[i] for i in range(3))
        ty = target.getCell(1, 3) - sum(ry[i] * center[i] for i in range(3))
        tz = target.getCell(2, 3) - sum(rz[i] * center[i] for i in range(3))
        target.setCell(0, 3, tx)
        target.setCell(1, 3, ty)
        target.setCell(2, 3, tz)
        return target

    def transformed_temp(body, mark):
        try:
            mgr = adsk.fusion.TemporaryBRepManager.get()
            copy = mgr.copy(body)
            if copy is None:
                return None
            if not mgr.transform(copy, placement_matrix(mark, body)):
                return None
            return copy
        except Exception as exc:
            log("temp placement failed: {}".format(exc))
            return None

    def thumb_for_body(body):
        """Return normalized 2D edge polylines for an inline SVG thumbnail."""
        polylines = []
        try:
            limit = min(int(body.edges.count), 72)
            for i in range(limit):
                pts = m._sample_edge(body.edges.item(i), 7)
                if len(pts) >= 2:
                    polylines.append(pts)
        except Exception:
            return []
        if not polylines:
            return []

        # Fixed isometric projection. Centering/normalization happens after all
        # points are projected, so imported model scale does not affect the card.
        projected = []
        xs, ys = [], []
        for poly in polylines:
            row = []
            for x, y, z in poly:
                u = (x - y) * 0.8660254038
                v = (x + y) * 0.50 - z
                row.append((u, v)); xs.append(u); ys.append(v)
            projected.append(row)
        if not xs or not ys:
            return []
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        w = max(xmax - xmin, 1e-6)
        h = max(ymax - ymin, 1e-6)
        scale = min(88.0 / w, 58.0 / h)
        ox = 50.0 - (xmin + xmax) * 0.5 * scale
        oy = 35.0 - (ymin + ymax) * 0.5 * scale
        return [[[round(u * scale + ox, 2), round(v * scale + oy, 2)]
                 for u, v in row] for row in projected]

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
        if bodies and len(bodies) >= 2:
            return bodies
        found = []
        for alt in mark.get("alternatives") or []:
            found.append(resolve_body(alt.get("token")))
        if len(found) >= 2 and all(found):
            g["alternatives"] = found
        return found

    def conflict_style(name, fallback_rgb, fallback_opacity):
        try:
            st = m.VISUAL_TOKENS.get(name, {})
            return tuple(st.get("rgb", fallback_rgb)), float(st.get("opacity", fallback_opacity))
        except Exception:
            return fallback_rgb, fallback_opacity

    def add_body_graphic(group, body, mark, rgb, opacity):
        temp = transformed_temp(body, mark)
        if temp is None:
            return
        try:
            cg = group.addBRepBody(temp)
            cg.color = m._solid(rgb)
            cg.setOpacity(opacity, True)
        except Exception:
            pass

    def draw_slot_cross(group, mark):
        a = list(mark.get("anchor") or [0.0, 0.0, 0.0])
        s = max(0.18, min(float(mark.get("size", 3.0)) * 0.06, 0.75))
        rgb, _ = conflict_style("conflict_marker", (128, 90, 180), 1.0)
        for axis in range(3):
            p0 = list(a); p1 = list(a)
            p0[axis] -= s; p1[axis] += s
            try:
                if hasattr(m, "_visual_stroke"):
                    m._visual_stroke(group, [tuple(p0), tuple(p1)], "conflict_marker",
                                     mark.get("id", 1) * 72001 + axis, size=mark.get("size", 3.0))
                else:
                    m._sketchy(group, [tuple(p0), tuple(p1)], rgb, 0.0,
                               mark.get("id", 1) * 72001 + axis, weight=2, strokes=1)
            except Exception:
                pass

    def draw_compare(group, mark, rgb, amp):
        bodies = alt_bodies(mark)
        if len(bodies) < 2 or not all(bodies[:2]):
            draw_slot_cross(group, mark)
            return
        choice = mark.get("selected")
        unresolved_a = conflict_style("conflict_alt_a", (126, 104, 180), 0.18)
        unresolved_b = conflict_style("conflict_alt_b", (92, 118, 170), 0.18)
        selected_style = conflict_style("conflict_selected", (92, 96, 104), 0.42)
        if choice in (0, 1):
            add_body_graphic(group, bodies[int(choice)], mark,
                             selected_style[0], selected_style[1])
        else:
            add_body_graphic(group, bodies[0], mark, unresolved_a[0], unresolved_a[1])
            add_body_graphic(group, bodies[1], mark, unresolved_b[0], unresolved_b[1])
        draw_slot_cross(group, mark)

    m._DRAW["compare"] = draw_compare

    def fields(mark):
        if mark.get("tool") == "compare":
            return []
        return old_fields(mark)
    m._fields = fields

    def summary(mark):
        if mark.get("tool") == "compare":
            choice = mark.get("selected")
            if choice in (0, 1):
                alts = mark.get("alternatives") or []
                name = alts[int(choice)].get("name", "Alternative {}".format(int(choice) + 1)) if len(alts) > int(choice) else "Alternative {}".format(int(choice) + 1)
                return "selected {}".format(name)
            return "2 alternatives · unresolved"
        return old_summary(mark)
    m._summary = summary

    def public(mark):
        out = old_public(mark)
        if mark.get("tool") == "compare":
            out["mtype"] = "conflict"
            out["alternatives"] = mark.get("alternatives") or []
            out["selected"] = mark.get("selected")
            out["target_label"] = mark.get("target_label", "Target")
        return out
    m._public = public

    def accept(mark):
        if mark.get("tool") != "compare":
            return old_accept(mark)
        choice = mark.get("selected")
        if choice not in (0, 1):
            try:
                m._ui.messageBox("Choose Alternative 1 or Alternative 2 before confirming this conflict.")
            except Exception:
                pass
            return False
        bodies = alt_bodies(mark)
        if len(bodies) < 2 or bodies[int(choice)] is None:
            return False
        src = bodies[int(choice)]
        try:
            root = m._design().rootComponent
            placed = src.copyToComponent(root)
            if placed is None:
                return False
            coll = adsk.core.ObjectCollection.create(); coll.add(placed)
            move = root.features.moveFeatures
            move.add(move.createInput(coll, placement_matrix(mark, src)))
            try:
                placed.name = "{} (chosen)".format((mark.get("alternatives") or [])[int(choice)].get("name", "Alternative {}".format(int(choice) + 1)))
            except Exception:
                pass
            return True
        except Exception:
            try:
                m._ui.messageBox("FuzzyCAD couldn't place the chosen alternative:\n{}".format(m.traceback.format_exc()))
            except Exception:
                pass
            return False
    m._accept = accept

    def create_mark(target, body_a, body_b):
        if body_a is None or body_b is None:
            return None
        if token(body_a) and token(body_a) == token(body_b):
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
                 "token": token(body_a), "thumb": thumb_for_body(body_a)},
                {"name": getattr(body_b, "name", "Alternative 2"),
                 "token": token(body_b), "thumb": thumb_for_body(body_b)},
            ],
        }
        m._marks.append(mark)
        m._geom[mid] = {"alternatives": [body_a, body_b]}
        if target is not None:
            m._entity[mid] = target
        m._redraw_marks()
        m._send_state()
        try:
            m._focus_camera(origin)
        except Exception:
            pass
        try:
            if hasattr(m, "_persist_state"):
                m._persist_state("compare-create")
        except Exception:
            pass
        log("created conflict={} target={} A={} B={}".format(
            mid, target_label, getattr(body_a, "name", "A"), getattr(body_b, "name", "B")))
        return mid

    class CompareInputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                if state["live_id"] is not None:
                    return
                inputs = args.inputs
                target_in = inputs.itemById("cmp_target")
                a_in = inputs.itemById("cmp_a")
                b_in = inputs.itemById("cmp_b")
                if not target_in or not a_in or not b_in:
                    return
                if target_in.selectionCount < 1 or a_in.selectionCount < 1 or b_in.selectionCount < 1:
                    return
                target = target_in.selection(0).entity
                body_a = adsk.fusion.BRepBody.cast(a_in.selection(0).entity)
                body_b = adsk.fusion.BRepBody.cast(b_in.selection(0).entity)
                mid = create_mark(target, body_a, body_b)
                if mid is None:
                    try:
                        m._ui.messageBox("Choose two different solid bodies for Compare.")
                    except Exception:
                        pass
                    return
                state["live_id"] = mid
            except Exception:
                try:
                    m._ui.messageBox("FuzzyCAD Compare failed:\n{}".format(m.traceback.format_exc()))
                except Exception:
                    pass

    class CompareDestroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            state["inputs"] = None
            state["live_id"] = None
            try:
                if hasattr(m, "_persist_state"):
                    m._persist_state("compare-done")
            except Exception:
                pass

    class CompareCreated(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                state["live_id"] = None
                cmd = args.command
                cmd.isRepeatable = False
                cmd.okButtonText = "Done"
                inputs = cmd.commandInputs
                target = inputs.addSelectionInput(
                    "cmp_target", "Target location",
                    "Select a Fusion Joint Origin (recommended), vertex, or construction point")
                for filt in ("JointOrigins", "Vertices", "ConstructionPoints"):
                    try: target.addSelectionFilter(filt)
                    except Exception: pass
                target.setSelectionLimits(1, 1)

                a = inputs.addSelectionInput("cmp_a", "Alternative 1", "Select the first imported solid body")
                a.addSelectionFilter("SolidBodies"); a.setSelectionLimits(1, 1)
                b = inputs.addSelectionInput("cmp_b", "Alternative 2", "Select the second imported solid body")
                b.addSelectionFilter("SolidBodies"); b.setSelectionLimits(1, 1)
                state["inputs"] = inputs

                ih = CompareInputChanged(); cmd.inputChanged.add(ih); m._handlers.append(ih)
                dh = CompareDestroy(); cmd.destroy.add(dh); m._handlers.append(dh)
            except Exception:
                try:
                    m._ui.messageBox("FuzzyCAD Compare setup failed:\n{}".format(m.traceback.format_exc()))
                except Exception:
                    pass

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__(); self._delegate = CurrentPaletteHTMLHandler()
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
                    m._redraw_marks(); m._send_state()
                    try:
                        if hasattr(m, "_persist_state"):
                            m._persist_state("compare-choice")
                    except Exception:
                        pass
                return
            self._delegate.notify(args)
    m.PaletteHTMLHandler = PaletteHTMLHandler

    def add_compare_command():
        try:
            existing = m._ui.commandDefinitions.itemById(COMPARE_CMD_ID)
            if existing:
                try: existing.deleteMe()
                except Exception: pass
            cd = m._ui.commandDefinitions.addButtonDefinition(
                COMPARE_CMD_ID, "Compare", "Compare two alternative bodies at a shared target slot", "")
            h = CompareCreated(); cd.commandCreated.add(h); m._handlers.append(h)
            panel = m._ui.allToolbarPanels.itemById(m.PANEL_ID)
            if panel and panel.controls.itemById(COMPARE_CMD_ID) is None:
                panel.controls.addCommand(cd)
        except Exception as exc:
            log("command registration failed: {}".format(exc))

    def run(context):
        result = old_run(context)
        add_compare_command()
        log("COMPARE READY: Joint Origin/point target + two BRep alternatives + switchable conflict card")
        return result

    def stop(context):
        try:
            panel = m._ui.allToolbarPanels.itemById(m.PANEL_ID) if m._ui else None
            if panel:
                ctrl = panel.controls.itemById(COMPARE_CMD_ID)
                if ctrl: ctrl.deleteMe()
            cd = m._ui.commandDefinitions.itemById(COMPARE_CMD_ID) if m._ui else None
            if cd: cd.deleteMe()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
