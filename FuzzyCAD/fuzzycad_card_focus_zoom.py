"""Gentle card focus for FuzzyCAD.

Card clicks should bring the relevant uncertainty into view without changing the
user's viewing orientation. Need Input cards then continue into their existing
edit-manipulator flow; Note and Conflict cards simply inspect/focus the mark.
"""


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    ZOOM_FACTOR = 0.72
    MAX_BODY_FRAMES = 5.0
    MIN_BODY_FRAMES = 1.8

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass

    def focus_mark(mark):
        if mark is None:
            return False
        try:
            viewport = m._app.activeViewport
            camera = viewport.camera
            anchor = mark.get("anchor") or [0.0, 0.0, 0.0]
            target = adsk.core.Point3D.create(float(anchor[0]), float(anchor[1]), float(anchor[2]))

            old_target = camera.target
            old_eye = camera.eye
            vx = old_eye.x - old_target.x
            vy = old_eye.y - old_target.y
            vz = old_eye.z - old_target.z
            current = (vx * vx + vy * vy + vz * vz) ** 0.5
            if current < 1e-8:
                return False

            size = max(float(mark.get("size", 1.0) or 1.0), 0.2)
            desired = min(current * ZOOM_FACTOR, size * MAX_BODY_FRAMES)
            desired = max(desired, size * MIN_BODY_FRAMES)
            desired = min(desired, current)  # focus never zooms out
            ratio = desired / current

            camera.target = target
            camera.eye = adsk.core.Point3D.create(
                target.x + vx * ratio,
                target.y + vy * ratio,
                target.z + vz * ratio)

            # Orthographic Fusion views need extents as well as eye movement.
            try:
                ext = camera.getExtents()
                width = height = None
                if isinstance(ext, tuple):
                    if len(ext) >= 3 and isinstance(ext[0], bool):
                        if ext[0]:
                            width, height = float(ext[1]), float(ext[2])
                    elif len(ext) >= 2:
                        width, height = float(ext[0]), float(ext[1])
                if width and height and ratio < 0.999:
                    camera.setExtents(width * ratio, height * ratio)
            except Exception:
                pass

            camera.isSmoothTransition = True
            viewport.camera = camera
            return True
        except Exception:
            return False

    m._focus_mark_card = focus_mark

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            # Inspection cards stop here so the old pan-only handler does not
            # immediately overwrite the zoom. Need Input continues to delegate;
            # its edit launcher calls the same focus helper before opening the
            # native manipulator.
            if action == "focus":
                mark = m._find(data.get("id"))
                if mark is not None and focus_mark(mark):
                    return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
