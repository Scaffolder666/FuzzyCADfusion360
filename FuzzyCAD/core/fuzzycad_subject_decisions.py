"""Shared-subject policy for multiple open FuzzyCAD decisions.

A body may legitimately carry several unresolved design decisions at once. The
old legacy `_body_locked` rule encoded the opposite assumption and is now replaced
with an explicit compatibility policy:

- transform decisions (Move/Rotate/Scale/Directional Scale/Axis Rotate) may stack;
- Rough may coexist with anything because it does not encode a geometric delta;
- one topology-changing decision (Extrude/Fillet/Hole) may coexist with transform
  decisions, but two topology decisions on the same body are temporarily blocked
  until topology-reference relinking is fully automatic.

When one decision is accepted, the remaining decisions on the same subject are
rebased onto the current body geometry. Their uncertain values stay unchanged;
only cached geometry, anchors, and still-valid face/edge references are refreshed.
This prevents an old Move/Scale preview from replaying geometry sampled before a
previous decision was committed.

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
        if cmd in ("extrude", "fillet", "hole"):
            return cmd
        if cmd in ("transform", "scale", "directional_scale", "axis_rotate"):
            return "transform"
        return cmd

    def body_locked(body):
        """Return True only for a currently-unsafe same-subject combination."""
        peers = open_marks_for_body(body)
        if not peers:
            return False

        req = requested_tool()
        if req in TOPOLOGY_TOOLS:
            # Multiple topology edits can invalidate each other's face/edge tokens.
            # Keep exactly this one safety boundary until automatic relinking lands.
            return any(p.get("tool") in TOPOLOGY_TOOLS for p in peers)

        if req in ("transform", "move", "rotate", "scale", "directional_scale", "axis_rotate"):
            return False
        if req in ("rough", "note", ""):
            return False

        # Unknown/legacy command: preserve the old safety behavior instead of
        # silently widening semantics we have not audited.
        try:
            return bool(old_body_locked(body)) if old_body_locked is not None else False
        except Exception:
            return False

    m._body_locked = body_locked

    def bbox(body):
        center, size = m._bbox_center_size(body)
        return list(center), float(size)

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

    def rebase_transform(mark, body):
        center, size = bbox(body)
        geom = (getattr(m, "_geom", None) or {}).setdefault(mark["id"], {})
        geom["edges"] = m._sample_edges(body.edges)
        mark["anchor"] = center
        mark["size"] = size
        m._body[mark["id"]] = body
        m._entity[mark["id"]] = body
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
        updater = getattr(m, "_hole_update_anchor", None)
        if updater is not None:
            updater(mark)
        else:
            mark["anchor"] = list(center)
        return True

    def rebase_peer(mark, body):
        tool = mark.get("tool")
        if tool in TRANSFORM_TOOLS:
            return rebase_transform(mark, body)
        if tool == "extrude":
            return rebase_extrude(mark, fresh_entity(mark["id"]))
        if tool == "fillet":
            return rebase_fillet(mark, fresh_entity(mark["id"]))
        if tool == "hole":
            return rebase_hole(mark, fresh_entity(mark["id"]))
        # Rough/annotations do not encode a candidate geometry delta. Keep their
        # body association but move the card anchor with the current body center.
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
        result = old_accept(mark)
        if not result or not peers:
            return result

        # Resolve the subject after the feature commit. Most Fusion features keep
        # the body token stable; if the old wrapper is still valid, prefer it.
        current_body = body if valid(body) else resolve_body(body_tok)
        if current_body is None:
            trace("SUBJECT_REBASE_SKIPPED", "accepted={} body_lost peers={}".format(
                mark.get("id"), peer_ids))
            return result

        rebased = []
        unresolved = []
        for peer in peers:
            try:
                if rebase_peer(peer, current_body):
                    rebased.append(peer.get("id"))
                else:
                    unresolved.append(peer.get("id"))
            except Exception:
                unresolved.append(peer.get("id"))

        # Geometry cache is pure Python; mark it dirty so comic/proposal layers do
        # not reuse samples from before the accepted feature.
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
    trace("SUBJECT_POLICY_READY", "multiple transforms + one topology decision per body")
