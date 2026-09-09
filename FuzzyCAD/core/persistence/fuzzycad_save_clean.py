"""Keep saved Fusion documents free of FuzzyCAD viewport graphics.

FuzzyCAD collaboration state lives in Design.attributes. CustomGraphics and
Fusion's OGS/DefaultScene are disposable presentation and are rebuilt from that
state whenever a design is opened.

Every normal Fusion save is protected by temporarily disabling
CompatibilityPreferences.isCacheGraphicsOnDocumentSave, restoring temporary body
opacity, and deleting API-visible FuzzyCAD CustomGraphics before serialization.
After a normal save, fresh viewport graphics are rebuilt from the current marks.

Older files can already contain orphaned OGS objects that Fusion renders but no
longer exposes through customGraphicsGroups. The palette's Clean Save action is
the explicit repair path for those files: force a real cache-free save, leave the
current baked viewport untouched, close the document, and reopen the newly saved
version. Reopen discards the old in-memory OGS scene and rebuilds one fresh set of
FuzzyCAD visuals from Design.attributes.

Native Fusion Canvases used for explicit user-attached reference images are not
CustomGraphics and are intentionally preserved.
"""

import time

ATTR_GROUP = "FuzzyCAD"
CLEAN_SAVE_STAMP = "ogs_clean_save_v1"


