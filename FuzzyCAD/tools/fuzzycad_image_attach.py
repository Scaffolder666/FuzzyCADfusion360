"""Attach a reference image to any mark -- part of the comment / annotation layer,
NOT a separate tool. A domain expert drops in a rough shape, then on that mark's
card writes a comment and/or attaches a picture of what it should be.

Two ways to show the image, both pick the file via the OS dialog (no upload):
  * "node"  -> a leader line runs out from the object to a floating image
               billboard (pure CustomGraphics, non-selectable, always faces you).
  * "face"  -> a native Canvas: the image pasted flat onto a picked planar face,
               at real size, sitting in the model.

Note: this touches native Fusion image APIs (createFileDialog, selectEntity,
Canvases). If the face-pick proves flaky from a palette event, it should be
deferred through a custom event like the tool launcher does.
"""

import os
import tempfile

# The floating image is shown as a screen-facing sprite, which renders at the
# image's pixel size -- a big photo would fill the screen. So it is downscaled to
# a small thumbnail first (needs Pillow, which Fusion's Python ships).
THUMB_MAX = 220     # longest side of the floating thumbnail, in pixels
_thumb_seq = [0]


def _make_thumb(path):
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        img = Image.open(path)
        img.thumbnail((THUMB_MAX, THUMB_MAX))
        _thumb_seq[0] += 1
        out = os.path.join(tempfile.gettempdir(),
                           "fuzzycad_thumb_{}.png".format(_thumb_seq[0]))
        img.convert("RGBA").save(out, "PNG")
        return out
    except Exception:
        return None


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_public = m._public
    old_remove_mark = m._remove_mark
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    GID = "FuzzyCAD_ImageNode"
    LEADER_RGB = (120, 124, 132)
    canvases_by_mark = {}   # mark id -> [Canvas objects created this session]

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD IMAGE] " + msg)
        except Exception:
            pass

    # ---- left-rail guidance ----------------------------------------------
    def stage(steps, active, title):
        fn = getattr(m, "_set_tool_stage", None)
        if fn:
            try:
                fn("image", steps, active, title)
            except Exception:
                pass

    def clear_stage():
        fn = getattr(m, "_set_tool_stage", None)
        if fn:
            try:
                fn(None, [], None, "")
            except Exception:
                pass

    # ---- file dialog ------------------------------------------------------
    def pick_image():
        try:
            dlg = m._ui.createFileDialog()
            dlg.title = "Choose a reference image"
            dlg.filter = ("Images (*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff)"
                          ";;All files (*.*)")
            dlg.isMultiSelectEnabled = False
            if dlg.showOpen() == adsk.core.DialogResults.DialogOK:
                return dlg.filename
        except Exception:
            log("file dialog failed\n{}".format(m.traceback.format_exc()))
        return None

    # ---- attach: floating billboard ("node") ------------------------------
    def attach_node(mark):
        if mark is None:
            return
        stage([{"label": "Choose the image", "done": False, "hint": "any picture file"}],
              0, "Floating image")
        try:
            path = pick_image()
            if not path:
                return
            thumb = _make_thumb(path)
            mark.setdefault("images", []).append(
                {"mode": "node", "path": path, "sprite_path": thumb or path})
            if not thumb:
                log("no thumbnail (Pillow missing?) — floating image may render large")
            log("attached node image to mark {}".format(mark.get("id")))
            try:
                m._redraw_marks()
            except Exception:
                pass
            m._send_state()
        finally:
            clear_stage()

    # ---- attach: native Canvas on a picked face ("face") ------------------
    def attach_face(mark):
        if mark is None:
            return
        steps = [{"label": "Select a planar face", "done": False, "hint": "where the image goes"},
                 {"label": "Choose the image", "done": False}]
        stage(steps, 0, "Image on face")
        try:
            try:
                sel = m._ui.selectEntity("Select a planar face for the image", "PlanarFaces")
            except Exception:
                sel = None
                log("selectEntity failed\n{}".format(m.traceback.format_exc()))
            if not sel:
                return
            try:
                face = sel.entity
            except Exception:
                return
            steps[0]["done"] = True
            stage(steps, 1, "Image on face")
            path = pick_image()
            if not path:
                return
            try:
                comp = face.body.parentComponent
                ci = comp.canvases.createInput(path, face)
                # Stretch the image to fill the face (ignore aspect): set the canvas
                # transform's scale to the face's in-plane extents. Best-effort --
                # guarded so a wrong assumption just falls back to the default size.
                try:
                    bb = face.boundingBox
                    ex = sorted([bb.maxPoint.x - bb.minPoint.x,
                                 bb.maxPoint.y - bb.minPoint.y,
                                 bb.maxPoint.z - bb.minPoint.z], reverse=True)
                    fw, fh = max(ex[0], 0.1), max(ex[1], 0.1)
                    mtx = adsk.core.Matrix2D.create()
                    mtx.setCell(0, 0, fw)
                    mtx.setCell(1, 1, fh)
                    ci.transform = mtx
                except Exception:
                    log("fill-scale skipped\n{}".format(m.traceback.format_exc()))
                canvas = comp.canvases.add(ci)
                # remember it so accept/reject can delete it (it's a native doc
                # entity, not tied to the mark on its own).
                canvases_by_mark.setdefault(mark["id"], []).append(canvas)
                tok = None
                try:
                    tok = canvas.entityToken
                except Exception:
                    pass
                mark.setdefault("images", []).append(
                    {"mode": "face", "path": path, "canvas_token": tok})
                log("placed canvas on face for mark {}".format(mark.get("id")))
                m._send_state()
            except Exception:
                m._ui.messageBox("FuzzyCAD couldn't place the image on that face:\n{}".format(
                    m.traceback.format_exc()))
        finally:
            clear_stage()

    # ---- render the floating "node" images --------------------------------
    def node_end(mark):
        a = mark.get("anchor", [0.0, 0.0, 0.0])
        s = mark.get("size", 3.0)
        try:
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
        except Exception:
            (xx, xy, xz), (yx, yy, yz) = (1, 0, 0), (0, 1, 0)
        d = max(2.0, min(float(s) * 0.9, 9.0))
        return (a[0] + (0.85 * xx + 0.7 * yx) * d,
                a[1] + (0.85 * xy + 0.7 * yy) * d,
                a[2] + (0.85 * xz + 0.7 * yz) * d)

    def draw_nodes():
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
            nodes = [im for im in (mark.get("images") or []) if im.get("mode") == "node"]
            if not nodes:
                continue
            a = mark.get("anchor", [0.0, 0.0, 0.0])
            end = node_end(mark)
            # leader line from the object out to the image
            try:
                m._sketchy(grp, [tuple(a), end], LEADER_RGB, 0.0,
                           int(mark.get("id", 0)) * 13, weight=1, strokes=1)
            except Exception:
                pass
            for im in nodes:
                sprite = im.get("sprite_path") or im.get("path")
                # Regenerate the thumbnail if the temp file was cleaned up (reopen).
                if sprite and not os.path.exists(sprite):
                    sprite = _make_thumb(im.get("path")) or im.get("path")
                    im["sprite_path"] = sprite
                if not sprite:
                    continue
                try:
                    coords = adsk.fusion.CustomGraphicsCoordinates.create(list(end))
                    grp.addPointSet(
                        coords, [0],
                        adsk.fusion.CustomGraphicsPointTypes.UserDefinedCustomGraphicsPointType,
                        sprite)
                    drew += 1
                except Exception:
                    log("sprite failed\n{}".format(m.traceback.format_exc()))
        if drew:
            log("drew {} floating image(s)".format(drew))
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            draw_nodes()
        except Exception:
            log("draw_nodes failed\n{}".format(m.traceback.format_exc()))
        return result

    m._redraw_marks = redraw

    # ---- delete a mark's images when it is accepted OR rejected ------------
    def delete_mark_images(mid):
        # Native face canvases created this session.
        for cv in canvases_by_mark.pop(mid, []):
            try:
                cv.deleteMe()
            except Exception:
                pass
        # Any still resolvable by token (e.g. after a reopen).
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if mark:
            design = m._design()
            for im in (mark.get("images") or []):
                tok = im.get("canvas_token")
                if tok and design is not None:
                    try:
                        for e in design.findEntityByToken(tok):
                            try:
                                e.deleteMe()
                            except Exception:
                                pass
                    except Exception:
                        pass

    def remove_mark(mid):
        try:
            delete_mark_images(mid)
        except Exception:
            log("image cleanup failed\n{}".format(m.traceback.format_exc()))
        return old_remove_mark(mid)

    m._remove_mark = remove_mark

    # ---- surface the attachment count on the card -------------------------
    def public(mark):
        out = old_public(mark)
        try:
            out["images"] = list(mark.get("images") or [])
        except Exception:
            out["images"] = []
        return out

    m._public = public

    # ---- palette actions --------------------------------------------------
    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                act = e.action if e is not None else None
                if act in ("attachImageNode", "attachImageFace"):
                    data = json.loads(e.data) if e.data else {}
                    mark = m._find(data.get("id"))
                    if act == "attachImageNode":
                        attach_node(mark)
                    else:
                        attach_face(mark)
                    try:
                        e.returnData = json.dumps({"ok": True})
                    except Exception:
                        pass
                    return
            except Exception:
                log("image action failed\n{}".format(m.traceback.format_exc()))
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    log("IMAGE ATTACH READY (node billboard + face canvas)")
