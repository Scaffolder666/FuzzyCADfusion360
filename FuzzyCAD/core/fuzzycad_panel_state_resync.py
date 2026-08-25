"""Make the collaboration sidebar actively pull persisted state when it is ready.

Fusion palette HTML can become ready before the Design/persistence wrappers finish
rehydrating.  A one-shot push is therefore not enough.  This wrapper treats
`ready` and `request_state` as idempotent pull requests: if runtime is still empty,
retry persistence load from the active design, then push the current marks.
"""


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD PANEL RESYNC] " + msg)
        except Exception:
            pass

    def pull_state(reason):
        # If the Python runtime is empty, retry the document snapshot now.  This
        # catches the common startup race where activeProduct was not ready yet.
        if len(getattr(m, "_marks", [])) == 0:
            try:
                reload_fn = getattr(m, "_reload_persisted_state", None)
                if reload_fn is not None:
                    reload_fn()
            except Exception:
                log("reload failed reason={}\n{}".format(
                    reason, m.traceback.format_exc()))
        try:
            m._send_state()
        except Exception:
            log("send failed reason={}\n{}".format(
                reason, m.traceback.format_exc()))
        log("PULL reason={} marks={}".format(reason, len(getattr(m, "_marks", []))))

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
            except Exception:
                pass

            if action in ("ready", "request_state"):
                # Reload first, so the delegated legacy `ready` handler sees the
                # recovered marks instead of publishing an empty state.
                pull_state(str(action))

            try:
                self._delegate.notify(args)
            except Exception:
                log("delegate failed action={}\n{}".format(
                    action, m.traceback.format_exc()))

            if action in ("ready", "request_state"):
                # Push once more after all nested handlers finish.  This is cheap
                # and closes the HTML/Python timing window deterministically.
                try:
                    m._send_state()
                except Exception:
                    pass

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._force_panel_state_sync = pull_state
    log("READY: sidebar can actively re-pull persisted collaboration state")
