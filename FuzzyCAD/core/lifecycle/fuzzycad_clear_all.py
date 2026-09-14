"""User-facing "Clear all" plus a state-JSON inspector for FuzzyCAD.

FuzzyCAD state lives inside the Fusion document as Design.attributes (group
"FuzzyCAD"), so reopening a file restores every saved question and there was no
UI to wipe it or even to see what is stored. This module adds two panel actions:

  clearAll  -- permanently delete this document's FuzzyCAD state. The request is
               deferred to a Fusion custom event so an active reopened manipulator
               can finish its own Execute/Destroy lifecycle before any mark/runtime
               state is deleted.
  dumpState -- write the raw stored JSON to a file next to the add-in and show
               its path, so the persisted attribute can actually be inspected.

Only FuzzyCAD's own attributes and overlays are touched, never user geometry.
"""

import os

# Kept in sync with fuzzycad_persistence.py.
ATTR_GROUP = "FuzzyCAD"
ATTR_NAMES = ("uncertainty_state_v1", "uncertainty_state_v1_backup")
CLEAR_EVENT_ID = "FuzzyCADClearAllSafe"
_HERE = os.path.dirname(os.path.abspath(__file__))


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD CLEAR] " + msg)
        except Exception:
            pass

    def finish_active_command_on_main_thread():
        """Finish the active FuzzyCAD command before state is destroyed.

        Reopened edits must close through Command.doExecute(True). Calling that
        directly from an HTML callback is unsafe, so Clear All is first deferred
        to this module's CustomEventHandler and only calls this helper there.
        """
        active = getattr(m, "_active_cmd", None)
        if active == "edit_existing":
            closer = getattr(m, "_close_active_edit_sync", None)
            if closer is None:
                log("CLEAR deferred: safe edit closer unavailable")
                return False
            try:
                if not closer("clear-all"):
                    log("CLEAR deferred: reopened edit did not close")
                    return False
            except Exception:
                log("CLEAR edit close failed\n{}".format(m.traceback.format_exc()))
                return False
            if getattr(m, "_active_cmd", None) == "edit_existing":
                log("CLEAR aborted: reopened edit still owns command")
                return False
            return True

        if active is not None:
            try:
                m._ui.terminateActiveCommand()
            except Exception:
                log("CLEAR active command terminate failed\n{}".format(
                    m.traceback.format_exc()))
                return False
            try:
                m._active_cmd = None
            except Exception:
                pass
        return True

    def delete_attributes(design):
        removed = 0
        try:
            attrs = design.attributes
        except Exception:
            return removed
        for name in ATTR_NAMES:
            try:
                a = attrs.itemByName(ATTR_GROUP, name)
                if a is not None:
                    a.deleteMe()
                    removed += 1
            except Exception:
                pass
        return removed

    def delete_graphics(design):
        """Remove every FuzzyCAD-owned graphics group on every component."""
        try:
            comps = design.allComponents
        except Exception:
            comps = None
        if comps is None:
            return
        for i in range(comps.count):
            try:
                groups = comps.item(i).customGraphicsGroups
            except Exception:
                continue
            for j in range(groups.count - 1, -1, -1):
                try:
                    g = groups.item(j)
                    if g is not None and str(g.id).startswith("FuzzyCAD"):
                        g.deleteMe()
                except Exception:
                    pass

    def clear_all_now():
        design = m._design()
        if design is None:
            return False

        # Put only FuzzyCAD-owned visual overrides back to their captured originals
        # before dropping the marks that own those records.
        try:
            m._restore_all_bodies()
        except Exception:
            pass
        delete_graphics(design)

        try:
            reset = getattr(m, "_reset_dependency_prompts", None)
            if reset:
                reset()
        except Exception:
            pass

        try:
            m._marks[:] = []
            m._geom.clear(); m._entity.clear(); m._body.clear()
            m._tool_count.clear()
            m._next_id = 1
        except Exception:
            pass
        for attr in ("_live", "_ghosted"):
            try:
                getattr(m, attr).clear()
            except Exception:
                pass
        try:
            m._pending = None
        except Exception:
            pass

        removed = delete_attributes(design)
        try:
            m._redraw_marks()
        except Exception:
            pass
        try:
            m._send_state()
        except Exception:
            pass
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        log("CLEARED all FuzzyCAD state (attributes removed={})".format(removed))
        return True

    def request_clear_all():
        try:
            m._app.fireCustomEvent(CLEAR_EVENT_ID, "")
            log("CLEAR queued on Fusion main thread")
            return True
        except Exception:
            log("CLEAR queue failed\n{}".format(m.traceback.format_exc()))
            return False

    class ClearAllEvent(adsk.core.CustomEventHandler):
        def notify(self, args):
            try:
                if not finish_active_command_on_main_thread():
                    return
                clear_all_now()
            except Exception:
                log("CLEAR custom event failed\n{}".format(m.traceback.format_exc()))

    def dump_state():
        """Write the raw persisted JSON to a file and report where it is."""
        design = m._design()
        text = None
        if design is not None:
            try:
                a = design.attributes.itemByName(ATTR_GROUP, ATTR_NAMES[0])
                text = a.value if a is not None else None
            except Exception:
                text = None
        path = os.path.join(_HERE, "fuzzycad_state_dump.json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text if text is not None else "{}  (no FuzzyCAD state stored in this document)")
        except Exception:
            log("dump write failed\n{}".format(m.traceback.format_exc()))
            return
        try:
            if text is None:
                m._ui.messageBox(
                    "This document has no stored FuzzyCAD state.\n\n"
                    "It is saved under Design attributes, group '{}', name '{}'.\n"
                    "An empty template was written to:\n{}".format(
                        ATTR_GROUP, ATTR_NAMES[0], path))
            else:
                m._ui.messageBox(
                    "FuzzyCAD state is stored inside this document as a Design "
                    "attribute (group '{}', name '{}').\n\n"
                    "The raw JSON ({} chars) was written to:\n{}".format(
                        ATTR_GROUP, ATTR_NAMES[0], len(text), path))
        except Exception:
            pass
        log("DUMPED state to {}".format(path))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                act = e.action if e is not None else None
                if act == "clearAll":
                    ok = request_clear_all()
                    try: e.returnData = json.dumps({"ok": bool(ok), "queued": bool(ok)})
                    except Exception: pass
                    return
                if act == "dumpState":
                    dump_state()
                    try: e.returnData = json.dumps({"ok": True})
                    except Exception: pass
                    return
            except Exception:
                log("action failed\n{}".format(m.traceback.format_exc()))
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._clear_all_uncertainty = request_clear_all
    m._dump_uncertainty_state = dump_state

    def run(context):
        result = old_run(context)
        try:
            m._app.unregisterCustomEvent(CLEAR_EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(CLEAR_EVENT_ID)
            h = ClearAllEvent()
            evt.add(h)
            m._handlers.append(h)
            log("CLEAR ALL SAFE EVENT READY")
        except Exception:
            log("clear-all event registration failed\n{}".format(
                m.traceback.format_exc()))
        return result

    def stop(context):
        try:
            m._app.unregisterCustomEvent(CLEAR_EVENT_ID)
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
    log("CLEAR ALL + STATE DUMP READY")
