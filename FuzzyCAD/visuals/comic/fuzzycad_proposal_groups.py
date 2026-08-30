"""Group-aware proposal infrastructure for FuzzyCAD.

A proposal is treated as one operation over one or more subject bodies.  This
module currently makes that model concrete for Move Together:
- the selected body and all confirmed related bodies share the same source style;
- the whole target group is rendered as one sketchy proposed state;
- related source bodies fade with the selected body;
- Move preview uses sampled edge polylines only, so no candidate BRep is rebuilt
  while dragging;
- a normalized proposal representation is exposed to the sidebar/persistence
  layers for the next data-model migration.

All visual styling is semantic and delegated to fuzzycad_visual_system.py.
"""

import time


def install(m):
    adsk = m.adsk
    old_public = m._public
    old_refresh_ghost = getattr(m, "_refresh_ghost", None)
    old_restore_all = getattr(m, "_restore_all_bodies", None)
    old_run = m.run
    old_stop = m.stop

    state = {
        "extra_ghosts": {},        # token -> (body, original opacity)
        "related_edges": {},       # (mark id, token) -> sampled polylines
        "draw_count": 0,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD PROPOSAL] " + msg)
        except Exception:
            pass

    def vcolor(role, fallback):
        try:
            return m._visual_color(role)
        except Exception:
            return fallback

    def vstroke(group, pts, role, seed, size=3.0, fallback_rgb=(86, 90, 94),
                fallback_weight=1, fallback_strokes=1, amp=None):
        try:
            return m._visual_stroke(group, pts, role, seed, size=size, amp=amp)
        except Exception:
            return m._sketchy(group, pts, fallback_rgb, 0.0 if amp is None else amp,
                              seed, weight=fallback_weight, strokes=fallback_strokes)

    def body_token(body):
        if body is None:
            return None
        try:
            return body.entityToken
        except Exception:
            return str(id(body))

    def proposal_from_mark(mark):
        """Return a normalized, JSON-safe collaboration proposal view."""
        mid = mark.get("id")
        primary = m._body.get(mid)
        subjects = []
        ptok = body_token(primary)
        if ptok:
            subjects.append(ptok)
        if mark.get("tool") == "move" and mark.get("move_scope") == "together":
            for body in mark.get("related_bodies") or []:
                tok = body_token(body)
                if tok and tok not in subjects:
                    subjects.append(tok)

        tool = mark.get("tool")
        params = {}
        if tool == "move":
            params["translation"] = list(mark.get("vec") or [0.0, 0.0, 0.0])
        elif tool == "rotate":
            params["rotation_deg"] = list(mark.get("rot") or [0.0, 0.0, 0.0])
        elif tool in ("scale", "scale_axis"):
            params["factor"] = float(mark.get("factor", 1.0))
            if tool == "scale_axis":
                params["axis"] = mark.get("axis", "X")
                params["side"] = mark.get("scale_side", "positive")
        elif tool == "axis_rotate":
            params["angle_deg"] = float(mark.get("angle", 0.0))
            params["axis_origin"] = list(mark.get("axis_origin") or [0.0, 0.0, 0.0])
            params["axis_dir"] = list(mark.get("axis_dir") or [0.0, 0.0, 1.0])
        elif tool in ("extrude", "fillet"):
            params["amount"] = float(mark.get("amount", 0.0))

        scope = {}
        if tool == "move":
            scope["mode"] = mark.get("move_scope", "only")
        elif tool == "scale_axis":
            scope["mode"] = mark.get("scale_side", "positive")

        return {
            "id": mid,
            "operation": tool,
            "subjects": subjects,
            "parameters": params,
            "scope": scope,
            "status": mark.get("status", "open"),
            "comments": list(mark.get("comments") or []),
        }

    m._proposal_from_mark = proposal_from_mark

    def public(mark):
        out = old_public(mark)
        try:
            out["proposal"] = proposal_from_mark(mark)
        except Exception:
            pass
        return out

    m._public = public

    # ------------------------------------------------------------------
    # Group source state.  The legacy ghost system knows about the primary body;
    # confirmed related bodies are faded here so Together reads as one source set.
    # ------------------------------------------------------------------
    def restore_extra(tok):
        row = state["extra_ghosts"].pop(tok, None)
        if row is None:
            return
        body, opacity = row
        try:
            if body is not None and body.isValid:
                body.opacity = opacity
        except Exception:
            pass

    def sync_group_ghosts():
        desired = {}
        for mark in list(getattr(m, "_marks", [])):
            if (mark.get("tool") != "move" or
                    mark.get("status", "open") != "open" or
                    mark.get("move_scope") != "together"):
                continue
            for body in mark.get("related_bodies") or []:
                tok = body_token(body)
                if tok:
                    desired[tok] = body

        for tok in list(state["extra_ghosts"].keys()):
            if tok not in desired:
                restore_extra(tok)

        for tok, body in desired.items():
            if tok not in state["extra_ghosts"]:
                try:
                    state["extra_ghosts"][tok] = (body, float(body.opacity))
                except Exception:
                    state["extra_ghosts"][tok] = (body, 1.0)
            try:
                body.opacity = float(getattr(m, "GHOST_OPACITY", 0.08))
            except Exception:
                pass

    if old_refresh_ghost is not None:
        def refresh_ghost(*args, **kwargs):
            result = old_refresh_ghost(*args, **kwargs)
            sync_group_ghosts()
            return result
        m._refresh_ghost = refresh_ghost

    if old_restore_all is not None:
        def restore_all_bodies(*args, **kwargs):
            result = old_restore_all(*args, **kwargs)
            for tok in list(state["extra_ghosts"].keys()):
                restore_extra(tok)
            return result
        m._restore_all_bodies = restore_all_bodies

    # ------------------------------------------------------------------
    # Lightweight Move renderer: source group -> matrix -> proposed group.
    # ------------------------------------------------------------------
    def related_edges(mark, body):
        tok = body_token(body)
        key = (mark.get("id"), tok)
        cached = state["related_edges"].get(key)
        if cached is not None:
            return cached
        try:
            cached = m._sample_edges(body.edges)
        except Exception:
            cached = []
        state["related_edges"][key] = cached
        return cached

    def primary_edges(mark):
        return m._geom.get(mark.get("id"), {}).get("edges", [])

    def transform_polys(polys, matrix):
        return [m._apply_matrix(poly, matrix) for poly in polys if poly and len(poly) >= 2]

    def draw_polys(group, polys, role, seed, size):
        count = 0
        for i, poly in enumerate(polys):
            if not poly or len(poly) < 2:
                continue
            vstroke(group, poly, role, seed + i, size=size)
            count += 1
        return count

    def body_center_size(body):
        try:
            return m._bbox_center_size(body)
        except Exception:
            return ([0.0, 0.0, 0.0], 3.0)

    def label(group, xyz, text, size, role):
        try:
            p = adsk.core.Point3D.create(*xyz)
            t = group.addText(text, "Arial", max(0.40, min(size * 0.075, 0.65)),
                              m._label_transform(p))
            t.color = m._solid(vcolor(role, (80, 84, 88)))
            m._apply_billboard(t, p)
        except Exception:
            pass

    def transformed_point(xyz, matrix):
        try:
            p = adsk.core.Point3D.create(*xyz)
            p.transformBy(matrix)
            return (p.x, p.y, p.z)
        except Exception:
            return tuple(xyz)

    def move_text(mark):
        v = list(mark.get("vec") or [0.0, 0.0, 0.0])
        mm = [x * 10.0 for x in v]
        nz = [("XYZ"[i], mm[i]) for i in range(3) if abs(mm[i]) > 1e-6]
        if len(nz) == 1:
            return "Move {} = {:.2f} mm".format(nz[0][0], nz[0][1])
        return "Move = ({:.2f}, {:.2f}, {:.2f}) mm".format(*mm)

    def callout(group, mark, anchor):
        try:
            s = mark.get("size", 3.0)
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            d = max(0.8, min(s * 0.28, 2.6))
            tip = (anchor[0] + (0.78 * xx + 0.45 * yx) * d,
                   anchor[1] + (0.78 * xy + 0.45 * yy) * d,
                   anchor[2] + (0.78 * xz + 0.45 * yz) * d)
            vstroke(group, [tuple(anchor), tip], "operation_cue",
                    mark["id"] * 19441, size=s)
            p = adsk.core.Point3D.create(*tip)
            txt = group.addText(move_text(mark), "Arial", max(0.45, min(s * 0.10, 0.9)),
                                m._label_transform(p))
            txt.color = m._solid(vcolor("warning", (200, 44, 32)))
            m._apply_billboard(txt, p)
        except Exception:
            pass

    def farthest_primary(mark):
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        best = tuple(a)
        best_d = -1.0
        for poly in primary_edges(mark):
            for p in poly:
                d = sum((p[i] - a[i]) ** 2 for i in range(3))
                if d > best_d:
                    best_d = d; best = p
        return best

    def draw_move(group, mark, rgb, amp):
        t0 = time.perf_counter()
        matrix = m._op_matrix(mark)
        scope = mark.get("move_scope", "only")
        related = list(mark.get("related_bodies") or [])
        size = float(mark.get("size", 3.0))
        line_count = 0

        # BRep topology is deliberately the thin/internal layer. The dedicated
        # view-silhouette layer owns the stronger proposal outer contour.
        p_edges = primary_edges(mark)
        line_count += draw_polys(group, p_edges, "current_outline",
                                 mark["id"] * 20000, size)
        line_count += draw_polys(group, transform_polys(p_edges, matrix),
                                 "proposal_internal", mark["id"] * 21000, size)

        if related:
            if scope == "together":
                for idx, body in enumerate(related):
                    edges = related_edges(mark, body)
                    _, related_size = body_center_size(body)
                    line_count += draw_polys(group, edges, "current_outline",
                                             mark["id"] * 22000 + idx * 300, related_size)
                    line_count += draw_polys(group, transform_polys(edges, matrix),
                                             "proposal_internal",
                                             mark["id"] * 23000 + idx * 300, related_size)
            elif getattr(m, "_active_cmd", None) == "transform":
                for idx, body in enumerate(related):
                    edges = related_edges(mark, body)
                    c, related_size = body_center_size(body)
                    line_count += draw_polys(group, edges, "affected_candidate",
                                             mark["id"] * 24000 + idx * 300, related_size)
                    label(group, c, "R{}".format(idx + 1), related_size,
                          "operation_cue")

        a = tuple(mark.get("anchor") or [0.0, 0.0, 0.0])
        b = transformed_point(a, matrix)
        vstroke(group, [a, b], "operation_cue", mark["id"] * 25001, size=size)
        callout(group, mark, b)

        p0 = farthest_primary(mark)
        p1 = transformed_point(p0, matrix)
        if sum((p1[i] - p0[i]) ** 2 for i in range(3)) > 1e-5:
            label(group, p0, "Original", size, "label_current")
            label(group, p1, "Proposed", size, "label_proposed")

        state["draw_count"] += 1
        dt = (time.perf_counter() - t0) * 1000.0
        if dt >= 12.0 or state["draw_count"] % 40 == 0:
            log("MOVE DRAW ms={:.2f} scope={} subjects={} polylines={}".format(
                dt, scope, 1 + (len(related) if scope == "together" else 0), line_count))

    m._DRAW["move"] = draw_move

    def run(context):
        result = old_run(context)
        log("PROPOSAL GROUPS READY: Move reads centralized semantic visual roles")
        return result

    def stop(context):
        try:
            for tok in list(state["extra_ghosts"].keys()):
                restore_extra(tok)
            state["related_edges"].clear()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
