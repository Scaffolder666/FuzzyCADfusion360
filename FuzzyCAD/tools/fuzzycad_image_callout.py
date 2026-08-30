"""Image callout attachment built on FuzzyCAD's proven node renderer.

The existing image-attach module already has the stable Fusion CustomGraphics path
for a screen-facing image plus leader line. This module changes only what image
that path receives: new floating-image attachments are pre-rendered as a small
white annotation panel and stored as ordinary mode="node" images.

Keeping the renderer unchanged avoids a second CustomGraphics group / point-set
path. A small redraw wrapper only regenerates a temporary panel PNG when Fusion
has restarted or the temp directory has been cleaned.
"""

import os
import tempfile

CONTENT_MAX_PX = 180
PANEL_PAD_PX = 14
PANEL_RADIUS_PX = 10
PANEL_BORDER_PX = 2
PANEL_BG = (255, 255, 255, 248)
PANEL_BORDER = (148, 153, 161, 255)
_panel_seq = [0]


def _make_panel(path):
    """Create a padded white callout-panel PNG around the source image."""
    if not path:
        return None
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
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD IMAGE CALLOUT] " + msg)
        except Exception:
            pass

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
                    [{
                        "label": "Choose the image",
                        "done": False,
                        "hint": "shown beside the CAD part",
                    }],
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
                # The original node renderer can still display the source image.
                # Do not block the attachment if Pillow is unavailable.
                panel_path = path
                log("panel PNG unavailable; using source image")

            # IMPORTANT: use mode="node". fuzzycad_image_attach.py already owns
            # the stable CustomGraphics point-sprite + leader-line renderer.
            mark.setdefault("images", []).append({
                "mode": "node",
                "callout": True,
                "path": path,
                "sprite_path": panel_path,
            })

            # The original node renderer is inside old_redraw, so this immediately
            # renders the panel through the same path that worked before callouts.
            m._redraw_marks()
            m._send_state()
            log("attached callout through node renderer mark={}".format(
                mark.get("id")))
        finally:
            if stage:
                try:
                    stage(None, [], None, "")
                except Exception:
                    pass

    def ensure_panel_paths():
        """Rebuild temp panel PNGs before the original node renderer runs."""
        for mark in list(getattr(m, "_marks", None) or []):
            for image in (mark.get("images") or []):
                if image.get("mode") != "node" or not image.get("callout"):
                    continue

                sprite_path = image.get("sprite_path")
                if sprite_path and os.path.exists(sprite_path):
                    continue

                panel_path = _make_panel(image.get("path"))
                if panel_path:
                    image["sprite_path"] = panel_path

    def redraw(*args, **kwargs):
        # Prepare the panel first, then delegate all drawing to the established
        # image_attach node renderer captured when this module was installed.
        try:
            ensure_panel_paths()
        except Exception:
            log("panel refresh failed\n{}".format(m.traceback.format_exc()))
        return old_redraw(*args, **kwargs)

    m._redraw_marks = redraw

    def remove_mark(mid):
        # Delete only generated temp panel files; never the user's source image.
        try:
            mark = m._find(mid)
        except Exception:
            mark = None

        if mark:
            for image in (mark.get("images") or []):
                if image.get("mode") != "node" or not image.get("callout"):
                    continue
                sprite_path = image.get("sprite_path")
                source_path = image.get("path")
                if sprite_path and sprite_path != source_path:
                    try:
                        if os.path.exists(sprite_path):
                            os.remove(sprite_path)
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
    log("IMAGE CALLOUT READY (panel uses stable node renderer)")
