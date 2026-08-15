"""Keep proposal silhouettes off Fusion's cameraChanged hot path.

The silhouette layer still redraws whenever proposal geometry is redrawn.  For
stability on large/imported BReps, do not delete/recreate CustomGraphics and
sample SurfaceEvaluators continuously while the user orbits the viewport.
"""


def install(m):
    old_run = m.run

    def run(context):
        result = old_run(context)
        try:
            event = m._app.cameraChanged
            for handler in list(getattr(m, "_handlers", []) or []):
                try:
                    cls = handler.__class__
                    if (getattr(cls, "__name__", "") == "CameraChanged" and
                            "fuzzycad_view_silhouette" in getattr(cls, "__module__", "")):
                        try:
                            event.remove(handler)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return result

    m.run = run
