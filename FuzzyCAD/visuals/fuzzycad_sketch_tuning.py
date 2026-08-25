"""Compatibility shim for the former standalone sketch tuning patch.

Sketch line weight, stroke count, wobble amplitude, and random frequency are now
owned exclusively by fuzzycad_visual_system.py. This module intentionally does
not override m._sketchy anymore; it remains in the loader only to avoid changing
the patch stack abruptly while the visual-system migration settles.
"""


def install(m):
    old_run = m.run

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD SKETCH] " + msg)
        except Exception:
            pass

    def run(context):
        result = old_run(context)
        log("SKETCH TUNING SHIM: styling is controlled by fuzzycad_visual_system.py")
        return result

    m.run = run
