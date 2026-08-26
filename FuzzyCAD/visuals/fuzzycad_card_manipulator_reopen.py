"""Reopen a live Fusion manipulator when a Need Input card is clicked.

A Need Input card represents unresolved geometry, so it must remain directly
editable in the viewport. Hover can replay a proposal, but click enters a
lightweight edit command that reuses the proposal's existing geometry reference
and current value. No new mark is created and no geometry is committed.

Supported existing proposals:
- Move / XYZ Rotate
- Uniform Scale / directional Scale
- Axis Rotate
- Extrude / Fillet / Hole

The card and manipulator stay synchronized in both directions. Closing the edit
command keeps the adjusted proposal open; Accept in the sidebar is still the
only action that commits it to real geometry.
"""

import math

EDIT_CMD_ID = "FuzzyCAD_EditExistingProposal"
EDIT_EVENT_ID = "FuzzyCADEditExistingProposal"
SUPPORTED = {"move", "rotate", "scale", "scale_axis", "axis_rotate", "extrude", "fillet", "hole"}


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    state = {
        "requested_id": None,
        "active_id": None,
        "inputs": None,
        "command": None,
        "meta": {},
        "updating": False,
        "launching": False,   # a cd.execute() is in flight, awaiting its CommandCreated
        "pending": None,      # newest card requested while launching -- fired once settled
        "current": None,      # the session dict owned by the active edit command
    }
    gen_counter = [0]         # monotonic edit-session generation

    def is_current(session):
        return session is not None and session is state.get("current")

    # Left-rail procedural steps for the edit (re-open) state, mirroring the tool
    # rail shown while first creating a proposal. The geometry is already picked,
    # so the selection step reads done and the adjust step is active. Confirm (the
    # rail button) or Done both close the edit; the sidebar Accept still commits.
    EDIT_STAGE = {
        "move":        ("Move",        "Body selected",   "Adjust move — drag or type"),
        "rotate":      ("Rotate",      "Body selected",   "Adjust rotation"),
        "scale":       ("Scale All",   "Body selected",   "Adjust size"),
        "scale_axis":  ("Scale X/Y/Z", "Body selected",   "Adjust size along axis"),
        "axis_rotate": ("Axis Rotate", "Body & axis set", "Adjust angle"),
        "extrude":     ("Extrude",     "Face selected",   "Adjust depth"),
        "fillet":      ("Fillet",      "Edge selected",   "Adjust radius"),
        "hole":        ("Hole",        "Face selected",   "Adjust diameter / depth"),
    }

    def edit_stage_show(tool):
        setter = getattr(m, "_set_tool_stage", None)
        entry = EDIT_STAGE.get(tool)
        if setter is None or entry is None:
            return
        title, sel_label, adj_label = entry
        steps = [
            {"label": sel_label, "done": True},
            {"label": adj_label, "done": False, "hint": "then Confirm"},
        ]
        try:
            setter(tool, steps, 1, "Editing · " + title)
        except Exception:
            pass

    def edit_stage_clear():
        setter = getattr(m, "_set_tool_stage", None)
        if setter is None:
            return
        try:
            setter(None, [], None, "")
        except Exception:
            pass

    def log(msg):
        # Crash-survival file log -- DEV only. It flushes to disk per line so the
        # last step survives a hard crash, but that synchronous I/O has no place on
        # the UI thread in the research build, so it is gated on DEV_MODE.
        if getattr(m, "DEV_MODE", False):
            try:
                import os, tempfile
                with open(os.path.join(tempfile.gettempdir(), "fuzzycad_reopen.log"), "a") as fh:
                    fh.write("[REOPEN] " + str(msg) + "\n")
                    fh.flush()
            except Exception:
                pass
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD REOPEN] " + msg)
        except Exception:
            pass

    def active_mark():
        mid = state.get("active_id")
        return m._find(mid) if mid is not None else None

    def is_editable(mark):
        return bool(mark and mark.get("status", "open") == "open" and
                    mark.get("mtype", "need_input") == "need_input" and
                    mark.get("tool") in SUPPORTED)

    def redraw_other_marks(active_id):
        """Keep the active mark out of the persistent group while its live preview
        is being manipulated, otherwise the old and current proposals overlap."""
        try:
            m._clear(m.GROUP_MARKS)
            group = m._group(m.GROUP_MARKS)
            if group is not None:
                for mark in list(m._marks):
                    if mark.get("id") == active_id or mark.get("id") not in m._geom:
                        continue
                    try:
                        m._draw_one(group, mark)
                    except Exception:
                        pass
            m._refresh_ghost()
        except Exception:
            pass

    def draw_active(send=True):
        mark = active_mark()
        if mark is None:
            return
        try:
            m._clear(m.GROUP_PREVIEW)
            group = m._group(m.GROUP_PREVIEW)
            if group is not None:
                m._draw_one(group, mark)
            m._refresh_ghost()
            if send:
                m._send_state()
            # Deliberately no activeViewport.refresh() here. Fusion refreshes the
            # CustomGraphics during its command preview cycle; forcing refresh in
            # a drag can release the native manipulator.
        except Exception:
            log("preview draw failed\n{}".format(m.traceback.format_exc()))

    def axis_unit(axis):
        return m._axis_unit(axis)

    def rotate_plane(axis):
        return {
            "X": ((0, 1, 0), (0, 0, 1)),
            "Y": ((0, 0, 1), (1, 0, 0)),
            "Z": ((1, 0, 0), (0, 1, 0)),
        }[axis]

    def perpendicular_basis(direction):
        n = adsk.core.Vector3D.create(*direction)
        n.normalize()
        helper = adsk.core.Vector3D.create(1, 0, 0)
        if abs(n.dotProduct(helper)) > 0.85:
            helper = adsk.core.Vector3D.create(0, 1, 0)
        x = n.crossProduct(helper); x.normalize()
        y = n.crossProduct(x); y.normalize()
        return x, y

    def body_bbox(body):
        bb = body.boundingBox
        mn = [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z]
        mx = [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
        return mn, mx

    def add_distance(inputs, cid, label, value, origin, direction):
        it = inputs.addDistanceValueCommandInput(
            cid, label, adsk.core.ValueInput.createByReal(float(value)))
        it.setManipulator(adsk.core.Point3D.create(*origin),
                          adsk.core.Vector3D.create(*direction))
        it.isVisible = True
        it.isEnabled = True
        return it

    def add_angle(inputs, cid, label, radians, origin, xdir, ydir):
        it = inputs.addAngleValueCommandInput(
            cid, label, adsk.core.ValueInput.createByReal(float(radians)))
        it.setManipulator(adsk.core.Point3D.create(*origin),
                          adsk.core.Vector3D.create(*xdir) if isinstance(xdir, tuple) else xdir,
                          adsk.core.Vector3D.create(*ydir) if isinstance(ydir, tuple) else ydir)
        it.isVisible = True
        it.isEnabled = True
        return it

    def setup_inputs(cmd, mark):
        inputs = cmd.commandInputs
        state["inputs"] = inputs
        state["meta"] = {}
        tool = mark.get("tool")
        anchor = list(mark.get("anchor") or [0.0, 0.0, 0.0])
        body = m._body.get(mark.get("id"))
        geom = m._geom.get(mark.get("id"), {})

        if tool == "move":
            vec = list(mark.get("vec") or [0.0, 0.0, 0.0])
            for i, axis in enumerate(("X", "Y", "Z")):
                add_distance(inputs, "em" + axis, "Move " + axis, vec[i],
                             anchor, axis_unit(axis))
            return

        if tool == "rotate":
            rot = list(mark.get("rot") or [0.0, 0.0, 0.0])
            for i, axis in enumerate(("X", "Y", "Z")):
                xd, yd = rotate_plane(axis)
                add_angle(inputs, "er" + axis, "Rotate " + axis,
                          math.radians(rot[i]), anchor, xd, yd)
            return

        if tool == "scale":
            if body is None:
                raise RuntimeError("Scale proposal lost its body reference")
            bb = body.boundingBox
            corner = [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
            direction = adsk.core.Vector3D.create(
                corner[0] - anchor[0], corner[1] - anchor[1], corner[2] - anchor[2])
            radial = max(direction.length, 1e-6)
            direction.normalize()
            state["meta"]["scale_len"] = radial
            delta = (float(mark.get("factor", 1.0)) - 1.0) * radial
            add_distance(inputs, "esc", "Scale", delta, corner,
                         (direction.x, direction.y, direction.z))
            return

        if tool == "scale_axis":
            if body is None:
                raise RuntimeError("Directional-scale proposal lost its body reference")
            axis = mark.get("axis", "X")
            idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            side = mark.get("scale_side", "positive")
            mn, mx = body_bbox(body)
            c = list(anchor)
            if side == "negative":
                c[idx] = mn[idx]
                direction = [-v for v in axis_unit(axis)]
            else:
                c[idx] = mx[idx]
                direction = list(axis_unit(axis))
            full = max(mx[idx] - mn[idx], 1e-6)
            length = full * 0.5 if side == "both" else full
            state["meta"].update({"scale_len": length, "axis": axis, "side": side})
            delta = (float(mark.get("factor", 1.0)) - 1.0) * length
            add_distance(inputs, "esa", "Scale " + axis, delta, c, direction)
            return

        if tool == "axis_rotate":
            origin = list(geom.get("axis_origin") or mark.get("axis_origin") or anchor)
            direction = list(geom.get("axis_dir") or mark.get("axis_dir") or [0.0, 0.0, 1.0])
            xdir, ydir = perpendicular_basis(direction)
            add_angle(inputs, "ear", "Angle", math.radians(float(mark.get("angle", 0.0))),
                      origin, xdir, ydir)
            return

        if tool == "extrude":
            normal = list(geom.get("normal") or [0.0, 0.0, 1.0])
            add_distance(inputs, "d", "Depth", float(mark.get("amount", 0.0)),
                         anchor, normal)
            return

        if tool == "fillet":
            stations = geom.get("stations") or []
            if stations:
                P, t1, t2 = stations[len(stations) // 2]
                v = adsk.core.Vector3D.create((t1[0] + t2[0]) / 2.0,
                                              (t1[1] + t2[1]) / 2.0,
                                              (t1[2] + t2[2]) / 2.0)
                if v.length < 1e-6:
                    v = adsk.core.Vector3D.create(*t1)
                origin = list(P)
            else:
                edge = geom.get("edge") or [anchor]
                origin = list(edge[len(edge) // 2])
                v = adsk.core.Vector3D.create(0, 0, 1)
            v.normalize()
            it = add_distance(inputs, "d", "Radius", float(mark.get("amount", 0.0)),
                              origin, (v.x, v.y, v.z))
            max_r = geom.get("max_radius")
            try:
                it.minimumValue = 0.01
                it.isMinimumValueInclusive = True
                if max_r is not None:
                    it.maximumValue = float(max_r)
                    it.isMaximumValueInclusive = True
            except Exception:
                pass
            return

        if tool == "hole":
            normal = list(geom.get("normal") or [0.0, 0.0, 1.0])
            try:
                n = adsk.core.Vector3D.create(*normal)
                n.normalize()
                u, _ = perpendicular_basis(normal)
                dia = add_distance(
                    inputs, "ehd", "Diameter", float(mark.get("diameter", 0.0)),
                    anchor, (u.x, u.y, u.z))
                dep = add_distance(
                    inputs, "ehp", "Depth", float(mark.get("depth", 0.0)),
                    anchor, (-n.x, -n.y, -n.z))
                for it in (dia, dep):
                    try:
                        it.minimumValue = 0.01
                        it.isMinimumValueInclusive = True
                    except Exception:
                        pass
            except Exception:
                raise RuntimeError("Hole proposal lost its face normal")
            return

        raise RuntimeError("Unsupported Need Input tool: {}".format(tool))

    def sync_mark_from_inputs():
        if state.get("updating"):
            return active_mark()
        mark = active_mark()
        inputs = state.get("inputs")
        if mark is None or inputs is None:
            return mark
        tool = mark.get("tool")
        try:
            if tool == "move":
                mark["vec"] = [float(inputs.itemById("em" + a).value) for a in "XYZ"]
            elif tool == "rotate":
                mark["rot"] = [math.degrees(float(inputs.itemById("er" + a).value)) for a in "XYZ"]
            elif tool == "scale":
                length = max(float(state["meta"].get("scale_len", 1.0)), 1e-6)
                mark["factor"] = max(0.05, 1.0 + float(inputs.itemById("esc").value) / length)
            elif tool == "scale_axis":
                length = max(float(state["meta"].get("scale_len", 1.0)), 1e-6)
                mark["factor"] = max(0.05, 1.0 + float(inputs.itemById("esa").value) / length)
            elif tool == "axis_rotate":
                mark["angle"] = math.degrees(float(inputs.itemById("ear").value))
            elif tool in ("extrude", "fillet"):
                value = float(inputs.itemById("d").value)
                if tool == "fillet":
                    value = max(0.01, value)
                    max_r = m._geom.get(mark["id"], {}).get("max_radius")
                    if max_r is not None:
                        value = min(value, float(max_r))
                mark["amount"] = value
                if tool == "extrude":
                    m._geom.get(mark["id"], {}).pop("real", None)
            elif tool == "hole":
                mark["diameter"] = max(0.01, float(inputs.itemById("ehd").value))
                mark["depth"] = max(0.01, float(inputs.itemById("ehp").value))
            return mark
        except Exception:
            log("input sync failed\n{}".format(m.traceback.format_exc()))
            return mark

    def sync_inputs_from_mark(mark):
        inputs = state.get("inputs")
        if inputs is None or mark is None:
            return
        state["updating"] = True
        try:
            tool = mark.get("tool")
            if tool == "move":
                for i, a in enumerate("XYZ"):
                    inputs.itemById("em" + a).value = float(mark.get("vec", [0, 0, 0])[i])
            elif tool == "rotate":
                for i, a in enumerate("XYZ"):
                    inputs.itemById("er" + a).value = math.radians(float(mark.get("rot", [0, 0, 0])[i]))
            elif tool == "scale":
                length = max(float(state["meta"].get("scale_len", 1.0)), 1e-6)
                inputs.itemById("esc").value = (float(mark.get("factor", 1.0)) - 1.0) * length
            elif tool == "scale_axis":
                length = max(float(state["meta"].get("scale_len", 1.0)), 1e-6)
                inputs.itemById("esa").value = (float(mark.get("factor", 1.0)) - 1.0) * length
            elif tool == "axis_rotate":
                inputs.itemById("ear").value = math.radians(float(mark.get("angle", 0.0)))
            elif tool in ("extrude", "fillet"):
                inputs.itemById("d").value = float(mark.get("amount", 0.0))
            elif tool == "hole":
                inputs.itemById("ehd").value = float(mark.get("diameter", 0.0))
                inputs.itemById("ehp").value = float(mark.get("depth", 0.0))
        except Exception:
            pass
        finally:
            state["updating"] = False

    # Every edit command owns a session. A handler only acts while its session is
    # the current one; a stale handler (the OLD command whose destroy/preview
    # arrives AFTER the new command was created) must NOT touch shared state.

    class EditInputChanged(adsk.core.InputChangedEventHandler):
        def __init__(self, session):
            super().__init__(); self.session = session
        def notify(self, args):
            if state.get("updating") or not is_current(self.session):
                return
            try:
                sync_mark_from_inputs()
                draw_active(True)
            except Exception:
                log("edit inputChanged failed\n{}".format(m.traceback.format_exc()))

    class EditPreview(adsk.core.CommandEventHandler):
        def __init__(self, session):
            super().__init__(); self.session = session
        def notify(self, args):
            args.isValidResult = True
            if not is_current(self.session):
                return
            try:
                sync_mark_from_inputs()
                draw_active(True)
            except Exception:
                log("edit preview failed\n{}".format(m.traceback.format_exc()))

    class EditActivate(adsk.core.CommandEventHandler):
        def __init__(self, session):
            super().__init__(); self.session = session
        def notify(self, args):
            if not is_current(self.session):
                return
            try:
                redraw_other_marks(state.get("active_id"))
                draw_active(False)
                m._app.activeViewport.refresh()
            except Exception:
                pass

    class EditExecute(adsk.core.CommandEventHandler):
        def __init__(self, session):
            super().__init__(); self.session = session
        def notify(self, args):
            sess = self.session
            if not is_current(sess):
                return
            try:
                mark = sync_mark_from_inputs()
                if mark is not None and mark.get("tool") in ("extrude", "fillet"):
                    try:
                        m._compute_real(mark)
                    except Exception:
                        pass
                # Confirm commits the edit: drop this mark out of the "editing" phase
                # NOW so the redraw already shows the proposed (comic) look, instead
                # of waiting for Destroy (which Fusion can defer). Ownership-guarded so
                # a successor command that grabbed the edit id isn't disturbed.
                if getattr(m, "_active_edit_id", None) == sess.get("mark_id"):
                    m._active_edit_id = None
                try:
                    m._redraw_marks()
                except Exception:
                    pass
                if getattr(m, "_persist_state", None):
                    m._persist_state("manipulator-edit")
            except Exception:
                pass

    class EditDestroy(adsk.core.CommandEventHandler):
        def __init__(self, session):
            super().__init__(); self.session = session
        def notify(self, args):
            sess = self.session
            if not is_current(sess):
                # A newer edit session already took over (Fusion fires the new
                # command's Created BEFORE the old one's Destroy). This is a pure
                # handoff: touch nothing global, don't redraw or persist -- doing so
                # would stomp the new command's state (the race we were fighting).
                log("DESTROY gen={} stale -> no-op".format(sess.get("gen")))
                return
            # Genuine close of the current session (no successor edit command).
            owned_cmd = (getattr(m, "_active_cmd", None) == "edit_existing")
            log("DESTROY gen={} current owned_cmd={}".format(sess.get("gen"), owned_cmd))
            try:
                mark = m._find(sess.get("mark_id"))
                if mark is not None and mark.get("tool") in ("extrude", "fillet"):
                    try:
                        m._compute_real(mark)
                    except Exception:
                        pass
                m._clear(m.GROUP_PREVIEW)
            except Exception:
                pass
            # Clear ONLY the globals this session still owns -- a toolbar command
            # (Scale/Move/...) may have grabbed them since, and we must never stomp
            # another command's runtime state.
            if state.get("current") is sess:
                state["current"] = None
            if state.get("active_id") == sess.get("mark_id"):
                state["active_id"] = None
            if getattr(m, "_active_edit_id", None) == sess.get("mark_id"):
                m._active_edit_id = None
            if state.get("command") is sess.get("command"):
                state["command"] = None
            if state.get("inputs") is sess.get("inputs"):
                state["inputs"] = None
                state["meta"] = {}
            if m._inputs is sess.get("inputs"):
                m._inputs = None
            if getattr(m, "_active_cmd", None) == "edit_existing":
                m._active_cmd = None
            # Restore the proposed UI only on a genuine close -- if a toolbar command
            # took over (owned_cmd False), or we're mid-switch (a toolbar Launch set
            # _switching while terminating us), let the incoming command own the
            # viewport; a full redraw here would just flash and fight it.
            if owned_cmd and not getattr(m, "_switching", False):
                try:
                    edit_stage_clear()
                    m._redraw_marks()
                    m._send_state()
                    if getattr(m, "_persist_state", None):
                        m._persist_state("manipulator-close")
                except Exception:
                    pass
            log("EDIT CLOSED gen={}".format(sess.get("gen")))

    def fire_pending():
        # The command from the current launch now exists; release the gate and, if
        # a newer card was clicked meanwhile, launch that one (latest wins).
        state["launching"] = False
        p = state.get("pending")
        state["pending"] = None
        if p is not None and p != state.get("active_id"):
            try:
                m._app.fireCustomEvent(EDIT_EVENT_ID, str(int(p)))
            except Exception:
                pass

    class EditCommandCreated(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                mid = state.get("requested_id")
                mark = m._find(mid) if mid is not None else None
                state["requested_id"] = None
                log("CREATE start tool={}".format(mark.get("tool") if mark else None))
                if not is_editable(mark):
                    fire_pending()
                    return
                # This command's own session -- becomes the current one. A stale
                # Destroy/Preview from a prior command will see it is no longer
                # current and leave this session's globals alone.
                gen_counter[0] += 1
                session = {
                    "gen": gen_counter[0],
                    "mark_id": mark["id"],
                    "tool": mark.get("tool"),
                    "command": args.command,
                    "inputs": None,
                }
                state["current"] = session
                state["active_id"] = mark["id"]
                # Expose which mark is being re-edited so other layers (e.g. the
                # fillet preview) can treat a reopened edit as "live".
                m._active_edit_id = mark["id"]
                state["command"] = args.command
                m._active_cmd = "edit_existing"
                args.command.isRepeatable = False
                args.command.okButtonText = "Done adjusting"
                setup_inputs(args.command, mark)
                edit_stage_show(mark.get("tool"))
                session["inputs"] = state.get("inputs")
                # Some renderers (notably Fillet) read m._inputs to clamp values
                # and keep candidate geometry synchronized.
                m._inputs = state["inputs"]

                for HandlerClass, event in (
                    (EditInputChanged, args.command.inputChanged),
                    (EditPreview, args.command.executePreview),
                    (EditActivate, args.command.activate),
                    (EditExecute, args.command.execute),
                    (EditDestroy, args.command.destroy),
                ):
                    handler = HandlerClass(session)
                    event.add(handler)
                    m._handlers.append(handler)
                log("EDIT OPEN mark={} tool={} native manipulator restored".format(
                    mark["id"], mark.get("tool")))
                fire_pending()
            except Exception:
                fire_pending()
                if m._ui:
                    m._ui.messageBox("FuzzyCAD couldn't reopen this manipulator:\n{}".format(
                        m.traceback.format_exc()))

    class EditLaunch(adsk.core.CustomEventHandler):
        def notify(self, args):
            try:
                mid = int(args.additionalInfo)
                mark = m._find(mid)
                if not is_editable(mark):
                    return
                # Clicking the card that is ALREADY being edited would pointlessly
                # terminate + recreate the command. That command churn corrupts
                # Fusion internally and, after a few rounds, the next
                # terminateActiveCommand hard-crashes. Ignore the re-click.
                if mid == state.get("active_id"):
                    log("LAUNCH ignored: mark {} is already the active edit".format(mid))
                    return
                # Serialize: one execute() in flight at a time. A click while a
                # launch is pending just records the newest request; it fires once
                # the current command has been created. Without this, rapid clicks
                # queued several execute()s, spawning empty tool=None commands and
                # create/destroy churn -- the jank.
                if state.get("launching"):
                    state["pending"] = mid
                    log("LAUNCH queued mid={}".format(mid))
                    return
                state["launching"] = True
                log("LAUNCH start mid={} tool={}".format(mid, mark.get("tool")))
                # Do NOT call terminateActiveCommand() ourselves. Terminating the
                # active edit command explicitly from inside this custom event
                # hard-crashed Fusion. Executing the new command lets Fusion end
                # the current one through its own safe command lifecycle.
                state["requested_id"] = mid
                try:
                    m._focus_camera(mark.get("anchor") or [0, 0, 0])
                except Exception:
                    pass
                log("LAUNCH pre_execute")
                cd = m._ui.commandDefinitions.itemById(EDIT_CMD_ID)
                if cd is not None:
                    # Mark the switch window so an outgoing toolbar command's Destroy
                    # skips its heavy redraw; this card edit owns the viewport now.
                    m._switching = True
                    try:
                        cd.execute()
                    finally:
                        m._switching = False
                log("LAUNCH post_execute")
            except Exception:
                log("edit launch failed\n{}".format(m.traceback.format_exc()))

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

            # Accept / Reject from the sidebar on the card that is CURRENTLY open
            # for edit: resolve it first, then end the now-orphaned edit command so
            # its left-rail Confirm card and dangling manipulator go away. Without
            # this the edit command stayed active after Accept and the stage rail
            # lingered. Terminating goes through the proven-safe deferred path
            # (fireCustomEvent), never a direct terminateActiveCommand from here.
            if action in ("accept", "reject"):
                try:
                    tid = int(data.get("id"))
                except Exception:
                    tid = None
                if tid is not None and tid == state.get("active_id"):
                    self._delegate.notify(args)
                    try:
                        edit_stage_clear()
                    except Exception:
                        pass
                    try:
                        m._app.fireCustomEvent(m.LAUNCH_EVENT_ID, "")
                    except Exception:
                        pass
                    return

            if action == "editManipulator":
                try:
                    mid = int(data.get("id"))
                    mark = m._find(mid)
                    if is_editable(mark):
                        m._app.fireCustomEvent(EDIT_EVENT_ID, str(mid))
                        return
                except Exception:
                    pass

            # If the user types a numeric value in the same card while its
            # manipulator is open, update the proposal and native input directly
            # without the legacy full redraw that would interrupt a drag.
            if action == "edit" and state.get("active_id") is not None:
                try:
                    mid = int(data.get("id"))
                    if mid == state.get("active_id"):
                        mark = m._find(mid)
                        if mark is not None:
                            m._apply_edit(mark, data.get("key"), data.get("value"))
                            sync_inputs_from_mark(mark)
                            draw_active(False)
                        return
                except Exception:
                    pass

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._reopen_need_input = lambda mid: m._app.fireCustomEvent(EDIT_EVENT_ID, str(int(mid)))

    def run(context):
        result = old_run(context)
        try:
            existing = m._ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if existing is not None:
                existing.deleteMe()
            cd = m._ui.commandDefinitions.addButtonDefinition(
                EDIT_CMD_ID, "Adjust FuzzyCAD Proposal",
                "Reopen the native manipulator for an unresolved Need Input proposal", "")
            h = EditCommandCreated()
            cd.commandCreated.add(h)
            m._handlers.append(h)
        except Exception:
            log("could not create edit command\n{}".format(m.traceback.format_exc()))

        try:
            m._app.unregisterCustomEvent(EDIT_EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(EDIT_EVENT_ID)
            h2 = EditLaunch()
            evt.add(h2)
            m._handlers.append(h2)
        except Exception:
            log("could not register edit event\n{}".format(m.traceback.format_exc()))

        log("CARD MANIPULATOR READY: Move/Rotate/Scale/Axis/Extrude/Fillet/Hole")
        return result

    def stop(context):
        try:
            m._app.unregisterCustomEvent(EDIT_EVENT_ID)
        except Exception:
            pass
        try:
            cd = m._ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if cd is not None:
                cd.deleteMe()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
