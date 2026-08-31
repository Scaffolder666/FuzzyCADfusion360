"""Keep saved Fusion documents free of FuzzyCAD CustomGraphics.

Fusion 2026 can serialize CustomGraphics into the document's OGS/DefaultScene.
After reopen those graphics may still render even though the API no longer exposes
corresponding CustomGraphicsGroups, leaving undeletable white image/text quads.

FuzzyCAD collaboration state already lives in Design.attributes, so viewport
CustomGraphics are disposable presentation. Immediately before Fusion saves the
active document this module persists the collaboration state, restores temporary
body opacity, and removes every FuzzyCAD CustomGraphics group. While the save is
in progress redraw is suspended. Once documentSaved fires, the current marks are
redrawn from authoritative runtime/persisted state.

Native Fusion Canvases used for explicit user-attached reference images are not
CustomGraphics and are intentionally left alone so those references still travel
with the document.
"""


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop
    old_redraw = m._redraw_marks

    state = {
        "suspended": False,
        "saving_handler": None,
        "saved_handler": None,
        "saving_document": None,
    }

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SAVE CLEAN] " + msg)
        except Exception:
            pass

    def active_document():
        try:
            return (m._app or adsk.core.Application.get()).activeDocument
        except Exception:
            return None

    def event_document(args):
        try:
            e = adsk.core.DocumentEventArgs.cast(args)
            return e.document if e is not None else None
        except Exception:
            return None

    def is_active_target(args):
        doc = event_document(args)
        active = active_document()
        if doc is None or active is None:
            return True
        try:
            return doc == active
        except Exception:
            return True

    def all_components():
        design = None
        try:
            design = m._design()
        except Exception:
            design = None
        if design is None:
            return []
        out = []
        try:
            comps = design.allComponents
            for i in range(comps.count):
                try:
                    out.append(comps.item(i))
                except Exception:
                    pass
        except Exception:
            try:
                out.append(design.rootComponent)
            except Exception:
                pass
        return out

    def purge_custom_graphics():
        """Delete all API-visible FuzzyCAD CustomGraphics in every component."""
        removed = 0
        for comp in all_components():
            try:
                groups = comp.customGraphicsGroups
            except Exception:
                continue
            for i in range(groups.count - 1, -1, -1):
                try:
                    grp = groups.item(i)
                    gid = str(getattr(grp, "id", "") or "")
                    if gid.startswith("FuzzyCAD"):
                        grp.deleteMe()
                        removed += 1
                except Exception:
                    pass
        return removed

    m._purge_fuzzycad_custom_graphics = purge_custom_graphics

    def redraw(*args, **kwargs):
        # Nothing is allowed to repopulate the OGS scene between documentSaving
        # and documentSaved. This wrapper is installed last, so it is the
        # outermost gate around the complete rendering stack.
        if state["suspended"]:
            return None
        return old_redraw(*args, **kwargs)

    m._redraw_marks = redraw

    class DocumentSaving(adsk.core.DocumentEventHandler):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            if not is_active_target(args):
                return
            try:
                persist = getattr(m, "_persist_state", None)
                if persist is not None:
                    persist("document-saving-clean")
            except Exception:
                log("persist before save failed\n{}".format(m.traceback.format_exc()))

            state["suspended"] = True
            state["saving_document"] = event_document(args) or active_document()

            # Body opacity is also presentation state and can otherwise be saved
            # into the .f3d. Restore it before serialization; redraw after save
            # re-applies the visual policy for open marks.
            try:
                restore = getattr(m, "_restore_all_bodies", None)
                if restore is not None:
                    restore()
            except Exception:
                log("opacity restore before save failed\n{}".format(m.traceback.format_exc()))

            removed = 0
            try:
                removed = purge_custom_graphics()
            except Exception:
                log("graphics purge before save failed\n{}".format(m.traceback.format_exc()))
            try:
                (m._app or adsk.core.Application.get()).activeViewport.refresh()
            except Exception:
                pass
            log("DOCUMENT SAVING: suspended redraw; purged {} FuzzyCAD groups".format(removed))

    class DocumentSaved(adsk.core.DocumentEventHandler):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            if not state["suspended"]:
                return
            saved_doc = event_document(args)
            target = state.get("saving_document")
            if saved_doc is not None and target is not None:
                try:
                    if saved_doc != target:
                        return
                except Exception:
                    pass

            state["suspended"] = False
            state["saving_document"] = None
            try:
                # Call the pre-gate renderer directly. It is the full rendering
                # stack as installed before this final save-clean patch.
                old_redraw()
            except Exception:
                log("post-save redraw failed\n{}".format(m.traceback.format_exc()))
            try:
                (m._app or adsk.core.Application.get()).activeViewport.refresh()
            except Exception:
                pass
            log("DOCUMENT SAVED: FuzzyCAD viewport rebuilt from marks")

    def bind_events():
        app = m._app or adsk.core.Application.get()
        try:
            h = DocumentSaving()
            app.documentSaving.add(h)
            m._handlers.append(h)
            state["saving_handler"] = h
        except Exception:
            log("documentSaving binding failed\n{}".format(m.traceback.format_exc()))
        try:
            h = DocumentSaved()
            app.documentSaved.add(h)
            m._handlers.append(h)
            state["saved_handler"] = h
        except Exception:
            log("documentSaved binding failed\n{}".format(m.traceback.format_exc()))

    def run(context):
        result = old_run(context)
        bind_events()
        log("READY: save strips CustomGraphics, saved event redraws them")
        return result

    def stop(context):
        # Do not leave the viewport blank if the add-in is stopped after a save
        # event but before its paired saved event for any reason.
        if state["suspended"]:
            state["suspended"] = False
            state["saving_document"] = None
            try:
                old_redraw()
            except Exception:
                pass
        return old_stop(context)

    m.run = run
    m.stop = stop
