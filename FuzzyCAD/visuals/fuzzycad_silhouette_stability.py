"""Keep proposal silhouettes responsive without putting them on a hot camera path.

The uncertainty silhouette is part of FuzzyCAD's representation and should follow
the user's view.  The original cameraChanged handler rebuilt it at roughly 8 Hz,
which is unnecessary on large/imported BReps.  Replace that handler with a much
slower, quantized camera observer: the silhouette remains view-dependent, but a
continuous orbit causes at most a few redraws per second and repeated tiny camera
changes are ignored.
"""

import time


def install(m):
    adsk = m.adsk
    old_run = m.run

    MIN_INTERVAL = 0.40
    VIEW_STEP = 0.10
    state = {"last": 0.0, "key": None, "handler": None}

    def camera_key():
        try:
            cam = m._app.activeViewport.camera
            v = adsk.core.Vector3D.create(
                cam.eye.x - cam.target.x,
                cam.eye.y - cam.target.y,
                cam.eye.z - cam.target.z)
            if v.length > 1e-9:
                v.normalize()
            def q(x):
                return round(float(x) / VIEW_STEP) * VIEW_STEP
            return (q(v.x), q(v.y), q(v.z))
        except Exception:
            return None

    class SlowCameraChanged(adsk.core.CameraEventHandler):
        def notify(self, args):
            now = time.perf_counter()
            if now - state["last"] < MIN_INTERVAL:
                return
            key = camera_key()
            if key is not None and key == state.get("key"):
                return
            state["last"] = now
            state["key"] = key
            if not getattr(m, "_marks", None):
                return
            try:
                fn = getattr(m, "_redraw_view_silhouettes", None)
                if fn is not None:
                    fn(False)
            except Exception:
                pass

    def run(context):
        result = old_run(context)
        try:
            event = m._app.cameraChanged
            # Remove only the original high-frequency silhouette handler.
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

            h = SlowCameraChanged()
            event.add(h)
            m._handlers.append(h)
            state["handler"] = h
            state["key"] = camera_key()
        except Exception:
            pass
        return result

    m.run = run
