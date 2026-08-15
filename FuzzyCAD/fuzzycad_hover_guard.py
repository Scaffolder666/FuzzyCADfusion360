"""Prevent card-hover replay from competing with a real active edit session.

The old guard blocked replay whenever _active_cmd was non-None.  Fusion can leave
that flag populated briefly after a command has effectively finished, which made
hover feel unreliable.  Only block animation when command state and live command
inputs/pending geometry show that an edit session is actually active.
"""

import json


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    def editing_active():
        active = getattr(m, "_active_cmd", None)
        if active is None:
            return False
        return bool(
            getattr(m, "_inputs", None) is not None or
            getattr(m, "_pending", None) is not None or
            getattr(m, "_note_inputs", None) is not None
        )

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

            # End events must still pass through so a replay can always clean up.
            # Start/frame events are blocked only while a real manipulator/edit
            # session is alive, not merely because a stale command id remains.
            if action in ("hoverMoveStart", "hoverMoveFrame", "hoverOpStart", "hoverOpFrame"):
                if editing_active():
                    return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
