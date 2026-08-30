"""Attach a reference image to any mark -- part of the comment / annotation layer,
NOT a separate tool. A domain expert drops in a rough shape, then on that mark's
card writes a comment and/or attaches a picture of what it should be.

Two ways to show the image, both pick the file via the OS dialog (no upload):
  * "node"  -> a leader line runs out from the object to a floating image
               billboard (pure CustomGraphics, non-selectable, always faces you).
  * "face"  -> a native Canvas: the image pasted flat onto a picked planar face,
               automatically oriented so image-up matches the current viewport-up
               as closely as possible.

Note: this touches native Fusion image APIs (createFileDialog, selectEntity,
Canvases). If the face-pick proves flaky from a palette event, it should be
deferred through a custom event like the tool launcher does.
"""

import os
import tempfile

# The floating image is shown as a screen-facing sprite, which renders at the
# image's pixel size -- a big photo would fill the screen. So it is downscaled to
# a small thumbnail first (needs Pillow, which Fusion's Python ships).
THUMB_MAX = 64      # longest side of the floating thumbnail, in pixels (small: the
                    #   sprite renders at pixel size, so this is its on-screen size)
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
        out = os.path.join(
            tempfile.gettempdir(),
            "fuzzycad_thumb_{}.png".format(_thumb_seq[0])
        )
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
            ct = getattr(m, "_crash_trace", None)
            if ct is not None:
                ct("IMAGE", str(msg))
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD IMAGE] " + msg)
        except Exception:
            pass

    try:
        from PIL import Image as _PILProbe  # noqa: F401
        log("Pillow available -- floating images downscale to THUMB_MAX px")
    except Exception:
        log("Pillow NOT available -- floating images render FULL SIZE (huge); "
            "resize impossible without Pillow")

    # ---- small vector helpers --------------------------------------------
    def dot3(a, b):
        return a.x * b.x + a.y * b.y + a.z * b.z

    def len3(v):
        return (v.x * v.x + v.y * v.y + v.z * v.z) ** 0.5

    def unit3(v):
        ln = len3(v)
        if ln < 1e-10:
            return None
        return adsk.core.Vector3D.create(v.x / ln, v.y / ln, v.z / ln)

    def scaled3(v, s):
        return adsk.core.Vector3D.create(v.x * s, v.y * s, v.z * s)

    def sub3(a, b):
        return adsk.core.Vector3D.create(a.x - b.x, a.y - b.y, a.z - b.z)

    def vec2_world(v2, u_world, v_world):
        """Convert a vector in Canvas plane coordinates into model-space."""
        return adsk.core.Vector3D.create(
            v2.x * u_world.x + v2.y * v_world.x,
            v2.x * u_world.y + v2.y * v_world.y,
            v2.x * u_world.z + v2.y * v_world.z,
        )

    # ---- orient native face Canvas to current viewport -------------------
    def orient_canvas_to_view(ci, face):
        """
        Rotate ONLY the default Canvas matrix.

        Important: CanvasInput.plane already exposes the exact coordinate system
        Fusion uses for this Canvas. For a BRepFace Fusion internally centers that
        system on the face and handles reversed face parameterization. Using raw
        BRep UV derivatives here mixes two coordinate systems and can make the
        image mirrored or displaced.

        This function therefore:
          * keeps Fusion's default origin,
          * keeps Fusion's default width / height / aspect ratio,
          * only rotates the X/Y axes so image-up matches viewport-up,
          * chooses X so image-right matches viewport-right (no mirror).
        """
        try:
            app = m._app or adsk.core.Application.get()
            cam = app.activeViewport.camera

            # This Plane is the coordinate system the Canvas transform is defined in.
            # Autodesk exposes it specifically for positioning the Canvas.
            plane = ci.plane
            if plane is None:
                log("auto-orient skipped: CanvasInput.plane is unavailable")
                return False

            u_world = unit3(plane.uDirection)
            v_world = unit3(plane.vDirection)
            normal = unit3(plane.normal)
            if u_world is None or v_world is None or normal is None:
                log("auto-orient skipped: invalid Canvas plane basis")
                return False

            # Desired image-up = WORLD up (Z axis) projected onto the face plane.
            # Anchoring to world-up instead of the camera is what stops the image
            # coming in tilted a little differently every time -- the camera is
            # almost never perfectly square to the face, so its up carried that
            # tilt into the Canvas. World-up is fixed, so the result is consistent
            # and reads straight. Only a horizontal face (world-up parallel to the
            # normal) has no in-plane up; there we fall back to the camera's up.
            world_up = adsk.core.Vector3D.create(0.0, 0.0, 1.0)
            up_on_plane = unit3(
                sub3(world_up, scaled3(normal, dot3(world_up, normal)))
            )

            if up_on_plane is None:
                cam_up = unit3(cam.upVector)
                if cam_up is not None:
                    up_on_plane = unit3(
                        sub3(cam_up, scaled3(normal, dot3(cam_up, normal)))
                    )
                if up_on_plane is None:
                    log("auto-orient skipped: no usable up direction for this face")
                    return False

            # Express desired UP in the Canvas plane's own U/V coordinate system.
            up2 = adsk.core.Vector2D.create(
                dot3(up_on_plane, u_world),
                dot3(up_on_plane, v_world),
            )
            up2_len = up2.length
            if up2_len < 1e-10:
                log("auto-orient skipped: up has no in-plane component")
                return False

            up2_unit = adsk.core.Vector2D.create(
                up2.x / up2_len,
                up2.y / up2_len,
            )

            # X = the rightward perpendicular of up. This particular choice,
            # (up.y, -up.x), is a pure rotation of the plane's own U/V basis
            # (determinant +1), so the image is only ever rotated -- never
            # mirrored -- regardless of where the camera sits.
            x2_unit = adsk.core.Vector2D.create(up2_unit.y, -up2_unit.x)

            # Preserve Fusion's default SCALE and, crucially, its visual CENTER.
            #
            # Matrix2D's origin here is the image's corner, not its center.  The
            # previous version kept that corner fixed while rotating X/Y, which
            # swung the whole image away from the selected face.  Compute the
            # old image center first, rotate the axes, then move the corner so
            # that the center remains exactly where Fusion originally placed it.
            default_matrix = ci.transform
            origin, default_x, default_y = default_matrix.getAsCoordinateSystem()
            width = default_x.length
            height = default_y.length

            if width < 1e-10 or height < 1e-10:
                log("auto-orient skipped: default Canvas has zero size")
                return False

            center_x = origin.x + 0.5 * default_x.x + 0.5 * default_y.x
            center_y = origin.y + 0.5 * default_x.y + 0.5 * default_y.y

            x_dir = adsk.core.Vector2D.create(
                x2_unit.x * width,
                x2_unit.y * width,
            )
            y_dir = adsk.core.Vector2D.create(
                up2_unit.x * height,
                up2_unit.y * height,
            )

            # New lower-left/corner position that preserves the old center.
            new_origin = adsk.core.Point2D.create(
                center_x - 0.5 * x_dir.x - 0.5 * y_dir.x,
                center_y - 0.5 * x_dir.y - 0.5 * y_dir.y,
            )

            matrix = adsk.core.Matrix2D.create()
            if not matrix.setWithCoordinateSystem(new_origin, x_dir, y_dir):
                log("auto-orient skipped: Fusion rejected Canvas matrix")
                return False

            ci.transform = matrix

            log(
                "canvas camera-oriented "
                "center=({:.4f},{:.4f}) "
                "origin=({:.4f},{:.4f}) "
                "size=({:.4f},{:.4f}) "
                "X=({:.4f},{:.4f}) Y=({:.4f},{:.4f})".format(
                    center_x, center_y,
                    new_origin.x, new_origin.y,
                    width, height,
                    x_dir.x, x_dir.y,
                    y_dir.x, y_dir.y,
                )
            )
            return True

        except Exception:
            log("canvas auto orientation failed\\n{}".format(m.traceback.format_exc()))
            return False

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

    # ---- attach: floating billboard ("node") -----------------------------
    def attach_node(mark):
        if mark is None:
            return

        stage(
            [{"label": "Choose the image", "done": False, "hint": "any picture file"}],
            0,
            "Floating image",
        )

        try:
            path = pick_image()
            if not path:
                return

            thumb = _make_thumb(path)
            mark.setdefault("images", []).append(
                {
                    "mode": "node",
                    "path": path,
                    "sprite_path": thumb or path,
                }
            )

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

    # ---- attach: native Canvas on a picked face ("face") -----------------
    def attach_face(mark):
        if mark is None:
            return

        steps = [
            {
                "label": "Select a planar face",
                "done": False,
                "hint": "where the image goes",
            },
            {
                "label": "Choose the image",
                "done": False,
            },
        ]
        stage(steps, 0, "Image on face")

        try:
            try:
                sel = m._ui.selectEntity(
                    "Select a planar face for the image",
                    "PlanarFaces",
                )
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

                # IMPORTANT:
                # Use the ORIGINAL image. Do not pre-rotate or pre-mirror pixels,
                # and do not call flipHorizontal/flipVertical. The Canvas matrix
                # itself handles orientation based on the current viewport.
                ci = comp.canvases.createInput(path, face)

                oriented = orient_canvas_to_view(ci, face)
                if not oriented:
                    log("placing Canvas with Fusion default orientation")

                canvas = comp.canvases.add(ci)

                # Remember it so accept/reject can delete it (it's a native doc
                # entity, not tied to the mark on its own).
                canvases_by_mark.setdefault(mark["id"], []).append(canvas)

                tok = None
                try:
                    tok = canvas.entityToken
                except Exception:
                    pass

                mark.setdefault("images", []).append(
                    {
                        "mode": "face",
                        "path": path,
                        "canvas_token": tok,
                    }
                )

                log("placed canvas on face for mark {}".format(mark.get("id")))
                m._send_state()

            except Exception:
                m._ui.messageBox(
                    "FuzzyCAD couldn't place the image on that face:\n{}".format(
                        m.traceback.format_exc()
                    )
                )

        finally:
            clear_stage()

    # ---- render the floating "node" images -------------------------------
    def node_end(mark):
        a = mark.get("anchor", [0.0, 0.0, 0.0])
        s = mark.get("size", 3.0)

        try:
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
        except Exception:
            (xx, xy, xz), (yx, yy, yz) = (1, 0, 0), (0, 1, 0)

        d = max(2.0, min(float(s) * 0.9, 9.0))
        return (
            a[0] + (0.85 * xx + 0.7 * yx) * d,
            a[1] + (0.85 * xy + 0.7 * yy) * d,
            a[2] + (0.85 * xz + 0.7 * yz) * d,
        )

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

            nodes = [
                im
                for im in (mark.get("images") or [])
                if im.get("mode") == "node" and not im.get("hidden")
            ]
            if not nodes:
                continue

            a = mark.get("anchor", [0.0, 0.0, 0.0])
            end = node_end(mark)

            # Leader line from the object out to the image.
            try:
                m._sketchy(
                    grp,
                    [tuple(a), end],
                    LEADER_RGB,
                    0.0,
                    int(mark.get("id", 0)) * 13,
                    weight=1,
                    strokes=1,
                )
            except Exception:
                pass

            for im in nodes:
                # (Re)generate the thumbnail when it is missing OR was made at a
                # different target size than the current THUMB_MAX, so an oversized
                # sprite from before a size change shrinks WITHOUT re-attaching.
                # Callout panels are owned by image_callout (skip here).
                sprite = im.get("sprite_path")
                if not im.get("callout"):
                    stale = (im.get("thumb_px") != THUMB_MAX
                             or not sprite or not os.path.exists(sprite))
                    if stale:
                        made = _make_thumb(im.get("path"))
                        if made:
                            sprite = made
                            im["sprite_path"] = made
                            im["thumb_px"] = THUMB_MAX
                        else:
                            # No Pillow -> cannot resize; use the full image once and
                            # stop retrying every redraw. It renders large; the log
                            # above says Pillow is missing.
                            sprite = im.get("path")
                            im["thumb_px"] = THUMB_MAX
                            log("floating image using FULL-SIZE source (no resize)")
                if sprite and not os.path.exists(sprite):
                    sprite = im.get("path")

                if not sprite:
                    continue

                try:
                    coords = adsk.fusion.CustomGraphicsCoordinates.create(list(end))
                    grp.addPointSet(
                        coords,
                        [0],
                        adsk.fusion.CustomGraphicsPointTypes.UserDefinedCustomGraphicsPointType,
                        sprite,
                    )
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

    # Also register the floating-image billboard as a persistent OVERLAY. The
    # authoritative render owner (fuzzycad_visual_transition.py) replaces
    # _redraw_marks with a single transaction that does NOT call the historical
    # wrappers, so the redraw wrapper above is bypassed whenever that owner is
    # installed -- which made floating images disappear. The overlay hook runs
    # draw_nodes inside that transaction. (The wrapper stays as a fallback for
    # builds without the render owner; draw_nodes clears its own group first, so
    # running twice is harmless.)
    overlays = getattr(m, "_persistent_overlays", None)
    if overlays is None:
        overlays = []
        m._persistent_overlays = overlays
    overlays.append(draw_nodes)

    # ---- delete a mark's images when it is accepted OR rejected ----------
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

    # ---- surface image thumbnails + hidden state on the card -------------
    _thumb_uri_cache = {}   # sprite/panel path -> base64 data URI (in-memory only,
                            #   never persisted, so the saved document stays small)

    def image_thumb_uri(im):
        path = im.get("sprite_path") or im.get("path")
        if not path:
            return None
        uri = _thumb_uri_cache.get(path)
        if uri:
            return uri
        try:
            if os.path.exists(path):
                import base64
                with open(path, "rb") as fh:
                    uri = "data:image/png;base64," + base64.b64encode(
                        fh.read()).decode("ascii")
                _thumb_uri_cache[path] = uri
                return uri
        except Exception:
            pass
        return None

    def public(mark):
        out = old_public(mark)
        try:
            imgs = []
            for i, im in enumerate(mark.get("images") or []):
                entry = {
                    "index": i,
                    "mode": im.get("mode"),
                    "callout": bool(im.get("callout")),
                    "hidden": bool(im.get("hidden")),
                }
                if im.get("mode") == "node":
                    # A small base64 thumbnail so the card can show the picture
                    # (the webview can't read local file paths).
                    entry["thumb_uri"] = image_thumb_uri(im)
                imgs.append(entry)
            out["images"] = imgs
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

                if act == "toggleImageNode":
                    # Per-card show/hide for one floating image. Flips hidden, then
                    # redraws (draw_nodes skips hidden) and refreshes the card.
                    data = json.loads(e.data) if e.data else {}
                    mark = m._find(data.get("id"))
                    idx = data.get("index")
                    if mark is not None and idx is not None:
                        imgs = mark.get("images") or []
                        try:
                            i = int(idx)
                        except Exception:
                            i = -1
                        if 0 <= i < len(imgs):
                            imgs[i]["hidden"] = not imgs[i].get("hidden")
                            try:
                                m._redraw_marks()
                            except Exception:
                                pass
                            try:
                                m._send_state()
                            except Exception:
                                pass
                    try:
                        e.returnData = json.dumps({"ok": True})
                    except Exception:
                        pass
                    return

            except Exception:
                log("image action failed\n{}".format(m.traceback.format_exc()))

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    log("IMAGE ATTACH READY (node billboard + face canvas, camera-oriented)")
