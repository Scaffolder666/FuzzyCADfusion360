"""Unified candidate visualization for FuzzyCAD geometry tools.

Fillet established the visual language we want across the add-in:
  faded original -> real candidate BRep -> sketch-like outline -> highlighted
  changed relation -> numeric callout.

This patch applies that language to Move, Rotate, Scale, and Extrude while
leaving the working Fillet implementation untouched.
"""

import math

CANDIDATE_RGB = (190, 190, 186)
CANDIDATE_OPACITY = 0.30
CHANGE_RGB = (225, 126, 38)
CALLOUT_RGB = (200, 44, 32)


def install(m):
    adsk = m.adsk
    old_build_pending = m._build_pending
    old_draw_label = m._draw_label
    CurrentInputChanged = m.FuzzyInputChanged
    old_run = m.run

    state = {"extrude_busy": False, "extrude_seq": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD VISUAL] " + msg)
        except Exception:
            pass

    def build_pending(cmd, ent):
        pending = old_build_pending(cmd, ent)
        if pending is None:
            return None
        if cmd == "extrude":
            try:
                pending["geom"]["face_token"] = ent.entityToken
            except Exception:
                pending["geom"]["face_token"] = None
            pending["geom"].setdefault("extrude_candidate_body", None)
            pending["geom"].setdefault("extrude_candidate_amount", None)
            pending["geom"].setdefault("extrude_candidate_edges", [])
            pending["geom"].setdefault("extrude_changed_edges", [])
        return pending

    m._build_pending = build_pending

    def candidate_matrix(mark):
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
        return adsk.core.Matrix3D.create()

    def add_candidate_body(group, body, matrix=None):
        if body is None:
            return None
        try:
            cg = group.addBRepBody(body)
            if matrix is not None:
                cg.transform = matrix
            cg.color = m._solid(CANDIDATE_RGB)
            cg.setOpacity(CANDIDATE_OPACITY, True)
            return cg
        except Exception as exc:
            log("candidate BRep draw failed: {}".format(exc))
            return None

    def callout(group, mark, text, anchor=None):
        try:
            a = list(anchor or mark.get("anchor") or [0.0, 0.0, 0.0])
            s = mark.get("size", 3.0)
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            d = max(0.8, min(s * 0.28, 2.6))
            tip = (a[0] + (0.78 * xx + 0.45 * yx) * d,
                   a[1] + (0.78 * xy + 0.45 * yy) * d,
                   a[2] + (0.78 * xz + 0.45 * yz) * d)
            m._sketchy(group, [tuple(a), tip], CALLOUT_RGB, 0.0,
                       mark["id"] * 1709, weight=2, strokes=1)
            cp = adsk.core.Point3D.create(*tip)
            txt = group.addText(text, "Arial", max(0.45, min(s * 0.10, 0.9)),
                                m._label_transform(cp))
            txt.color = m._solid(CALLOUT_RGB)
            m._apply_billboard(txt, cp)
        except Exception as exc:
            log("callout draw failed tool={}: {}".format(mark.get("tool"), exc))

    def outline_transformed(group, mark, matrix, rgb, amp, seed_base):
        g = m._geom.get(mark["id"], {})
        for i, loop in enumerate(g.get("edges", [])):
            pts = m._apply_matrix(loop, matrix)
            m._sketchy(group, pts, rgb, amp * 0.8,
                       mark["id"] * seed_base + i, weight=1, strokes=2)

    def transformed_anchor(mark, matrix):
        p = adsk.core.Point3D.create(*mark["anchor"])
        p.transformBy(matrix)
        return (p.x, p.y, p.z)

    def draw_move(group, mark, rgb, amp):
        body = m._body.get(mark["id"])
        matrix = candidate_matrix(mark)
        add_candidate_body(group, body, matrix)
        outline_transformed(group, mark, matrix, rgb, amp, 120)

        a = tuple(mark["anchor"])
        b = transformed_anchor(mark, matrix)
        m._sketchy(group, [a, b], CHANGE_RGB, 0.0,
                   mark["id"] * 121, weight=3, strokes=1)
        v = mark.get("vec") or [0.0, 0.0, 0.0]
        mm = [x * 10.0 for x in v]
        nz = [("XYZ"[i], mm[i]) for i in range(3) if abs(mm[i]) > 1e-6]
        if len(nz) == 1:
            text = "Move {} = {:.2f} mm".format(nz[0][0], nz[0][1])
        else:
            text = "Move = ({:.2f}, {:.2f}, {:.2f}) mm".format(*mm)
        callout(group, mark, text, b)

    def arc_points(anchor, axis, radius, angle_deg):
        ax = m._axis_unit(axis)
        u = m._plane_basis(axis)
        w = (ax[1] * u[2] - ax[2] * u[1],
             ax[2] * u[0] - ax[0] * u[2],
             ax[0] * u[1] - ax[1] * u[0])
        steps = max(8, min(72, int(abs(angle_deg) / 5.0) + 2))
        end = math.radians(angle_deg)
        pts = []
        for i in range(steps + 1):
            t = end * i / steps
            c, s = math.cos(t), math.sin(t)
            pts.append((anchor[0] + radius * (c * u[0] + s * w[0]),
                        anchor[1] + radius * (c * u[1] + s * w[1]),
                        anchor[2] + radius * (c * u[2] + s * w[2])))
        return pts

    def draw_rotate(group, mark, rgb, amp):
        body = m._body.get(mark["id"])
        matrix = candidate_matrix(mark)
        add_candidate_body(group, body, matrix)
        outline_transformed(group, mark, matrix, rgb, amp, 220)

        rot = mark.get("rot") or [0.0, 0.0, 0.0]
        idx = max(range(3), key=lambda i: abs(rot[i]))
        axis = "XYZ"[idx]
        angle = rot[idx]
        r = mark.get("size", 3.0) * 0.62
        arc = arc_points(mark["anchor"], axis, r, angle)
        if len(arc) >= 2:
            m._sketchy(group, arc, CHANGE_RGB, 0.0,
                       mark["id"] * 221, weight=3, strokes=1)
            callout(group, mark, "Rotate {} = {:.1f}°".format(axis, angle), arc[-1])
        else:
            callout(group, mark, "Rotate {} = {:.1f}°".format(axis, angle))

    def farthest_point(mark):
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        best = None
        best_d = -1.0
        for loop in m._geom.get(mark["id"], {}).get("edges", []):
            for p in loop:
                d = ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2 + (p[2] - a[2]) ** 2)
                if d > best_d:
                    best_d = d
                    best = p
        return best or tuple(a)

    def draw_scale(group, mark, rgb, amp):
        body = m._body.get(mark["id"])
        matrix = candidate_matrix(mark)
        add_candidate_body(group, body, matrix)
        outline_transformed(group, mark, matrix, rgb, amp, 320)

        p0 = farthest_point(mark)
        p1o = adsk.core.Point3D.create(*p0)
        p1o.transformBy(matrix)
        p1 = (p1o.x, p1o.y, p1o.z)
        m._sketchy(group, [p0, p1], CHANGE_RGB, 0.0,
                   mark["id"] * 321, weight=3, strokes=1)
        callout(group, mark, "Scale = {:.3f}x".format(mark.get("factor", 1.0)), p1)

    def resolve_extrude_face(mark):
        if mark is None:
            return None
        fallback = m._entity.get(mark["id"])
        try:
            if fallback is not None and fallback.isValid:
                return fallback
        except Exception:
            pass
        token = m._geom.get(mark["id"], {}).get("face_token")
        design = m._design()
        if design is None or not token:
            return None
        try:
            for ent in design.findEntityByToken(token):
                if isinstance(ent, adsk.fusion.BRepFace):
                    return ent
        except Exception:
            pass
        return None

    def sample_face_edges(faces):
        out = []
        seen = set()
        try:
            for i in range(faces.count):
                face = faces.item(i)
                for j in range(face.edges.count):
                    edge = face.edges.item(j)
                    try:
                        key = edge.entityToken
                    except Exception:
                        key = "{}:{}".format(i, j)
                    if key in seen:
                        continue
                    seen.add(key)
                    pts = m._sample_edge(edge)
                    if len(pts) >= 2:
                        out.append(pts)
        except Exception:
            pass
        return out

    def compute_extrude_candidate(mark):
        g = m._geom.get(mark["id"], {})
        amount = float(mark.get("amount", 0.0))
        cached = (g.get("extrude_candidate_body") is not None and
                  g.get("extrude_candidate_amount") is not None and
                  abs(g["extrude_candidate_amount"] - amount) <= 1e-8)
        if cached:
            return True

        face = resolve_extrude_face(mark)
        if face is None or abs(amount) < 1e-9:
            return False
        feat = None
        try:
            comp = face.body.parentComponent
            ext = comp.features.extrudeFeatures
            ei = ext.createInput(face, adsk.fusion.FeatureOperations.JoinFeatureOperation)
            ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(amount))
            feat = ext.add(ei)
            if feat is None:
                return False
            try:
                if feat.healthState == adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState:
                    return False
            except Exception:
                pass

            body = feat.bodies.item(0) if feat.bodies.count > 0 else face.body
            mgr = adsk.fusion.TemporaryBRepManager.get()
            candidate = mgr.copy(body)
            if candidate is None:
                return False
            g["extrude_candidate_body"] = candidate
            g["extrude_candidate_amount"] = amount
            g["extrude_candidate_edges"] = m._sample_edges(body.edges)
            g["extrude_changed_edges"] = sample_face_edges(feat.faces)
            return True
        except Exception as exc:
            log("extrude candidate failed mark={} amount_mm={:.3f}: {}".format(
                mark.get("id"), amount * 10.0, exc))
            return False
        finally:
            if feat is not None:
                try:
                    feat.deleteMe()
                except Exception:
                    pass

    def draw_extrude(group, mark, rgb, amp):
        g = m._geom.get(mark["id"], {})
        ok = compute_extrude_candidate(mark)
        if ok:
            candidate = g.get("extrude_candidate_body")
            add_candidate_body(group, candidate)
            for i, poly in enumerate(g.get("extrude_candidate_edges", [])):
                m._sketchy(group, poly, rgb, amp * 0.8,
                           mark["id"] * 420 + i, weight=1, strokes=2)
            for i, poly in enumerate(g.get("extrude_changed_edges", [])):
                m._sketchy(group, poly, CHANGE_RGB, amp * 0.35,
                           mark["id"] * 520 + i, weight=2, strokes=1)
        else:
            d = g.get("normal", (0.0, 0.0, 1.0))
            amt = mark.get("amount", 0.0)
            for i, loop in enumerate(g.get("loops", [])):
                off = m._translate(loop, d, amt)
                m._sketchy(group, off, rgb, amp,
                           mark["id"] * 430 + i, weight=1, strokes=2)

        d = g.get("normal", (0.0, 0.0, 1.0))
        amt = mark.get("amount", 0.0)
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        tip = (a[0] + d[0] * amt, a[1] + d[1] * amt, a[2] + d[2] * amt)
        m._sketchy(group, [tuple(a), tip], CHANGE_RGB, 0.0,
                   mark["id"] * 421, weight=3, strokes=1)
        callout(group, mark, "D = {:.2f} mm".format(amt * 10.0), tip)

    m._draw_move = draw_move
    m._draw_rotate = draw_rotate
    m._draw_scale = draw_scale
    m._draw_extrude = draw_extrude
    m._DRAW["move"] = draw_move
    m._DRAW["rotate"] = draw_rotate
    m._DRAW["scale"] = draw_scale
    m._DRAW["extrude"] = draw_extrude

    def draw_label(group, mark, rgb):
        if mark.get("tool") in ("move", "rotate", "scale", "extrude", "fillet"):
            return
        return old_draw_label(group, mark, rgb)

    m._draw_label = draw_label

    def sync_extrude_from_input():
        try:
            mid = m._live.get("extrude")
            mark = m._find(mid) if mid is not None else None
            if mark is None:
                mark = m._sync_category("extrude")
            else:
                mark.update(m._category_raw("extrude"))
            if mark is None:
                return
            g = m._geom.get(mark["id"], {})
            g["extrude_candidate_body"] = None
            g["extrude_candidate_amount"] = None
            compute_extrude_candidate(mark)
            m._clear(m.GROUP_PREVIEW)
            group = m._group(m.GROUP_PREVIEW)
            m._draw_one(group, mark)
            m._refresh_ghost()
            m._send_state()
            state["extrude_seq"] += 1
            log("EXTRUDE INPUT #{:05d} depth_mm={:.4f} candidate={}".format(
                state["extrude_seq"], mark.get("amount", 0.0) * 10.0,
                g.get("extrude_candidate_body") is not None))
        except Exception:
            try:
                log("EXTRUDE LIVE VISUAL EXCEPTION\n{}".format(m.traceback.format_exc()))
            except Exception:
                pass

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)
            if state["extrude_busy"]:
                return
            if getattr(m, "_active_cmd", None) != "extrude" or cid != "d":
                return
            state["extrude_busy"] = True
            try:
                sync_extrude_from_input()
            finally:
                state["extrude_busy"] = False

    m.FuzzyInputChanged = FuzzyInputChanged

    def run(context):
        result = old_run(context)
        log("UNIFIED CANDIDATE VISUALS READY: Move/Rotate/Scale/Extrude now follow Fillet visual language")
        return result

    m.run = run
