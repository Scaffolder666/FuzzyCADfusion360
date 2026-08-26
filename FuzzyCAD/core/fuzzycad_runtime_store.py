"""Runtime-only data/render registry for FuzzyCAD.

This layer separates collaboration data, geometry caches, and viewport graphics.
It deliberately stores no long-lived Fusion native wrapper objects. Persistent
records are JSON-safe Python values (ids, entity tokens, sampled XYZ points,
group-id strings, signatures, dirty/visibility flags). Whenever Fusion graphics
need to be touched, the group is resolved fresh from the active design by id.

Phase 1 is intentionally conservative: the existing mark/persistence model remains
the source of truth and this registry mirrors it. The fuzzy-boundary renderer is
the first consumer; other visuals can migrate later without changing appearance.
"""

import hashlib


def install(m):
    if getattr(m, "_runtime_store", None) is not None:
        return

    state = {
        "proposals": {},
        "geometry": {},
        "render": {},
        "geometry_epoch": 0,
    }
    m._runtime_store = state

    def entity_token(ent):
        if ent is None:
            return None
        try:
            return str(ent.entityToken)
        except Exception:
            return None

    def safe_phase(mark):
        try:
            return str(m._mark_phase(mark))
        except Exception:
            return "proposed" if mark.get("status", "open") == "open" else "resolved"

    def json_safe(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [json_safe(v) for v in value]
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                sv = json_safe(v)
                if sv is not None or v is None:
                    out[str(k)] = sv
            return out
        return None

    def proposal_record(mark):
        mid = mark.get("id")
        try:
            p = m._proposal_from_mark(mark)
            p = json_safe(p) or {}
        except Exception:
            p = {
                "id": mid,
                "operation": mark.get("tool"),
                "status": mark.get("status", "open"),
            }
        p["phase"] = safe_phase(mark)
        p["mtype"] = mark.get("mtype", "need_input")
        p["entity_token"] = entity_token(m._entity.get(mid))
        p["body_token"] = entity_token(m._body.get(mid))
        return p

    def sync_proposals():
        live = set()
        for mark in list(getattr(m, "_marks", None) or []):
            try:
                mid = int(mark.get("id"))
            except Exception:
                continue
            live.add(mid)
            state["proposals"][mid] = proposal_record(mark)
        for mid in list(state["proposals"].keys()):
            if mid not in live:
                state["proposals"].pop(mid, None)
        return state["proposals"]

    def body_signature(body):
        tok = entity_token(body) or "id:{}".format(id(body))
        try:
            rev = str(getattr(body, "revisionId", "") or "")
        except Exception:
            rev = ""
        try:
            edge_count = int(body.edges.count)
        except Exception:
            edge_count = -1
        try:
            face_count = int(body.faces.count)
        except Exception:
            face_count = -1
        try:
            bb = body.boundingBox
            bbox = tuple(round(float(v), 9) for v in (
                bb.minPoint.x, bb.minPoint.y, bb.minPoint.z,
                bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z))
        except Exception:
            bbox = ()
        return (tok, rev, edge_count, face_count, bbox)

    def body_geometry(body):
        """Return a pure-Python sampled geometry snapshot for a live body."""
        tok = entity_token(body) or "id:{}".format(id(body))
        sig = (int(state.get("geometry_epoch", 0)), body_signature(body))
        cached = state["geometry"].get(tok)
        if cached is not None and cached.get("signature") == sig:
            return cached

        try:
            edges = m._sample_edges(body.edges)
        except Exception:
            edges = []
        pure_edges = []
        for poly in edges or []:
            out = []
            for q in poly or []:
                try:
                    out.append((float(q[0]), float(q[1]), float(q[2])))
                except Exception:
                    pass
            if len(out) >= 2:
                pure_edges.append(out)
        try:
            center, size = m._bbox_center_size(body)
            center = tuple(float(x) for x in center)
            size = float(size)
        except Exception:
            center, size = (0.0, 0.0, 0.0), 3.0

        cached = {
            "token": tok,
            "signature": sig,
            "edges": pure_edges,
            "center": center,
            "size": size,
        }
        state["geometry"][tok] = cached
        return cached

    def invalidate_geometry(subject=None):
        state["geometry_epoch"] = int(state.get("geometry_epoch", 0)) + 1
        if subject is None:
            state["geometry"].clear()
            return
        tok = subject if isinstance(subject, str) else entity_token(subject)
        if tok:
            state["geometry"].pop(tok, None)

    def group_id(subject_token, role):
        raw = str(subject_token or "none").encode("utf-8", "ignore")
        digest = hashlib.sha1(raw).hexdigest()[:16]
        return "FuzzyCAD_Runtime_{}_{}".format(str(role), digest)

    def find_group(gid, create=False):
        try:
            design = m._design()
            if design is None:
                return None
            groups = design.rootComponent.customGraphicsGroups
            for i in range(groups.count):
                g = groups.item(i)
                if g is not None and g.id == gid:
                    return g
            if not create:
                return None
            g = groups.add()
            g.id = gid
            return g
        except Exception:
            return None

    def group_exists(gid):
        return find_group(gid, False) is not None

    def group_visible(gid):
        g = find_group(gid, False)
        if g is None:
            return None
        try:
            return bool(g.isVisible)
        except Exception:
            return None

    def set_group_visible(gid, visible):
        g = find_group(gid, False)
        if g is None:
            return False
        try:
            wanted = bool(visible)
            current = bool(g.isVisible)
            if current == wanted:
                return True
            g.isVisible = wanted
            return bool(g.isVisible) == wanted
        except Exception:
            return False

    def delete_group(gid):
        try:
            design = m._design()
            if design is None:
                return False
            groups = design.rootComponent.customGraphicsGroups
            changed = False
            for i in range(groups.count - 1, -1, -1):
                try:
                    g = groups.item(i)
                    if g is not None and g.id == gid:
                        g.deleteMe()
                        changed = True
                except Exception:
                    pass
            return changed
        except Exception:
            return False

    def render_entry(subject_token, role, create=False):
        rows = state["render"].get(subject_token)
        if rows is None:
            if not create:
                return None
            rows = {}
            state["render"][subject_token] = rows
        row = rows.get(role)
        if row is None and create:
            row = {
                "gid": group_id(subject_token, role),
                "signature": None,
                "visible": False,
                "dirty": True,
            }
            rows[role] = row
        return row

    def render_tokens(role=None):
        out = []
        for tok, rows in state["render"].items():
            if role is None or role in rows:
                out.append(tok)
        return out

    def drop_render(subject_token, role=None, delete_graphics=True):
        rows = state["render"].get(subject_token)
        if not rows:
            return False
        changed = False
        roles = list(rows.keys()) if role is None else [role]
        for r in roles:
            row = rows.pop(r, None)
            if row and delete_graphics:
                changed = delete_group(row.get("gid")) or changed
        if not rows:
            state["render"].pop(subject_token, None)
        return changed

    def reset_runtime_graphics(prefix="FuzzyCAD_Runtime_"):
        try:
            design = m._design()
            if design is None:
                return False
            groups = design.rootComponent.customGraphicsGroups
            changed = False
            for i in range(groups.count - 1, -1, -1):
                try:
                    g = groups.item(i)
                    if g is not None and str(g.id).startswith(prefix):
                        g.deleteMe()
                        changed = True
                except Exception:
                    pass
            for tok in list(state["render"].keys()):
                rows = state["render"].get(tok) or {}
                for role in list(rows.keys()):
                    row = rows.get(role) or {}
                    if str(row.get("gid", "")).startswith(prefix):
                        rows.pop(role, None)
                if not rows:
                    state["render"].pop(tok, None)
            return changed
        except Exception:
            return False

    m._runtime_entity_token = entity_token
    m._runtime_sync_proposals = sync_proposals
    m._runtime_body_signature = body_signature
    m._runtime_body_geometry = body_geometry
    m._runtime_invalidate_geometry = invalidate_geometry
    m._runtime_group_id = group_id
    m._runtime_find_group = find_group
    m._runtime_group_exists = group_exists
    m._runtime_group_visible = group_visible
    m._runtime_set_group_visible = set_group_visible
    m._runtime_delete_group = delete_group
    m._runtime_render_entry = render_entry
    m._runtime_render_tokens = render_tokens
    m._runtime_drop_render = drop_render
    m._runtime_reset_graphics = reset_runtime_graphics

    old_accept = getattr(m, "_accept", None)
    if old_accept is not None:
        def accept(*args, **kwargs):
            invalidate_geometry()
            result = old_accept(*args, **kwargs)
            invalidate_geometry()
            return result
        m._accept = accept
