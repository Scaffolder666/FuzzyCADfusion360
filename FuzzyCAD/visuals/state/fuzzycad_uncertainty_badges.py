"""Viewport badge visualization for FuzzyCAD uncertainty marks.

Badge placement is intentionally model-space stable:
- each badge gets one fixed position beside its related body;
- the leader starts on the body's +X bounding-box face and ends at the badge center;
- camera rotation, pan, and zoom do not recompute badge position;
- viewScale is used only to keep the badge itself readable on screen;
- multiple badges on the same body are stacked in model-space Z.

The badge primitives are local geometry translated to the exact leader endpoint,
so the badge and line cannot drift apart because of separate placement systems.
"""

import importlib.util
import os
import sys


# ~44 px wide triangle at normal focus (triangle width is about 1.84 local units).
BADGE_PIXEL_SCALE = 24.0
BADGE_FOCUS_SCALE = 1.15
LEADER_RGB = (42, 42, 42)
LEADER_WEIGHT = 2

# Fixed model-space placement. Fusion model units are cm.
FIXED_GAP_MIN_CM = 1.5
FIXED_GAP_MAX_CM = 4.0
FIXED_GAP_BODY_FRAC = 0.35
STACK_MIN_CM = 0.8
STACK_MAX_CM = 2.0
STACK_BODY_FRAC = 0.18


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

    def stack_index(mark, body):
        rows = badge_siblings(mark, body)
        try:
            idx = next(i for i, row in enumerate(rows)
                       if row.get("id") == mark.get("id"))
        except Exception:
            idx = 0
        return idx - (len(rows) - 1) * 0.5

    def badge_scale(mark):
        scale = BADGE_PIXEL_SCALE
        try:
            state = m._visual_state(mark)
            if state.get("phase") == "editing" or state.get("show_persistent_detail"):
                scale *= BADGE_FOCUS_SCALE
        except Exception:
            pass
        return scale

    def fixed_badge_layout(mark, body):
        """Return (leader_start, badge_center) in stable model coordinates.

        Position never depends on the camera. The badge lives on the +X side of the
        body's world-space bounding box. The decision anchor only chooses Y/Z on
        that face so the leader still points near the relevant part of the object.
        """
        anchor = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        if body is None:
            return anchor, (anchor[0] + 2.5, anchor[1], anchor[2])

        try:
            bb = body.boundingBox
            mn, mx = bb.minPoint, bb.maxPoint
            sx = max(float(mx.x - mn.x), 1.0e-6)
            sy = max(float(mx.y - mn.y), 1.0e-6)
            sz = max(float(mx.z - mn.z), 1.0e-6)
            body_size = max(sx, sy, sz, 1.0)

            # Clamp the stored decision anchor to the +X face of this body.
            ay = max(float(mn.y), min(float(mx.y), float(anchor[1])))
            az = max(float(mn.z), min(float(mx.z), float(anchor[2])))

            gap = max(FIXED_GAP_MIN_CM,
                      min(FIXED_GAP_MAX_CM, body_size * FIXED_GAP_BODY_FRAC))
            stack_step = max(STACK_MIN_CM,
                             min(STACK_MAX_CM, body_size * STACK_BODY_FRAC))
            dz = stack_index(mark, body) * stack_step

            start = (float(mx.x), ay, az)
            center = (float(mx.x) + gap, ay, az + dz)
            return start, center
        except Exception:
            return anchor, (anchor[0] + 2.5, anchor[1], anchor[2])

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
        """Draw badge geometry around local origin, then translate it to center.

        Camera axes are used only to keep the icon facing the viewer. They do not
        affect its model-space position.
        """
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
            xdir.normalize()
            ydir.normalize()
            zdir.normalize()
            xf = m.adsk.core.Matrix3D.create()
            xf.setWithCoordinateSystem(origin, xdir, ydir, zdir)
            line.transform = xf
        except Exception:
            # If camera orientation cannot be read, the local badge remains in its
            # default plane but still stays at the same center through the transform
            # path above whenever possible.
            try:
                xf = m.adsk.core.Matrix3D.create()
                xf.translation = m.adsk.core.Vector3D.create(
                    float(center[0]), float(center[1]), float(center[2]))
                line.transform = xf
            except Exception:
                pass

        try:
            # Autodesk defines viewScale in pixels around this local anchor.
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
                                 center, rgb, 5, scale)
            add_local_badge_line(group, [(0.28, 0.38), (0.28, -0.35)],
                                 center, rgb, 5, scale)
            return
        if mtype == "conflict":
            add_local_badge_line(group, [(-0.38, 0.32), (0.38, -0.32)],
                                 center, rgb, 5, scale)
            add_local_badge_line(group, [(0.38, 0.32), (-0.38, -0.32)],
                                 center, rgb, 5, scale)
            return
        add_local_badge_line(group, [(0.0, 0.45), (0.0, -0.16)],
                             center, rgb, 5, scale)
        add_local_badge_line(group, [(-0.045, -0.47), (0.045, -0.47)],
                             center, rgb, 6, scale)

    def draw_badge(group, mark):
        if not visible(mark):
            return

        body = primary_body(mark)
        mtype = presentation_type(mark)
        rgb = m.MTYPE_COLOR.get(
            mtype, getattr(m, "COLOR_WARN", (200, 44, 32)))
        scale = badge_scale(mark)
        leader_start, center = fixed_badge_layout(mark, body)

        # The leader and badge share exactly one model-space endpoint.
        try:
            add_world_line(group, leader_start, center,
                           LEADER_RGB, LEADER_WEIGHT)
        except Exception:
            pass

        try:
            add_local_badge_line(
                group,
                [(0.0, 1.0), (0.92, -0.72), (-0.92, -0.72), (0.0, 1.0)],
                center, rgb, 5, scale)
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

    # Notes use the same badge + leader. Avoid a duplicate legacy callout line.
    def draw_note_reload_safe(group, mark, rgb, amp):
        return

    try:
        m._DRAW["note"] = draw_note_reload_safe
    except Exception:
        pass

    # Remove the old camera-driven redraw hook if a previous version installed it.
    # Badge position is now model-space fixed and should not move when the camera does.
    try:
        app = m._app or m.adsk.core.Application.get()
        old_handler = getattr(m, "_badge_camera_handler", None)
        if old_handler is not None:
            try:
                app.cameraChanged.remove(old_handler)
            except Exception:
                pass
            m._badge_camera_handler = None
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

    log("BADGES READY: fixed model-space position + larger screen-size icon")
