"""Global show/hide switch for all uncertainty visuals.

A single panel button toggles ``m._uncertainty_hidden``. The render owner
(fuzzycad_visual_transition) and the silhouette / floating-image overlays read
that flag and suppress every comic / badge / silhouette / leader-line graphic and
restore each body's original opacity, so the model reads as ordinary solids.

Display-only: no mark data changes, and the flag is not persisted (each session
starts with uncertainty shown).
"""

import json


def install(m):
    adsk = m.adsk

    if not hasattr(m, "_uncertainty_hidden"):
        m._uncertainty_hidden = False

    CurrentPaletteHandler = m.PaletteHTMLHandler

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHandler()

        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action if e is not None else None

                if action == "toggleUncertainty":
                    m._uncertainty_hidden = not bool(
                        getattr(m, "_uncertainty_hidden", False))
                    try:
                        m._redraw_marks()
                    except Exception:
                        pass
                    try:
                        m._send_state()
                    except Exception:
                        pass
                    try:
                        e.returnData = json.dumps(
                            {"ok": True, "hidden": bool(m._uncertainty_hidden)})
                    except Exception:
                        pass
                    return

                if action == "uncertaintyStatus":
                    try:
                        e.returnData = json.dumps(
                            {"ok": True,
                             "hidden": bool(getattr(m, "_uncertainty_hidden", False))})
                    except Exception:
                        pass
                    return
            except Exception:
                pass
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
