"""Keep saved Fusion documents free of FuzzyCAD CustomGraphics.

Fusion 2026 can serialize CustomGraphics into the document's OGS/DefaultScene.
After reopen those graphics may still render even though the API no longer exposes
corresponding CustomGraphicsGroups, leaving undeletable white image/text quads or
stacked badges.

FuzzyCAD collaboration state already lives in Design.attributes, so viewport
CustomGraphics are disposable presentation. At the very start of a save this
module temporarily disables Fusion's document graphics cache, persists the
collaboration state, restores temporary body opacity, and removes every
API-visible FuzzyCAD CustomGraphics group. Once documentSaved fires, the user's
graphics-cache preference is restored and the current marks are redrawn from
authoritative runtime/persisted state.

Legacy documents need one migration save. A document that already contains saved
FuzzyCAD state but has never been saved by this guard may contain orphaned OGS
objects that cannot be deleted from the current viewport. During that first
legacy session we hydrate cards and geometry references but suppress fresh
FuzzyCAD redraws, preventing a second badge from being stacked on the baked one.
The clean-save marker is written only while Fusion's graphics cache is disabled.
After that save, the document must be closed and reopened once; the old OGS is no
longer serialized, and fresh visuals are rebuilt from Design.attributes.

Native Fusion Canvases used for explicit user-attached reference images are not
CustomGraphics and are intentionally left alone so those references still travel
with the document.
"""

ATTR_GROUP = "FuzzyCAD"
STATE_ATTR = "uncertainty_state_v1"
OGS_CLEAN_ATTR = "ogs_cache_clean_v1"
OGS_CLEAN_VALUE = "1"


