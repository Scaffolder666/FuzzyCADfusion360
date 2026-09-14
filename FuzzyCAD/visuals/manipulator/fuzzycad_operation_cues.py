"""Operation-specific visual cues for FuzzyCAD proposals.

The proposal outline communicates WHAT geometry may change. This layer makes
HOW it changes immediately legible.

Editing deliberately uses a stronger visual grammar than Proposed reveal:
- Move editing: an offset double-rail translation arrow with a large arrowhead.
- Rotate editing: a double-line rotation band, larger radius, arrowhead, and axis.
- Proposed reveal: keep the lighter legacy cue so persistent state stays quiet.
- Scale / Axis Scale / Extrude keep their existing semantic cues.

Orange is reserved for change locus/direction/control; proposal geometry stays gray.
"""

import math


def install(m):
    adsk = m.adsk
    old_run = m.run

    ORANGE = (225, 126, 38)
    QUIET = (105, 110, 116)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD CUES] " + msg)
        except Exception:
            pass

    def dist(a, b):
        return math.sqrt(sum((float(b[i]) - float(a[i])) ** 2 for i in range(3)))

    def add(a, b, s=1.0):
        return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)

    def normalized(v):
        n = math.sqrt(sum(float(x) * float(x) for x in v))
        if n < 1e-9:
            return (0.0, 0.0, 0.0)
        return tuple(float(x) / n for x in v)

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def is_editing(mark):
        try:
            return m._mark_phase(mark) == "editing"
        except Exception:
            try:
                return m._visual_state(mark).get("phase") == "editing"
            except Exception:
                return False

    def screen_side(direction):
        """A camera-friendly model-space vector perpendicular to direction."""
        d = normalized(direction)
        try:
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            # Prefer screen-up. If nearly parallel to the motion, use screen-right.
            side = cross(d, (yx, yy, yz))
            if sum(x * x for x in side) < 1e-7:
                side = cross(d, (xx, xy, xz))
            side = normalized(side)
            if sum(x * x for x in side) > 1e-7:
                return side
        except Exception:
            pass
        helper = (0.0, 0.0, 1.0)
        if abs(sum(d[i] * helper[i] for i in range(3))) > 0.9:
            helper = (0.0, 1.0, 0.0)
        return normalized(cross(d, helper))

    def solid_polyline(group, pts, rgb=ORANGE, weight=3, depth=30):
        """Crisp high-priority line strip for active Editing cues."""
        if not pts or len(pts) < 2:
            return None
        flat = []
        for p in pts:
            flat.extend([float(p[0]), float(p[1]), float(p[2])])
        coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
        line = group.addLines(coords, list(range(len(pts))), True)
        line.color = m._solid(rgb)
        line.weight = int(weight)
        try:
            line.depthPriority = int(depth)
        except Exception:
            pass
        return line

    def arrow_head(group, start, end, size, seed, rgb=ORANGE, strong=False):
        if dist(start, end) < 1e-7:
            return
        d = normalized((end[0] - start[0], end[1] - start[1], end[2] - start[2]))
        side = screen_side(d)
        if strong:
            h = max(0.30, min(float(size) * 0.14, 1.20))
            width = 0.62
        else:
            h = max(0.16, min(float(size) * 0.075, 0.72))
            width = 0.48
        base = add(end, d, -h)
        w1 = add(base, side, h * width)
        w2 = add(base, side, -h * width)
        if strong:
            solid_polyline(group, [w1, tuple(end), w2], rgb, weight=4, depth=35)
        else:
            m._sketchy(group, [w1, tuple(end), w2], rgb, 0.0, seed,
                       weight=2, strokes=1)

    def dashed(group, pts, seed, rgb=ORANGE):
        if len(pts) < 2:
            return
        for i in range(len(pts) - 1):
            if i % 2:
                continue
            m._sketchy(group, [pts[i], pts[i + 1]], rgb, 0.0,
                       seed + i, weight=1, strokes=1)

    def circle_arc(anchor, axis, radius, angle_deg):
        ax = m._axis_unit(axis)
        u = m._plane_basis(axis)
        w = (ax[1] * u[2] - ax[2] * u[1],
             ax[2] * u[0] - ax[0] * u[2],
             ax[0] * u[1] - ax[1] * u[0])
        steps = max(18, min(100, int(abs(float(angle_deg)) / 3.0) + 12))
        end = math.radians(float(angle_deg))
        pts = []
        for i in range(steps + 1):
            t = end * i / float(steps)
            c, s = math.cos(t), math.sin(t)
            pts.append((anchor[0] + radius * (c * u[0] + s * w[0]),
                        anchor[1] + radius * (c * u[1] + s * w[1]),
                        anchor[2] + radius * (c * u[2] + s * w[2])))
        return pts

    def arbitrary_arc(origin, direction, radius, angle_deg):
        n = adsk.core.Vector3D.create(*direction)
        try:
            n.normalize()
        except Exception:
            return []
        helper = adsk.core.Vector3D.create(1, 0, 0)
        if abs(n.dotProduct(helper)) > 0.85:
            helper = adsk.core.Vector3D.create(0, 1, 0)
        u = n.crossProduct(helper)
        u.normalize()
        v = n.crossProduct(u)
        v.normalize()
        steps = max(18, min(100, int(abs(float(angle_deg)) / 3.0) + 12))
        end = math.radians(float(angle_deg))
        pts = []
        for i in range(steps + 1):
            t = end * i / float(steps)
            c, s = math.cos(t), math.sin(t)
            pts.append((origin[0] + radius * (u.x * c + v.x * s),
                        origin[1] + radius * (u.y * c + v.y * s),
                        origin[2] + radius * (u.z * c + v.z * s)))
        return pts

    def transform_point(xyz, matrix):
        p = adsk.core.Point3D.create(*xyz)
        p.transformBy(matrix)
        return (p.x, p.y, p.z)

    def scale_matrix(anchor, factors):
        fx, fy, fz = factors
        a = list(anchor or [0.0, 0.0, 0.0])
        mat = adsk.core.Matrix3D.create()
        mat.setCell(0, 0, fx)
        mat.setCell(1, 1, fy)
        mat.setCell(2, 2, fz)
        mat.translation = adsk.core.Vector3D.create(
            a[0] * (1.0 - fx), a[1] * (1.0 - fy), a[2] * (1.0 - fz))
        return mat

    def farthest(mark):
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        best, best_d = tuple(a), -1.0
        for poly in m._geom.get(mark.get("id"), {}).get("edges", []):
            for p in poly:
                d = sum((p[i] - a[i]) ** 2 for i in range(3))
                if d > best_d:
                    best, best_d = p, d
        return best

    def draw_move_cue(group, mark):
        v = mark.get("vec") or [0.0, 0.0, 0.0]
        a = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        b = (a[0] + v[0], a[1] + v[1], a[2] + v[2])
        if dist(a, b) <= 1e-7:
            return

        size = float(mark.get("size", 3.0) or 3.0)
        seed = mark.get("id", 1) * 41001
        if not is_editing(mark):
            # Proposed reveal stays quiet. The base renderer already owns the shaft.
            arrow_head(group, a, b, size, seed)
            return

        # During Editing, separate the FuzzyCAD motion cue from Fusion's native
        # distance/manipulator line. Two parallel rails read as an operation band,
        # not another dimension line.
        d = normalized(v)
        side = screen_side(d)
        offset = max(0.28, min(size * 0.16, 1.25))
        half_band = max(0.10, min(size * 0.045, 0.34))

        center_a = add(a, side, offset)
        center_b = add(b, side, offset)
        rail1_a = add(center_a, side, half_band)
        rail1_b = add(center_b, side, half_band)
        rail2_a = add(center_a, side, -half_band)
        rail2_b = add(center_b, side, -half_band)

        solid_polyline(group, [rail1_a, rail1_b], ORANGE, weight=4, depth=35)
        solid_polyline(group, [rail2_a, rail2_b], ORANGE, weight=4, depth=35)
        solid_polyline(group, [rail1_a, rail2_a], ORANGE, weight=3, depth=35)
        arrow_head(group, center_a, center_b, size * 1.10, seed,
                   rgb=ORANGE, strong=True)

    def draw_rotate_cue(group, mark):
        rot = mark.get("rot") or [0.0, 0.0, 0.0]
        idx = max(range(3), key=lambda i: abs(rot[i]))
        angle = float(rot[idx])
        if abs(angle) < 1e-6:
            return

        axis = "XYZ"[idx]
        a = mark.get("anchor") or [0, 0, 0]
        size = float(mark.get("size", 3.0) or 3.0)
        seed = mark.get("id", 1) * 42001

        if is_editing(mark):
            # The old single dashed arc was too easy to confuse with construction
            # geometry. A double-line band is visually categorical and is placed
            # farther from the body/native manipulator.
            r = max(1.00, size * 1.05)
            band = max(0.18, min(size * 0.10, 0.62))
            inner = circle_arc(a, axis, max(0.35, r - band * 0.5), angle)
            outer = circle_arc(a, axis, r + band * 0.5, angle)
            mid = circle_arc(a, axis, r, angle)

            solid_polyline(group, inner, ORANGE, weight=4, depth=35)
            solid_polyline(group, outer, ORANGE, weight=4, depth=35)
            if inner and outer:
                solid_polyline(group, [inner[0], outer[0]], ORANGE, weight=3, depth=35)
                solid_polyline(group, [inner[-1], outer[-1]], ORANGE, weight=3, depth=35)
            if len(mid) >= 3:
                arrow_head(group, mid[-3], mid[-1], size * 1.18,
                           seed + 90, rgb=ORANGE, strong=True)

            unit = m._axis_unit(axis)
            h = max(1.0, size * 0.70)
            p0 = add(a, unit, -h)
            p1 = add(a, unit, h)
            solid_polyline(group, [p0, p1], QUIET, weight=2, depth=32)
            return

        # Proposed reveal remains light and sketch-like.
        r = max(0.7, size * 0.72)
        arc = circle_arc(a, axis, r, angle)
        dashed(group, arc, seed)
        if len(arc) >= 3:
            arrow_head(group, arc[-3], arc[-1], size, seed + 90)
        unit = m._axis_unit(axis)
        h = max(0.8, size * 0.55)
        p0 = add(a, unit, -h)
        p1 = add(a, unit, h)
        m._sketchy(group, [p0, p1], QUIET, 0.0,
                   seed + 94, weight=1, strokes=1)

    def draw_scale_cue(group, mark):
        f = max(0.05, float(mark.get("factor", 1.0)))
        if abs(f - 1.0) < 1e-5:
            return
        a = mark.get("anchor") or [0, 0, 0]
        p0 = farthest(mark)
        mat = scale_matrix(a, (f, f, f))
        p1 = transform_point(p0, mat)
        arrow_head(group, p0, p1, mark.get("size", 3.0),
                   mark.get("id", 1) * 43001,
                   strong=is_editing(mark))
        s = max(0.12, min(float(mark.get("size", 3.0)) * 0.035, 0.42))
        (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
        q0 = (a[0] - xx * s, a[1] - xy * s, a[2] - xz * s)
        q1 = (a[0] + xx * s, a[1] + xy * s, a[2] + xz * s)
        q2 = (a[0] - yx * s, a[1] - yy * s, a[2] - yz * s)
        q3 = (a[0] + yx * s, a[1] + yy * s, a[2] + yz * s)
        if is_editing(mark):
            solid_polyline(group, [q0, q1], ORANGE, weight=3, depth=35)
            solid_polyline(group, [q2, q3], ORANGE, weight=3, depth=35)
        else:
            m._sketchy(group, [q0, q1], ORANGE, 0.0,
                       mark.get("id", 1) * 43002, weight=1, strokes=1)
            m._sketchy(group, [q2, q3], ORANGE, 0.0,
                       mark.get("id", 1) * 43003, weight=1, strokes=1)

    def draw_axis_scale_cue(group, mark):
        f = max(0.05, float(mark.get("factor", 1.0)))
        axis = mark.get("axis", "X")
        side_name = mark.get("scale_side", "positive")
        base = mark.get("base_anchor") or mark.get("anchor") or [0, 0, 0]
        body = m._body.get(mark.get("id"))
        if body is None:
            return
        try:
            bb = body.boundingBox
            c = list(mark.get("anchor") or [0, 0, 0])
            idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            vals_min = [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z]
            vals_max = [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
            c[idx] = vals_min[idx] if side_name == "negative" else vals_max[idx]
            src = tuple(c)
        except Exception:
            return
        factors = ((f, 1.0, 1.0) if axis == "X" else
                   ((1.0, f, 1.0) if axis == "Y" else (1.0, 1.0, f)))
        dst = transform_point(src, scale_matrix(base, factors))
        if dist(src, dst) > 1e-7:
            if is_editing(mark):
                solid_polyline(group, [src, dst], ORANGE, weight=4, depth=35)
            else:
                m._sketchy(group, [src, dst], ORANGE, 0.0,
                           mark.get("id", 1) * 44001, weight=1, strokes=1)
            arrow_head(group, src, dst, mark.get("size", 3.0),
                       mark.get("id", 1) * 44002,
                       strong=is_editing(mark))

    def draw_axis_rotate_cue(group, mark):
        angle = float(mark.get("angle", 0.0))
        if abs(angle) < 1e-6:
            return
        g = m._geom.get(mark.get("id"), {})
        origin = g.get("axis_origin") or mark.get("axis_origin") or [0, 0, 0]
        direction = g.get("axis_dir") or mark.get("axis_dir") or [0, 0, 1]
        size = float(mark.get("size", 3.0) or 3.0)
        seed = mark.get("id", 1) * 45001

        if is_editing(mark):
            r = max(1.00, size * 1.05)
            band = max(0.18, min(size * 0.10, 0.62))
            inner = arbitrary_arc(origin, direction, max(0.35, r - band * 0.5), angle)
            outer = arbitrary_arc(origin, direction, r + band * 0.5, angle)
            mid = arbitrary_arc(origin, direction, r, angle)
            solid_polyline(group, inner, ORANGE, weight=4, depth=35)
            solid_polyline(group, outer, ORANGE, weight=4, depth=35)
            if inner and outer:
                solid_polyline(group, [inner[0], outer[0]], ORANGE, weight=3, depth=35)
                solid_polyline(group, [inner[-1], outer[-1]], ORANGE, weight=3, depth=35)
            if len(mid) >= 3:
                arrow_head(group, mid[-3], mid[-1], size * 1.18,
                           seed + 90, rgb=ORANGE, strong=True)
            return

        r = max(0.7, size * 0.72)
        arc = arbitrary_arc(origin, direction, r, angle)
        dashed(group, arc, seed)
        if len(arc) >= 3:
            arrow_head(group, arc[-3], arc[-1], size, seed + 90)

    def draw_extrude_cue(group, mark):
        g = m._geom.get(mark.get("id"), {})
        d = g.get("normal") or [0.0, 0.0, 1.0]
        amt = float(mark.get("amount", 0.0))
        a = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        b = (a[0] + d[0] * amt, a[1] + d[1] * amt, a[2] + d[2] * amt)
        if dist(a, b) > 1e-7:
            if is_editing(mark):
                solid_polyline(group, [a, b], ORANGE, weight=4, depth=35)
            arrow_head(group, a, b, mark.get("size", 3.0),
                       mark.get("id", 1) * 46001,
                       strong=is_editing(mark))

    cue_by_tool = {
        "move": draw_move_cue,
        "rotate": draw_rotate_cue,
        "scale": draw_scale_cue,
        "scale_axis": draw_axis_scale_cue,
        "axis_rotate": draw_axis_rotate_cue,
        "extrude": draw_extrude_cue,
    }

    for tool, cue in cue_by_tool.items():
        previous = m._DRAW.get(tool)
        if previous is None:
            continue

        def make_draw(prev, cue_fn):
            def draw(group, mark, rgb, amp):
                prev(group, mark, rgb, amp)
                try:
                    cue_fn(group, mark)
                except Exception:
                    pass
            return draw

        m._DRAW[tool] = make_draw(previous, cue)

    def run(context):
        result = old_run(context)
        log("OPERATION CUES READY: bold Editing bands / quiet Proposed cues")
        return result

    m.run = run
