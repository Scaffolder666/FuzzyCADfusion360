"""Keep FuzzyCAD display graphics out of the saved document.

Fusion bakes whatever is on screen -- including add-in CustomGraphics (comic
sketch lines, badges, silhouettes) and body opacity overrides -- into the saved
document's display cache (the OGS blob). Those baked pixels are NOT live
CustomGraphicsGroups on reopen, so the add-in cannot enumerate or delete them:
purge/reclaim never see them. The result is the reported "leftover frames" and
stacked "!" badges that survive Repair and even survive opening the file without
the add-in, and that accumulate one more layer on every save -> reopen cycle.

The fix is to make sure NONE of FuzzyCAD's display graphics (and none of its
display-only opacity overrides) exist at the instant Fusion saves:

    documentSaving -> strip every FuzzyCAD graphics group + restore body opacity,
                      then refresh so the baked cache is clean;
    documentSaved  -> redraw the authoritative visual state so the live session
                      looks normal again.

Collaboration data is untouched -- marks live in Design.attributes, which save
normally. This only removes the transient viewport layer during the save itself.
A one-time recovery for already-polluted files: open, Save once (this strips the
graphics and rebuilds the display cache from the clean live scene), reopen.
"""


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    state = {"registered": False, "handlers": []}

    def log(msg):
        try:
            fn = getattr(m, "_crash_trace", None)
            if fn is not None:
                fn("SAVE_HYGIENE", msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SAVE] " + msg)
        except Exception:
            pass

    def design():
        try:
            return m._design()
        except Exception:
            return None

    def strip_fuzzycad_graphics(des):
        """Delete every FuzzyCAD-owned CustomGraphics group on every component."""
        removed = 0
        try:
            comps = des.allComponents
        except Exception:
            return 0
        for ci in range(comps.count):
            try:
                groups = comps.item(ci).customGraphicsGroups
            except Exception:
                continue
            for i in range(groups.count - 1, -1, -1):
                try:
                    g = groups.item(i)
                    gid = str(getattr(g, "id", "") or "")
                    if gid.startswith("FuzzyCAD"):
                        g.deleteMe()
                        removed += 1
                except Exception:
                    continue
        return removed

    def clean_for_save():
        des = design()
        if des is None:
            return
        # 1) Restore body opacity so no display-only fade is persisted. Use both
        #    the record-based restore and the value-based orphan reclaim, so a body
        #    faded in this session AND one inherited from an older polluted file are
        #    both returned to solid before the save captures body appearance.
        try:
            fn = getattr(m, "_restore_all_bodies", None)
            if fn:
                fn()
        except Exception:
            pass
        try:
            fn = getattr(m, "_reclaim_orphan_visual_opacity", None)
            if fn:
                fn(des)
        except Exception:
            pass
        # 2) Remove every FuzzyCAD graphics group (comic lines, badges, previews,
        #    silhouettes) so none of them are baked into the saved display cache.
        removed = strip_fuzzycad_graphics(des)
        # 3) Force the viewport to re-render the now-clean scene before Fusion
        #    bakes its display cache during the save.
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass
        log("cleaned-for-save removed_groups={}".format(removed))

    def restore_after_save():
        # Rebuild the authoritative visual state so the live session is unchanged.
        try:
            m._redraw_marks()
        except Exception:
            pass
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass
        log("restored-after-save")

    class DocumentSaving(adsk.core.DocumentEventHandler):
        def notify(self, args):
            try:
                clean_for_save()
            except Exception:
                log("saving handler failed\n{}".format(m.traceback.format_exc()))

    class DocumentSaved(adsk.core.DocumentEventHandler):
        def notify(self, args):
            try:
                restore_after_save()
            except Exception:
                log("saved handler failed\n{}".format(m.traceback.format_exc()))

    def register():
        if state["registered"]:
            return
        try:
            app = m._app or adsk.core.Application.get()
            saving = DocumentSaving()
            saved = DocumentSaved()
            app.documentSaving.add(saving)
            app.documentSaved.add(saved)
            # Keep references alive both on our own list and the shared handler list
            # so Fusion does not garbage-collect the callbacks.
            state["handlers"] = [saving, saved]
            for h in state["handlers"]:
                try:
                    m._handlers.append(h)
                except Exception:
                    pass
            state["registered"] = True
            log("document save/saved handlers registered")
        except Exception:
            log("register failed\n{}".format(m.traceback.format_exc()))

    def run(context):
        result = old_run(context)
        try:
            register()
        except Exception:
            pass
        return result

    def stop(context):
        # The shared _handlers list is cleared by the legacy stop; just drop our
        # own references and let that teardown detach the events.
        state["registered"] = False
        state["handlers"] = []
        return old_stop(context)

    m.run = run
    m.stop = stop
    m._save_hygiene_clean = clean_for_save
