"""Operation-specific visual cues for FuzzyCAD proposals.

The proposal outline communicates WHAT geometry may change.  This layer makes
HOW it changes immediately legible:
- Move: one large translation arrow.
- Rotate: a dashed sweep ruler + arrowhead around the active axis.
- Scale: a fixed/pivot cue plus an outward/inward stretch arrow.
- Axis Rotate: the same dashed sweep around the selected arbitrary axis.
- Extrude: a depth arrow along the face normal.

Orange is reserved for change locus/direction; proposal geometry stays gray.
"""

import math


def install(m):
    adsk = m.adsk
    old_run = m.run

    ORANGE = (225, 126, 38)
    QUIET = (125, 130, 135)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
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

    def arrow_head(group, start, end, size, seed, rgb=ORANGE):
        if dist(start, end) < 1e-7:
            return
        d = normalized((end[0] - start[0], end[1] - start[1], end[2] - start[2]))
        try:
            (_, _), = ()
        except Exception:
            pass
        # Pick a screen-friendly side vector so the arrowhead remains readable
        # from oblique CAD camera angles.
        try:
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            helper = (yx, yy, yz)
            side = (d[1] * helper[2] - d[2] * helper[1],
                    d[2] * helper[0] - d[0] * helper[2],
                    d[0] * helper[1] - d[1] * helper[0])
            if sum(x * x for x in side) < 1e-7:
                helper = (xx, xy, xz)
                side = (d[1] * helper[2] - d[2] * helper[1],
                        d[2] * helper[0] - d[0] * helper[2],
                        d[0] * helper[1] - d[1] * helper[0])
            side = normalized(side)
        except Exception:
            side = (0.0, 1.0, 0.0)
        h = max(0.16, min(float(size) * 0.075, 0.72))
        base = add(end, d, -h)
        w1 = add(base, side, h * 0.48)
        w2 = add(base, side, -h * 0.48)
        m._sketchy(group, [w1, tuple(end), w2], rgb, 0.0, seed,
                   weight=2, strokes=1)

    def dashed(group, pts, seed, rgb=ORANGE):
        if len(pts) < 2:
            return
        # Draw alternating small segments.  This is intentionally separate from
        # the native manipulator ring: it reads as an annotation/ruler.
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
        steps = max(14, min(88, int(abs(float(angle_deg)) / 4.0) + 8))
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
        try: n.normalize()
        except Exception: return []
        helper = adsk.core.Vector3D.create(1, 0, 0)
        if abs(n.dotProduct(helper)) > 0.85:
            helper = adsk.core.Vector3D.create(0, 1, 0)
        u = n.crossProduct(helper); u.normalize()
        v = n.crossProduct(u); v.normalize()
        steps = max(14, min(88, int(abs(float(angle_deg)) / 4.0) + 8))
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
        mat.setCell(0, 0, fx); mat.setCell(1, 1, fy); mat.setCell(2, 2, fz)
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
        if dist(a, b) > 1e-7:
            # Main renderer already draws the shaft; add the semantic arrowhead.
            arrow_head(group, a, b, mark.get("size", 3.0), mark.get("id", 1) * 41001)

    def draw_rotate_cue(group, mark):
        rot = mark.get("rot") or [0.0, 0.0, 0.0]
        idx = max(range(3), key=lambda i: abs(rot[i]))
        angle = float(rot[idx])
        if abs(angle) < 1e-6:
            return
        axis = "XYZ"[idx]
        r = max(0.7, float(mark.get("size", 3.0)) * 0.72)
        arc = circle_arc(mark.get("anchor") or [0, 0, 0], axis, r, angle)
        dashed(group, arc, mark.get("id", 1) * 42001)
        if len(arc) >= 3:
            arrow_head(group, arc[-3], arc[-1], mark.get("size", 3.0),
                       mark.get("id", 1) * 42091)
        # A quiet axis line makes the sweep's center unambiguous.
        unit = m._axis_unit(axis)
        a = mark.get("anchor") or [0, 0, 0]
        h = max(0.8, float(mark.get("size", 3.0)) * 0.55)
        p0 = add(a, unit, -h); p1 = add(a, unit, h)
        m._sketchy(group, [p0, p1], QUIET, 0.0,
                   mark.get("id", 1) * 42095, weight=1, strokes=1)

    def draw_scale_cue(group, mark):
        f = max(0.05, float(mark.get("factor", 1.0)))
        if abs(f - 1.0) < 1e-5:
            return
        a = mark.get("anchor") or [0, 0, 0]
        p0 = farthest(mark)
        mat = scale_matrix(a, (f, f, f))
        p1 = transform_point(p0, mat)
        arrow_head(group, p0, p1, mark.get("size", 3.0), mark.get("id", 1) * 43001)
        # Scale owns a visible pivot; Move deliberately does not.
        s = max(0.12, min(float(mark.get("size", 3.0)) * 0.035, 0.42))
        (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
        q0 = (a[0] - xx * s, a[1] - xy * s, a[2] - xz * s)
        q1 = (a[0] + xx * s, a[1] + xy * s, a[2] + xz * s)
        q2 = (a[0] - yx * s, a[1] - yy * s, a[2] - yz * s)
        q3 = (a[0] + yx * s, a[1] + yy * s, a[2] + yz * s)
        m._sketchy(group, [q0, q1], ORANGE, 0.0, mark.get("id", 1) * 43002, weight=1, strokes=1)
        m._sketchy(group, [q2, q3], ORANGE, 0.0, mark.get("id", 1) * 43003, weight=1, strokes=1)

    def draw_axis_scale_cue(group, mark):
        f = max(0.05, float(mark.get("factor", 1.0)))
        axis = mark.get("axis", "X")
        side = mark.get("scale_side", "positive")
        base = mark.get("base_anchor") or mark.get("anchor") or [0, 0, 0]
        body = m._body.get(mark.get("id"))
        if body is None:
            return
        try:
            bb = body.boundingBox
            c = list(mark.get("anchor") or [0, 0, 0])
            idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            if side == "negative":
                c[idx] = bb.minPoint.asArray()[idx] if hasattr(bb.minPoint, "asArray") else [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z][idx]
            else:
                c[idx] = bb.maxPoint.asArray()[idx] if hasattr(bb.maxPoint, "asArray") else [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z][idx]
            src = tuple(c)
        except Exception:
            return
        factors = (f, 1.0, 1.0) if axis == "X" else ((1.0, f, 1.0) if axis == "Y" else (1.0, 1.0, f))
        dst = transform_point(src, scale_matrix(base, factors))
        if dist(src, dst) > 1e-7:
            # This arrow originates at the moving side, while the opposite-side
            # fixed marker comes from the scale-scope renderer.
            m._sketchy(group, [src, dst], ORANGE, 0.0,
                       mark.get("id", 1) * 44001, weight=1, strokes=1)
            arrow_head(group, src, dst, mark.get("size", 3.0), mark.get("id", 1) * 44002)

    def draw_axis_rotate_cue(group, mark):
        angle = float(mark.get("angle", 0.0))
        if abs(angle) < 1e-6:
            return
        g = m._geom.get(mark.get("id"), {})
        origin = g.get("axis_origin") or mark.get("axis_origin") or [0, 0, 0]
        direction = g.get("axis_dir") or mark.get("axis_dir") or [0, 0, 1]
        r = max(0.7, float(mark.get("size", 3.0)) * 0.72)
        arc = arbitrary_arc(origin, direction, r, angle)
        dashed(group, arc, mark.get("id", 1) * 45001)
        if len(arc) >= 3:
            arrow_head(group, arc[-3], arc[-1], mark.get("size", 3.0),
                       mark.get("id", 1) * 45091)

    def draw_extrude_cue(group, mark):
        g = m._geom.get(mark.get("id"), {})
        d = g.get("normal") or [0.0, 0.0, 1.0]
        amt = float(mark.get("amount", 0.0))
        a = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        b = (a[0] + d[0] * amt, a[1] + d[1] * amt, a[2] + d[2] * amt)
        if dist(a, b) > 1e-7:
            arrow_head(group, a, b, mark.get("size", 3.0), mark.get("id", 1) * 46001)

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
                try: cue_fn(group, mark)
                except Exception: pass
            return draw
        m._DRAW[tool] = make_draw(previous, cue)

    def run(context):
        result = old_run(context)
        log("OPERATION CUES READY: Move arrow / Rotate dashed sweep / Scale stretch / Extrude depth")
        return result

    m.run = run
