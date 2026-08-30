"""Shared-subject policy for multiple open FuzzyCAD decisions.

A body may legitimately carry several unresolved design decisions at once. The
legacy `_body_locked` rule encoded the opposite assumption; this module is the
current product owner of same-subject compatibility.

Current policy:
- transform decisions (Move/Rotate/Scale/Directional Scale/Axis Rotate) may stack;
- Rough may coexist with anything because it does not encode a geometric delta;
- topology decisions (Extrude/Fillet/Hole) may also coexist. If one topology
  decision is accepted and Fusion invalidates another decision's face/edge token,
  the surviving decision is conservatively relinked to the closest matching
  face/edge on the same body. If no unambiguous match exists, the card is kept and
  marked `reference_lost` instead of silently binding it to the wrong geometry.

When one decision is accepted, remaining decisions on the same subject are
rebased onto the current body geometry. Their uncertain values stay unchanged;
only cached geometry, anchors, and references are refreshed.

All long-lived state is pure Python. Native bodies/faces/edges are resolved and
used only inside the synchronous event that needs them.
"""

import math

TRANSFORM_TOOLS = {"move", "rotate", "scale", "scale_axis", "axis_rotate"}
TOPOLOGY_TOOLS = {"extrude", "fillet", "hole"}
NON_BLOCKING_TOOLS = {"rough", "note"}


