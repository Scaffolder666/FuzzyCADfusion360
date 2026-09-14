"""Viewport badge visualization for FuzzyCAD uncertainty marks.

Keep badge placement deliberately simple:
- project the related body to viewport pixels;
- choose one pixel position just outside that projected body;
- convert that position back to ONE model-space endpoint;
- draw the leader to that endpoint;
- draw a local, view-scaled badge translated to exactly the same endpoint;
- recompute the layout whenever the Fusion camera changes.

The badge does not use CustomGraphicsBillBoard anchoring. Fusion has retired the
billboard anchor argument, so relying on it can make the visible badge drift away
from the model-space endpoint used by the leader.
"""

import importlib.util
import os
import sys


BADGE_PIXEL_SCALE = 19.0
VISIBLE_LEADER_PX = 34.0
BADGE_STACK_GAP_PX = 34.0
BADGE_FOCUS_SCALE = 1.15
LEADER_RGB = (42, 42, 42)
LEADER_WEIGHT = 2


def install(m):
    old_icon_path = m._icon_path
    m._VIEWPORT_LABELS = False

    try:
        m.MTYPE_LABEL["conflict"] = "Conflict"
        m.MTYPE_COLOR["conflict"] = (128, 90, 180)
        m.MTYPE_GLYPH["conflict"] = u"⑂"
    except Exception:
        pass

    def icon_path(mtype):
        if mtype == "constraint":
            try:
                icon_dir = getattr(m, "_ICON_DIR", None)
                if icon_dir:
                    p = os.path.join(icon_dir, "constraint.png")
                    if os.path.exists(p):
                        return p
            except Exception:
                pass
        return old_icon_path(mtype)

    m._icon_path = icon_path

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD BADGES] " + msg)
        except Exception:
            pass

    def visible(mark):
        if mark is None:
            return False
        try:
            return bool(m._visual_state(mark).get("show_badge"))
        except Exception:
            return mark.get("status", "open") == "open"

    def presentation_type(mark):
        tool = mark.get("tool")
        if tool == "note":
            return "constraint"
        if tool == "compare":
            return "conflict"
        return mark.get("mtype", "need_input")

    def same_entity(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        try:
            return bool(a == b)
        except Exception:
            return False

    def native_body(body):
        if body is None:
            return None
        try:
            native = body.nativeObject
            return native if native is not None else body
        except Exception:
            return body

    def body_occurrence(body):
        try:
            return m.adsk.fusion.Occurrence.cast(body.assemblyContext)
        except Exception:
            return None

    def same_body_instance(a, b):
        if same_entity(a, b):
            return True
        if a is None or b is None:
            return False
        if not same_entity(native_body(a), native_body(b)):
            return False
        oa, ob = body_occurrence(a), body_occurrence(b)
        if oa is None and ob is None:
            return True
        if oa is None or ob is None:
            return False
        return same_entity(oa, ob)

    def subject_bodies(mark):
        try:
            fn = getattr(m, "_visual_subject_bodies", None)
            if fn is not None:
                rows = list(fn(mark) or [])
                if rows:
                    return rows
        except Exception:
            pass
        mid = mark.get("id")
        try:
            body = m._body.get(mid)
            if body is not None:
                return [body]
        except Exception:
            pass
        try:
            ent = m._entity.get(mid)
            body = m._entity_body(ent)
            if body is not None:
                return [body]
        except Exception:
            pass
        return []

    def primary_body(mark):
        rows = subject_bodies(mark)
        return rows[0] if rows else None

    def badge_siblings(mark, body):
        if body is None:
            return [mark]
        rows = []
        for candidate in list(getattr(m, "_marks", None) or []):
            if not visible(candidate):
                continue
            if same_body_instance(body, primary_body(candidate)):
                rows.append(candidate)
        rows.sort(key=lambda row: int(row.get("id", 0) or 0))
        return rows or [mark]

    def stack_offset_px(mark, body):
        rows = badge_siblings(mark, body)
        try:
            idx = next(i for i, row in enumerate(rows)
                       if row.get("id") == mark.get("id"))
        except Exception:
            idx = 0
        return (idx - (len(rows) - 1) * 0.5) * BADGE_STACK_GAP_PX

    def body_view_bounds(body, fallback_view):
        if body is None:
            return (fallback_view.x, fallback_view.x,
                    fallback_view.y, fallback_view.y)
        try:
            bb = body.boundingBox
            mn, mx = bb.minPoint, bb.maxPoint
            pts = []
            vp = m._app.activeViewport
            for x in (mn.x, mx.x):
                for y in (mn.y, mx.y):
                    for z in (mn.z, mx.z):
                        p = vp.modelToViewSpace(
                            m.adsk.core.Point3D.create(x, y, z))
                        if p is not None:
                            pts.append(p)
            if pts:
                return (min(p.x for p in pts), max(p.x for p in pts),
                        min(p.y for p in pts), max(p.y for p in pts))
        except Exception:
            pass
        return (fallback_view.x, fallback_view.x,
                fallback_view.y, fallback_view.y)

    def view_target_at_reference_depth(reference, target_x, target_y):
        try:
            vp = m._app.activeViewport
            xf = vp.modelToViewSpaceTransform
            p = m.adsk.core.Point3D.create(*reference)
            if not p.transformBy(xf):
                raise RuntimeError("model->view transform failed")
            p.x = float(target_x)
            p.y = float(target_y)
            inv = xf.copy()
            if not inv.invert():
                raise RuntimeError("view transform not invertible")
            if not p.transformBy(inv):
                raise RuntimeError("view->model transform failed")
            return (p.x, p.y, p.z)
        except Exception:
            try:
                q = m._app.activeViewport.viewToModelSpace(
                    m.adsk.core.Point2D.create(float(target_x), float(target_y)))
                if q is not None:
                    return (q.x, q.y, q.z)
            except Exception:
                pass
        return None

    def badge_scale(mark):
        scale = BADGE_PIXEL_SCALE
        try:
            state = m._visual_state(mark)
            if state.get("phase") == "editing" or state.get("show_persistent_detail"):
                scale *= BADGE_FOCUS_SCALE
        except Exception:
            pass
        return scale

    def badge_layout(mark, body, scale):
        anchor = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        try:
            vp = m._app.activeViewport
            av = vp.modelToViewSpace(m.adsk.core.Point3D.create(*anchor))
            if av is None:
                raise RuntimeError("anchor is not projectable")

            minx, maxx, miny, maxy = body_view_bounds(body, av)
            half_badge_w = 0.92 * float(scale)
            half_badge_h = 1.00 * float(scale)

            anchor_y = max(miny, min(maxy, float(av.y)))
            target_y = anchor_y + stack_offset_px(mark, body)
            target_y = max(half_badge_h + 8.0,
                           min(float(vp.height) - half_badge_h - 8.0, target_y))

            right_center_x = maxx + VISIBLE_LEADER_PX + half_badge_w
            left_center_x = minx - VISIBLE_LEADER_PX - half_badge_w

            if right_center_x + half_badge_w + 8.0 <= float(vp.width):
                target_x = right_center_x
                edge_x = maxx
            else:
                target_x = max(half_badge_w + 8.0, left_center_x)
                edge_x = minx

            edge_y = max(miny, min(maxy, target_y))
            center = view_target_at_reference_depth(anchor, target_x, target_y)
            start = view_target_at_reference_depth(anchor, edge_x, edge_y)
            if center is not None and start is not None:
                return start, center
        except Exception:
            pass

        try:
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            s = float(mark.get("size", 3.0) or 3.0)
            off = max(1.2, min(s * 0.65, 4.0))
            center = (anchor[0] + xx * off,
                      anchor[1] + xy * off,
                      anchor[2] + xz * off)
            return anchor, center
        except Exception:
            return anchor, anchor

    def add_world_line(group, start, end, rgb, weight):
        coords = m.adsk.fusion.CustomGraphicsCoordinates.create([
            float(start[0]), float(start[1]), float(start[2]),
            float(end[0]), float(end[1]), float(end[2]),
        ])
        line = group.addLines(coords, [0, 1], True)
        line.color = m._solid(rgb)
        line.weight = int(weight)
        try:
            line.depthPriority = 10
        except Exception:
            pass
        return line

    def add_local_badge_line(group, points, center, rgb, weight, scale):
        flat = []
        for x, y in points:
            flat.extend([float(x), float(y), 0.0])
        coords = m.adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(len(points))), True)
        line.color = m._solid(rgb)
        line.weight = int(weight)

        try:
            origin = m.adsk.core.Point3D.create(*center)
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            xdir = m.adsk.core.Vector3D.create(xx, xy, xz)
            ydir = m.adsk.core.Vector3D.create(yx, yy, yz)
            zdir = xdir.crossProduct(ydir)
            xdir.normalize(); ydir.normalize(); zdir.normalize()
            xf = m.adsk.core.Matrix3D.create()
            xf.setWithCoordinateSystem(origin, xdir, ydir, zdir)
            line.transform = xf
        except Exception:
            pass

        try:
            line.viewScale = m.adsk.fusion.CustomGraphicsViewScale.create(
                float(scale), m.adsk.core.Point3D.create(0.0, 0.0, 0.0))
        except Exception:
            pass
        try:
            line.depthPriority = 20
        except Exception:
            pass
        return line

    def draw_symbol(group, center, mtype, rgb, scale):
        if mtype == "constraint":
            add_local_badge_line(group, [(-0.28, 0.38), (-0.28, -0.35)],
                                 center, rgb, 4, scale)
            add_local_badge_line(group, [(0.28, 0.38), (0.28, -0.35)],
                                 center, rgb, 4, scale)
            return
        if mtype == "conflict":
            add_local_badge_line(group, [(-0.38, 0.32), (0.38, -0.32)],
                                 center, rgb, 4, scale)
            add_local_badge_line(group, [(0.38, 0.32), (-0.38, -0.32)],
                                 center, rgb, 4, scale)
            return
        add_local_badge_line(group, [(0.0, 0.45), (0.0, -0.16)],
                             center, rgb, 4, scale)
        add_local_badge_line(group, [(-0.04, -0.47), (0.04, -0.47)],
                             center, rgb, 5, scale)

    def draw_badge(group, mark):
        if not visible(mark):
            return

        body = primary_body(mark)
        mtype = presentation_type(mark)
        rgb = m.MTYPE_COLOR.get(
            mtype, getattr(m, "COLOR_WARN", (200, 44, 32)))
        scale = badge_scale(mark)
        leader_start, center = badge_layout(mark, body, scale)

        try:
            add_world_line(group, leader_start, center,
                           LEADER_RGB, LEADER_WEIGHT)
        except Exception:
            pass

        try:
            add_local_badge_line(
                group,
                [(0.0, 1.0), (0.92, -0.72), (-0.92, -0.72), (0.0, 1.0)],
                center, rgb, 4, scale)
            draw_symbol(group, center, mtype, rgb, scale)
        except Exception:
            try:
                old = getattr(m, "_legacy_badge_fallback", None)
                if old is not None:
                    old(group, mark)
            except Exception:
                pass

    try:
        m._legacy_badge_fallback = m._draw_badge
    except Exception:
        pass
    m._draw_badge = draw_badge

    def draw_note_reload_safe(group, mark, rgb, amp):
        return

    try:
        m._DRAW["note"] = draw_note_reload_safe
    except Exception:
        pass

    # Badge placement is view-dependent, so recompute it after every camera
    # change. Without this, rotating/panning/zooming leaves badges at positions
    # calculated for the previous view.
    try:
        app = m._app or m.adsk.core.Application.get()
        old_handler = getattr(m, "_badge_camera_handler", None)
        if old_handler is not None:
            try:
                app.cameraChanged.remove(old_handler)
            except Exception:
                pass

        class BadgeCameraChangedHandler(m.adsk.core.CameraEventHandler):
            def __init__(self):
                super().__init__()

            def notify(self, args):
                if bool(getattr(m, "_badge_camera_redrawing", False)):
                    return
                try:
                    m._badge_camera_redrawing = True
                    m._redraw_marks()
                except Exception:
                    pass
                finally:
                    m._badge_camera_redrawing = False

        handler = BadgeCameraChangedHandler()
        if app.cameraChanged.add(handler):
            m._badge_camera_handler = handler
            try:
                m._handlers.append(handler)
            except Exception:
                pass
    except Exception:
        log("cameraChanged handler install failed")

    try:
        root = os.path.dirname(os.path.abspath(m.__file__))
        path = os.path.join(root, "core", "persistence", "fuzzycad_save_clean.py")
        spec = importlib.util.spec_from_file_location("fuzzycad_save_clean", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fuzzycad_save_clean"] = mod
        spec.loader.exec_module(mod)
        mod.install(m)
    except Exception:
        try:
            log("save-clean guard failed\n{}".format(m.traceback.format_exc()))
        except Exception:
            pass

    log("BADGES READY: exact endpoint + exact view transform + camera refresh")
