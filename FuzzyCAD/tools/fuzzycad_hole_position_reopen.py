"""Reopen support for Hole's face-local U/V position controls.

The generic card-manipulator owner creates Hole's diameter/depth inputs. Hole
position is a tool-specific extension, so this module attaches two additional
DistanceValueCommandInputs to the same native edit command after the generic
reopen handler has created it.

No geometry/lifecycle semantics are duplicated here. Position data and the
face-local basis remain owned by tools/fuzzycad_hole.py through `_hole_basis`,
`_hole_center`, and `_hole_update_anchor`.
"""

EDIT_CMD_ID = "FuzzyCAD_EditExistingProposal"


def install(m):
    adsk = m.adsk
    old_run = m.run
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    state = {"updating": False}

    def active_hole():
        if getattr(m, "_active_cmd", None) != "edit_existing":
            return None
        mid = getattr(m, "_active_edit_id", None)
        if mid is None:
            return None
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if mark is None or mark.get("tool") != "hole" or mark.get("status", "open") != "open":
            return None
        return mark

    def base_anchor(mark):
        geom = (getattr(m, "_geom", None) or {}).get(mark.get("id"), {}) or {}
        return list(mark.get("base_anchor") or geom.get("base_anchor") or
                    mark.get("anchor") or [0.0, 0.0, 0.0])

    def position_inputs():
        inputs = getattr(m, "_inputs", None)
        if inputs is None:
            return None, None
        try:
            return inputs.itemById("ehu"), inputs.itemById("ehv")
        except Exception:
            return None, None

    def reposition_all(mark):
        inputs = getattr(m, "_inputs", None)
        basis = getattr(m, "_hole_basis", None)
        center_fn = getattr(m, "_hole_center", None)
        if inputs is None or basis is None or center_fn is None:
            return
        try:
            n, u, v = basis(mark)
            base = base_anchor(mark)
            center = center_fn(mark)
            pbase = adsk.core.Point3D.create(*base)
            pcenter = adsk.core.Point3D.create(*center)

            ehu = inputs.itemById("ehu")
            if ehu is not None:
                ehu.setManipulator(pbase, adsk.core.Vector3D.create(*u))
                ehu.isVisible = True
                ehu.isEnabled = True
            ehv = inputs.itemById("ehv")
            if ehv is not None:
                ehv.setManipulator(pbase, adsk.core.Vector3D.create(*v))
                ehv.isVisible = True
                ehv.isEnabled = True

            # Diameter/depth belong to the moving hole center, not the original
            # face reference center. Re-anchor them whenever U/V changes.
            ehd = inputs.itemById("ehd")
            if ehd is not None:
                ehd.setManipulator(pcenter, adsk.core.Vector3D.create(*u))
                ehd.isVisible = True
                ehd.isEnabled = True
            ehp = inputs.itemById("ehp")
            if ehp is not None:
                ehp.setManipulator(
                    pcenter, adsk.core.Vector3D.create(-n[0], -n[1], -n[2]))
                ehp.isVisible = True
                ehp.isEnabled = True
        except Exception:
            pass

    def redraw_position(mark):
        try:
            m._clear(m.GROUP_PREVIEW)
            group = m._group(m.GROUP_PREVIEW)
            if group is not None:
                m._draw_one(group, mark)
            m._refresh_ghost()
            (getattr(m, "_send_state_throttled", None) or m._send_state)()
        except Exception:
            pass

    class PositionChanged(adsk.core.InputChangedEventHandler):
        def notify(self, args):
            if state.get("updating"):
                return
            try:
                cid = args.input.id
            except Exception:
                return
            if cid not in ("ehu", "ehv"):
                return
            mark = active_hole()
            if mark is None:
                return
            try:
                inputs = getattr(m, "_inputs", None)
                if inputs is None:
                    return
                mark["offset_u"] = float(inputs.itemById("ehu").value)
                mark["offset_v"] = float(inputs.itemById("ehv").value)
                updater = getattr(m, "_hole_update_anchor", None)
                if updater is not None:
                    updater(mark)
                reposition_all(mark)
                redraw_position(mark)
            except Exception:
                pass

    class ReopenCreated(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            mark = active_hole()
            if mark is None:
                return
            try:
                inputs = args.command.commandInputs
                if inputs.itemById("ehu") is None:
                    inputs.addDistanceValueCommandInput(
                        "ehu", "Position U",
                        adsk.core.ValueInput.createByReal(float(mark.get("offset_u", 0.0))))
                if inputs.itemById("ehv") is None:
                    inputs.addDistanceValueCommandInput(
                        "ehv", "Position V",
                        adsk.core.ValueInput.createByReal(float(mark.get("offset_v", 0.0))))
                reposition_all(mark)
                handler = PositionChanged()
                args.command.inputChanged.add(handler)
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

            self._delegate.notify(args)

            # The generic reopen owner synchronizes diameter/depth after sidebar
            # numeric edits. Synchronize the two Hole position inputs here as the
            # tool-specific extension, then move the dependent handles with them.
            if action != "edit":
                return
            mark = active_hole()
            if mark is None:
                return
            try:
                if int(data.get("id")) != int(mark.get("id")):
                    return
            except Exception:
                return
            try:
                ehu, ehv = position_inputs()
                state["updating"] = True
                if ehu is not None:
                    ehu.value = float(mark.get("offset_u", 0.0))
                if ehv is not None:
                    ehv.value = float(mark.get("offset_v", 0.0))
                reposition_all(mark)
            except Exception:
                pass
            finally:
                state["updating"] = False

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
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
