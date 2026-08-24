"""A user-facing "Clear all" for FuzzyCAD uncertainty.

FuzzyCAD state lives inside the Fusion document as Design.attributes (group
"FuzzyCAD"), so reopening a file brings every saved question back. There was no
way to wipe that from the UI -- individual Reject only removes cards one by one,
and nothing exposed the stored attributes. This adds a single "Clear all" action
that permanently deletes the document's FuzzyCAD state:

  * removes both persistence attributes (primary + backup),
  * restores every ghosted body to full opacity,
  * deletes all FuzzyCAD custom-graphics groups,
  * empties the in-memory marks and pushes an empty panel.

It intentionally does nothing else in the document -- only FuzzyCAD's own
attributes and overlays are touched, never the user's geometry.
"""

# Kept in sync with fuzzycad_persistence.py.
ATTR_GROUP = "FuzzyCAD"
ATTR_NAMES = ("uncertainty_state_v1", "uncertainty_state_v1_backup")


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
        try:
            root = design.rootComponent
        except Exception:
            return
        # Delete every FuzzyCAD-owned graphics group, iterating backwards since
        # deleteMe shifts indices.
        try:
            groups = root.customGraphicsGroups
            for i in range(groups.count - 1, -1, -1):
                try:
                    g = groups.item(i)
                    if g is not None and str(g.id).startswith("FuzzyCAD"):
                        g.deleteMe()
                except Exception:
                    pass
        except Exception:
            pass

    def clear_all():
        design = m._design()
        if design is None:
            return
        # 1) put every ghosted body back to full opacity before we drop the marks
        #    that let the ghost bookkeeping find them.
        try:
            m._restore_all_bodies()
        except Exception:
            pass
        # 2) drop all overlays.
        delete_graphics(design)
        # 3) forget the in-memory collaboration state.
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
        # 4) delete the persisted document attributes so a reopen stays empty.
        removed = delete_attributes(design)
        # 5) refresh the panel and viewport.
        try:
            m._send_state()
        except Exception:
            pass
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass
        log("CLEARED all FuzzyCAD state (attributes removed={})".format(removed))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                if e is not None and e.action == "clearAll":
                    clear_all()
                    try:
                        e.returnData = json.dumps({"ok": True})
                    except Exception:
                        pass
                    return
            except Exception:
                log("clearAll failed\n{}".format(m.traceback.format_exc()))
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._clear_all_uncertainty = clear_all
    log("CLEAR ALL READY")
