"""Next-step interaction structure for FuzzyCAD.

This patch makes the tool model match the collaboration model more explicitly:
- Transform is Move + Rotate only.
- Scale is its own tool with one uniform handle and X/Y/Z directional handles.
- Axis Rotate rotates a body around the axis inferred from a selected circular edge.
- Geometry-changing tools remain Need Input; Note remains a Constraint; Conflict is
  reserved for Compare.

It also fixes the XYZ rotation widgets. AngleValueCommandInput.setManipulator
expects two in-plane directions, not the desired rotation axis. The old code fed
X/Y/Z as the first in-plane direction, which cyclically put the rings on the
wrong planes. Here each ring is explicitly placed in the plane perpendicular to
its named axis.
"""

import math

CANDIDATE_RGB = (190, 190, 186)
CANDIDATE_OPACITY = 0.30
CHANGE_RGB = (225, 126, 38)
CALLOUT_RGB = (200, 44, 32)


def install(m):
    adsk = m.adsk

    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    CurrentInputChanged = m.FuzzyInputChanged
    CurrentPreview = m.FuzzyPreview
    old_place_manipulator = m._place_manipulator
    old_is_default = m._is_default
    old_fields = m._fields
    old_apply_edit = m._apply_edit
    old_summary = m._summary
    old_public = m._public
    old_accept = m._accept
    old_draw_label = m._draw_label
    old_run = m.run

    state = {"axis_scale_seq": 0, "axis_rotate_seq": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD NEXT] " + msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Collaboration/tool taxonomy.
    # ------------------------------------------------------------------
    m.MTYPES = ("need_input", "constraint", "conflict")
    m.MTYPE_LABEL["need_input"] = "Need Input"
    m.MTYPE_LABEL["constraint"] = "Constraint"
    m.MTYPE_LABEL["conflict"] = "Conflict"
    m.MTYPE_COLOR["conflict"] = (128, 90, 180)
    m.MTYPE_GLYPH["conflict"] = u"⑂"
    # Compatibility for any older card that was stored as "alternative".
    m.MTYPE_LABEL["alternative"] = "Conflict"
    m.MTYPE_COLOR["alternative"] = m.MTYPE_COLOR["conflict"]
    m.MTYPE_GLYPH["alternative"] = m.MTYPE_GLYPH["conflict"]

    # Move + Rotate stay together. Scale becomes a separate command.
    m.COMMANDS = ("transform", "scale", "axis_rotate", "extrude", "fillet")
    m.CMD_ID["scale"] = "FuzzyCAD_Scale"
    m.CMD_ID["axis_rotate"] = "FuzzyCAD_AxisRotate"
    m.CMD_LABEL["transform"] = "Move / Rotate"
    m.CMD_LABEL["scale"] = "Scale"
    m.CMD_LABEL["axis_rotate"] = "Axis Rotate"
    m.CMD_FILTER["scale"] = "SolidBodies"
    m.CMD_FILTER["axis_rotate"] = "SolidBodies"
    m.CMD_HINT["transform"] = "Select a body, then grab a move or rotate handle."
    m.CMD_HINT["scale"] = "Select a body, then use uniform or X/Y/Z directional scale."
    m.CMD_HINT["axis_rotate"] = "Select a body and a circular edge to use as the rotation axis."
    m.CMD_CATS["transform"] = ("move", "rotate")
    m.CMD_CATS["scale"] = ("scale",)
    m.CMD_CATS["axis_rotate"] = ()

    # ------------------------------------------------------------------
    # Shared helpers.
    # ------------------------------------------------------------------
    def add_candidate(group, body, matrix):
        if body is None:
            return
        try:
            cg = group.addBRepBody(body)
            cg.transform = matrix
            cg.color = m._solid(CANDIDATE_RGB)
            cg.setOpacity(CANDIDATE_OPACITY, True)
        except Exception as exc:
            log("candidate body draw failed: {}".format(exc))

    def callout(group, mark, text, anchor=None):
        try:
            a = list(anchor or mark.get("anchor") or [0.0, 0.0, 0.0])
            s = mark.get("size", 3.0)
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            d = max(0.8, min(s * 0.28, 2.6))
            tip = (a[0] + (0.78 * xx + 0.45 * yx) * d,
                   a[1] + (0.78 * xy + 0.45 * yy) * d,
                   a[2] + (0.78 * xz + 0.45 * yz) * d)
            m._sketchy(group, [tuple(a), tip], CALLOUT_RGB, 0.0,
                       mark["id"] * 5011, weight=2, strokes=1)
            cp = adsk.core.Point3D.create(*tip)
            txt = group.addText(text, "Arial", max(0.45, min(s * 0.10, 0.9)),
                                m._label_transform(cp))
            txt.color = m._solid(CALLOUT_RGB)
            m._apply_billboard(txt, cp)
        except Exception as exc:
            log("callout failed: {}".format(exc))

    def apply_matrix_pts(pts, matrix):
        out = []
        for xyz in pts:
            p = adsk.core.Point3D.create(*xyz)
            p.transformBy(matrix)
            out.append((p.x, p.y, p.z))
        return out

    def draw_transformed_edges(group, mark, matrix, rgb, amp, seed):
        for i, loop in enumerate(m._geom.get(mark["id"], {}).get("edges", [])):
            m._sketchy(group, apply_matrix_pts(loop, matrix), rgb, amp * 0.8,
                       mark["id"] * seed + i, weight=1, strokes=2)

    def scale_matrix(anchor, factors):
        fx, fy, fz = factors
        a = anchor or [0.0, 0.0, 0.0]
        mat = adsk.core.Matrix3D.create()
        mat.setCell(0, 0, fx)
        mat.setCell(1, 1, fy)
        mat.setCell(2, 2, fz)
        mat.translation = adsk.core.Vector3D.create(
            a[0] * (1.0 - fx), a[1] * (1.0 - fy), a[2] * (1.0 - fz))
        return mat

    def axis_scale_factors(mark):
        f = max(0.05, float(mark.get("factor", 1.0)))
        axis = mark.get("axis", "X")
        return (f, 1.0, 1.0) if axis == "X" else ((1.0, f, 1.0) if axis == "Y" else (1.0, 1.0, f))

    def draw_axis_scale(group, mark, rgb, amp):
        matrix = scale_matrix(mark.get("anchor"), axis_scale_factors(mark))
        add_candidate(group, m._body.get(mark["id"]), matrix)
        draw_transformed_edges(group, mark, matrix, rgb, amp, 510)

        axis = mark.get("axis", "X")
        f = max(0.05, float(mark.get("factor", 1.0)))
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        unit = m._axis_unit(axis)
        extent = max(0.6, mark.get("size", 3.0) * 0.42)
        p0 = (a[0] + unit[0] * extent,
              a[1] + unit[1] * extent,
              a[2] + unit[2] * extent)
        p1 = (a[0] + unit[0] * extent * f,
              a[1] + unit[1] * extent * f,
              a[2] + unit[2] * extent * f)
        m._sketchy(group, [p0, p1], CHANGE_RGB, 0.0,
                   mark["id"] * 511, weight=3, strokes=1)
        callout(group, mark, "Scale {} = {:.3f}x".format(axis, f), p1)

    m._DRAW["scale_axis"] = draw_axis_scale

    def axis_rotate_matrix(mark):
        g = m._geom.get(mark["id"], {})
        origin = g.get("axis_origin") or mark.get("axis_origin") or [0.0, 0.0, 0.0]
        direction = g.get("axis_dir") or mark.get("axis_dir") or [0.0, 0.0, 1.0]
        mat = adsk.core.Matrix3D.create()
        mat.setToRotation(
            math.radians(float(mark.get("angle", 0.0))),
            adsk.core.Vector3D.create(*direction),
            adsk.core.Point3D.create(*origin))
        return mat

    def perpendicular_basis(axis_tuple):
        n = adsk.core.Vector3D.create(*axis_tuple)
        n.normalize()
        # Choose a helper that is not nearly parallel to the axis.
        helper = adsk.core.Vector3D.create(1, 0, 0)
        if abs(n.dotProduct(helper)) > 0.85:
            helper = adsk.core.Vector3D.create(0, 1, 0)
        x = n.crossProduct(helper)
        x.normalize()
        y = n.crossProduct(x)
        y.normalize()
        return x, y

    def draw_axis_guide(group, origin, direction, size, seed):
        d = adsk.core.Vector3D.create(*direction)
        d.normalize()
        half = max(1.0, size * 0.75)
        p0 = (origin[0] - d.x * half, origin[1] - d.y * half, origin[2] - d.z * half)
        p1 = (origin[0] + d.x * half, origin[1] + d.y * half, origin[2] + d.z * half)
        m._sketchy(group, [p0, p1], CHANGE_RGB, 0.0, seed,
                   weight=3, strokes=1)

    def draw_axis_rotate(group, mark, rgb, amp):
        matrix = axis_rotate_matrix(mark)
        add_candidate(group, m._body.get(mark["id"]), matrix)
        draw_transformed_edges(group, mark, matrix, rgb, amp, 610)

        g = m._geom.get(mark["id"], {})
        origin = g.get("axis_origin") or mark.get("axis_origin") or [0.0, 0.0, 0.0]
        direction = g.get("axis_dir") or mark.get("axis_dir") or [0.0, 0.0, 1.0]
        draw_axis_guide(group, origin, direction, mark.get("size", 3.0), mark["id"] * 611)
        callout(group, mark, "Axis Rotate = {:.1f}°".format(mark.get("angle", 0.0)), origin)

    m._DRAW["axis_rotate"] = draw_axis_rotate

    # ------------------------------------------------------------------
    # Correct the native XYZ rotation widgets and place directional scale.
    # Angle manipulators need X/Y directions that define the ring plane.
    # ------------------------------------------------------------------
    def place_manipulator():
        old_place_manipulator()
        if not m._pending or m._inputs is None:
            return
        origin = adsk.core.Point3D.create(*m._pending["anchor"])

        if getattr(m, "_active_cmd", None) == "transform":
            planes = {
                "X": ((0, 1, 0), (0, 0, 1)),  # YZ plane -> normal X
                "Y": ((0, 0, 1), (1, 0, 0)),  # ZX plane -> normal Y
                "Z": ((1, 0, 0), (0, 1, 0)),  # XY plane -> normal Z
            }
            for axis, (xd, yd) in planes.items():
                it = m._inputs.itemById("r" + axis)
                if it is None:
                    continue
                try:
                    it.setManipulator(
                        origin,
                        adsk.core.Vector3D.create(*xd),
                        adsk.core.Vector3D.create(*yd))
                except Exception as exc:
                    log("rotate {} manipulator placement failed: {}".format(axis, exc))

        if getattr(m, "_active_cmd", None) == "scale":
            for axis in ("X", "Y", "Z"):
                it = m._inputs.itemById("s" + axis)
                if it is None:
                    continue
                try:
                    it.setManipulator(origin, adsk.core.Vector3D.create(*m._axis_unit(axis)))
                    it.isVisible = True
                    it.isEnabled = True
                except Exception as exc:
                    log("directional scale {} placement failed: {}".format(axis, exc))

    m._place_manipulator = place_manipulator

    # ------------------------------------------------------------------
    # Directional scale live proposal.
    # ------------------------------------------------------------------
    def remove_live(cat):
        mid = m._live.pop(cat, None)
        if mid is not None:
            try:
                m._remove_mark(mid)
            except Exception:
                pass

    def sync_axis_scale(axis):
        if not m._pending:
            return None
        length = max(float(m._pending.get("scale_len", 1.0)), 1e-6)
        delta = m._val("s" + axis)
        factor = max(0.05, 1.0 + delta / length)
        mid = m._live.get("scale_axis")
        if mid is None:
            if abs(factor - 1.0) < 1e-3:
                return None
            mid = m._next_id
            m._next_id += 1
            mark = m._make_mark("scale_axis", {"axis": axis, "factor": factor})
            mark["id"] = mid
            m._geom[mid] = m._pending["geom"]
            m._entity[mid] = m._pending["entity"]
            m._body[mid] = m._pending["body"]
            m._marks.append(mark)
            m._live["scale_axis"] = mid
        else:
            mark = m._find(mid)
            if mark is not None:
                mark["axis"] = axis
                mark["factor"] = factor
        return m._find(mid)

    def draw_axis_scale_preview(mark):
        if mark is None:
            return
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is not None:
            m._draw_one(group, mark)
        m._refresh_ghost()
        m._send_state()

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)

            if getattr(m, "_active_cmd", None) != "scale" or not m._pending:
                return
            try:
                if cid == "sel":
                    m._pending["scale_mode"] = "uniform"
                    place_manipulator()
                    return
                if cid == "sc":
                    m._pending["scale_mode"] = "uniform"
                    remove_live("scale_axis")
                    return
                if cid in ("sX", "sY", "sZ"):
                    axis = cid[-1]
                    m._pending["scale_mode"] = "axis"
                    m._pending["scale_axis"] = axis
                    remove_live("scale")
                    mark = sync_axis_scale(axis)
                    draw_axis_scale_preview(mark)
                    state["axis_scale_seq"] += 1
                    log("DIRECTIONAL SCALE #{:05d} axis={} factor={:.4f}".format(
                        state["axis_scale_seq"], axis,
                        mark.get("factor", 1.0) if mark else 1.0))
            except Exception:
                log("directional scale input failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    class FuzzyPreview(CurrentPreview):
        def notify(self, args):
            if (getattr(m, "_active_cmd", None) == "scale" and m._pending and
                    m._pending.get("scale_mode") == "axis"):
                try:
                    mark = sync_axis_scale(m._pending.get("scale_axis", "X"))
                    draw_axis_scale_preview(mark)
                    args.isValidResult = True
                except Exception:
                    log("directional scale preview failed\n{}".format(m.traceback.format_exc()))
                return
            super().notify(args)

    m.FuzzyPreview = FuzzyPreview

    def is_default(cat, op):
        if cat == "scale_axis":
            return abs(float(op.get("factor", 1.0)) - 1.0) < 1e-3
        if cat == "axis_rotate":
            return abs(float(op.get("angle", 0.0))) < 1e-6
        return old_is_default(cat, op)

    m._is_default = is_default

    # ------------------------------------------------------------------
    # Circular-edge Axis Rotate command.
    # ------------------------------------------------------------------
    def circular_axis(edge):
        try:
            geo = edge.geometry
            if isinstance(geo, (adsk.core.Circle3D, adsk.core.Arc3D)):
                c = geo.center
                n = geo.normal.copy()
                n.normalize()
                return [c.x, c.y, c.z], [n.x, n.y, n.z]
        except Exception:
            pass
        return None

    def axis_rotate_mark():
        mid = m._live.get("axis_rotate")
        return m._find(mid) if mid is not None else None

    def sync_axis_rotate():
        if not m._pending:
            return None
        angle = math.degrees(m._val("ar"))
        mid = m._live.get("axis_rotate")
        if mid is None:
            if abs(angle) < 1e-6:
                return None
            mid = m._next_id
            m._next_id += 1
            g = m._pending["geom"]
            mark = m._make_mark("axis_rotate", {
                "angle": angle,
                "axis_origin": list(g["axis_origin"]),
                "axis_dir": list(g["axis_dir"]),
            })
            mark["id"] = mid
            m._geom[mid] = g
            m._entity[mid] = m._pending["entity"]
            m._body[mid] = m._pending["body"]
            m._marks.append(mark)
            m._live["axis_rotate"] = mid
        else:
            mark = m._find(mid)
            if mark is not None:
                mark["angle"] = angle
        return m._find(mid)

    def configure_axis_rotate():
        if m._inputs is None:
            return
        bsel = m._inputs.itemById("arb")
        asel = m._inputs.itemById("ara")
        if bsel is None or asel is None or bsel.selectionCount < 1 or asel.selectionCount < 1:
            return
        body = bsel.selection(0).entity
        edge = asel.selection(0).entity
        axis = circular_axis(edge)
        if axis is None:
            m._ui.messageBox("Axis Rotate needs a circular or arc edge to define the axis.")
            try:
                asel.clearSelection()
            except Exception:
                pass
            return
        if m._body_locked(body):
            m._ui.messageBox("This object already has an open question — resolve it in the panel first.")
            try:
                bsel.clearSelection()
            except Exception:
                pass
            return

        center, size = m._bbox_center_size(body)
        origin, direction = axis
        m._pending = {
            "geom": {
                "edges": m._sample_edges(body.edges),
                "axis_origin": origin,
                "axis_dir": direction,
            },
            "anchor": center,
            "size": size,
            "entity": body,
            "body": body,
        }
        it = m._inputs.itemById("ar")
        if it is not None:
            xdir, ydir = perpendicular_basis(direction)
            it.setManipulator(adsk.core.Point3D.create(*origin), xdir, ydir)
            it.isVisible = True
            it.isEnabled = True
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is not None:
            draw_axis_guide(group, origin, direction, size, 73001)
        m._app.activeViewport.refresh()
        log("AXIS ROTATE READY origin={} dir={}".format(
            [round(v, 4) for v in origin], [round(v, 4) for v in direction]))

    class AxisRotateInputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                m._inputs = args.inputs
                cid = args.input.id
                if cid in ("arb", "ara"):
                    # Rebuild only after both selections are present.
                    remove_live("axis_rotate")
                    m._pending = None
                    configure_axis_rotate()
            except Exception:
                log("axis rotate input failed\n{}".format(m.traceback.format_exc()))

    class AxisRotatePreview(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                m._clear(m.GROUP_PREVIEW)
                group = m._group(m.GROUP_PREVIEW)
                if m._pending and group is not None:
                    mark = sync_axis_rotate()
                    if mark is not None:
                        m._draw_one(group, mark)
                    else:
                        g = m._pending["geom"]
                        draw_axis_guide(group, g["axis_origin"], g["axis_dir"],
                                        m._pending["size"], 73002)
                    m._refresh_ghost()
                    m._send_state()
                args.isValidResult = True
                state["axis_rotate_seq"] += 1
            except Exception:
                log("axis rotate preview failed\n{}".format(m.traceback.format_exc()))

    class AxisRotateExecute(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                if m._pending:
                    sync_axis_rotate()
                m._clear(m.GROUP_PREVIEW)
                m._redraw_marks()
                m._send_state()
            except Exception:
                log("axis rotate execute failed\n{}".format(m.traceback.format_exc()))

    class AxisRotateDestroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                mid = m._live.get("axis_rotate")
                mark = m._find(mid) if mid is not None else None
                if mark is not None and is_default("axis_rotate", mark):
                    m._remove_mark(mid)
                m._clear(m.GROUP_PREVIEW)
                m._redraw_marks()
                m._send_state()
            except Exception:
                pass
            m._pending = None
            m._inputs = None
            m._active_cmd = None
            m._live = {}

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            if self.cmd == "axis_rotate":
                try:
                    m._active_cmd = "axis_rotate"
                    m._pending = None
                    m._live = {}
                    cmd = args.command
                    cmd.isRepeatable = False
                    cmd.okButtonText = "Add to panel"
                    inputs = cmd.commandInputs
                    m._inputs = inputs
                    bsel = inputs.addSelectionInput("arb", "Body", "Select the body to rotate")
                    bsel.addSelectionFilter("SolidBodies")
                    bsel.setSelectionLimits(1, 1)
                    asel = inputs.addSelectionInput("ara", "Axis", "Select a circular edge")
                    asel.addSelectionFilter("Edges")
                    asel.setSelectionLimits(1, 1)
                    ang = inputs.addAngleValueCommandInput(
                        "ar", "Angle", adsk.core.ValueInput.createByReal(0.0))
                    ang.isVisible = False
                    ang.isEnabled = False

                    for handler, event in (
                        (AxisRotateInputChanged(), cmd.inputChanged),
                        (AxisRotatePreview(), cmd.executePreview),
                        (AxisRotateExecute(), cmd.execute),
                        (AxisRotateDestroy(), cmd.destroy),
                    ):
                        event.add(handler)
                        m._handlers.append(handler)
                except Exception:
                    m._ui.messageBox("FuzzyCAD Axis Rotate setup failed:\n{}".format(
                        m.traceback.format_exc()))
                return

            super().notify(args)
            if self.cmd == "scale":
                try:
                    inputs = args.command.commandInputs
                    for axis in ("X", "Y", "Z"):
                        it = inputs.addDistanceValueCommandInput(
                            "s" + axis, "Scale " + axis,
                            adsk.core.ValueInput.createByReal(0.0))
                        it.isVisible = False
                        it.isEnabled = False
                except Exception:
                    m._ui.messageBox("FuzzyCAD directional scale setup failed:\n{}".format(
                        m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    # ------------------------------------------------------------------
    # Cards / editing / final application.
    # ------------------------------------------------------------------
    def fields(mark):
        tool = mark.get("tool")
        if tool == "scale_axis":
            return [{"key": "f", "label": "Scale " + mark.get("axis", "X"),
                     "value": round(mark.get("factor", 1.0), 3), "unit": "×"}]
        if tool == "axis_rotate":
            return [{"key": "a", "label": "Angle",
                     "value": round(mark.get("angle", 0.0), 1), "unit": "°"}]
        return old_fields(mark)

    m._fields = fields

    def apply_edit(mark, key, value):
        tool = mark.get("tool")
        if tool == "scale_axis":
            try:
                mark["factor"] = max(0.05, float(value))
            except Exception:
                pass
            return
        if tool == "axis_rotate":
            try:
                mark["angle"] = float(value)
            except Exception:
                pass
            return
        return old_apply_edit(mark, key, value)

    m._apply_edit = apply_edit

    def summary(mark):
        if mark.get("tool") == "scale_axis":
            return "scale {} ×{:.3f}".format(mark.get("axis", "X"), mark.get("factor", 1.0))
        if mark.get("tool") == "axis_rotate":
            return "rotate about selected axis {:.1f}°".format(mark.get("angle", 0.0))
        return old_summary(mark)

    m._summary = summary

    def public(mark):
        out = old_public(mark)
        if mark.get("mtype") == "alternative":
            out["mtype"] = "conflict"
        if mark.get("tool") == "scale_axis":
            out["title"] = "Directional Scale {}".format(mark.get("num", 1))
        elif mark.get("tool") == "axis_rotate":
            out["title"] = "Axis Rotate {}".format(mark.get("num", 1))
        return out

    m._public = public

    def draw_label(group, mark, rgb):
        if mark.get("tool") == "scale_axis":
            old = mark.get("label", "")
            if not old:
                mark["label"] = "Scale {} {}".format(mark.get("axis", "X"), mark.get("num", 1))
            try:
                return old_draw_label(group, mark, rgb)
            finally:
                mark["label"] = old
        if mark.get("tool") == "axis_rotate":
            old = mark.get("label", "")
            if not old:
                mark["label"] = "Axis Rotate {}".format(mark.get("num", 1))
            try:
                return old_draw_label(group, mark, rgb)
            finally:
                mark["label"] = old
        return old_draw_label(group, mark, rgb)

    m._draw_label = draw_label

    def accept(mark):
        tool = mark.get("tool")
        if tool == "scale_axis":
            body = m._body.get(mark["id"])
            if body is None:
                return False
            try:
                body.opacity = 1.0
            except Exception:
                pass
            try:
                comp = body.parentComponent
                coll = adsk.core.ObjectCollection.create()
                coll.add(body)
                cpi = comp.constructionPoints.createInput()
                cpi.setByPoint(adsk.core.Point3D.create(*mark["anchor"]))
                base = comp.constructionPoints.add(cpi)
                scales = comp.features.scaleFeatures
                si = scales.createInput(
                    coll, base, adsk.core.ValueInput.createByReal(1.0))
                fx, fy, fz = axis_scale_factors(mark)
                ok = si.setToNonUniform(
                    adsk.core.ValueInput.createByReal(fx),
                    adsk.core.ValueInput.createByReal(fy),
                    adsk.core.ValueInput.createByReal(fz))
                if not ok:
                    return False
                scales.add(si)
                return True
            except Exception:
                m._ui.messageBox("FuzzyCAD couldn't apply directional scale:\n{}".format(
                    m.traceback.format_exc()))
                return False

        if tool == "axis_rotate":
            body = m._body.get(mark["id"])
            if body is None:
                return False
            try:
                body.opacity = 1.0
            except Exception:
                pass
            try:
                comp = body.parentComponent
                coll = adsk.core.ObjectCollection.create()
                coll.add(body)
                comp.features.moveFeatures.add(
                    comp.features.moveFeatures.createInput(coll, axis_rotate_matrix(mark)))
                return True
            except Exception:
                m._ui.messageBox("FuzzyCAD couldn't apply Axis Rotate:\n{}".format(
                    m.traceback.format_exc()))
                return False

        return old_accept(mark)

    m._accept = accept

    def run(context):
        result = old_run(context)
        log("TOOL STRUCTURE READY: Transform=Move+Rotate | Scale=uniform+XYZ | Axis Rotate=circular edge")
        log("ROTATION MANIPULATORS FIXED: X=YZ plane, Y=ZX plane, Z=XY plane")
        log("TAXONOMY READY: Need Input | Mark Constraint | Conflict/Compare")
        return result

    m.run = run
