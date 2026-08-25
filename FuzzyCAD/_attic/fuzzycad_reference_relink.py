"""Expose and recover persistence rows whose Fusion geometry reference changed.

Persistence deliberately keeps the collaboration decision when an entity token
can no longer be resolved. This patch makes that degraded state visible to the
sidebar, blocks unsafe Apply/Adjust actions, and offers a small tool-specific
Relink command for the common Need Input and Note cases.
"""

import json

CMD_ID = "FuzzyCAD_RelinkReference"
EVENT_ID = "FuzzyCADRelinkReference"


def install(m):
    adsk = m.adsk
    old_public = m._public
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {"requested_id": None}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD RELINK] " + msg)
        except Exception:
            pass

    def public(mark):
        out = old_public(mark)
        lost = bool(mark.get("reference_lost"))
        out["reference_lost"] = lost
        out["can_relink"] = bool(lost and mark.get("tool") != "compare")
        return out

    m._public = public

    def body_of(entity):
        if entity is None:
            return None
        if isinstance(entity, adsk.fusion.BRepBody):
            return entity
        try:
            return entity.body
        except Exception:
            return None

    def point_from_selection(selection, entity):
        try:
            p = selection.point
            if p is not None:
                return [p.x, p.y, p.z]
        except Exception:
            pass
        try:
            p = entity.centroid
            return [p.x, p.y, p.z]
        except Exception:
            pass
        try:
            bb = entity.boundingBox
            return [(bb.minPoint.x + bb.maxPoint.x) * 0.5,
                    (bb.minPoint.y + bb.maxPoint.y) * 0.5,
                    (bb.minPoint.z + bb.maxPoint.z) * 0.5]
        except Exception:
            return [0.0, 0.0, 0.0]

    def center_size(body, fallback_anchor=None, fallback_size=3.0):
        if body is not None:
            try:
                return m._bbox_center_size(body)
            except Exception:
                pass
        return (list(fallback_anchor or [0.0, 0.0, 0.0]), float(fallback_size or 3.0))

    def pending_for(mark, entity, selection):
        tool = mark.get("tool")
        body = body_of(entity)

        if tool in ("move", "rotate"):
            if body is None:
                return None
            return m._build_pending("transform", body)

        if tool in ("scale", "scale_axis"):
            if body is None:
                return None
            return m._build_pending("scale", body)

        if tool == "extrude":
            if not isinstance(entity, adsk.fusion.BRepFace):
                return None
            return m._build_pending("extrude", entity)

        if tool == "fillet":
            if not isinstance(entity, adsk.fusion.BRepEdge):
                return None
            return m._build_pending("fillet", entity)

        if tool == "axis_rotate":
            if body is None:
                return None
            c, s = center_size(body, mark.get("anchor"), mark.get("size", 3.0))
            old_geom = m._geom.get(mark.get("id"), {}) or {}
            axis_origin = old_geom.get("axis_origin") or mark.get("axis_origin")
            axis_dir = old_geom.get("axis_dir") or mark.get("axis_dir")
            if axis_origin is None or axis_dir is None:
                return None
            return {
                "geom": {
                    "edges": m._sample_edges(body.edges),
                    "axis_origin": list(axis_origin),
                    "axis_dir": list(axis_dir),
                },
                "anchor": c,
                "size": s,
                "entity": body,
                "body": body,
            }

        if tool == "note":
            c = point_from_selection(selection, entity)
            _, s = center_size(body, c, mark.get("size", 3.0))
            return {
                "geom": {},
                "anchor": c,
                "size": s,
                "entity": entity,
                "body": body,
            }

        return None

    def filters_for(tool):
        if tool == "fillet":
            return ("Edges",), "Select the replacement edge"
        if tool == "extrude":
            return ("Faces",), "Select the replacement face"
        if tool in ("move", "rotate", "scale", "scale_axis", "axis_rotate"):
            return ("SolidBodies",), "Select the replacement body"
        if tool == "note":
            return ("Vertices", "Edges", "Faces", "SolidBodies"), "Select the new note location"
        return (), "Select replacement geometry"

    class RelinkExecute(adsk.core.CommandEventHandler):
        def notify(self, args):
            mid = state.get("requested_id")
            mark = m._find(mid) if mid is not None else None
            if mark is None:
                return
            try:
                sel = args.command.commandInputs.itemById("relink_sel")
                if sel is None or sel.selectionCount < 1:
                    return
                selection = sel.selection(0)
                entity = selection.entity
                pending = pending_for(mark, entity, selection)
                if pending is None:
                    m._ui.messageBox("That geometry cannot replace this {} reference.".format(
                        mark.get("tool", "proposal")))
                    return

                m._geom[mid] = pending.get("geom") or {}
                if pending.get("entity") is not None:
                    m._entity[mid] = pending.get("entity")
                else:
                    m._entity.pop(mid, None)
                if pending.get("body") is not None:
                    m._body[mid] = pending.get("body")
                else:
                    m._body.pop(mid, None)
                if pending.get("anchor") is not None:
                    mark["anchor"] = list(pending.get("anchor"))
                if pending.get("size") is not None:
                    mark["size"] = float(pending.get("size"))
                mark.pop("reference_lost", None)

                if mark.get("tool") in ("extrude", "fillet"):
                    try:
                        m._compute_real(mark)
                    except Exception:
                        pass

                try:
                    if hasattr(m, "_mark_dirty"):
                        m._mark_dirty(mid, mark.get("tool"))
                    m._redraw_marks()
                except Exception:
                    pass
                try:
                    m._send_state()
                except Exception:
                    pass
                try:
                    if getattr(m, "_persist_state", None):
                        m._persist_state("reference-relink")
                except Exception:
                    pass
                log("RELINKED mark={} tool={}".format(mid, mark.get("tool")))
            except Exception:
                log("execute failed\n{}".format(m.traceback.format_exc()))
            finally:
                state["requested_id"] = None

    class RelinkCreated(adsk.core.CommandCreatedEventHandler):
        def notify(self, args):
            mid = state.get("requested_id")
            mark = m._find(mid) if mid is not None else None
            if mark is None or not mark.get("reference_lost"):
                return
            try:
                cmd = args.command
                cmd.isRepeatable = False
                try:
                    cmd.isExecutedWhenPreEmpted = False
                except Exception:
                    pass
                cmd.okButtonText = "Relink"
                filters, hint = filters_for(mark.get("tool"))
                sel = cmd.commandInputs.addSelectionInput("relink_sel", "Replacement", hint)
                for f in filters:
                    sel.addSelectionFilter(f)
                sel.setSelectionLimits(1, 1)
                h = RelinkExecute()
                cmd.execute.add(h)
                m._handlers.append(h)
            except Exception:
                log("command setup failed\n{}".format(m.traceback.format_exc()))

    class RelinkLaunch(adsk.core.CustomEventHandler):
        def notify(self, args):
            try:
                mid = int(args.additionalInfo)
                mark = m._find(mid)
                if mark is None or not mark.get("reference_lost"):
                    return
                if mark.get("tool") == "compare":
                    m._ui.messageBox(
                        "This comparison lost one of its assembly references. Reject it and create Compare again so all three connectors are explicit.")
                    return
                try:
                    m._ui.terminateActiveCommand()
                except Exception:
                    pass
                state["requested_id"] = mid
                cd = m._ui.commandDefinitions.itemById(CMD_ID)
                if cd is not None:
                    cd.execute()
            except Exception:
                state["requested_id"] = None
                log("launch failed\n{}".format(m.traceback.format_exc()))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            if action == "relink":
                try:
                    m._app.fireCustomEvent(EVENT_ID, str(int(data.get("id"))))
                except Exception:
                    pass
                return

            if action in ("accept", "editManipulator"):
                mark = m._find(data.get("id"))
                if mark is not None and mark.get("reference_lost"):
                    try:
                        m._ui.messageBox(
                            "This question is no longer linked to its original geometry. Relink it before adjusting or applying the change.")
                    except Exception:
                        pass
                    return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def run(context):
        result = old_run(context)
        try:
            existing = m._ui.commandDefinitions.itemById(CMD_ID)
            if existing is not None:
                existing.deleteMe()
            cd = m._ui.commandDefinitions.addButtonDefinition(
                CMD_ID, "Relink FuzzyCAD Reference",
                "Reconnect a persisted FuzzyCAD question to replacement Fusion geometry", "")
            h = RelinkCreated()
            cd.commandCreated.add(h)
            m._handlers.append(h)
        except Exception:
            log("command registration failed\n{}".format(m.traceback.format_exc()))

        try:
            m._app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(EVENT_ID)
            h2 = RelinkLaunch()
            evt.add(h2)
            m._handlers.append(h2)
        except Exception:
            log("event registration failed\n{}".format(m.traceback.format_exc()))

        log("READY: lost geometry references are visible and recoverable")
        return result

    def stop(context):
        try:
            m._app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            pass
        try:
            cd = m._ui.commandDefinitions.itemById(CMD_ID)
            if cd is not None:
                cd.deleteMe()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
