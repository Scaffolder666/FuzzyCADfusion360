"""Prevent card-hover replay from competing with an active Fusion command."""

import json


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                if e.data:
                    json.loads(e.data)
            except Exception:
                pass

            # End events must still pass through so an animation that started
            # before another command can clean up its graphics group.
            if action in ("hoverMoveStart", "hoverMoveFrame", "hoverOpStart", "hoverOpFrame"):
                if getattr(m, "_active_cmd", None) is not None:
                    return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
