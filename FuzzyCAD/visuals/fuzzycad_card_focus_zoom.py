"""Gentle card focus for FuzzyCAD.

Card clicks bring the relevant uncertainty into view without changing the user's
viewing orientation. Need Input cards then continue into their existing native
edit-manipulator flow; Rough Shape, Note, and Conflict cards simply inspect/focus
the mark because they do not own a native manipulator.
"""


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_focus_camera = m._focus_camera
    old_run = m.run

    # Card focus should feel like inspection, not a camera jump.  Keep enough
    # context around the object and never zoom out from the user's current view.
    ZOOM_FACTOR = 0.78
    MAX_BODY_FRAMES = 4.5
    MIN_BODY_FRAMES = 1.9
    BODY_TOOLS = {"move", "rotate", "scale", "scale_axis", "rough"}
    EDIT_CMD_ID = "FuzzyCAD_EditExistingProposal"
    state = {"pending_edit_id": None}

    def body_center(mark):
        """Use the body center for whole-body operations.

        The mark anchor can intentionally sit on a manipulator handle or another
        local construction point.  Centering on the body makes a card click read
        as "show me this object" while local operations still focus their exact
        edge/face/annotation anchor.
        """
        try:
            if mark.get("tool") not in BODY_TOOLS:
                return None
            body = m._body.get(mark.get("id"))
            if body is None:
                return None
            bb = body.boundingBox
            return [
                (bb.minPoint.x + bb.maxPoint.x) * 0.5,
                (bb.minPoint.y + bb.maxPoint.y) * 0.5,
                (bb.minPoint.z + bb.maxPoint.z) * 0.5,
            ]
        except Exception:
            return None

    def focus_mark(mark):
        if mark is None:
            return False
        try:
            viewport = m._app.activeViewport
            camera = viewport.camera
            target_xyz = body_center(mark) or mark.get("anchor") or [0.0, 0.0, 0.0]
            target = adsk.core.Point3D.create(
                float(target_xyz[0]), float(target_xyz[1]), float(target_xyz[2]))

            old_target = camera.target
            old_eye = camera.eye
            vx = old_eye.x - old_target.x
            vy = old_eye.y - old_target.y
            vz = old_eye.z - old_target.z
            current = (vx * vx + vy * vy + vz * vz) ** 0.5
            if current < 1e-8:
                return False

            size = max(float(mark.get("size", 1.0) or 1.0), 0.2)
            desired = min(current * ZOOM_FACTOR, size * MAX_BODY_FRAMES)
            desired = max(desired, size * MIN_BODY_FRAMES)
            desired = min(desired, current)  # a card click must never zoom out
            ratio = desired / current

            # Preserve the exact view direction. Only retarget and move the eye
            # along the existing view ray, which avoids disorienting rotations.
            camera.target = target
            camera.eye = adsk.core.Point3D.create(
                target.x + vx * ratio,
                target.y + vy * ratio,
                target.z + vz * ratio)

            # Orthographic Fusion views need extents as well as eye movement.
            try:
                ext = camera.getExtents()
                width = height = None
                if isinstance(ext, tuple):
                    if len(ext) >= 3 and isinstance(ext[0], bool):
                        if ext[0]:
                            width, height = float(ext[1]), float(ext[2])
                    elif len(ext) >= 2:
                        width, height = float(ext[0]), float(ext[1])
                if width and height and ratio < 0.999:
                    camera.setExtents(width * ratio, height * ratio)
            except Exception:
                pass

            camera.isSmoothTransition = True
            viewport.camera = camera
            return True
        except Exception:
            return False

    m._focus_mark_card = focus_mark

    # The existing Need Input edit launcher calls m._focus_camera immediately
    # before reopening the native manipulator. Mark that one call so the same
    # focus+zoom behavior happens before edit mode; all unrelated camera calls
    # retain their original behavior.
    def focus_camera(point):
        mid = state.get("pending_edit_id")
        if mid is not None:
            state["pending_edit_id"] = None
            try:
                mark = m._find(mid)
                if mark is not None and focus_mark(mark):
                    return
            except Exception:
                pass
        return old_focus_camera(point)

    m._focus_camera = focus_camera

    # Fusion can hide a DistanceValueCommandInput manipulator between
    # CommandCreated and Activate when the reopened Extrude also rebuilds its
    # CustomGraphics preview. Reassert the same face-center/normal manipulator
    # once, after the reopen owner's Activate handler has finished. This is a
    # command-boundary repair only; it does no per-frame work.
    def reassert_extrude_manipulator():
        if getattr(m, "_active_cmd", None) != "edit_existing":
            return False
        mid = getattr(m, "_active_edit_id", None)
        if mid is None:
            return False
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if mark is None or mark.get("tool") != "extrude" or mark.get("status", "open") != "open":
            return False
        inputs = getattr(m, "_inputs", None)
        if inputs is None:
            return False
        try:
            it = inputs.itemById("d")
            if it is None:
                return False
            anchor = list(mark.get("anchor") or [0.0, 0.0, 0.0])
            geom = (getattr(m, "_geom", None) or {}).get(mark.get("id"), {}) or {}
            raw = list(geom.get("normal") or [0.0, 0.0, 1.0])
            direction = adsk.core.Vector3D.create(float(raw[0]), float(raw[1]), float(raw[2]))
            if direction.length < 1e-9:
                direction = adsk.core.Vector3D.create(0.0, 0.0, 1.0)
            else:
                direction.normalize()
            it.setManipulator(
                adsk.core.Point3D.create(float(anchor[0]), float(anchor[1]), float(anchor[2])),
                direction)
            it.isVisible = True
            it.isEnabled = True
            try:
                trace = getattr(m, "_crash_trace", None)
                if trace is not None:
                    trace("EXTRUDE_MANIPULATOR_REASSERT", "id={}".format(mark.get("id")))
            except Exception:
                pass
            return True
        except Exception:
            return False

    class ReopenActivate(adsk.core.CommandEventHandler):
        def notify(self, args):
            if not reassert_extrude_manipulator():
                return
            try:
                if m._app and m._app.activeViewport:
                    m._app.activeViewport.refresh()
            except Exception:
                pass

    class ReopenCreated(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            try:
                handler = ReopenActivate()
                args.command.activate.add(handler)
                m._handlers.append(handler)
            except Exception:
                pass

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

            if action == "focus":
                # Note / Conflict / lost-reference inspection. Consume the old
                # pan-only action so it cannot immediately overwrite this zoom.
                mark = m._find(data.get("id"))
                if mark is not None and focus_mark(mark):
                    return

            if action == "editManipulator":
                try:
                    mid = int(data.get("id"))
                    mark = m._find(mid)
                    # Rough Shape is Need Input semantically, but it intentionally
                    # has no geometric parameter/manipulator. Its card click is
                    # therefore pure inspection/focus, not a failed reopen attempt.
                    if mark is not None and mark.get("tool") == "rough":
                        state["pending_edit_id"] = None
                        if focus_mark(mark):
                            return
                    state["pending_edit_id"] = mid
                except Exception:
                    state["pending_edit_id"] = None

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        # card_manipulator_reopen creates/recreates this command definition in its
        # run(). Register second so our Activate handler is attached after the
        # reopen owner's own handler and can reassert the native arrow last.
        try:
            cd = m._ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if cd is not None:
                handler = ReopenCreated()
                cd.commandCreated.add(handler)
                m._handlers.append(handler)
        except Exception:
            pass
        return result

    m.run = run