def install(m):
    # uncertainty_badges installs this module defensively before the rest of the
    # render stack. FuzzyCAD.py installs it again at the very end. Use that second
    # call to wrap PaletteHTMLHandler after every other palette patch is present.
    if getattr(m, "_fuzzycad_save_clean_installed", False):
        finalize = getattr(m, "_fuzzycad_save_clean_finalize", None)
        if finalize is not None:
            try:
                finalize()
            except Exception:
                pass
        return
    m._fuzzycad_save_clean_installed = True

    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    state = {
        "saving": False,
        "saving_document": None,
        "saving_handler": None,
        "saved_handler": None,
        "events_bound": False,
        "graphics_cache_previous": None,
        "graphics_cache_overridden": False,
        "prepared": False,
        "manual_clean": False,
        "finalized": False,
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

    def design():
        try:
            return m._design()
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
        des = design()
        if des is None:
            return []
        out = []
        try:
            comps = des.allComponents
            for i in range(comps.count):
                try:
                    out.append(comps.item(i))
                except Exception:
                    pass
        except Exception:
            try:
                out.append(des.rootComponent)
            except Exception:
                pass
        return out

    def purge_custom_graphics():
        """Delete API-visible FuzzyCAD CustomGraphics in every component."""
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
        """Prevent OGS/DefaultScene from being serialized during this save."""
        if state["graphics_cache_overridden"]:
            return True
        prefs = compatibility_preferences()
        if prefs is None:
            log("graphics-cache preference unavailable")
            return False
        try:
            previous = bool(prefs.isCacheGraphicsOnDocumentSave)
            state["graphics_cache_previous"] = previous
            prefs.isCacheGraphicsOnDocumentSave = False
            state["graphics_cache_overridden"] = True
            log("graphics cache disabled for save; previous={}".format(previous))
            return True
        except Exception:
            state["graphics_cache_previous"] = None
            state["graphics_cache_overridden"] = False
            log("could not disable graphics cache\n{}".format(m.traceback.format_exc()))
            return False

    def restore_graphics_cache_setting():
        if not state["graphics_cache_overridden"]:
            return
        previous = state.get("graphics_cache_previous")
        try:
            prefs = compatibility_preferences()
            if prefs is not None and previous is not None:
                prefs.isCacheGraphicsOnDocumentSave = bool(previous)
                log("graphics cache preference restored to {}".format(bool(previous)))
        except Exception:
            log("could not restore graphics cache preference\n{}".format(
                m.traceback.format_exc()))
        finally:
            state["graphics_cache_previous"] = None
            state["graphics_cache_overridden"] = False

    def refresh_viewport():
        try:
            a = app()
            if a is not None and a.activeViewport:
                a.activeViewport.refresh()
        except Exception:
            pass

    def prepare_cache_free_save(reason):
        """Prepare the active design immediately before Fusion serializes it."""
        if state["prepared"]:
            return True

        cache_disabled = disable_graphics_cache_for_save()

        try:
            persist = getattr(m, "_persist_state", None)
            if persist is not None:
                persist(reason)
        except Exception:
            log("persist before save failed\n{}".format(m.traceback.format_exc()))

        # Opacity is display state. Restore it before serialization so FuzzyCAD's
        # temporary comic/preview presentation never becomes document state.
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

        refresh_viewport()
        state["prepared"] = True
        log("PREPARED cache_free={} purged_groups={} reason={}".format(
            cache_disabled, removed, reason))
        return cache_disabled

    def finish_cache_free_save(redraw):
        """Restore Fusion preferences and optionally rebuild runtime visuals."""
        restore_graphics_cache_setting()
        state["prepared"] = False
        if redraw:
            try:
                m._redraw_marks()
            except Exception:
                log("post-save redraw failed\n{}".format(m.traceback.format_exc()))
            refresh_viewport()

    def force_document_dirty():
        """Guarantee Clean Save performs a real serialization, even if unchanged."""
        des = design()
        if des is None:
            return False
        try:
            # This is bookkeeping, not render state. A changing value guarantees
            # Document.save cannot short-circuit because the design is unchanged.
            des.attributes.add(ATTR_GROUP, CLEAN_SAVE_STAMP, str(time.time_ns()))
            return True
        except Exception:
            log("could not stamp clean save\n{}".format(m.traceback.format_exc()))
            return False

    def clean_save_and_reopen():
        """Explicitly remove legacy OGS from the saved file and reload it."""
        a = app()
        doc = active_document()
        if a is None or doc is None:
            return False

        try:
            if not bool(doc.isSaved):
                a.userInterface.messageBox(
                    "Save this design once with Fusion's normal Save As first, then use Clean Save.",
                    "FuzzyCAD Clean Save")
                return False
        except Exception:
            pass

        # Saving/closing is not legal while a Fusion command transaction is open.
        if getattr(m, "_active_cmd", None) is not None:
            try:
                a.userInterface.messageBox(
                    "Finish the active FuzzyCAD edit first, then run Clean Save.",
                    "FuzzyCAD Clean Save")
            except Exception:
                pass
            return False

        try:
            data_file = doc.dataFile
        except Exception:
            data_file = None
        if data_file is None:
            try:
                a.userInterface.messageBox(
                    "This design does not have a saved Fusion data file yet.",
                    "FuzzyCAD Clean Save")
            except Exception:
                pass
            return False

        force_document_dirty()
        state["manual_clean"] = True
        ok = False
        try:
            # documentSaving performs the actual cache suppression + graphics purge.
            ok = bool(doc.save("FuzzyCAD clean viewport cache"))
        except Exception:
            log("manual clean save failed\n{}".format(m.traceback.format_exc()))
            ok = False
        finally:
            # documentSaved normally restores this. Also restore here so a failed
            # save can never leave the user's global Fusion preference modified.
            finish_cache_free_save(redraw=False)

        if not ok:
            state["manual_clean"] = False
            try:
                m._redraw_marks()
                refresh_viewport()
            except Exception:
                pass
            try:
                a.userInterface.messageBox(
                    "Fusion did not complete the clean save.",
                    "FuzzyCAD Clean Save")
            except Exception:
                pass
            return False

        # Do not redraw in the old viewport. A legacy orphan OGS object can still
        # exist there in memory even though it is now absent from the saved file.
        # Closing that viewport is the only reliable way to discard it.
        state["manual_clean"] = False
        try:
            closed = bool(doc.close(False))
        except Exception:
            closed = False

        if not closed:
            try:
                m._redraw_marks()
                refresh_viewport()
            except Exception:
                pass
            try:
                a.userInterface.messageBox(
                    "The file was saved clean, but Fusion could not reload it automatically. "
                    "Close and reopen the design once.",
                    "FuzzyCAD Clean Save")
            except Exception:
                pass
            return True

        try:
            reopened = a.documents.open(data_file, True)
        except Exception:
            reopened = None
        if reopened is None:
            try:
                a.userInterface.messageBox(
                    "The file was saved clean. Reopen the design from the Data Panel to rebuild "
                    "the FuzzyCAD viewport.",
                    "FuzzyCAD Clean Save")
            except Exception:
                pass
        return True

    m._purge_fuzzycad_custom_graphics = purge_custom_graphics
    m._clean_save_and_reopen = clean_save_and_reopen

    class DocumentSaving(adsk.core.DocumentEventHandler):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            if not is_active_target(args):
                return
            state["saving"] = True
            state["saving_document"] = event_document(args) or active_document()
            prepare_cache_free_save("document-saving-clean")

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

            # Clean Save immediately closes/reopens this viewport, so rebuilding
            # here would only stack a fresh marker beside a legacy baked marker.
            if state["manual_clean"]:
                finish_cache_free_save(redraw=False)
                log("CLEAN SAVE serialized without OGS; waiting for reopen")
                return

            finish_cache_free_save(redraw=True)
            log("DOCUMENT SAVED: cache-free file + rebuilt FuzzyCAD viewport")

    def bind_events():
        if state["events_bound"]:
            return
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
        state["events_bound"] = True

    def finalize():
        """Add the Clean Save palette action after all other palette wrappers."""
        if state["finalized"]:
            return
        state["finalized"] = True

        CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

        class CleanSavePaletteHTMLHandler(adsk.core.HTMLEventHandler):
            def __init__(self):
                super().__init__()
                self._delegate = CurrentPaletteHTMLHandler()

            def notify(self, args):
                action = None
                try:
                    e = adsk.core.HTMLEventArgs.cast(args)
                    action = e.action if e is not None else None
                except Exception:
                    pass
                if action == "cleanSave":
                    clean_save_and_reopen()
                    return
                self._delegate.notify(args)

        m.PaletteHTMLHandler = CleanSavePaletteHTMLHandler
        log("FINALIZED: Clean Save palette action installed")

    m._fuzzycad_save_clean_finalize = finalize

    def run(context):
        result = old_run(context)
        bind_events()
        log("READY: normal saves omit OGS; Clean Save also reloads legacy files")
        return result

    def stop(context):
        state["saving"] = False
        state["saving_document"] = None
        state["manual_clean"] = False
        state["prepared"] = False
        restore_graphics_cache_setting()
        return old_stop(context)

    m.run = run
    m.stop = stop
