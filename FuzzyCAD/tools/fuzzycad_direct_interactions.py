"""Restore FuzzyCAD's direct-manipulation interaction model.

The interaction rule is intentionally simple:
- Uniform Scale: choose body -> one obvious corner/diagonal handle -> drag.
- Directional Scale: choose body -> X/Y/Z scale handles -> drag one axis.
- Axis Rotate: choose body -> selection focus automatically advances to the
  rotation reference -> choose circular edge -> angle ring appears -> drag.

This patch is loaded after fuzzycad_scale_axis_rotate_taxonomy.py and keeps its
mark rendering/card/application code, while replacing the ambiguous interaction
flow introduced by combining uniform and directional scale in one command.
"""

import math


def install(m):
    adsk = m.adsk

    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    CurrentInputChanged = m.FuzzyInputChanged
    old_run = m.run

    state = {
        "dir_axis": "X",
        "dir_seq": 0,
        "axis_seq": 0,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD DIRECT] " + msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Make the tool choices explicit. No hidden scale mode switching.
    # ------------------------------------------------------------------
    m.COMMANDS = (
        "transform", "scale", "directional_scale", "axis_rotate", "extrude", "fillet"
    )
    m.CMD_ID["scale"] = "FuzzyCAD_Scale"
    m.CMD_ID["directional_scale"] = "FuzzyCAD_DirectionalScale"
    m.CMD_ID["axis_rotate"] = "FuzzyCAD_AxisRotate"

    m.CMD_LABEL["scale"] = "Uniform Scale"
    m.CMD_LABEL["directional_scale"] = "Scale X/Y/Z"
    m.CMD_LABEL["axis_rotate"] = "Axis Rotate"

    m.CMD_HINT["scale"] = "Select a body, then drag the corner handle to scale the whole body."
    m.CMD_HINT["directional_scale"] = "Select a body, then drag X, Y, or Z to scale only that axis."
    m.CMD_HINT["axis_rotate"] = "Select the body first; FuzzyCAD will then ask for the rotation axis."

    m.CMD_FILTER["scale"] = "SolidBodies"
    m.CMD_FILTER["directional_scale"] = "SolidBodies"
    m.CMD_FILTER["axis_rotate"] = "SolidBodies"
    m.CMD_CATS["scale"] = ("scale",)
    m.CMD_CATS["directional_scale"] = ()
    m.CMD_CATS["axis_rotate"] = ()

    # ------------------------------------------------------------------
    # Uniform scale: keep the original scale mark/apply path, but make the
    # manipulator read like a normal object-scale handle at the bbox corner.
    # ------------------------------------------------------------------
    def place_uniform_corner_handle():
        if getattr(m, "_active_cmd", None) != "scale" or not m._pending or m._inputs is None:
            return
        body = m._pending.get("body") or m._pending.get("entity")
        it = m._inputs.itemById("sc")
        if body is None or it is None:
            return
        try:
            bb = body.boundingBox
            a = m._pending["anchor"]
            corner = adsk.core.Point3D.create(bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)
            direction = adsk.core.Vector3D.create(
                corner.x - a[0], corner.y - a[1], corner.z - a[2])
            radial = direction.length
            if radial < 1e-6:
                direction = adsk.core.Vector3D.create(1, 1, 1)
                radial = direction.length
            direction.normalize()
            # _category_raw('scale') converts handle distance to factor using
            # scale_len, so use the actual center-to-corner distance.
            m._pending["scale_len"] = radial
            it.setManipulator(corner, direction)
            it.isVisible = True
            it.isEnabled = True
            log("UNIFORM SCALE READY body selected -> corner handle visible")
        except Exception as exc:
            log("uniform scale handle placement failed: {}".format(exc))

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)
            if getattr(m, "_active_cmd", None) == "scale" and cid == "sel" and m._pending:
                place_uniform_corner_handle()

    m.FuzzyInputChanged = FuzzyInputChanged

    # ------------------------------------------------------------------
    # Shared directional-scale helpers.
    # ------------------------------------------------------------------
    def build_body_pending(body):
        center, size = m._bbox_center_size(body)
        bb = body.boundingBox
        hx = max((bb.maxPoint.x - bb.minPoint.x) * 0.5, 1e-4)
        hy = max((bb.maxPoint.y - bb.minPoint.y) * 0.5, 1e-4)
        hz = max((bb.maxPoint.z - bb.minPoint.z) * 0.5, 1e-4)
        return {
            "geom": {"edges": m._sample_edges(body.edges)},
            "anchor": center,
            "size": size,
            "entity": body,
            "body": body,
            "axis_half": {"X": hx, "Y": hy, "Z": hz},
            "bbox": (
                [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z],
                [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z],
            ),
        }

    def hide_directional_handles():
        if m._inputs is None:
            return
        for axis in ("X", "Y", "Z"):
            it = m._inputs.itemById("ds" + axis)
            if it is not None:
                try:
                    it.isVisible = False
                    it.isEnabled = False
                except Exception:
                    pass

    def chosen_axis():
        try:
            it = m._inputs.itemById("dsAxis") if m._inputs else None
            item = it.selectedItem if it else None
            name = item.name if item else ""
            if name in ("X", "Y", "Z"):
                return name
        except Exception:
            pass
        return None

    def reset_axis_choice():
        it = m._inputs.itemById("dsAxis") if m._inputs else None
        if it is not None:
            try:
                it.listItems.item(0).isSelected = True   # back to "Choose…"
                it.isVisible = True
            except Exception:
                pass

    def place_chosen_axis():
        # Delegate to the scope module's placement (it also honours the +/-/both
        # side); it reads the picked axis from m._pending["dir_axis"].
        place = getattr(m, "_place_directional_handles", None)
        if place is not None:
            place()
        else:
            hide_directional_handles()

    def directional_mark():
        mid = m._live.get("scale_axis")
        return m._find(mid) if mid is not None else None

    def sync_directional(axis):
        if not m._pending:
            return None
        half = max(float(m._pending["axis_half"].get(axis, 1.0)), 1e-6)
        delta = m._val("ds" + axis)
        factor = max(0.05, 1.0 + delta / half)
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

    def draw_directional_preview(mark):
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is not None and mark is not None:
            m._draw_one(group, mark)
        m._refresh_ghost()
        m._send_state()

    class DirectionalScaleInputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                m._inputs = args.inputs
                cid = args.input.id
                if cid == "dsb":
                    sel = adsk.core.SelectionCommandInput.cast(args.input)
                    if sel is None or sel.selectionCount < 1:
                        m._pending = None
                        return
                    body = sel.selection(0).entity
                    m._pending = build_body_pending(body)
                    # Force a direction choice first: no handle until X/Y/Z picked.
                    state["dir_axis"] = None
                    if m._pending is not None:
                        m._pending["dir_axis"] = None
                    hide_directional_handles()
                    reset_axis_choice()
                    return

                if cid == "dsAxis" and m._pending:
                    axis = chosen_axis()
                    state["dir_axis"] = axis
                    m._pending["dir_axis"] = axis
                    if axis is None:
                        hide_directional_handles()
                    else:
                        place_chosen_axis()
                    return

                if cid in ("dsX", "dsY", "dsZ") and m._pending:
                    axis = cid[-1]
                    state["dir_axis"] = axis
                    m._pending["dir_axis"] = axis
                    mark = sync_directional(axis)
                    draw_directional_preview(mark)
                    state["dir_seq"] += 1
                    log("DIRECTIONAL SCALE #{:05d} axis={} factor={:.4f}".format(
                        state["dir_seq"], axis,
                        mark.get("factor", 1.0) if mark else 1.0))
            except Exception:
                log("directional scale input failed\n{}".format(m.traceback.format_exc()))

    class DirectionalScalePreview(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                if m._pending and state.get("dir_axis"):
                    mark = sync_directional(state["dir_axis"])
                    draw_directional_preview(mark)
                args.isValidResult = True
            except Exception:
                log("directional scale preview failed\n{}".format(m.traceback.format_exc()))

    class DirectionalScaleExecute(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                if m._pending and state.get("dir_axis"):
                    sync_directional(state["dir_axis"])
                m._clear(m.GROUP_PREVIEW)
                m._redraw_marks()
                m._send_state()
            except Exception:
                log("directional scale execute failed\n{}".format(m.traceback.format_exc()))

    class DirectionalScaleDestroy(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                mid = m._live.get("scale_axis")
                mark = m._find(mid) if mid is not None else None
                if mark is not None and abs(float(mark.get("factor", 1.0)) - 1.0) < 1e-3:
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

    # ------------------------------------------------------------------
    # Axis Rotate: explicit two-step selection with automatic focus advance.
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

    def perpendicular_basis(direction):
        n = adsk.core.Vector3D.create(*direction)
        n.normalize()
        helper = adsk.core.Vector3D.create(1, 0, 0)
        if abs(n.dotProduct(helper)) > 0.85:
            helper = adsk.core.Vector3D.create(0, 1, 0)
        x = n.crossProduct(helper)
        x.normalize()
        y = n.crossProduct(x)
        y.normalize()
        return x, y

    def draw_axis_guide(origin, direction, size):
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is None:
            return
        d = adsk.core.Vector3D.create(*direction)
        d.normalize()
        half = max(1.0, size * 0.75)
        p0 = (origin[0] - d.x * half, origin[1] - d.y * half, origin[2] - d.z * half)
        p1 = (origin[0] + d.x * half, origin[1] + d.y * half, origin[2] + d.z * half)
        m._sketchy(group, [p0, p1], (225, 126, 38), 0.0, 88001,
                   weight=3, strokes=1)
        m._app.activeViewport.refresh()

    def configure_axis_rotate():
        bsel = m._inputs.itemById("arb") if m._inputs else None
        asel = m._inputs.itemById("ara") if m._inputs else None
        if bsel is None or asel is None or bsel.selectionCount < 1 or asel.selectionCount < 1:
            return False

        body = bsel.selection(0).entity
        edge = asel.selection(0).entity
        axis = circular_axis(edge)
        if axis is None:
            m._ui.messageBox("Please select a circular or arc edge to define the rotation axis.")
            try:
                asel.clearSelection()
                asel.hasFocus = True
            except Exception:
                pass
            return False

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

        ang = m._inputs.itemById("ar")
        if ang is not None:
            xdir, ydir = perpendicular_basis(direction)
            ang.setManipulator(adsk.core.Point3D.create(*origin), xdir, ydir)
            ang.isVisible = True
            ang.isEnabled = True

        try:
            bsel.hasFocus = False
            asel.hasFocus = False
        except Exception:
            pass
        draw_axis_guide(origin, direction, size)
        log("AXIS ROTATE READY axis selected origin={} dir={}".format(
            [round(v, 4) for v in origin], [round(v, 4) for v in direction]))
        return True

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

    def draw_axis_rotate_preview(mark):
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is not None:
            if mark is not None:
                m._draw_one(group, mark)
            elif m._pending:
                g = m._pending["geom"]
                d = adsk.core.Vector3D.create(*g["axis_dir"])
                d.normalize()
                half = max(1.0, m._pending["size"] * 0.75)
                o = g["axis_origin"]
                p0 = (o[0] - d.x * half, o[1] - d.y * half, o[2] - d.z * half)
                p1 = (o[0] + d.x * half, o[1] + d.y * half, o[2] + d.z * half)
                m._sketchy(group, [p0, p1], (225, 126, 38), 0.0, 88002,
                           weight=3, strokes=1)
        m._refresh_ghost()
        m._send_state()

    class AxisRotateInputChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            try:
                m._inputs = args.inputs
                cid = args.input.id
                bsel = m._inputs.itemById("arb")
                asel = m._inputs.itemById("ara")

                if cid == "arb":
                    # WHAT first. Once the body exists, immediately move selection
                    # focus to WHICH AXIS. No extra click in a dialog is required.
                    m._pending = None
                    mid = m._live.pop("axis_rotate", None)
                    if mid is not None:
                        m._remove_mark(mid)
                    if bsel is not None and bsel.selectionCount > 0:
                        if asel is not None:
                            try:
                                asel.clearSelection()
                            except Exception:
                                pass
                            asel.commandPrompt = "Now select a circular edge to define the rotation axis"
                            asel.hasFocus = True
                        log("AXIS ROTATE STEP 1 complete: body selected -> axis selection focused")
                    else:
                        if bsel is not None:
                            bsel.hasFocus = True
                    return

                if cid == "ara":
                    if asel is not None and asel.selectionCount > 0:
                        if configure_axis_rotate():
                            log("AXIS ROTATE STEP 2 complete: axis selected -> angle handle visible")
                    return

                if cid == "ar" and m._pending:
                    mark = sync_axis_rotate()
                    draw_axis_rotate_preview(mark)
                    state["axis_seq"] += 1
                    log("AXIS ROTATE INPUT #{:05d} angle_deg={:.3f}".format(
                        state["axis_seq"], mark.get("angle", 0.0) if mark else 0.0))
            except Exception:
                log("axis rotate input failed\n{}".format(m.traceback.format_exc()))

    class AxisRotatePreview(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                if m._pending:
                    draw_axis_rotate_preview(sync_axis_rotate())
                args.isValidResult = True
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
                if mark is not None and abs(float(mark.get("angle", 0.0))) < 1e-6:
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

    # ------------------------------------------------------------------
    # Command creation. Axis Rotate and Directional Scale are intentionally
    # custom; Uniform Scale reuses the original simple scale path.
    # ------------------------------------------------------------------
    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            if self.cmd == "directional_scale":
                try:
                    m._active_cmd = "directional_scale"
                    m._pending = None
                    m._live = {}
                    cmd = args.command
                    cmd.isRepeatable = False
                    cmd.okButtonText = "Add to panel"
                    inputs = cmd.commandInputs
                    m._inputs = inputs

                    sel = inputs.addSelectionInput(
                        "dsb", "Body", "Select the body to scale along X, Y, or Z")
                    sel.addSelectionFilter("SolidBodies")
                    sel.setSelectionLimits(1, 1)
                    try:
                        sel.isUseCurrentSelections = False
                        sel.hasFocus = True
                    except Exception:
                        pass

                    for axis in ("X", "Y", "Z"):
                        it = inputs.addDistanceValueCommandInput(
                            "ds" + axis, "Scale " + axis,
                            adsk.core.ValueInput.createByReal(0.0))
                        it.isVisible = False
                        it.isEnabled = False

                    axis_choice = inputs.addRadioButtonGroupCommandInput(
                        "dsAxis", "Scale along — pick a direction")
                    axis_choice.listItems.add("Choose…", True)
                    axis_choice.listItems.add("X", False)
                    axis_choice.listItems.add("Y", False)
                    axis_choice.listItems.add("Z", False)
                    axis_choice.isVisible = False

                    for handler, event in (
                        (DirectionalScaleInputChanged(), cmd.inputChanged),
                        (DirectionalScalePreview(), cmd.executePreview),
                        (DirectionalScaleExecute(), cmd.execute),
                        (DirectionalScaleDestroy(), cmd.destroy),
                    ):
                        event.add(handler)
                        m._handlers.append(handler)
                    log("DIRECTIONAL SCALE CREATED: waiting for body selection")
                except Exception:
                    m._ui.messageBox("FuzzyCAD Directional Scale setup failed:\n{}".format(
                        m.traceback.format_exc()))
                return

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

                    bsel = inputs.addSelectionInput(
                        "arb", "1. Body", "First select the body to rotate")
                    bsel.addSelectionFilter("SolidBodies")
                    bsel.setSelectionLimits(1, 1)
                    asel = inputs.addSelectionInput(
                        "ara", "2. Axis", "Then select a circular edge")
                    asel.addSelectionFilter("Edges")
                    asel.setSelectionLimits(1, 1)
                    try:
                        bsel.isUseCurrentSelections = False
                        asel.isUseCurrentSelections = False
                        bsel.hasFocus = True
                        asel.hasFocus = False
                    except Exception:
                        pass

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
                    log("AXIS ROTATE CREATED: step 1 body selection focused")
                except Exception:
                    m._ui.messageBox("FuzzyCAD Axis Rotate setup failed:\n{}".format(
                        m.traceback.format_exc()))
                return

            # Uniform scale goes through the existing implementation. The older
            # taxonomy patch added hidden X/Y/Z inputs to it; remove those so the
            # user sees exactly one interaction: whole-object scale.
            super().notify(args)
            if self.cmd == "scale":
                inputs = args.command.commandInputs
                for cid in ("sX", "sY", "sZ"):
                    it = inputs.itemById(cid)
                    if it is not None:
                        try:
                            it.deleteMe()
                        except Exception:
                            pass

    m.FuzzyCommandCreated = FuzzyCommandCreated

    def run(context):
        result = old_run(context)
        log("DIRECT INTERACTIONS READY: Uniform Scale | Scale X/Y/Z | Axis Rotate body->axis->angle")
        return result

    m.run = run
