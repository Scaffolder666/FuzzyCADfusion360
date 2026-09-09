"""Keep saved Fusion documents free of FuzzyCAD CustomGraphics.

Fusion 2026 can serialize CustomGraphics into the document's OGS/DefaultScene.
After reopen those graphics may still render even though the API no longer exposes
corresponding CustomGraphicsGroups, leaving undeletable white image/text quads.

FuzzyCAD collaboration state already lives in Design.attributes, so viewport
CustomGraphics are disposable presentation. At the very start of a save this
module temporarily disables Fusion's document graphics cache, persists the
collaboration state, restores temporary body opacity, and removes every
API-visible FuzzyCAD CustomGraphics group. Once documentSaved fires, the user's
graphics-cache preference is restored and the current marks are redrawn from
authoritative runtime/persisted state.

Native Fusion Canvases used for explicit user-attached reference images are not
CustomGraphics and are intentionally left alone so those references still travel
with the document.
"""


def install(m):
    if getattr(m, "_fuzzycad_save_clean_installed", False):
        return
    m._fuzzycad_save_clean_installed = True

    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    state = {
        "saving": False,
        "saving_handler": None,
        "saved_handler": None,
        "saving_document": None,
        "graphics_cache_previous": None,
        "graphics_cache_overridden": False,
    }

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SAVE CLEAN] " + msg)
        except Exception:
            pass

    def app():
        try:
            return m._app or adsk.core.Application.get()
        except Exception:
            return None

    def active_document():
        try:
            a = app()
            return a.activeDocument if a is not None else None
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

    def compatibility_preferences():
        try:
            a = app()
            if a is None:
                return None
            prefs = a.preferences
            return prefs.compatibilityPreferences if prefs is not None else None
        except Exception:
            return None

    def disable_graphics_cache_for_save():
        """Temporarily prevent OGS/DefaultScene from being serialized."""
        if state["graphics_cache_overridden"]:
            return
        prefs = compatibility_preferences()
        if prefs is None:
            log("graphics-cache preference unavailable; relying on CustomGraphics purge")
            return
        try:
            previous = bool(prefs.isCacheGraphicsOnDocumentSave)
            state["graphics_cache_previous"] = previous
            prefs.isCacheGraphicsOnDocumentSave = False
            state["graphics_cache_overridden"] = True
            log("disabled document graphics cache for this save (previous={})".format(previous))
        except Exception:
            state["graphics_cache_previous"] = None
            state["graphics_cache_overridden"] = False
            log("could not disable document graphics cache\n{}".format(
                m.traceback.format_exc()))

    def restore_graphics_cache_setting():
        """Restore the Fusion preference changed for the current save."""
        if not state["graphics_cache_overridden"]:
            return
        previous = state.get("graphics_cache_previous")
        try:
            prefs = compatibility_preferences()
            if prefs is not None and previous is not None:
                prefs.isCacheGraphicsOnDocumentSave = bool(previous)
                log("restored document graphics cache preference to {}".format(bool(previous)))
        except Exception:
            log("could not restore document graphics cache preference\n{}".format(
                m.traceback.format_exc()))
        finally:
            state["graphics_cache_previous"] = None
            state["graphics_cache_overridden"] = False

    m._purge_fuzzycad_custom_graphics = purge_custom_graphics

    class DocumentSaving(adsk.core.DocumentEventHandler):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            if not is_active_target(args):
                return

            # documentSaving fires at the very start of serialization. Disable
            # Fusion's document graphics cache first so orphaned OGS objects that
            # are no longer API-enumerable cannot travel with the saved design.
            disable_graphics_cache_for_save()

            try:
                persist = getattr(m, "_persist_state", None)
                if persist is not None:
                    persist("document-saving-clean")
            except Exception:
                log("persist before save failed\n{}".format(m.traceback.format_exc()))

            state["saving"] = True
            state["saving_document"] = event_document(args) or active_document()

            # Body opacity is presentation state too and can otherwise be written
            # into the document. Restore it before serialization; documentSaved
            # redraw re-applies FuzzyCAD's visual policy for the open marks.
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
                a = app()
                if a is not None and a.activeViewport:
                    a.activeViewport.refresh()
            except Exception:
                pass
            log("DOCUMENT SAVING: graphics cache OFF; purged {} FuzzyCAD groups".format(
                removed))

    class DocumentSaved(adsk.core.DocumentEventHandler):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            if not state["saving"]:
                return
            saved_doc = event_document(args)
            target = state.get("saving_document")
            if saved_doc is not None and target is not None:
                try:
                    if saved_doc != target:
                        return
                except Exception:
                    pass

            state["saving"] = False
            state["saving_document"] = None

            # The file is already serialized. Return Fusion to the user's original
            # graphics-cache setting before rebuilding transient viewport graphics.
            restore_graphics_cache_setting()

            try:
                # Resolve dynamically so every renderer installed after this module
                # also participates in the post-save rebuild.
                m._redraw_marks()
            except Exception:
                log("post-save redraw failed\n{}".format(m.traceback.format_exc()))
            try:
                a = app()
                if a is not None and a.activeViewport:
                    a.activeViewport.refresh()
            except Exception:
                pass
            log("DOCUMENT SAVED: preference restored; FuzzyCAD viewport rebuilt from marks")

    def bind_events():
        a = app()
        if a is None:
            return
        try:
            h = DocumentSaving()
            a.documentSaving.add(h)
            m._handlers.append(h)
            state["saving_handler"] = h
        except Exception:
            log("documentSaving binding failed\n{}".format(m.traceback.format_exc()))
        try:
            h = DocumentSaved()
            a.documentSaved.add(h)
            m._handlers.append(h)
            state["saved_handler"] = h
        except Exception:
            log("documentSaved binding failed\n{}".format(m.traceback.format_exc()))

    def run(context):
        result = old_run(context)
        bind_events()
        log("READY: saves omit OGS cache, strip FuzzyCAD graphics, then redraw")
        return result

    def stop(context):
        state["saving"] = False
        state["saving_document"] = None
        # Also repair the global preference if the add-in is stopped while a save
        # lifecycle is incomplete.
        restore_graphics_cache_setting()
        return old_stop(context)

    m.run = run
    m.stop = stop
