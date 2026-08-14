"""Geometry-aware sparse surface scaffolds for smooth FuzzyCAD proposals.

BRep topology edges are enough to describe boxes and other edge-rich solids, but
smooth bodies such as cylinders can collapse visually to only two circular edge
loops once the proposed body fill is removed. This patch adds a small number of
lightweight surface iso-curves only when topology is too sparse to communicate
volume. Appearance is owned by fuzzycad_visual_system.py.
"""

import math


def install(m):
    adsk = m.adsk
    old_run = m.run
    old_stop = m.stop

    MAX_BODY_CURVES = 7
    MAX_CURVED_FACES = 2
    ISO_SAMPLES = 18

    state = {"cache": {}, "draws": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SCAFFOLD] " + msg)
        except Exception:
            pass

    def body_token(body):
        try:
            return body.entityToken
        except Exception:
            return str(id(body))

    def body_fingerprint(body):
        try:
            bb = body.boundingBox
            dims = (
                round(bb.minPoint.x, 4), round(bb.minPoint.y, 4), round(bb.minPoint.z, 4),
                round(bb.maxPoint.x, 4), round(bb.maxPoint.y, 4), round(bb.maxPoint.z, 4),
            )
        except Exception:
            dims = ()
        try: ec = body.edges.count
        except Exception: ec = -1
        try: fc = body.faces.count
        except Exception: fc = -1
        return (body_token(body), ec, fc, dims)

    def surface_type(face):
        try:
            return str(face.geometry.objectType or "")
        except Exception:
            return ""

    def is_curved(face):
        typ = surface_type(face).lower()
        if "plane" in typ:
            return False
        return True

    def curved_faces(body):
        out = []
        try:
            for i in range(body.faces.count):
                face = body.faces.item(i)
                if is_curved(face):
                    out.append(face)
        except Exception:
            pass
        return out

    def should_scaffold(body, curved):
        if body is None or not curved:
            return False
        try: edges = int(body.edges.count)
        except Exception: edges = 999
        try: faces = max(1, int(body.faces.count))
        except Exception: faces = max(1, len(curved))
        if edges <= 14:
            return True
        curved_ratio = float(len(curved)) / float(faces)
        return curved_ratio >= 0.60 and edges <= 28

    def range_box(ev):
        try:
            pr = ev.parametricRange()
            if isinstance(pr, tuple):
                pr = pr[-1]
            return pr
        except Exception:
            return None

    def point_on_face(ev, u, v):
        try:
            param = adsk.core.Point2D.create(float(u), float(v))
            try:
                if not ev.isParameterOnFace(param):
                    return None
            except Exception:
                pass
            ok, p = ev.getPointAtParameter(param)
            if ok and p is not None:
                return (p.x, p.y, p.z)
        except Exception:
            pass
        return None

    def longest_segment(points):
        best = []
        cur = []
        for p in points:
            if p is None:
                if len(cur) > len(best): best = cur
                cur = []
            else:
                cur.append(p)
        if len(cur) > len(best): best = cur
        return best if len(best) >= 3 else []

    def iso_curve(face, const_axis, fraction):
        try:
            ev = face.evaluator
            pr = range_box(ev)
            if pr is None:
                return []
            u0, u1 = float(pr.minPoint.x), float(pr.maxPoint.x)
            v0, v1 = float(pr.minPoint.y), float(pr.maxPoint.y)
            vals = []
            for i in range(ISO_SAMPLES + 1):
                t = i / float(ISO_SAMPLES)
                if const_axis == "u":
                    u = u0 + (u1 - u0) * float(fraction)
                    v = v0 + (v1 - v0) * t
                else:
                    u = u0 + (u1 - u0) * t
                    v = v0 + (v1 - v0) * float(fraction)
                vals.append(point_on_face(ev, u, v))
            return longest_segment(vals)
        except Exception:
            return []

    def poly_length(poly):
        total = 0.0
        for i in range(1, len(poly)):
            a, b = poly[i - 1], poly[i]
            total += math.sqrt(sum((b[k] - a[k]) ** 2 for k in range(3)))
        return total

    def openness(poly):
        if len(poly) < 2:
            return 0.0
        path = poly_length(poly)
        if path <= 1e-9:
            return 0.0
        a, b = poly[0], poly[-1]
        chord = math.sqrt(sum((b[k] - a[k]) ** 2 for k in range(3)))
        return chord / path

    def curves_for_face(face):
        probe_u = iso_curve(face, "u", 0.5)
        probe_v = iso_curve(face, "v", 0.5)
        ou, ov = openness(probe_u), openness(probe_v)
        if max(ou, ov) < 0.18:
            plan = [("u", 0.28), ("u", 0.62), ("v", 0.32), ("v", 0.68)]
        elif ou >= ov:
            plan = [("u", 0.22), ("u", 0.50), ("u", 0.78), ("v", 0.50)]
        else:
            plan = [("v", 0.22), ("v", 0.50), ("v", 0.78), ("u", 0.50)]
        out = []
        for axis, frac in plan:
            poly = iso_curve(face, axis, frac)
            if len(poly) >= 3:
                out.append(poly)
        return out

    def scaffold_for_body(body):
        if body is None:
            return []
        key = body_fingerprint(body)
        if key in state["cache"]:
            return state["cache"][key]
        curved = curved_faces(body)
        if not should_scaffold(body, curved):
            state["cache"][key] = []
            return []
        curves = []
        for face in curved[:MAX_CURVED_FACES]:
            for poly in curves_for_face(face):
                curves.append(poly)
                if len(curves) >= MAX_BODY_CURVES:
                    break
            if len(curves) >= MAX_BODY_CURVES:
                break
        state["cache"][key] = curves
        log("CACHE body={} edges={} curved_faces={} scaffold_curves={}".format(
            getattr(body, "name", "body"),
            getattr(getattr(body, "edges", None), "count", -1),
            len(curved), len(curves)))
        return curves

    def scale_matrix(anchor, factors):
        fx, fy, fz = factors
        a = list(anchor or [0.0, 0.0, 0.0])
        mat = adsk.core.Matrix3D.create()
        mat.setCell(0, 0, fx); mat.setCell(1, 1, fy); mat.setCell(2, 2, fz)
        mat.translation = adsk.core.Vector3D.create(
            a[0] * (1.0 - fx), a[1] * (1.0 - fy), a[2] * (1.0 - fz))
        return mat

    def proposal_matrix(mark):
        tool = mark.get("tool")
        try:
            if tool in ("move", "rotate"):
                return m._op_matrix(mark)
            if tool == "scale":
                f = max(0.05, float(mark.get("factor", 1.0)))
                return scale_matrix(mark.get("anchor"), (f, f, f))
            if tool == "scale_axis":
                f = max(0.05, float(mark.get("factor", 1.0)))
                axis = mark.get("axis", "X")
                factors = (f, 1.0, 1.0) if axis == "X" else ((1.0, f, 1.0) if axis == "Y" else (1.0, 1.0, f))
                return scale_matrix(mark.get("base_anchor") or mark.get("anchor"), factors)
            if tool == "axis_rotate":
                origin = mark.get("axis_origin") or mark.get("anchor") or [0.0, 0.0, 0.0]
                direction = mark.get("axis_dir") or [0.0, 0.0, 1.0]
                mat = adsk.core.Matrix3D.create()
                mat.setToRotation(math.radians(float(mark.get("angle", 0.0))),
                                  adsk.core.Vector3D.create(*direction),
                                  adsk.core.Point3D.create(*origin))
                return mat
        except Exception:
            pass
        return None

    def transformed(poly, matrix):
        if matrix is None:
            return poly
        try:
            return m._apply_matrix(poly, matrix)
        except Exception:
            out = []
            for xyz in poly:
                p = adsk.core.Point3D.create(*xyz)
                p.transformBy(matrix)
                out.append((p.x, p.y, p.z))
            return out

    def draw_scaffold(group, body, matrix, mark, seed_offset=0):
        curves = scaffold_for_body(body)
        if not curves:
            return 0
        size = float(mark.get("size", 3.0))
        for i, poly in enumerate(curves):
            try:
                m._visual_stroke(group, transformed(poly, matrix), "surface_scaffold",
                                 mark.get("id", 1) * 31001 + seed_offset + i,
                                 size=size)
            except Exception:
                m._sketchy(group, transformed(poly, matrix), (142, 146, 150), 0.0,
                           mark.get("id", 1) * 31001 + seed_offset + i,
                           weight=1, strokes=1)
        return len(curves)

    for tool in ("move", "rotate", "scale", "scale_axis", "axis_rotate"):
        previous = m._DRAW.get(tool)
        if previous is None:
            continue
        def make_draw(prev, tool_name):
            def draw(group, mark, rgb, amp):
                prev(group, mark, rgb, amp)
                matrix = proposal_matrix(mark)
                body = m._body.get(mark.get("id"))
                count = draw_scaffold(group, body, matrix, mark, 0)
                if tool_name == "move" and mark.get("move_scope") == "together":
                    for idx, related in enumerate(mark.get("related_bodies") or []):
                        count += draw_scaffold(group, related, matrix, mark, (idx + 1) * 97)
                state["draws"] += 1
                if count and state["draws"] % 40 == 0:
                    log("DRAW tool={} curves={}".format(tool_name, count))
            return draw
        m._DRAW[tool] = make_draw(previous, tool)

    def run(context):
        result = old_run(context)
        log("SURFACE SCAFFOLD READY: style comes from visual_system surface_scaffold token")
        return result

    def stop(context):
        state["cache"].clear()
        return old_stop(context)

    m.run = run
    m.stop = stop
