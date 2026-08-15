"""View-dependent silhouette overlay for FuzzyCAD proposals.

Topology edges are a poor shape cue for smooth solids. A cylinder can collapse
into two circular loops even though the viewer clearly perceives two longitudinal
contours. This module adds a Drawing-like apparent-contour layer for transformed
proposals without meshing the body or filling its surfaces.

The silhouette is geometry/view driven only. Sidebar state transmission is kept
independent so numeric card updates never trigger an unrelated 3D rebuild.
"""

import math
import time

GROUP_ID = "FuzzyCAD_Silhouette"


def install(m):
    adsk = m.adsk
    old_redraw_marks = m._redraw_marks
    old_run = m.run
    old_stop = m.stop

    SILHOUETTE_RGB = (72, 76, 80)
    GRID_U = 12
    GRID_V = 9
    MAX_CURVED_FACES = 3
    MAX_CURVES_PER_BODY = 8
    VIEW_QUANT = 0.08

    state = {
        "cache": {},
        "busy": False,
        "last_camera_draw": 0.0,
        "draws": 0,
        "camera_handler": None,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SILHOUETTE] " + msg)
        except Exception:
            pass

    def body_token(body):
        try:
            return body.entityToken
        except Exception:
            return str(id(body))

    def curved_face(face):
        try:
            typ = str(face.geometry.objectType or "").lower()
            return "plane" not in typ
        except Exception:
            return True

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
                factors = ((f, 1.0, 1.0) if axis == "X" else
                           ((1.0, f, 1.0) if axis == "Y" else (1.0, 1.0, f)))
                return scale_matrix(mark.get("base_anchor") or mark.get("anchor"), factors)
            if tool == "axis_rotate":
                g = m._geom.get(mark.get("id"), {})
                origin = g.get("axis_origin") or mark.get("axis_origin") or mark.get("anchor") or [0, 0, 0]
                direction = g.get("axis_dir") or mark.get("axis_dir") or [0, 0, 1]
                mat = adsk.core.Matrix3D.create()
                mat.setToRotation(
                    math.radians(float(mark.get("angle", 0.0))),
                    adsk.core.Vector3D.create(*direction),
                    adsk.core.Point3D.create(*origin))
                return mat
        except Exception:
            pass
        return None

    def camera_vector():
        try:
            cam = m._app.activeViewport.camera
            v = adsk.core.Vector3D.create(
                cam.eye.x - cam.target.x,
                cam.eye.y - cam.target.y,
                cam.eye.z - cam.target.z)
            if v.length < 1e-9:
                return adsk.core.Vector3D.create(0, 0, 1)
            v.normalize()
            return v
        except Exception:
            return adsk.core.Vector3D.create(0, 0, 1)

    def source_view(matrix):
        v = camera_vector()
        if matrix is not None:
            try:
                inv = matrix.copy()
                if inv.invert():
                    v.transformBy(inv)
            except Exception:
                pass
        try:
            if v.length > 1e-9:
                v.normalize()
        except Exception:
            pass
        return v

    def qview(v):
        def q(x):
            return round(float(x) / VIEW_QUANT) * VIEW_QUANT
        return (q(v.x), q(v.y), q(v.z))

    def param_range(ev):
        try:
            box = ev.parametricRange()
            if isinstance(box, tuple):
                box = box[-1]
            vals = [box.minPoint.x, box.maxPoint.x, box.minPoint.y, box.maxPoint.y]
            if any((not math.isfinite(float(x)) or abs(float(x)) > 1.0e8) for x in vals):
                return None
            return tuple(float(x) for x in vals)
        except Exception:
            return None

    def sample(ev, u, v, view):
        try:
            uv = adsk.core.Point2D.create(float(u), float(v))
            if not ev.isParameterOnFace(uv):
                return None
            okp, p = ev.getPointAtParameter(uv)
            okn, n = ev.getNormalAtParameter(uv)
            if not okp or not okn or p is None or n is None:
                return None
            s = n.x * view.x + n.y * view.y + n.z * view.z
            return ((p.x, p.y, p.z), float(s))
        except Exception:
            return None

    def lerp_cross(a, b):
        if a is None or b is None:
            return None
        p0, s0 = a; p1, s1 = b
        if s0 == 0.0:
            s0 = 1.0e-12
        if s1 == 0.0:
            s1 = -1.0e-12
        if s0 * s1 > 0.0:
            return None
        den = abs(s0) + abs(s1)
        if den < 1.0e-12:
            return None
        t = abs(s0) / den
        return (p0[0] + (p1[0] - p0[0]) * t,
                p0[1] + (p1[1] - p0[1]) * t,
                p0[2] + (p1[2] - p0[2]) * t)

    def segments_for_face(face, view):
        try:
            ev = face.evaluator
            r = param_range(ev)
            if r is None:
                return []
            u0, u1, v0, v1 = r
            grid = []
            for j in range(GRID_V + 1):
                vv = v0 + (v1 - v0) * j / float(GRID_V)
                row = []
                for i in range(GRID_U + 1):
                    uu = u0 + (u1 - u0) * i / float(GRID_U)
                    row.append(sample(ev, uu, vv, view))
                grid.append(row)

            segs = []
            for j in range(GRID_V):
                for i in range(GRID_U):
                    c00 = grid[j][i]
                    c10 = grid[j][i + 1]
                    c11 = grid[j + 1][i + 1]
                    c01 = grid[j + 1][i]
                    hits = []
                    for a, b in ((c00, c10), (c10, c11), (c11, c01), (c01, c00)):
                        p = lerp_cross(a, b)
                        if p is not None:
                            if not hits or sum((p[k] - hits[-1][k]) ** 2 for k in range(3)) > 1.0e-12:
                                hits.append(p)
                    if len(hits) >= 2:
                        segs.append((hits[0], hits[1]))
                        if len(hits) >= 4:
                            segs.append((hits[2], hits[3]))
            return segs
        except Exception:
            return []

    def pkey(p):
        return (round(float(p[0]), 5), round(float(p[1]), 5), round(float(p[2]), 5))

    def stitch(segs):
        if not segs:
            return []
        rows = []
        unused = list(segs)
        while unused:
            a, b = unused.pop()
            chain = [a, b]
            changed = True
            while changed and unused:
                changed = False
                ka, kb = pkey(chain[0]), pkey(chain[-1])
                for idx, (x, y) in enumerate(unused):
                    kx, ky = pkey(x), pkey(y)
                    if kx == kb:
                        chain.append(y)
                    elif ky == kb:
                        chain.append(x)
                    elif ky == ka:
                        chain.insert(0, x)
                    elif kx == ka:
                        chain.insert(0, y)
                    else:
                        continue
                    unused.pop(idx)
                    changed = True
                    break
            if len(chain) >= 3:
                rows.append(chain)
        rows.sort(key=lambda poly: -sum(
            math.sqrt(sum((poly[i][k] - poly[i - 1][k]) ** 2 for k in range(3)))
            for i in range(1, len(poly))))
        return rows[:MAX_CURVES_PER_BODY]

    def contour_for_body(body, matrix):
        if body is None:
            return []
        view = source_view(matrix)
        key = (body_token(body), qview(view))
        if key in state["cache"]:
            return state["cache"][key]

        segs = []
        curved = 0
        try:
            for i in range(body.faces.count):
                face = body.faces.item(i)
                if not curved_face(face):
                    continue
                curved += 1
                segs.extend(segments_for_face(face, view))
                if curved >= MAX_CURVED_FACES:
                    break
        except Exception:
            pass
        curves = stitch(segs)
        state["cache"][key] = curves
        if curves:
            log("CACHE body={} view={} curves={}".format(
                getattr(body, "name", "body"), qview(view), len(curves)))
        return curves

    def transform_poly(poly, matrix):
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

    def draw_body(group, body, matrix, mark, seed_offset=0):
        curves = contour_for_body(body, matrix)
        for i, poly in enumerate(curves):
            try:
                m._sketchy(group, transform_poly(poly, matrix), SILHOUETTE_RGB, 0.0,
                           mark.get("id", 1) * 61001 + seed_offset + i,
                           weight=1, strokes=1)
            except Exception:
                pass
        return len(curves)

    def redraw_silhouettes(force=False):
        if state["busy"]:
            return
        state["busy"] = True
        try:
            m._clear(GROUP_ID)
            if not getattr(m, "_marks", None):
                return
            group = m._group(GROUP_ID)
            if group is None:
                return
            total = 0
            for mark in list(m._marks):
                if mark.get("status", "open") != "open":
                    continue
                if mark.get("tool") not in ("move", "rotate", "scale", "scale_axis", "axis_rotate"):
                    continue
                matrix = proposal_matrix(mark)
                body = m._body.get(mark.get("id"))
                total += draw_body(group, body, matrix, mark)
                if mark.get("tool") == "move" and mark.get("move_scope") == "together":
                    for idx, related in enumerate(mark.get("related_bodies") or []):
                        total += draw_body(group, related, matrix, mark, (idx + 1) * 97)
            state["draws"] += 1
            if total and (force or state["draws"] % 30 == 0):
                log("DRAW curves={} marks={}".format(total, len(m._marks)))
        finally:
            state["busy"] = False

    m._redraw_view_silhouettes = redraw_silhouettes

    # Persistent-geometry redraws remain a valid silhouette invalidation point.
    # Sending state to the HTML palette is deliberately NOT one.
    def redraw_marks(*args, **kwargs):
        result = old_redraw_marks(*args, **kwargs)
        redraw_silhouettes(False)
        return result

    m._redraw_marks = redraw_marks

    class CameraChanged(adsk.core.CameraEventHandler):
        def notify(self, args):
            now = time.perf_counter()
            if now - state["last_camera_draw"] < 0.12:
                return
            state["last_camera_draw"] = now
            if getattr(m, "_marks", None):
                redraw_silhouettes(False)

    def run(context):
        result = old_run(context)
        try:
            h = CameraChanged()
            m._app.cameraChanged.add(h)
            m._handlers.append(h)
            state["camera_handler"] = h
        except Exception as exc:
            log("camera handler unavailable: {}".format(exc))
        redraw_silhouettes(True)
        log("READY: silhouette reacts to geometry/camera changes, not panel state sends")
        return result

    def stop(context):
        try:
            m._clear(GROUP_ID)
            state["cache"].clear()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
