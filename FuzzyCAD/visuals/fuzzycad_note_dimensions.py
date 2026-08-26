"""Screen-facing notes and lightweight CAD-style size dimensions.

This patch keeps annotation text readable while the camera rotates and adds a
small three-axis bounding-size frame for the currently manipulated candidate.
The size frame is deliberately limited to the active proposal so persistent
marks do not turn the viewport into a dense engineering drawing and redraw cost
stays low.
"""

DIM_RGB = (92, 92, 92)
NOTE_RGB = (77, 77, 77)


def install(m):
    adsk = m.adsk
    old_draw_one = m._draw_one
    old_run = m.run

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD ANNOTATION] " + msg)
        except Exception:
            pass

    def screen_billboard(entity, anchor):
        """Use Fusion's native screen billboard explicitly.

        Do not depend on the older namespace fallback helper here. Autodesk's
        CustomGraphicsBillBoard ScreenBillBoardStyle keeps the graphics XY plane
        parallel to the view plane as the camera rotates.
        """
        try:
            bb = adsk.fusion.CustomGraphicsBillBoard.create(anchor)
            bb.billBoardStyle = adsk.fusion.CustomGraphicsBillBoardStyles.ScreenBillBoardStyle
            entity.billBoarding = bb
            return True
        except Exception as exc:
            log("billboard failed: {}".format(exc))
            return False

    def note_text(mark):
        txt = (mark.get("text") or "").strip().replace("\n", " ")
        if not txt:
            return "(note)"
        return txt if len(txt) <= 48 else txt[:46] + "…"

    def draw_note(group, mark, rgb, amp):
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        s = mark.get("size", 3.0)
        (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
        off = max(1.0, min(s * 0.9, 3.2))
        tip = (
            a[0] + (0.22 * xx + 0.94 * yx) * off,
            a[1] + (0.22 * xy + 0.94 * yy) * off,
            a[2] + (0.22 * xz + 0.94 * yz) * off,
        )

        # The leader remains in model space; only the label itself billboards.
        m._sketchy(group, [tuple(a), tip], rgb, max(0.01, amp),
                   mark["id"] * 3001, weight=2, strokes=1)

        cp = adsk.core.Point3D.create(*tip)
        text = group.addText(
            note_text(mark), "Arial", max(0.48, min(s * 0.16, 0.95)),
            m._label_transform(cp))
        text.color = m._solid(rgb)
        screen_billboard(text, cp)

    # Notes use their callout as the annotation, so no generic tool label is
    # necessary. Replacing the dispatch entry means existing open notes redraw
    # with the corrected behavior too.
    m._DRAW["note"] = draw_note

    def matrix_for(mark):
        tool = mark.get("tool")
        if tool in ("move", "rotate"):
            return m._op_matrix(mark)
        if tool == "scale":
            f = max(0.05, float(mark.get("factor", 1.0)))
            a = mark.get("anchor") or [0.0, 0.0, 0.0]
            mat = adsk.core.Matrix3D.create()
            mat.setCell(0, 0, f)
            mat.setCell(1, 1, f)
            mat.setCell(2, 2, f)
            mat.translation = adsk.core.Vector3D.create(
                a[0] * (1.0 - f), a[1] * (1.0 - f), a[2] * (1.0 - f))
            return mat
        return None

    def transformed_bbox(mark):
        """Return candidate axis-aligned bbox as (min_xyz, max_xyz)."""
        tool = mark.get("tool")

        if tool == "extrude":
            body = m._geom.get(mark["id"], {}).get("extrude_candidate_body")
            if body is not None:
                try:
                    bb = body.boundingBox
                    return ((bb.minPoint.x, bb.minPoint.y, bb.minPoint.z),
                            (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z))
                except Exception:
                    pass
            return None

        if tool not in ("move", "rotate", "scale"):
            return None
        body = m._body.get(mark["id"])
        if body is None:
            return None
        try:
            bb = body.boundingBox
            mn, mx = bb.minPoint, bb.maxPoint
            corners = []
            for x in (mn.x, mx.x):
                for y in (mn.y, mx.y):
                    for z in (mn.z, mx.z):
                        corners.append(adsk.core.Point3D.create(x, y, z))
            mat = matrix_for(mark)
            if mat is not None:
                for p in corners:
                    p.transformBy(mat)
            xs = [p.x for p in corners]
            ys = [p.y for p in corners]
            zs = [p.z for p in corners]
            return ((min(xs), min(ys), min(zs)),
                    (max(xs), max(ys), max(zs)))
        except Exception:
            return None

    def active_mark(mark):
        try:
            return mark.get("id") in set(m._live.values())
        except Exception:
            return False

    def add_dim_text(group, point, text, seed):
        cp = adsk.core.Point3D.create(*point)
        t = group.addText(text, "Arial", 0.46, m._label_transform(cp))
        t.color = m._solid(DIM_RGB)
        screen_billboard(t, cp)

    def draw_tick(group, p, axis, scale, seed):
        # Tiny perpendicular tick. Kept in model space so it still reads as a
        # dimension line rather than another UI badge.
        if axis == 0:
            q1 = (p[0], p[1] - scale, p[2])
            q2 = (p[0], p[1] + scale, p[2])
        elif axis == 1:
            q1 = (p[0] - scale, p[1], p[2])
            q2 = (p[0] + scale, p[1], p[2])
        else:
            q1 = (p[0] - scale, p[1], p[2])
            q2 = (p[0] + scale, p[1], p[2])
        m._sketchy(group, [q1, q2], DIM_RGB, 0.0, seed,
                   weight=1, strokes=1)

    def draw_size_frame(group, mark):
        # Main operation values are already shown by the unified visualization
        # (Move/Rotate/Scale/Extrude callouts). This is secondary CAD context and
        # is shown only for the currently manipulated proposal.
        if not active_mark(mark):
            return
        if mark.get("tool") not in ("move", "rotate", "scale", "extrude"):
            return
        bbox = transformed_bbox(mark)
        if bbox is None:
            return
        mn, mx = bbox
        dims = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
        span = max(max(dims), 0.1)
        offset = max(0.18, min(span * 0.035, 0.8))
        tick = max(0.10, min(span * 0.018, 0.35))

        # Offset the three dimension edges slightly outside the candidate bbox.
        lines = [
            ((mn[0], mn[1] - offset, mn[2] - offset),
             (mx[0], mn[1] - offset, mn[2] - offset), 0, dims[0], "X"),
            ((mn[0] - offset, mn[1], mn[2] - offset),
             (mn[0] - offset, mx[1], mn[2] - offset), 1, dims[1], "Y"),
            ((mn[0] - offset, mn[1] - offset, mn[2]),
             (mn[0] - offset, mn[1] - offset, mx[2]), 2, dims[2], "Z"),
        ]

        for i, (p0, p1, axis, length, label) in enumerate(lines):
            if length <= 1e-5:
                continue
            seed = mark["id"] * 4100 + i * 17
            m._sketchy(group, [p0, p1], DIM_RGB, 0.0, seed,
                       weight=1, strokes=1)
            draw_tick(group, p0, axis, tick, seed + 1)
            draw_tick(group, p1, axis, tick, seed + 2)
            mid = ((p0[0] + p1[0]) * 0.5,
                   (p0[1] + p1[1]) * 0.5,
                   (p0[2] + p1[2]) * 0.5)
            add_dim_text(group, mid, "{} {:.1f} mm".format(label, length * 10.0), seed + 3)

    # ---- rough-shape L/W/H: automatic, oriented, always on -----------------
    def oriented_box(body):
        """Oriented bounding box aligned to the body's own axes.

        getOrientedBoundingBox fits the box to the orientation we hand it, so we
        derive that orientation from the body's largest planar face (its normal
        plus an in-plane edge). Falls back to world axes for a face-less lump.
        """
        try:
            app = m._app or adsk.core.Application.get()
            mgr = getattr(app, "measureManager", None)
            if mgr is None:
                return None
            l_dir = w_dir = None
            try:
                best, best_area = None, -1.0
                for f in body.faces:
                    g = f.geometry
                    if isinstance(g, adsk.core.Plane) and f.area > best_area:
                        best, best_area = f, f.area
                if best is not None:
                    n = best.geometry.normal
                    for e in best.edges:
                        sp = e.startVertex.geometry
                        ep = e.endVertex.geometry
                        d = adsk.core.Vector3D.create(
                            ep.x - sp.x, ep.y - sp.y, ep.z - sp.z)
                        if d.length > 1e-6:
                            d.normalize()
                            w = n.crossProduct(d)
                            if w.length > 1e-6:
                                w.normalize()
                                l_dir, w_dir = d, w
                                break
            except Exception:
                pass
            if l_dir is None:
                l_dir = adsk.core.Vector3D.create(1.0, 0.0, 0.0)
                w_dir = adsk.core.Vector3D.create(0.0, 1.0, 0.0)
            return mgr.getOrientedBoundingBox(body, l_dir, w_dir)
        except Exception as exc:
            log("oriented bbox failed: {}".format(exc))
            return None

    def draw_rough_dims(group, mark):
        # Rough shapes are all about intended size, so show L/W/H automatically
        # for the whole (open) body -- no manipulation and no manual picking.
        if mark.get("tool") != "rough" or mark.get("status", "open") != "open":
            return
        body = m._body.get(mark["id"])
        if body is None:
            return
        obb = oriented_box(body)
        if obb is None:
            log("rough dims: no oriented box for mark {}".format(mark.get("id")))
            return
        log("rough dims mark {} L/W/H = {:.2f}/{:.2f}/{:.2f} cm".format(
            mark.get("id"), obb.length, obb.width, obb.height))

        c = obb.centerPoint
        L, W, H = obb.lengthDirection, obb.widthDirection, obb.heightDirection
        hl, hw, hh = obb.length * 0.5, obb.width * 0.5, obb.height * 0.5

        def at(sx, sy, sz):
            return (c.x + sx * hl * L.x + sy * hw * W.x + sz * hh * H.x,
                    c.y + sx * hl * L.y + sy * hw * W.y + sz * hh * H.y,
                    c.z + sx * hl * L.z + sy * hw * W.z + sz * hh * H.z)

        # Label the three edges meeting at the corner nearest the camera, so the
        # values sit on the visible front of the body rather than behind it.
        try:
            eye = (m._app or adsk.core.Application.get()).activeViewport.camera.eye
        except Exception:
            eye = c
        best_sgn, best_d = (1, 1, 1), None
        for sx in (1, -1):
            for sy in (1, -1):
                for sz in (1, -1):
                    p = at(sx, sy, sz)
                    d = (p[0] - eye.x) ** 2 + (p[1] - eye.y) ** 2 + (p[2] - eye.z) ** 2
                    if best_d is None or d < best_d:
                        best_d, best_sgn = d, (sx, sy, sz)
        sx, sy, sz = best_sgn

        size = max(mark.get("size", 3.0), 0.1)
        off = max(0.15, min(size * 0.06, 0.8))

        def place(mid, out, value_cm, seed):
            ol = (out[0] ** 2 + out[1] ** 2 + out[2] ** 2) ** 0.5 or 1.0
            p = (mid[0] + out[0] / ol * off,
                 mid[1] + out[1] / ol * off,
                 mid[2] + out[2] / ol * off)
            add_dim_text(group, p, "{:.1f} mm".format(value_cm * 10.0), seed)

        base = mark["id"] * 4700
        # length edge: runs along L, fixed at the near-corner's W/H side
        if obb.length > 1e-4:
            mid = (c.x + sy * hw * W.x + sz * hh * H.x,
                   c.y + sy * hw * W.y + sz * hh * H.y,
                   c.z + sy * hw * W.z + sz * hh * H.z)
            out = (sy * W.x + sz * H.x, sy * W.y + sz * H.y, sy * W.z + sz * H.z)
            place(mid, out, obb.length, base + 1)
        if obb.width > 1e-4:
            mid = (c.x + sx * hl * L.x + sz * hh * H.x,
                   c.y + sx * hl * L.y + sz * hh * H.y,
                   c.z + sx * hl * L.z + sz * hh * H.z)
            out = (sx * L.x + sz * H.x, sx * L.y + sz * H.y, sx * L.z + sz * H.z)
            place(mid, out, obb.width, base + 2)
        if obb.height > 1e-4:
            mid = (c.x + sx * hl * L.x + sy * hw * W.x,
                   c.y + sx * hl * L.y + sy * hw * W.y,
                   c.z + sx * hl * L.z + sy * hw * W.z)
            out = (sx * L.x + sy * W.x, sx * L.y + sy * W.y, sx * L.z + sy * W.z)
            place(mid, out, obb.height, base + 3)

    def draw_one(group, mark):
        old_draw_one(group, mark)
        try:
            draw_size_frame(group, mark)
        except Exception as exc:
            log("size frame failed mark={}: {}".format(mark.get("id"), exc))
        try:
            draw_rough_dims(group, mark)
        except Exception as exc:
            log("rough dims failed mark={}: {}".format(mark.get("id"), exc))

    m._draw_one = draw_one

    def run(context):
        result = old_run(context)
        log("SCREEN-FACING NOTES + ACTIVE SIZE DIMENSIONS READY")
        return result

    m.run = run
