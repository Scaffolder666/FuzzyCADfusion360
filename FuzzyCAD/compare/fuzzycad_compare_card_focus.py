"""Give Compare cards a more deliberate camera focus interaction.

Clicking a Conflict/Compare card already pans the camera target to the mark.
This patch keeps the current viewing orientation, moves the target to the
conflict, and adds a modest zoom-in with a smooth transition. Alternative
buttons still stop event propagation, so choosing A/B does not unexpectedly move
the camera.

This module also installs the rail-first Compare selection shell after the
in-place Compare renderer/accept logic has loaded. Keeping that interaction in a
separate file lets the existing Compare mark semantics stay unchanged while the
creation flow becomes independent of Fusion's native command panel.
"""


def install(m):
    adsk = m.adsk
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    ZOOM_FACTOR = 0.70

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
                "[FuzzyCAD CARD FOCUS] " + msg)
        except Exception:
            pass

    def focus_compare(mark):
        try:
            viewport = m._app.activeViewport
            camera = viewport.camera
            anchor = mark.get("anchor") or [0.0, 0.0, 0.0]
            target = adsk.core.Point3D.create(
                float(anchor[0]), float(anchor[1]), float(anchor[2]))

            old_target = camera.target
            old_eye = camera.eye
            vx = old_eye.x - old_target.x
            vy = old_eye.y - old_target.y
            vz = old_eye.z - old_target.z

            # Preserve the exact view direction/orientation and move only along
            # that ray. Perspective cameras visibly zoom by shortening the eye
            # distance. Orthographic cameras additionally use setExtents below.
            camera.target = target
            camera.eye = adsk.core.Point3D.create(
                target.x + vx * ZOOM_FACTOR,
                target.y + vy * ZOOM_FACTOR,
                target.z + vz * ZOOM_FACTOR)

            # Current Fusion versions use getExtents/setExtents for orthographic
            # zoom. The Python return shape has varied, so accept both common
            # tuple forms and simply fall back to the eye move if unavailable.
            try:
                ext = camera.getExtents()
                width = height = None
                if isinstance(ext, tuple):
                    if len(ext) >= 3 and isinstance(ext[0], bool):
                        if ext[0]:
                            width, height = float(ext[1]), float(ext[2])
                    elif len(ext) >= 2:
                        width, height = float(ext[0]), float(ext[1])
                if width and height and width > 1e-9 and height > 1e-9:
                    camera.setExtents(width * ZOOM_FACTOR, height * ZOOM_FACTOR)
            except Exception:
                pass

            camera.isSmoothTransition = True
            viewport.camera = camera
            log("FOCUS compare id={} zoom={}".format(mark.get("id"), ZOOM_FACTOR))
            return True
        except Exception:
            log("focus failed\n{}".format(m.traceback.format_exc()))
            return False

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

            if action == "focus":
                mark = m._find(data.get("id"))
                if mark is not None and mark.get("tool") == "compare":
                    if focus_compare(mark):
                        return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
    m._focus_compare_card = focus_compare

    # Install the selection/Confirm shell after this handler so it becomes the
    # outer Compare-specific interaction owner. The helper re-registers the same
    # CompareHere command id after the legacy shell and therefore wins cleanly at
    # runtime without changing the renderer/accept implementation.
    try:
        import importlib.util
        import os
        import sys
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fuzzycad_compare_selection_flow.py")
        name = "fuzzycad_compare_selection_flow"
        mod = sys.modules.get(name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        mod.install(m)
        log("READY: Compare cards focus + rail-first selection")
    except Exception:
        log("selection flow install failed\n{}".format(m.traceback.format_exc()))
