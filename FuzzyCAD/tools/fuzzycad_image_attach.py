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


def install(m):
    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_public = m._public
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    GID = "FuzzyCAD_ImageNode"
    LEADER_RGB = (120, 124, 132)

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD IMAGE] " + msg)
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
        path = pick_image()
        if not path:
            return
        mark.setdefault("images", []).append({"mode": "node", "path": path})
        log("attached node image to mark {}".format(mark.get("id")))
        try:
            m._redraw_marks()
        except Exception:
            pass
        m._send_state()

    # ---- attach: native Canvas on a picked face ("face") ------------------
    def attach_face(mark):
        if mark is None:
            return
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
        path = pick_image()
        if not path:
            return
        try:
            comp = face.body.parentComponent
            ci = comp.canvases.createInput(path, face)
            comp.canvases.add(ci)
            mark.setdefault("images", []).append({"mode": "face", "path": path})
            log("placed canvas on face for mark {}".format(mark.get("id")))
            m._send_state()
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't place the image on that face:\n{}".format(
                m.traceback.format_exc()))

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
                try:
                    coords = adsk.fusion.CustomGraphicsCoordinates.create(list(end))
                    grp.addPointSet(
                        coords, [0],
                        adsk.fusion.CustomGraphicsPointTypes.UserDefinedCustomGraphicsPointType,
                        im["path"])
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
