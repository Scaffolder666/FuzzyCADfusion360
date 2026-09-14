"""Viewport badge visualization for FuzzyCAD uncertainty marks.

Badge rules:
- one screen-consistent badge size across marks;
- place badges just outside the projected subject bounds when possible;
- connect every badge from the subject boundary to the visible badge edge;
- stack multiple badges that belong to the same body instead of letting them overlap;
- keep the graphics vector-only because Fusion can reopen saved text/PNG billboards
  as white placeholder quads.

Badge lifecycle still comes from the central uncertainty visual authority.
"""

import importlib.util
import os
import sys


# CustomGraphicsViewScale interprets model-coordinate size in pixels. The vector
# triangle is ~1.84 units wide, so 19 gives a badge about 35 px wide at normal
# focus, large enough to read without dominating the model.
BADGE_PIXEL_SCALE = 19.0
BADGE_EDGE_GAP_PX = 9.0
BADGE_STACK_GAP_PX = 30.0
BADGE_FOCUS_SCALE = 1.15
# The leader endpoint sits inside the triangle instead of stopping exactly at the
# outline. Because the badge is drawn afterward, the overlap is hidden and reads
# as one continuous object-to-badge connection.
BADGE_SOCKET_RADIUS_UNITS = 0.38
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
            other = primary_body(candidate)
            if same_body_instance(body, other):
                rows.append(candidate)
        rows.sort(key=lambda row: int(row.get("id", 0) or 0))
        return rows or [mark]

    def stack_offset_px(mark, body):
        rows = badge_siblings(mark, body)
        try:
            idx = next(i for i, row in enumerate(rows) if row.get("id") == mark.get("id"))
        except Exception:
            idx = 0
        return (idx - (len(rows) - 1) * 0.5) * BADGE_STACK_GAP_PX

    def body_view_bounds(body, anchor_view):
        if body is None:
            return (anchor_view.x, anchor_view.x, anchor_view.y, anchor_view.y)
        try:
            bb = body.boundingBox
            mn, mx = bb.minPoint, bb.maxPoint
            pts = []
            for x in (mn.x, mx.x):
                for y in (mn.y, mx.y):
                    for z in (mn.z, mx.z):
                        q = m._app.activeViewport.modelToViewSpace(
                            m.adsk.core.Point3D.create(x, y, z))
                        if q is not None:
                            pts.append(q)
            if pts:
                return (
                    min(p.x for p in pts), max(p.x for p in pts),
                    min(p.y for p in pts), max(p.y for p in pts))
        except Exception:
            pass
        return (anchor_view.x, anchor_view.x, anchor_view.y, anchor_view.y)

    def model_delta_for_view_delta(anchor, dx_px, dy_px):
        """Convert a small screen-space offset into a model-space camera-plane offset."""
        try:
            vp = m._app.activeViewport
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            a = m.adsk.core.Point3D.create(*anchor)
            av = vp.modelToViewSpace(a)
            xp = m.adsk.core.Point3D.create(anchor[0] + xx, anchor[1] + xy, anchor[2] + xz)
            yp = m.adsk.core.Point3D.create(anchor[0] + yx, anchor[1] + yy, anchor[2] + yz)
            xv = vp.modelToViewSpace(xp)
            yv = vp.modelToViewSpace(yp)
            if av is None or xv is None or yv is None:
                return None

            xdx, xdy = xv.x - av.x, xv.y - av.y
            ydx, ydy = yv.x - av.x, yv.y - av.y
            det = xdx * ydy - xdy * ydx
            if abs(det) < 1.0e-9:
                return None

            cx = (dx_px * ydy - dy_px * ydx) / det
            cy = (xdx * dy_px - xdy * dx_px) / det
            return (
                cx * xx + cy * yx,
                cx * xy + cy * yy,
                cx * xz + cy * yz,
            )
        except Exception:
            return None

    def model_point_for_view_target(origin, target_x, target_y):
        try:
            vp = m._app.activeViewport
            ov = vp.modelToViewSpace(m.adsk.core.Point3D.create(*origin))
            if ov is None:
                return None
            delta = model_delta_for_view_delta(
                origin, float(target_x - ov.x), float(target_y - ov.y))
            if delta is None:
                return None
            return (
                origin[0] + delta[0],
                origin[1] + delta[1],
                origin[2] + delta[2],
            )
        except Exception:
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
        """Return badge center plus a leader start on the projected subject boundary."""
        anchor = list(mark.get("anchor") or [0.0, 0.0, 0.0])
        try:
            vp = m._app.activeViewport
            av = vp.modelToViewSpace(m.adsk.core.Point3D.create(*anchor))
            if av is None:
                raise RuntimeError("no view point")

            minx, maxx, miny, maxy = body_view_bounds(body, av)
            half_w = 0.92 * float(scale)
            half_h = 1.00 * float(scale)
            right_x = maxx + BADGE_EDGE_GAP_PX + half_w
            left_x = minx - BADGE_EDGE_GAP_PX - half_w

            if right_x + half_w + 6.0 <= float(vp.width):
                target_x = right_x
                edge_x = maxx
            else:
                target_x = max(half_w + 6.0, left_x)
                edge_x = minx

            target_y = (miny + maxy) * 0.5 + stack_offset_px(mark, body)
            target_y = max(half_h + 6.0,
                           min(float(vp.height) - half_h - 6.0, target_y))

            # Start the leader on the projected bounding edge, not at the internal
            # decision anchor. This makes the association read as object -> badge.
            edge_y = max(miny, min(maxy, target_y))
            center = model_point_for_view_target(anchor, target_x, target_y)
            leader_start = model_point_for_view_target(anchor, edge_x, edge_y)
            if center is not None:
                return center, (leader_start or tuple(anchor))
        except Exception:
            pass

        try:
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            s = float(mark.get("size", 3.0) or 3.0)
            off = max(0.8, min(s * 0.45, 2.6))
            center = (
                anchor[0] + xx * off + yx * off * 0.18,
                anchor[1] + xy * off + yy * off * 0.18,
                anchor[2] + xz * off + yz * off * 0.18,
            )
            return center, tuple(anchor)
        except Exception:
            return tuple(anchor), tuple(anchor)

    def badge_socket(center, leader_start, scale):
        """Return a point inside the visible badge toward the subject.

        The badge itself is view-scaled/billboarded while the leader is ordinary
        model-space graphics. Computing the socket in view space makes the two meet
        visually after zooming or rotating the camera. The endpoint intentionally
        overlaps the triangle interior so anti-aliasing cannot leave a visible gap.
        """
        try:
            vp = m._app.activeViewport
            cv = vp.modelToViewSpace(m.adsk.core.Point3D.create(*center))
            sv = vp.modelToViewSpace(m.adsk.core.Point3D.create(*leader_start))
            if cv is None or sv is None:
                return center
            dx, dy = float(sv.x - cv.x), float(sv.y - cv.y)
            ln = (dx * dx + dy * dy) ** 0.5
            if ln < 1.0e-6:
                return center
            radius_px = float(scale) * BADGE_SOCKET_RADIUS_UNITS
            delta = model_delta_for_view_delta(
                center, dx / ln * radius_px, dy / ln * radius_px)
            if delta is None:
                return center
            return (
                center[0] + delta[0],
                center[1] + delta[1],
                center[2] + delta[2],
            )
        except Exception:
            return center

    def add_lines(group, points, rgb, weight=2, view_scale=None, billboard_anchor=None):
        if not points or len(points) < 2:
            return None
        flat = []
        for p in points:
            flat.extend([float(p[0]), float(p[1]), float(p[2])])
        coords = m.adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(len(points))), True)
        line.color = m._solid(rgb)
        line.weight = int(weight)

        if view_scale is not None and billboard_anchor is not None:
            try:
                anchor_pt = m.adsk.core.Point3D.create(*billboard_anchor)
                line.viewScale = m.adsk.fusion.CustomGraphicsViewScale.create(
                    float(view_scale), anchor_pt)
            except Exception:
                pass
            try:
                anchor_pt = m.adsk.core.Point3D.create(*billboard_anchor)
                billboard = m.adsk.fusion.CustomGraphicsBillBoard.create(anchor_pt)
                billboard.billBoardStyle = (
                    m.adsk.fusion.CustomGraphicsBillBoardStyles.ScreenBillBoardStyle)
                line.billBoarding = billboard
            except Exception:
                pass
        return line

    def draw_symbol(group, center, mtype, rgb, scale):
        cx, cy, cz = center

        def P(x, y):
            return (cx + x, cy + y, cz)

        if mtype == "constraint":
            add_lines(group, [P(-0.28, 0.38), P(-0.28, -0.35)], rgb, 4, scale, center)
            add_lines(group, [P(0.28, 0.38), P(0.28, -0.35)], rgb, 4, scale, center)
            return

        if mtype == "conflict":
            add_lines(group, [P(-0.38, 0.32), P(0.38, -0.32)], rgb, 4, scale, center)
            add_lines(group, [P(0.38, 0.32), P(-0.38, -0.32)], rgb, 4, scale, center)
            return

        add_lines(group, [P(0.0, 0.45), P(0.0, -0.16)], rgb, 4, scale, center)
        add_lines(group, [P(-0.03, -0.47), P(0.03, -0.47)], rgb, 5, scale, center)

    def draw_badge(group, mark):
        if not visible(mark):
            return

        body = primary_body(mark)
        mtype = presentation_type(mark)
        rgb = m.MTYPE_COLOR.get(mtype, getattr(m, "COLOR_WARN", (200, 44, 32)))
        scale = badge_scale(mark)
        center, leader_start = badge_layout(mark, body, scale)
        leader_end = badge_socket(center, leader_start, scale)

        try:
            add_lines(group, [leader_start, leader_end], LEADER_RGB,
                      weight=LEADER_WEIGHT)
        except Exception:
            pass

        cx, cy, cz = center
        tri = [
            (cx, cy + 1.0, cz),
            (cx + 0.92, cy - 0.72, cz),
            (cx - 0.92, cy - 0.72, cz),
            (cx, cy + 1.0, cz),
        ]
        try:
            add_lines(group, tri, rgb, weight=4,
                      view_scale=scale, billboard_anchor=center)
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

    # Notes use the same badge + leader system now. Avoid drawing a second legacy
    # callout line that would compete with the unified leader.
    def draw_note_reload_safe(group, mark, rgb, amp):
        return

    try:
        m._DRAW["note"] = draw_note_reload_safe
    except Exception:
        pass

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

    log("BADGES READY: larger + darker leaders + edge-to-edge attachment")