def install(m):
    # fuzzycad_uncertainty_badges historically installs this module defensively,
    # while FuzzyCAD.py also installs it explicitly at the end of the patch stack.
    # The second call is useful: finalize the redraw guard only after every later
    # renderer has wrapped _redraw_marks / _reload_persisted_state.
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
        "saving_handler": None,
        "saved_handler": None,
        "saving_document": None,
        "graphics_cache_previous": None,
        "graphics_cache_overridden": False,
        "clean_marker_written": False,
        "legacy_pending": False,
        "legacy_notice_shown": False,
        "post_save_notice_shown": False,
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

    def attribute(name):
        des = design()
        if des is None:
            return None
        try:
            return des.attributes.itemByName(ATTR_GROUP, name)
        except Exception:
            return None

    def legacy_needs_migration():
        """Conservatively treat old persisted FuzzyCAD documents as OGS legacy."""
        if attribute(STATE_ATTR) is None:
            return False
        clean = attribute(OGS_CLEAN_ATTR)
        if clean is None:
            return True
        try:
            return str(clean.value or "") != OGS_CLEAN_VALUE
        except Exception:
            return True

    def set_legacy_pending(reason):
        pending = bool(legacy_needs_migration())
        state["legacy_pending"] = pending
        # A reopened document gets a fresh notice lifecycle.
        state["post_save_notice_shown"] = False
        try:
            m._fuzzycad_legacy_ogs_pending = pending
        except Exception:
            pass
        log("legacy OGS migration pending={} reason={}".format(pending, reason))
        return pending

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
            return True
        prefs = compatibility_preferences()
        if prefs is None:
            log("graphics-cache preference unavailable; relying on CustomGraphics purge")
            return False
        try:
            previous = bool(prefs.isCacheGraphicsOnDocumentSave)
            state["graphics_cache_previous"] = previous
            prefs.isCacheGraphicsOnDocumentSave = False
            state["graphics_cache_overridden"] = True
            log("disabled document graphics cache for this save (previous={})".format(previous))
            return True
        except Exception:
            state["graphics_cache_previous"] = None
            state["graphics_cache_overridden"] = False
            log("could not disable document graphics cache\n{}".format(
                m.traceback.format_exc()))
            return False

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

    def mark_document_ogs_clean():
        """Persist the migration marker only when cache suppression is active."""
        state["clean_marker_written"] = False
        if not state["graphics_cache_overridden"]:
            return False
        des = design()
        if des is None:
            return False
        try:
            des.attributes.add(ATTR_GROUP, OGS_CLEAN_ATTR, OGS_CLEAN_VALUE)
            state["clean_marker_written"] = True
            log("wrote {} while graphics cache was disabled".format(OGS_CLEAN_ATTR))
            return True
        except Exception:
            log("could not write OGS clean marker\n{}".format(m.traceback.format_exc()))
            return False

    def show_legacy_notice():
        if not state["legacy_pending"] or state["legacy_notice_shown"]:
            return
        state["legacy_notice_shown"] = True
        try:
            ui = (app()).userInterface
            ui.messageBox(
                "This design was saved by an older FuzzyCAD build and may contain a baked viewport cache.\n\n"
                "FuzzyCAD loaded the cards but is not drawing a second set of viewport markers. "
                "Save the design once, then close and reopen it. The reopened file will rebuild "
                "FuzzyCAD visuals from the saved decision state without the legacy OGS cache.",
                "FuzzyCAD legacy viewport cleanup")
        except Exception:
            pass

    def show_post_save_notice():
        if state["post_save_notice_shown"]:
            return
        state["post_save_notice_shown"] = True
        try:
            ui = (app()).userInterface
            ui.messageBox(
                "The clean version has been saved without Fusion's viewport graphics cache.\n\n"
                "Close this design and reopen it once. The old baked marker is still loaded in the "
                "current Fusion viewport and cannot be removed through the CustomGraphics API, so "
                "FuzzyCAD will keep fresh viewport redraws suppressed until reopen.",
                "FuzzyCAD cleanup saved")
        except Exception:
            pass

    def finalize():
        """Install migration guards after the full renderer stack is assembled."""
        if state["finalized"]:
            return
        state["finalized"] = True

        old_redraw = getattr(m, "_redraw_marks", None)
        if old_redraw is not None:
            def redraw_marks(*args, **kwargs):
                if state["legacy_pending"]:
                    log("REDRAW SUPPRESSED: legacy OGS remains loaded until reopen")
                    return None
                return old_redraw(*args, **kwargs)
            m._redraw_marks = redraw_marks

        old_reload = getattr(m, "_reload_persisted_state", None)
        if old_reload is not None:
            def reload_persisted_state(*args, **kwargs):
                set_legacy_pending("document-reload")
                return old_reload(*args, **kwargs)
            m._reload_persisted_state = reload_persisted_state

        log("FINALIZED: legacy hydration/redraw migration guard installed")

    m._fuzzycad_save_clean_finalize = finalize
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
            cache_disabled = disable_graphics_cache_for_save()

            try:
                persist = getattr(m, "_persist_state", None)
                if persist is not None:
                    persist("document-saving-clean")
            except Exception:
                log("persist before save failed\n{}".format(m.traceback.format_exc()))

            state["saving"] = True
            state["saving_document"] = event_document(args) or active_document()

            # Record that this file has passed through a cache-disabled save. On a
            # legacy file the runtime flag deliberately stays True until reopen,
            # because the old orphaned OGS object is still resident in this viewport.
            if cache_disabled:
                mark_document_ogs_clean()

            # Body opacity is presentation state too and can otherwise be written
            # into the document. Restore it before serialization; documentSaved
            # redraw re-applies FuzzyCAD's visual policy for clean/open marks.
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
            log("DOCUMENT SAVING: graphics cache OFF={} purged {} FuzzyCAD groups".format(
                cache_disabled, removed))

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

            if state["legacy_pending"]:
                # Do not draw a second marker into the current viewport. The clean
                # marker has been serialized, but the baked legacy OGS object stays
                # alive in memory until this document is actually reopened.
                if state["clean_marker_written"]:
                    log("LEGACY CLEAN SAVE complete; waiting for document reopen")
                    show_post_save_notice()
                else:
                    log("LEGACY CLEAN SAVE incomplete; clean marker was not written")
                return

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
        # This must happen before persistence.run hydrates the saved marks. The
        # late-finalized _redraw_marks wrapper will therefore suppress the second
        # badge while still allowing the cards/references to load normally.
        set_legacy_pending("startup")
        result = old_run(context)
        bind_events()
        show_legacy_notice()
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