def install(m):
    adsk = m.adsk
    old_body_locked = getattr(m, "_body_locked", None)
    old_accept = m._accept

    def trace(event, detail=""):
        try:
            fn = getattr(m, "_crash_trace", None)
            if fn is not None:
                fn(event, detail)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD SUBJECT] {} {}".format(event, detail))
        except Exception:
            pass

    def token(obj):
        if obj is None:
            return None
        try:
            return str(obj.entityToken)
        except Exception:
            return None

    def valid(obj):
        if obj is None:
            return False
        try:
            return bool(obj.isValid)
        except Exception:
            return True

    def entity_body(ent):
        if ent is None:
            return None
        try:
            body = adsk.fusion.BRepBody.cast(ent)
            if body is not None:
                return body
        except Exception:
            pass
        try:
            return ent.body
        except Exception:
            return None

    def resolve_body(tok):
        if not tok:
            return None
        try:
            design = m._design()
            if design is None:
                return None
            for ent in design.findEntityByToken(str(tok)):
                body = adsk.fusion.BRepBody.cast(ent)
                if body is not None:
                    return body
        except Exception:
            pass
        return None

    def open_marks_for_body(body, exclude_id=None):
        tok = token(body)
        if not tok:
            return []
        rows = []
        for mark in list(getattr(m, "_marks", None) or []):
            if mark.get("status", "open") != "open":
                continue
            if exclude_id is not None and mark.get("id") == exclude_id:
                continue
            b = (getattr(m, "_body", None) or {}).get(mark.get("id"))
            if token(b) == tok:
                rows.append(mark)
        return rows

    m._subject_open_marks = open_marks_for_body

    def requested_tool():
        cmd = str(getattr(m, "_active_cmd", None) or "")
        if cmd in TOPOLOGY_TOOLS:
            return cmd
        if cmd in ("transform", "scale", "directional_scale", "axis_rotate"):
            return "transform"
        return cmd

    def body_locked(body):
        """Current audited tools do not lock a body merely because it has a mark."""
        peers = open_marks_for_body(body)
        if not peers:
            return False

        req = requested_tool()
        if req in TOPOLOGY_TOOLS:
            return False
        if req in ("transform", "move", "rotate", "scale", "directional_scale", "axis_rotate"):
            return False
        if req in ("rough", "note", ""):
            return False

        # Unknown/legacy command: preserve the old conservative behavior. This is
        # compatibility only; current product semantics live in this module.
        try:
            return bool(old_body_locked(body)) if old_body_locked is not None else False
        except Exception:
            return False

    m._body_locked = body_locked

    def bbox(body):
        center, size = m._bbox_center_size(body)
        return list(center), float(size)

    def body_scale(body):
        try:
            bb = body.boundingBox
            dx = bb.maxPoint.x - bb.minPoint.x
            dy = bb.maxPoint.y - bb.minPoint.y
            dz = bb.maxPoint.z - bb.minPoint.z
            return max(math.sqrt(dx * dx + dy * dy + dz * dz), 1e-6)
        except Exception:
            return 1.0

    def distance(a, b):
        try:
            return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))
        except Exception:
            return 1e9

    def rel_delta(a, b):
        try:
            aa = abs(float(a)); bb = abs(float(b))
            return abs(aa - bb) / max(aa, bb, 1e-6)
        except Exception:
            return 1.0

    def dot(a, b):
        try:
            return sum(float(a[i]) * float(b[i]) for i in range(3))
        except Exception:
            return -1.0

    def fresh_entity(mid):
        ent = (getattr(m, "_entity", None) or {}).get(mid)
        if valid(ent):
            return ent
        tok = token(ent)
        if not tok:
            return None
        try:
            for row in m._design().findEntityByToken(tok):
                return row
        except Exception:
            return None
        return None

    def plane_basis(normal):
        n = list(normal or [0.0, 0.0, 1.0])
        ln = math.sqrt(sum(float(v) * float(v) for v in n)) or 1.0
        n = [float(v) / ln for v in n]
        ref = [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0]
        u = [n[1] * ref[2] - n[2] * ref[1],
             n[2] * ref[0] - n[0] * ref[2],
             n[0] * ref[1] - n[1] * ref[0]]
        lu = math.sqrt(sum(v * v for v in u)) or 1.0
        u = [v / lu for v in u]
        v = [n[1] * u[2] - n[2] * u[1],
             n[2] * u[0] - n[0] * u[2],
             n[0] * u[1] - n[1] * u[0]]
        lv = math.sqrt(sum(x * x for x in v)) or 1.0
        return n, u, [x / lv for x in v]

    # ------------------------------------------------------------------
    # Conservative topology-reference fingerprints. These are captured before
    # a sibling topology feature is committed, then used only if Fusion invalidates
    # the original face/edge reference. A high score is rejected instead of being
    # silently rebound to a merely-nearby entity.
    # ------------------------------------------------------------------
    def face_fingerprint(face):
        if face is None:
            return None
        try:
            p = face.pointOnFace
            center = [p.x, p.y, p.z]
        except Exception:
            center, _ = m._bbox_center_size(face)
            center = list(center)
        try:
            area = float(face.area)
        except Exception:
            area = 0.0
        try:
            edges = int(face.edges.count)
        except Exception:
            edges = 0
        return {
            "kind": "face",
            "center": center,
            "normal": list(m._face_normal(face)),
            "area": area,
            "edges": edges,
        }

    def edge_fingerprint(edge):
        if edge is None:
            return None
        try:
            pts = list(m._sample_edge(edge, 6) or [])
        except Exception:
            pts = []
        if pts:
            center = list(pts[len(pts) // 2])
        else:
            center, _ = m._bbox_center_size(edge)
            center = list(center)
        try:
            length = float(edge.length)
        except Exception:
            length = 0.0
            for i in range(1, len(pts)):
                length += distance(pts[i - 1], pts[i])
        try:
            faces = int(edge.faces.count)
        except Exception:
            faces = 0
        return {"kind": "edge", "center": center, "length": length, "faces": faces}

    def reference_hint(mark):
        tool = mark.get("tool")
        ent = (getattr(m, "_entity", None) or {}).get(mark.get("id"))
        if tool in ("extrude", "hole"):
            return face_fingerprint(adsk.fusion.BRepFace.cast(ent))
        if tool == "fillet":
            return edge_fingerprint(adsk.fusion.BRepEdge.cast(ent))
        return None

    def face_score(face, fp, scale):
        try:
            cand = face_fingerprint(face)
            pos = distance(cand["center"], fp["center"]) / max(scale, 1e-6)
            # Direction matters for Extrude/Hole, so reversed normals are not
            # treated as equivalent.
            nd = max(-1.0, min(1.0, dot(cand["normal"], fp["normal"])))
            normal_penalty = (1.0 - nd) * 0.5
            area_penalty = rel_delta(cand["area"], fp["area"])
            edge_penalty = abs(cand["edges"] - fp["edges"]) / max(fp["edges"], 1)
            return pos + 0.55 * normal_penalty + 0.25 * area_penalty + 0.05 * edge_penalty
        except Exception:
            return 1e9

    def edge_score(edge, fp, scale):
        try:
            cand = edge_fingerprint(edge)
            pos = distance(cand["center"], fp["center"]) / max(scale, 1e-6)
            length_penalty = rel_delta(cand["length"], fp["length"])
            face_penalty = abs(cand["faces"] - fp["faces"]) / max(fp["faces"], 1)
            return pos + 0.45 * length_penalty + 0.08 * face_penalty
        except Exception:
            return 1e9

    def best_face(body, fp):
        if body is None or not fp:
            return None, None
        scale = body_scale(body)
        ranked = []
        try:
            for i in range(body.faces.count):
                face = body.faces.item(i)
                ranked.append((face_score(face, fp, scale), face))
        except Exception:
            return None, None
        ranked.sort(key=lambda row: row[0])
        if not ranked or ranked[0][0] > 0.42:
            return None, ranked[0][0] if ranked else None
        # If the top two are almost indistinguishable, refuse to guess.
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 0.025:
            return None, ranked[0][0]
        return ranked[0][1], ranked[0][0]

    def best_edge(body, fp):
        if body is None or not fp:
            return None, None
        scale = body_scale(body)
        ranked = []
        try:
            for i in range(body.edges.count):
                edge = body.edges.item(i)
                ranked.append((edge_score(edge, fp, scale), edge))
        except Exception:
            return None, None
        ranked.sort(key=lambda row: row[0])
        if not ranked or ranked[0][0] > 0.38:
            return None, ranked[0][0] if ranked else None
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 0.025:
            return None, ranked[0][0]
        return ranked[0][1], ranked[0][0]

    def belongs_to_body(ent, body):
        return token(entity_body(ent)) == token(body) if ent is not None and body is not None else False

    def topology_entity(mark, body, hint):
        ent = fresh_entity(mark["id"])
        if valid(ent) and belongs_to_body(ent, body):
            return ent

        tool = mark.get("tool")
        matched = None
        score = None
        if tool in ("extrude", "hole"):
            matched, score = best_face(body, hint)
        elif tool == "fillet":
            matched, score = best_edge(body, hint)

        if matched is not None:
            m._entity[mark["id"]] = matched
            mark.pop("reference_lost", None)
            trace("SUBJECT_RELINK", "mark={} tool={} score={:.4f}".format(
                mark.get("id"), tool, float(score or 0.0)))
            return matched

        mark["reference_lost"] = True
        try:
            m._entity.pop(mark["id"], None)
        except Exception:
            pass
        trace("SUBJECT_RELINK_FAILED", "mark={} tool={} score={}".format(
            mark.get("id"), tool, "none" if score is None else round(float(score), 4)))
        return None

    def rebase_transform(mark, body):
        center, size = bbox(body)
        geom = (getattr(m, "_geom", None) or {}).setdefault(mark["id"], {})
        geom["edges"] = m._sample_edges(body.edges)
        mark["anchor"] = center
        mark["size"] = size
        m._body[mark["id"]] = body
        m._entity[mark["id"]] = body
        mark.pop("reference_lost", None)
        geom.pop("real", None)
        return True

    def rebase_extrude(mark, ent):
        face = adsk.fusion.BRepFace.cast(ent)
        if face is None:
            return False
        center, size = m._bbox_center_size(face)
        normal = m._face_normal(face)
        geom = (getattr(m, "_geom", None) or {}).setdefault(mark["id"], {})
        geom["loops"] = m._sample_edges(face.edges)
        geom["normal"] = normal
        geom.pop("real", None)
        mark["anchor"] = list(center)
        mark["size"] = float(size)
        m._entity[mark["id"]] = face
        m._body[mark["id"]] = face.body
        mark.pop("reference_lost", None)
        return True

    def rebase_fillet(mark, ent):
        edge = adsk.fusion.BRepEdge.cast(ent)
        if edge is None:
            return False
        center, size = m._bbox_center_size(edge)
        geom = (getattr(m, "_geom", None) or {}).setdefault(mark["id"], {})
        geom["edge"] = m._sample_edge(edge)
        geom["stations"] = m._fillet_stations(edge)
        geom.pop("real", None)
        mark["anchor"] = list(center)
        mark["size"] = float(size)
        m._entity[mark["id"]] = edge
        m._body[mark["id"]] = edge.body
        mark.pop("reference_lost", None)
        return True

    def rebase_hole(mark, ent):
        face = adsk.fusion.BRepFace.cast(ent)
        if face is None:
            return False
        center, size = m._bbox_center_size(face)
        normal = m._face_normal(face)
        n, u, v = plane_basis(normal)
        geom = (getattr(m, "_geom", None) or {}).setdefault(mark["id"], {})
        geom.update({
            "loops": m._sample_edges(face.edges),
            "normal": n,
            "basis_u": u,
            "basis_v": v,
            "base_anchor": list(center),
        })
        geom.pop("real", None)
        mark["base_anchor"] = list(center)
        mark["size"] = float(size)
        m._entity[mark["id"]] = face
        m._body[mark["id"]] = face.body
        mark.pop("reference_lost", None)
        updater = getattr(m, "_hole_update_anchor", None)
        if updater is not None:
            updater(mark)
        else:
            mark["anchor"] = list(center)
        return True

    def rebase_peer(mark, body, hint=None):
        tool = mark.get("tool")
        if tool in TRANSFORM_TOOLS:
            return rebase_transform(mark, body)
        if tool == "extrude":
            return rebase_extrude(mark, topology_entity(mark, body, hint))
        if tool == "fillet":
            return rebase_fillet(mark, topology_entity(mark, body, hint))
        if tool == "hole":
            return rebase_hole(mark, topology_entity(mark, body, hint))
        if tool in NON_BLOCKING_TOOLS:
            try:
                center, size = bbox(body)
                mark["anchor"] = center
                mark["size"] = size
                m._body[mark["id"]] = body
            except Exception:
                pass
            return True
        return False

    def accept(mark):
        body = (getattr(m, "_body", None) or {}).get(mark.get("id"))
        body_tok = token(body)
        peers = open_marks_for_body(body, exclude_id=mark.get("id")) if body is not None else []
        peer_ids = [p.get("id") for p in peers]

        # Capture topology references BEFORE the feature commit, while every peer's
        # face/edge is still valid. Pure-Python fingerprints survive the commit.
        hints = {}
        for peer in peers:
            if peer.get("tool") in TOPOLOGY_TOOLS:
                try:
                    hints[peer.get("id")] = reference_hint(peer)
                except Exception:
                    hints[peer.get("id")] = None

        result = old_accept(mark)
        if not result or not peers:
            return result

        current_body = body if valid(body) else resolve_body(body_tok)
        if current_body is None:
            trace("SUBJECT_REBASE_SKIPPED", "accepted={} body_lost peers={}".format(
                mark.get("id"), peer_ids))
            for peer in peers:
                if peer.get("tool") in TOPOLOGY_TOOLS:
                    peer["reference_lost"] = True
            return result

        rebased = []
        unresolved = []
        for peer in peers:
            try:
                if rebase_peer(peer, current_body, hints.get(peer.get("id"))):
                    rebased.append(peer.get("id"))
                else:
                    unresolved.append(peer.get("id"))
                    if peer.get("tool") in TOPOLOGY_TOOLS:
                        peer["reference_lost"] = True
            except Exception:
                unresolved.append(peer.get("id"))
                if peer.get("tool") in TOPOLOGY_TOOLS:
                    peer["reference_lost"] = True

        try:
            invalidate = getattr(m, "_runtime_invalidate_body", None)
            if invalidate is not None:
                invalidate(body_tok)
        except Exception:
            pass

        trace("SUBJECT_REBASE", "accepted={} rebased={} unresolved={}".format(
            mark.get("id"), rebased, unresolved))
        return result

    m._accept = accept
    trace("SUBJECT_POLICY_READY", "multiple decisions + conservative topology relinking")
