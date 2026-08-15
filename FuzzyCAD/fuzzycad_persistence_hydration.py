"""Make persisted FuzzyCAD uncertainty hydrate the UI reliably.

Design.attributes remains the source of truth.  This patch closes two lifecycle
gaps:
1) palette HTML can become ready before/after persistence rehydration, so attach
   a dedicated ready handler and also push current state immediately;
2) switching/opening Fusion documents while the add-in stays running must save
   the old document and reload the newly activated document.
"""


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    state = {
        "ready_handler": None,
        "activated_handler": None,
        "deactivating_handler": None,
        "bound_palette": None,
        "active_document": None,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD HYDRATE] " + msg)
        except Exception:
            pass

    def clear_runtime_for_document_switch():
        """Clear only transient in-memory/render state before loading a new file."""
        try:
            m._restore_all_bodies()
        except Exception:
            pass
        try:
            m._clear(m.GROUP_PREVIEW)
            m._clear(m.GROUP_MARKS)
        except Exception:
            pass
        try:
            m._marks[:] = []
            m._geom.clear()
            m._entity.clear()
            m._body.clear()
            m._tool_count.clear()
            m._next_id = 1
        except Exception:
            pass
        try:
            m._send_state()
        except Exception:
            pass

    def reload_active_document(reason):
        """Load Design.attributes into runtime, then push exactly that state to HTML."""
        clear_runtime_for_document_switch()
        try:
            reload_fn = getattr(m, "_reload_persisted_state", None)
            if reload_fn:
                reload_fn()
        except Exception:
            log("reload failed reason={}\n{}".format(reason, m.traceback.format_exc()))
        try:
            m._send_state()
        except Exception:
            pass
        log("HYDRATED reason={} marks={}".format(reason, len(getattr(m, "_marks", []))))

    class ReadyHandler(adsk.core.HTMLEventHandler):
        """Dedicated side-panel handshake; does not intercept normal palette actions."""
        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                if e is not None and e.action == "ready":
                    # By the time this handler is bound, persistence has normally
                    # loaded. If HTML became ready later, this is the missing push.
                    m._send_state()
                    log("PANEL READY -> pushed {} marks".format(len(m._marks)))
            except Exception:
                log("panel ready sync failed\n{}".format(m.traceback.format_exc()))

    class DocumentDeactivating(adsk.core.DocumentEventHandler):
        def notify(self, args):
            try:
                save_fn = getattr(m, "_persist_state", None)
                if save_fn:
                    save_fn("document-deactivating")
                log("SAVED before document switch")
            except Exception:
                log("save before switch failed\n{}".format(m.traceback.format_exc()))

    class DocumentActivated(adsk.core.DocumentEventHandler):
        def notify(self, args):
            try:
                doc = adsk.core.DocumentEventArgs.cast(args).document
            except Exception:
                doc = getattr(m._app, "activeDocument", None)
            # documentActivated fires at the end of activation, so activeProduct
            # now belongs to the document whose Design.attributes we need.
            state["active_document"] = doc
            reload_active_document("document-activated")

    def bind_panel_ready():
        try:
            palette = m._ui.palettes.itemById(m.PALETTE_ID)
        except Exception:
            palette = None
        if palette is None:
            return
        # run() is normally called once, but avoid stacking our dedicated handler.
        if state.get("bound_palette") is palette and state.get("ready_handler") is not None:
            try:
                m._send_state()
            except Exception:
                pass
            return
        try:
            h = ReadyHandler()
            palette.incomingFromHTML.add(h)
            m._handlers.append(h)
            state["ready_handler"] = h
            state["bound_palette"] = palette
            # If HTML is already ready, this succeeds now. If it is not, its
            # subsequent `ready` message is caught by the same handler.
            m._send_state()
            log("BOUND side-panel hydration handshake")
        except Exception:
            log("could not bind panel hydration\n{}".format(m.traceback.format_exc()))

    def bind_document_events():
        try:
            h1 = DocumentDeactivating()
            m._app.documentDeactivating.add(h1)
            m._handlers.append(h1)
            state["deactivating_handler"] = h1
        except Exception:
            log("documentDeactivating binding failed")
        try:
            h2 = DocumentActivated()
            m._app.documentActivated.add(h2)
            m._handlers.append(h2)
            state["activated_handler"] = h2
        except Exception:
            log("documentActivated binding failed")

    def run(context):
        result = old_run(context)
        try:
            state["active_document"] = m._app.activeDocument
        except Exception:
            state["active_document"] = None
        bind_panel_ready()
        bind_document_events()
        # Persistence's run wrapper already loads the current design. Push once
        # more after the full patch stack has started to eliminate HTML timing races.
        try:
            m._send_state()
        except Exception:
            pass
        log("READY: persisted marks hydrate without creating a new uncertainty")
        return result

    def stop(context):
        try:
            save_fn = getattr(m, "_persist_state", None)
            if save_fn:
                save_fn("hydration-stop")
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
