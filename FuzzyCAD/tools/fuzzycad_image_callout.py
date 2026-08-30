"""Camera-facing image callouts for FuzzyCAD marks.

This module replaces the old floating-image attachment path with a compact
annotation-style callout:

    mark/object anchor ---- leader line ---- [ white image panel ]

The panel is a screen-facing CustomGraphics point sprite, so its on-screen size
stays readable as the camera moves. Its PNG already contains the white panel,
border, padding, and the user's image. The panel position is derived from the
mark anchor and the current camera basis on every persistent redraw; we do not
store world-space panel corners or any Fusion native graphics wrapper.

The existing "Image on face" path remains owned by fuzzycad_image_attach.py.
This module intercepts only the palette's attachImageNode action so existing
files with legacy node images still render through the old implementation while
new attachments use mode="callout".
"""

import os
import tempfile

CONTENT_MAX_PX = 180
PANEL_PAD_PX = 14
PANEL_RADIUS_PX = 10
PANEL_BORDER_PX = 2
PANEL_BG = (255, 255, 255, 248)
PANEL_BORDER = (148, 153, 161, 255)
LEADER_RGB = (112, 117, 126)
GID = "FuzzyCAD_ImageCallout"
_panel_seq = [0]


def _make_panel(path):
    """Create one padded callout-panel PNG around the source image."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    try:
        image = Image.open(path).convert("RGBA")
        image.thumbnail((CONTENT_MAX_PX, CONTENT_MAX_PX))

        width = image.size[0] + 2 * PANEL_PAD_PX
        height = image.size[1] + 2 * PANEL_PAD_PX
        panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(panel)

        bounds = [0, 0, width - 1, height - 1]
        try:
            draw.rounded_rectangle(
                bounds,
                radius=PANEL_RADIUS_PX,
                fill=PANEL_BG,
                outline=PANEL_BORDER,
                width=PANEL_BORDER_PX,
            )
        except Exception:
            # Older Pillow fallback used by some Fusion Python builds.
            draw.rectangle(
                bounds,
                fill=PANEL_BG,
                outline=PANEL_BORDER,
                width=PANEL_BORDER_PX,
            )

        panel.alpha_composite(image, (PANEL_PAD_PX, PANEL_PAD_PX))

        _panel_seq[0] += 1
        out = os.path.join(
            tempfile.gettempdir(),
            "fuzzycad_callout_{}.png".format(_panel_seq[0]),
        )
        panel.save(out, "PNG")
        return out
    except Exception:
        return None


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_remove_mark = m._remove_mark
    old_stop = m.stop
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD IMAGE CALLOUT] " + msg)
        except Exception:
            pass

    def add3(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def mul3(v, s):
        return (v[0] * s, v[1] * s, v[2] * s)

    def camera_basis():
        try:
            right, up = m._camera_xy()
            return tuple(right), tuple(up)
        except Exception:
            return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

    def callout_layout(mark, index=0):
        """Return anchor, leader end, and panel center for one callout.

        The sprite itself has fixed pixel dimensions. World-space placement only
        needs to keep the panel visibly off the part and stack multiple callouts.
        """
        anchor = tuple(mark.get("anchor", [0.0, 0.0, 0.0]))
        try:
            size = float(mark.get("size", 3.0) or 3.0)
        except Exception:
            size = 3.0

        right, up = camera_basis()

        # Keep the annotation outside small parts without sending it too far away
        # from large assemblies. `size` is the mark's existing bbox-derived scale.
        distance = max(3.0, min(size * 1.05, 10.0))
        vertical = distance * (0.35 - 0.70 * float(index))

        panel_center = add3(
            add3(anchor, mul3(right, distance * 1.25)),
            mul3(up, vertical),
        )

        # End the 3D leader under the camera-facing sprite. The opaque panel hides
        # the final portion, so the visible line reads as terminating at its edge.
        leader_end = panel_center
        return anchor, leader_end, panel_center

    def pick_image():
        try:
            dlg = m._ui.createFileDialog()
            dlg.title = "Choose an image for the callout"
            dlg.filter = (
                "Images (*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff)"
                ";;All files (*.*)"
            )
            dlg.isMultiSelectEnabled = False
            if dlg.showOpen() == adsk.core.DialogResults.DialogOK:
                return dlg.filename
        except Exception:
            log("file dialog failed\n{}".format(m.traceback.format_exc()))
        return None

    def attach_callout(mark):
        if mark is None:
            return

        stage = getattr(m, "_set_tool_stage", None)
        if stage:
            try:
                stage(
                    "image",
                    [{"label": "Choose the image", "done": False,
                      "hint": "shown beside the CAD part"}],
                    0,
                    "Image callout",
                )
            except Exception:
                pass

        try:
            path = pick_image()
            if not path:
                return

            panel_path = _make_panel(path)
            if not panel_path:
                # Still show the original image if Pillow is unavailable; the
                # leader/placement remain useful and the feature never blocks work.
                panel_path = path
                log("panel thumbnail unavailable; using source image")

            mark.setdefault("images", []).append({
                "mode": "callout",
                "path": path,
                "panel_path": panel_path,
            })

            m._redraw_marks()
            m._send_state()
            log("attached callout image to mark {}".format(mark.get("id")))
        finally:
            if stage:
                try:
                    stage(None, [], None, "")
                except Exception:
                    pass

    def draw_callouts():
        try:
            m._clear(GID)
        except Exception:
            pass

        grp = m._group(GID)
        if grp is None:
            return

        drew = 0
        for mark in list(getattr(m, "_marks", None) or []):
            if mark.get("status", "open") != "open":
                continue

            callouts = [
                image for image in (mark.get("images") or [])
                if image.get("mode") == "callout"
            ]
            if not callouts:
                continue

            for index, image in enumerate(callouts):
                anchor, leader_end, panel_center = callout_layout(mark, index)

                try:
                    m._sketchy(
                        grp,
                        [anchor, leader_end],
                        LEADER_RGB,
                        0.0,
                        int(mark.get("id", 0)) * 37 + index,
                        weight=1,
                        strokes=1,
                    )
                except Exception:
                    pass

                panel_path = image.get("panel_path")
                if not panel_path or not os.path.exists(panel_path):
                    panel_path = _make_panel(image.get("path")) or image.get("path")
                    image["panel_path"] = panel_path
                if not panel_path:
                    continue

                try:
                    coords = adsk.fusion.CustomGraphicsCoordinates.create(
                        list(panel_center))
                    grp.addPointSet(
                        coords,
                        [0],
                        adsk.fusion.CustomGraphicsPointTypes.UserDefinedCustomGraphicsPointType,
                        panel_path,
                    )
                    drew += 1
                except Exception:
                    log("callout sprite failed\n{}".format(
                        m.traceback.format_exc()))

        if drew:
            log("drew {} callout image(s)".format(drew))

        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            draw_callouts()
        except Exception:
            log("draw callouts failed\n{}".format(m.traceback.format_exc()))
        return result

    m._redraw_marks = redraw

    def remove_mark(mid):
        # Only delete generated temp panel PNGs. Never touch the user's source file.
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if mark:
            for image in (mark.get("images") or []):
                if image.get("mode") != "callout":
                    continue
                panel_path = image.get("panel_path")
                if panel_path and panel_path != image.get("path"):
                    try:
                        if os.path.exists(panel_path):
                            os.remove(panel_path)
                    except Exception:
                        pass
        return old_remove_mark(mid)

    m._remove_mark = remove_mark

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                import json
                event = adsk.core.HTMLEventArgs.cast(args)
                action = event.action if event is not None else None
                if action == "attachImageNode":
                    data = json.loads(event.data) if event.data else {}
                    attach_callout(m._find(data.get("id")))
                    try:
                        event.returnData = json.dumps({"ok": True})
                    except Exception:
                        pass
                    return
            except Exception:
                log("callout action failed\n{}".format(m.traceback.format_exc()))

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    def stop(context):
        try:
            m._clear(GID)
        except Exception:
            pass
        return old_stop(context)

    m.stop = stop
    log("IMAGE CALLOUT READY (camera-facing panel + leader)")
