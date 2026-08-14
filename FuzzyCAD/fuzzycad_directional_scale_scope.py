"""Directional scale scope for FuzzyCAD.

Default behavior is deliberately direct: scale one side while the opposite side
stays fixed.  The user can flip which side is active or switch to symmetric
scaling.  The selected scope is expressed both in the command UI and in the
viewport with a fixed-end marker or center marker.
"""


def install(m):
    adsk = m.adsk
    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    old_draw_axis = m._DRAW.get("scale_axis")
    old_accept = m._accept
    old_summary = m._summary
    old_run = m.run

    CANDIDATE_RGB = (190, 190, 186)
    CHANGE_RGB = (225, 126, 38)
    CALLOUT_RGB = (200, 44, 32)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SCALE SCOPE] " + msg)
        except Exception:
            pass

    def selected_side():
        try:
            it = m._inputs.itemById("dsSide") if m._inputs else None
            item = it.selectedItem if it else None
            name = item.name if item else "+ side"
            if name.startswith("-"):
                return "negative"
            if name.startswith("Both"):
                return "both"
        except Exception:
            pass
        return "positive"

    def bbox_data():
        if not m._pending:
            return None
        data = m._pending.get("bbox")
        if data:
            return data
        body = m._pending.get("body")
        if body is None:
            return None
        bb = body.boundingBox
        data = ([bb.minPoint.x, bb.minPoint.y, bb.minPoint.z],
                [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z])
        m._pending["bbox"] = data
        return data

    def side_origin(axis, side):
        data = bbox_data()
        if data is None or not m._pending:
            return list(m._pending.get("anchor", [0, 0, 0])) if m._pending else [0, 0, 0]
        mn, mx = data
        c = list(m._pending["anchor"])
        idx = {"X": 0, "Y": 1, "Z": 2}[axis]
        if side == "both":
            return c
        # Dragging the positive side fixes the negative side; dragging the
        # negative side fixes the positive side.
        c[idx] = mn[idx] if side == "positive" else mx[idx]
        return c

    def active_handle_origin(axis, side):
        data = bbox_data()
        mn, mx = data
        c = list(m._pending["anchor"])
        idx = {"X": 0, "Y": 1, "Z": 2}[axis]
        if side == "negative":
            c[idx] = mn[idx]
        else:
            c[idx] = mx[idx]
        return c

    def place_handles():
        if getattr(m, "_active_cmd", None) != "directional_scale" or not m._pending or m._inputs is None:
            return
        side = selected_side()
        m._pending["scale_side"] = side
        for axis in ("X", "Y", "Z"):
            it = m._inputs.itemById("ds" + axis)
            if it is None:
                continue
            try:
                origin = active_handle_origin(axis, side)
                unit = list(m._axis_unit(axis))
                if side == "negative":
                    unit = [-unit[0], -unit[1], -unit[2]]
                it.setManipulator(adsk.core.Point3D.create(*origin),
                                  adsk.core.Vector3D.create(*unit))
                it.isVisible = True
                it.isEnabled = True
            except Exception as exc:
                log("handle {} placement failed: {}".format(axis, exc))
        log("MODE={} handles repositioned".format(side))

    def annotate_mark(mark):
        if mark is None or mark.get("tool") != "scale_axis":
            return
        side = selected_side()
        axis = mark.get("axis", "X")
        mark["scale_side"] = side
        mark["base_anchor"] = side_origin(axis, side)

    def axis_factors(mark):
        f = max(0.05, float(mark.get("factor", 1.0)))
        axis = mark.get("axis", "X")
        if axis == "X":
            return (f, 1.0, 1.0)
        if axis == "Y":
            return (1.0, f, 1.0)
        return (1.0, 1.0, f)

    def scale_matrix(base, factors):
        fx, fy, fz = factors
        mat = adsk.core.Matrix3D.create()
        mat.setCell(0, 0, fx)
        mat.setCell(1, 1, fy)
        mat.setCell(2, 2, fz)
        mat.translation = adsk.core.Vector3D.create(
            base[0] * (1.0 - fx),
            base[1] * (1.0 - fy),
            base[2] * (1.0 - fz))
        return mat

    def transformed_points(points, matrix):
        out = []
        for xyz in points:
            p = adsk.core.Point3D.create(*xyz)
            p.transformBy(matrix)
            out.append((p.x, p.y, p.z))
        return out

    def callout(group, mark, text, anchor):
        try:
            s = mark.get("size", 3.0)
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            d = max(0.8, min(s * 0.30, 2.6))
            tip = (anchor[0] + (0.78 * xx + 0.45 * yx) * d,
                   anchor[1] + (0.78 * xy + 0.45 * yy) * d,
                   anchor[2] + (0.78 * xz + 0.45 * yz) * d)
            m._sketchy(group, [tuple(anchor), tip], CALLOUT_RGB, 0.0,
                       mark["id"] * 9301, weight=2, strokes=1)
            cp = adsk.core.Point3D.create(*tip)
            txt = group.addText(text, "Arial", max(0.45, min(s * 0.10, 0.9)),
                                m._label_transform(cp))
            txt.color = m._solid(CALLOUT_RGB)
            m._apply_billboard(txt, cp)
        except Exception:
            pass

    def draw_fixed_marker(group, mark, base, axis, side):
        # A short orange cross means "this side stays fixed".  Symmetric mode
        # uses a centered double-ended cue instead.
        unit = m._axis_unit(axis)
        s = max(0.20, min(mark.get("size", 3.0) * 0.06, 0.65))
        if side == "both":
            c = mark.get("anchor") or base
            p0 = (c[0] - unit[0] * s * 2.0,
                  c[1] - unit[1] * s * 2.0,
                  c[2] - unit[2] * s * 2.0)
            p1 = (c[0] + unit[0] * s * 2.0,
                  c[1] + unit[1] * s * 2.0,
                  c[2] + unit[2] * s * 2.0)
            m._sketchy(group, [p0, tuple(c), p1], CHANGE_RGB, 0.0,
                       mark["id"] * 9302, weight=3, strokes=1)
            return

        # Pick a simple perpendicular direction for the fixed-end bar.
        if axis == "X":
            perp = (0, 1, 0)
        elif axis == "Y":
            perp = (1, 0, 0)
        else:
            perp = (1, 0, 0)
        p0 = (base[0] - perp[0] * s, base[1] - perp[1] * s, base[2] - perp[2] * s)
        p1 = (base[0] + perp[0] * s, base[1] + perp[1] * s, base[2] + perp[2] * s)
        m._sketchy(group, [p0, p1], CHANGE_RGB, 0.0,
                   mark["id"] * 9303, weight=4, strokes=1)

    def draw_axis_scale(group, mark, rgb, amp):
        axis = mark.get("axis", "X")
        side = mark.get("scale_side") or (
            m._pending.get("scale_side", "positive") if m._pending else "positive")
        base = mark.get("base_anchor")
        if base is None:
            base = side_origin(axis, side) if m._pending else list(mark.get("anchor", [0, 0, 0]))
        factors = axis_factors(mark)
        matrix = scale_matrix(base, factors)

        body = m._body.get(mark["id"])
        if body is not None:
            try:
                cg = group.addBRepBody(body)
                cg.transform = matrix
                cg.color = m._solid(CANDIDATE_RGB)
                cg.setOpacity(0.30, True)
            except Exception:
                pass

        for i, loop in enumerate(m._geom.get(mark["id"], {}).get("edges", [])):
            try:
                m._sketchy(group, transformed_points(loop, matrix), rgb, amp * 0.8,
                           mark["id"] * 9310 + i, weight=1, strokes=2)
            except Exception:
                pass

        draw_fixed_marker(group, mark, base, axis, side)
        f = max(0.05, float(mark.get("factor", 1.0)))
        label = ("Both sides {} = {:.3f}x" if side == "both" else
                 ("+{} one side = {:.3f}x" if side == "positive" else
                  "-{} one side = {:.3f}x"))
        callout(group, mark, label.format(axis, f), mark.get("anchor") or base)

    m._DRAW["scale_axis"] = draw_axis_scale

    class ScopeInputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                cid = args.input.id
                if getattr(m, "_active_cmd", None) != "directional_scale":
                    return
                if cid == "dsb":
                    if m._pending:
                        m._pending["scale_side"] = selected_side()
                        place_handles()
                    return
                if cid == "dsSide":
                    if m._pending:
                        m._pending["scale_side"] = selected_side()
                        place_handles()
                    mid = m._live.get("scale_axis")
                    mark = m._find(mid) if mid is not None else None
                    if mark is not None:
                        annotate_mark(mark)
                        m._clear(m.GROUP_PREVIEW)
                        g = m._group(m.GROUP_PREVIEW)
                        if g is not None:
                            m._draw_one(g, mark)
                        m._refresh_ghost()
                        m._send_state()
                    return
                if cid in ("dsX", "dsY", "dsZ"):
                    mid = m._live.get("scale_axis")
                    mark = m._find(mid) if mid is not None else None
                    if mark is not None:
                        annotate_mark(mark)
                        # Re-render once with the fixed/base point recorded so the
                        # candidate exactly matches what Accept will create.
                        m._clear(m.GROUP_PREVIEW)
                        g = m._group(m.GROUP_PREVIEW)
                        if g is not None:
                            m._draw_one(g, mark)
                        m._refresh_ghost()
                        m._send_state()
                        log("axis={} side={} factor={:.4f}".format(
                            mark.get("axis"), mark.get("scale_side"), mark.get("factor", 1.0)))
            except Exception:
                log("scope input failed\n{}".format(m.traceback.format_exc()))

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            if self.cmd != "directional_scale":
                return
            try:
                inputs = args.command.commandInputs
                side = inputs.addRadioButtonGroupCommandInput("dsSide", "Scale direction")
                side.listItems.add("+ side (opposite side fixed)", True)
                side.listItems.add("- side (opposite side fixed)", False)
                side.listItems.add("Both sides (symmetric)", False)
                h = ScopeInputChanged()
                args.command.inputChanged.add(h)
                m._handlers.append(h)
            except Exception:
                log("could not add scale scope control\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    def accept(mark):
        if mark.get("tool") != "scale_axis":
            return old_accept(mark)
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
            base_xyz = mark.get("base_anchor") or mark.get("anchor")
            cpi = comp.constructionPoints.createInput()
            cpi.setByPoint(adsk.core.Point3D.create(*base_xyz))
            base = comp.constructionPoints.add(cpi)
            scales = comp.features.scaleFeatures
            si = scales.createInput(coll, base, adsk.core.ValueInput.createByReal(1.0))
            fx, fy, fz = axis_factors(mark)
            if not si.setToNonUniform(
                    adsk.core.ValueInput.createByReal(fx),
                    adsk.core.ValueInput.createByReal(fy),
                    adsk.core.ValueInput.createByReal(fz)):
                return False
            scales.add(si)
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't apply directional scale:\n{}".format(
                m.traceback.format_exc()))
            return False

    m._accept = accept

    def summary(mark):
        if mark.get("tool") == "scale_axis":
            axis = mark.get("axis", "X")
            f = mark.get("factor", 1.0)
            side = mark.get("scale_side", "positive")
            if side == "both":
                return "scale {} both sides ×{:.3f}".format(axis, f)
            sign = "+" if side == "positive" else "-"
            return "scale {}{} one side ×{:.3f}".format(sign, axis, f)
        return old_summary(mark)

    m._summary = summary

    def run(context):
        result = old_run(context)
        log("ONE-SIDED SCALE READY: + side default | - side | Both sides symmetric")
        return result

    m.run = run
