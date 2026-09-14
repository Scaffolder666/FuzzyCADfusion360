"""Viewport badge visualization for FuzzyCAD uncertainty marks.

Badge placement is intentionally model-space stable, but obstacle-aware:
- each badge gets one fixed model-space position beside its related body;
- several nearby candidate sides are tested instead of always using +X;
- candidates that overlap nearby visible body bounding boxes are rejected first;
- leaders that would pass through nearby bodies are strongly penalized;
- the overall visible-model bounding box biases placement toward an outer side;
- camera rotation, pan, and zoom never recompute badge position;
- viewScale is used only to keep the badge itself readable on screen;
- multiple badges on the same body are stacked in model space.

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

# Base model-space placement. Fusion model units are cm.
FIXED_GAP_MIN_CM = 1.8
FIXED_GAP_MAX_CM = 4.6
FIXED_GAP_BODY_FRAC = 0.40
STACK_MIN_CM = 0.8
STACK_MAX_CM = 2.0
STACK_BODY_FRAC = 0.18

# Obstacle-aware placement. The badge itself is view-scaled, so this is a small
# model-space safety envelope around its anchor, not an attempt to convert pixels
# into centimeters. It simply keeps the anchor comfortably outside nearby solids.
BADGE_CLEAR_MIN_CM = 0.55
BADGE_CLEAR_MAX_CM = 1.5
BADGE_CLEAR_BODY_FRAC = 0.12
LEADER_CLEAR_CM = 0.08
PUSH_MULTIPLIERS = (1.0, 1.45, 2.0, 2.8)
MAX_OBSTACLE_BODIES = 500


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

    def body_bbox(body):
        """Return the tightest available axis-aligned world/context bounding box."""
        if body is None:
            return None
        try:
            bb = body.preciseBoundingBox
            if bb is not None:
                return bb
        except Exception:
            pass
        try:
            return body.boundingBox
        except Exception:
            return None

    def bbox_tuple(bb):
        if bb is None:
            return None
        try:
            mn, mx = bb.minPoint, bb.maxPoint
            return (float(mn.x), float(mn.y), float(mn.z),
                    float(mx.x), float(mx.y), float(mx.z))
        except Exception:
            return None

    def bbox_union(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
                max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))

    def bbox_intersects(a, b, pad=0.0):
        if a is None or b is None:
            return False
        p = float(pad)
        return not (
            a[3] + p < b[0] or a[0] - p > b[3] or
            a[4] + p < b[1] or a[1] - p > b[4] or
            a[5] + p < b[2] or a[2] - p > b[5])

    def point_box(center, half):
        x, y, z = center
        h = float(half)
        return (x - h, y - h, z - h, x + h, y + h, z + h)

    def segment_box(a, b, pad=0.0):
        p = float(pad)
        return (min(a[0], b[0]) - p,
                min(a[1], b[1]) - p,
                min(a[2], b[2]) - p,
                max(a[0], b[0]) + p,
                max(a[1], b[1]) + p,
                max(a[2], b[2]) + p)

    def body_is_visible(body):
        try:
            return bool(body.isVisible)
        except Exception:
            return True

    def obstacle_boxes(target_body):
        """Visible root-context body boxes, excluding the badge's own body instance.

        Occurrence.bRepBodies returns proxies in assembly context, so their boxes
        describe where the user actually sees those bodies in the root assembly.
        """
        design = None
        try:
            design = m._design()
        except Exception:
            pass
        if design is None:
            return []

        rows = []

        def consider(body):
            if len(rows) >= MAX_OBSTACLE_BODIES:
                return
            if body is None or same_body_instance(target_body, body):
                return
            if not body_is_visible(body):
                return
            bt = bbox_tuple(body_bbox(body))
            if bt is not None:
                rows.append(bt)

        try:
            root = design.rootComponent
            bodies = root.bRepBodies
            for i in range(bodies.count):
                consider(bodies.item(i))
        except Exception:
            pass

        try:
            occs = design.rootComponent.allOccurrences
            for i in range(occs.count):
                if len(rows) >= MAX_OBSTACLE_BODIES:
                    break
                occ = occs.item(i)
                try:
                    if not occ.isVisible:
                        continue
                except Exception:
                    pass
                try:
                    bodies = occ.bRepBodies
                    for j in range(bodies.count):
                        consider(bodies.item(j))
                except Exception:
                    continue
        except Exception:
            pass

        return rows

    def clamp(v, lo, hi):
        return max(float(lo), min(float(hi), float(v)))

    def face_start(target, anchor, direction):
        """Point on the chosen target bounding-box face nearest the mark anchor."""
        x0, y0, z0, x1, y1, z1 = target
        ax, ay, az = anchor
        name = direction[0]
        if name == "+X":
            return (x1, clamp(ay, y0, y1), clamp(az, z0, z1))
        if name == "-X":
            return (x0, clamp(ay, y0, y1), clamp(az, z0, z1))
        if name == "+Y":
            return (clamp(ax, x0, x1), y1, clamp(az, z0, z1))
        if name == "-Y":
            return (clamp(ax, x0, x1), y0, clamp(az, z0, z1))
        if name == "+Z":
            return (clamp(ax, x0, x1), clamp(ay, y0, y1), z1)
        return (clamp(ax, x0, x1), clamp(ay, y0, y1), z0)

    def stack_vector(direction):
        name = direction[0]
        # Side badges stack vertically in world Z. Top/bottom badges stack in Y.
        if name in ("+Z", "-Z"):
            return (0.0, 1.0, 0.0)
        return (0.0, 0.0, 1.0)

    def distance_to_global_edge(target, overall, direction):
        if overall is None:
            return 0.0
        name = direction[0]
        if name == "+X":
            return max(0.0, overall[3] - target[3])
        if name == "-X":
            return max(0.0, target[0] - overall[0])
        if name == "+Y":
            return max(0.0, overall[4] - target[4])
        if name == "-Y":
            return max(0.0, target[1] - overall[1])
        if name == "+Z":
            return max(0.0, overall[5] - target[5])
        return max(0.0, target[2] - overall[2])

    def center_outside_overall(center, overall, clearance):
        if overall is None:
            return False
        x, y, z = center
        c = float(clearance)
        return (x < overall[0] - c or x > overall[3] + c or
                y < overall[1] - c or y > overall[4] + c or
                z < overall[2] - c or z > overall[5] + c)

    def obstacle_aware_badge_layout(mark, body):
        """Choose a nearby free model-space side for this badge.

        The search is intentionally small and deterministic. It prefers a short
        leader, but avoids putting the badge inside another visible body. The
        global model box only biases which free side is preferable; it does not
        force every badge all the way to the outside of the whole assembly.
        """
        anchor = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        target = bbox_tuple(body_bbox(body))
        if target is None:
            return anchor, (anchor[0] + 2.5, anchor[1], anchor[2])

        sx = max(target[3] - target[0], 1.0e-6)
        sy = max(target[4] - target[1], 1.0e-6)
        sz = max(target[5] - target[2], 1.0e-6)
        body_size = max(sx, sy, sz, 1.0)
        gap = max(FIXED_GAP_MIN_CM,
                  min(FIXED_GAP_MAX_CM, body_size * FIXED_GAP_BODY_FRAC))
        stack_step = max(STACK_MIN_CM,
                         min(STACK_MAX_CM, body_size * STACK_BODY_FRAC))
        clearance = max(BADGE_CLEAR_MIN_CM,
                        min(BADGE_CLEAR_MAX_CM,
                            body_size * BADGE_CLEAR_BODY_FRAC))
        stack = stack_index(mark, body) * stack_step

        obstacles = obstacle_boxes(body)
        overall = target
        for row in obstacles:
            overall = bbox_union(overall, row)

        # Keep +X first for visual continuity with the previous version, but let
        # collision/global-box scoring move the badge when that side is occupied.
        directions = (
            ("+X", (1.0, 0.0, 0.0)),
            ("-X", (-1.0, 0.0, 0.0)),
            ("+Y", (0.0, 1.0, 0.0)),
            ("-Y", (0.0, -1.0, 0.0)),
            ("+Z", (0.0, 0.0, 1.0)),
            ("-Z", (0.0, 0.0, -1.0)),
        )

        best = None
        for rank, direction in enumerate(directions):
            start = face_start(target, anchor, direction)
            dx, dy, dz = direction[1]
            sv = stack_vector(direction)
            edge_cost = distance_to_global_edge(target, overall, direction)

            for push_rank, mult in enumerate(PUSH_MULTIPLIERS):
                d = gap * float(mult)
                center = (start[0] + dx * d + sv[0] * stack,
                          start[1] + dy * d + sv[1] * stack,
                          start[2] + dz * d + sv[2] * stack)

                badge_box = point_box(center, clearance)
                leader_box = segment_box(start, center, LEADER_CLEAR_CM)
                badge_hit = any(bbox_intersects(badge_box, ob) for ob in obstacles)
                leader_hit = any(bbox_intersects(leader_box, ob) for ob in obstacles)
                outside = center_outside_overall(center, overall, clearance)

                # Collision dominates. Then prefer a leader that does not pass
                # through another object, then a side already near the assembly
                # exterior, then the shorter/earlier candidate.
                score = 0.0
                if badge_hit:
                    score += 10000.0
                if leader_hit:
                    score += 1800.0
                if outside:
                    score -= 120.0
                score += (edge_cost / max(body_size, 1.0)) * 14.0
                score += d * 0.8
                score += rank * 0.35 + push_rank * 0.15

                row = (score, start, center)
                if best is None or row[0] < best[0]:
                    best = row

                # The closest clean candidate on this side is enough; no reason
                # to keep pushing it farther away.
                if not badge_hit and not leader_hit:
                    break

        if best is not None:
            return best[1], best[2]
        return anchor, (anchor[0] + gap, anchor[1], anchor[2])

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
            try:
                xf = m.adsk.core.Matrix3D.create()
                xf.translation = m.adsk.core.Vector3D.create(
                    float(center[0]), float(center[1]), float(center[2]))
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
                             center, rgb, 4, scale)
        add_local_badge_line(group, [(-0.045, -0.47), (0.045, -0.47)],
                             center, rgb, 5, scale)

    def draw_badge(group, mark):
        if not visible(mark):
            return

        body = primary_body(mark)
        mtype = presentation_type(mark)
        rgb = m.MTYPE_COLOR.get(
            mtype, getattr(m, "COLOR_WARN", (200, 44, 32)))
        scale = badge_scale(mark)
        leader_start, center = obstacle_aware_badge_layout(mark, body)

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
    # Badge position is model-space fixed and should not move when the camera does.
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

    log("BADGES READY: stable model-space position + obstacle-aware placement")
