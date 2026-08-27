"""User-facing "Clear all" plus a state-JSON inspector for FuzzyCAD.

FuzzyCAD state lives inside the Fusion document as Design.attributes (group
"FuzzyCAD"), so reopening a file restores every saved question and there was no
UI to wipe it or even to see what is stored. This module adds two panel actions:

  clearAll  -- permanently delete this document's FuzzyCAD state: end any active
               FuzzyCAD command, remove both persistence attributes (primary +
               backup), delete every FuzzyCAD custom-graphics overlay across all
               components (not just root -- a Compare connector preview can live
               on a sub-component), restore every ghosted body to full opacity,
               and empty the in-memory marks.
  dumpState -- write the raw stored JSON to a file next to the add-in and show
               its path, so the persisted attribute can actually be inspected.

Only FuzzyCAD's own attributes and overlays are touched, never user geometry.
"""

import os

# Kept in sync with fuzzycad_persistence.py.
ATTR_GROUP = "FuzzyCAD"
ATTR_NAMES = ("uncertainty_state_v1", "uncertainty_state_v1_backup")
_HERE = os.path.dirname(os.path.abspath(__file__))


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

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

    def end_active_command():
        # A live Compare/manipulator command keeps redrawing its own preview
        # group, so deleting graphics without ending it would let the overlay
        # (e.g. connector sprites) come straight back.
        #
        # A reopened card edit (`edit_existing`) must NEVER be closed with
        # terminateActiveCommand() -- that native-manipulator terminate hard-crashes
        # Fusion (ARCHITECTURE.md §5). Ask safe_confirm to finish it through the
        # deferred doExecute path instead, and DON'T null _active_cmd here (the
        # deferred close needs to still see it as the active edit). For any other
        # command the normal terminate is fine.
        if getattr(m, "_active_cmd", None) == "edit_existing":
            finisher = getattr(m, "_safe_finish_reopen", None)
            if finisher is not None:
                try:
                    finisher("confirm", getattr(m, "_active_edit_id", None))
                    return
                except Exception:
                    pass
        try:
            m._ui.terminateActiveCommand()
        except Exception:
            pass
        try:
            m._active_cmd = None
        except Exception:
            pass

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
        """Remove every FuzzyCAD-owned graphics group on every component.

        Groups are normally added to the root, but a Compare preview can attach
        to an occurrence's component, which a root-only sweep would miss.
        """
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

    def clear_all():
        design = m._design()
        if design is None:
            return
        end_active_command()
        # Put every ghosted body back to full opacity before dropping the marks
        # that let the ghost bookkeeping find them.
        try:
            m._restore_all_bodies()
        except Exception:
            pass
        delete_graphics(design)
        # Drop any runtime dependency nudges (left-rail banner + tint).
        try:
            reset = getattr(m, "_reset_dependency_prompts", None)
            if reset:
                reset()
        except Exception:
            pass
        # Forget the in-memory collaboration state.
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
        # Delete the persisted document attributes so a reopen stays empty.
        removed = delete_attributes(design)
        # A final clean redraw (marks empty) plus a state push.
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
                    clear_all()
                    try: e.returnData = json.dumps({"ok": True})
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
    m._clear_all_uncertainty = clear_all
    m._dump_uncertainty_state = dump_state
    log("CLEAR ALL + STATE DUMP READY")
