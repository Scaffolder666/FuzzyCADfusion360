"""Treat a Compare alternative as its containing component/occurrence when possible.

The connector is still picked on one face/edge, but the clicked body is only the
anchor used to discover the alternative scope:
- body in an assembly occurrence -> all BRep bodies in that occurrence;
- body in a non-root component -> all BRep bodies in that component;
- body in the root component -> that body only.

All bodies in the alternative receive the same connector placement transform, so
multi-body alternatives preview and commit as one design option without requiring
Ctrl/multi-selection.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept
    old_public = m._public
    old_draw = m._DRAW.get("compare")
    thumb_cache = {}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMPARE GROUP] " + msg)
        except Exception:
            pass

    def token(ent):
        if ent is None:
            return None
        try:
            return ent.entityToken
        except Exception:
            return None

    def resolve_body(tok):
        if not tok:
            return None
        try:
            for ent in m._design().findEntityByToken(tok):
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def collection_items(coll):
        out = []
        try:
            for i in range(coll.count):
                item = coll.item(i)
                if item is not None:
                    out.append(item)
        except Exception:
            pass
        return out

    def same_component(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        ta, tb = token(a), token(b)
        return bool(ta and tb and ta == tb)

    def group_for_body(body):
        """Return one semantic alternative group around the clicked body."""
        if body is None:
            return {"kind": "missing", "name": "Alternative", "bodies": [], "key": None}

        # In assembly context, use the occurrence proxies. They preserve the
        # occurrence transform, which matches the world-space connector frame.
        try:
            occ = body.assemblyContext
        except Exception:
            occ = None
        if occ is not None:
            try:
                bodies = collection_items(occ.bRepBodies)
            except Exception:
                bodies = []
            if bodies:
                return {
                    "kind": "occurrence",
                    "name": getattr(occ, "name", "Alternative"),
                    "bodies": bodies,
                    "key": token(occ) or "occ:{}".format(id(occ)),
                }

        try:
            comp = body.parentComponent
            root = m._design().rootComponent
        except Exception:
            comp = None; root = None
        if comp is not None and not same_component(comp, root):
            bodies = collection_items(getattr(comp, "bRepBodies", None))
            if bodies:
                return {
                    "kind": "component",
                    "name": getattr(comp, "name", "Alternative"),
                    "bodies": bodies,
                    "key": token(comp) or "comp:{}".format(id(comp)),
                }

        return {
            "kind": "body",
            "name": getattr(body, "name", "Alternative"),
            "bodies": [body],
            "key": token(body),
        }

    def clicked_body(mark, index):
        try:
            geom = m._geom.get(mark.get("id"), {})
            alts = geom.get("alternatives") or []
            if index < len(alts) and alts[index] is not None:
                return alts[index]
        except Exception:
            pass
        try:
            alt = (mark.get("alternatives") or [])[index]
            return resolve_body(alt.get("token"))
        except Exception:
            return None

    def alternative_group(mark, index):
        return group_for_body(clicked_body(mark, index))

    def list_to_matrix(vals):
        mat = adsk.core.Matrix3D.create()
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return None
        k = 0
        try:
            for r in range(4):
                for c in range(4):
                    mat.setCell(r, c, float(vals[k])); k += 1
            return mat
        except Exception:
            return None

    def matrix_to_list(mat):
        return [float(mat.getCell(r, c)) for r in range(4) for c in range(4)]

    def mat_mul(a, b):
        return [sum(float(a[r * 4 + k]) * float(b[k * 4 + c]) for k in range(4))
                for r in range(4) for c in range(4)]

    def placement_matrix(mark, index):
        try:
            target = list(mark.get("target_frame"))
            source = list((mark.get("alternatives") or [])[index].get("connector_frame"))
        except Exception:
            return None
        if len(target) != 16 or len(source) != 16:
            return None
        sm = list_to_matrix(source)
        if sm is None:
            return None
        try:
            if not sm.invert():
                return None
            inv_source = matrix_to_list(sm)
        except Exception:
            return None
        identity = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
        flip = [1,0,0,0, 0,-1,0,0, 0,0,-1,0, 0,0,0,1]
        local = flip if mark.get("oppose_normals", True) else identity
        return list_to_matrix(mat_mul(mat_mul(target, local), inv_source))

    def conflict_style(name, rgb, opacity):
        try:
            st = m.VISUAL_TOKENS.get(name, {})
            return tuple(st.get("rgb", rgb)), float(st.get("opacity", opacity))
        except Exception:
            return rgb, opacity

    def add_group_graphics(group, bodies, matrix, rgb, opacity):
        if group is None or matrix is None:
            return
        try:
            mgr = adsk.fusion.TemporaryBRepManager.get()
        except Exception:
            return
        for body in bodies:
            try:
                temp = mgr.copy(body)
                if temp is None or not mgr.transform(temp, matrix):
                    continue
                cg = group.addBRepBody(temp)
                cg.color = m._solid(rgb)
                cg.setOpacity(opacity, True)
            except Exception:
                continue

    def draw_target_connector(group, mark):
        vals = mark.get("target_frame")
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return
        o = [float(vals[3]), float(vals[7]), float(vals[11])]
        x = [float(vals[0]), float(vals[4]), float(vals[8])]
        y = [float(vals[1]), float(vals[5]), float(vals[9])]
        z = [float(vals[2]), float(vals[6]), float(vals[10])]
        size = float(mark.get("size", 3.0) or 3.0)
        r = float(mark.get("target_connector_radius") or max(size * 0.08, 0.25))
        r = max(0.08, min(r, max(0.2, size * 0.3)))

        def P(axis, scale):
            return tuple(o[i] + axis[i] * scale for i in range(3))

        try:
            m._visual_stroke(group, [P(z, -r * .55), P(z, r * .90)],
                             "conflict_marker", mark["id"] * 77001, size=size)
            ring = []
            for i in range(33):
                a = math.pi * 2.0 * i / 32.0
                ring.append(tuple(o[j] + (x[j] * math.cos(a) + y[j] * math.sin(a)) * r
                                  for j in range(3)))
            m._visual_stroke(group, ring, "conflict_marker",
                             mark["id"] * 77002, size=size)
        except Exception:
            pass

    def draw_compare(group, mark, rgb, amp):
        ga = alternative_group(mark, 0)
        gb = alternative_group(mark, 1)
        if not ga["bodies"] or not gb["bodies"]:
            if old_draw:
                return old_draw(group, mark, rgb, amp)
            draw_target_connector(group, mark)
            return
        sa = conflict_style("conflict_alt_a", (126, 104, 180), .18)
        sb = conflict_style("conflict_alt_b", (92, 118, 170), .18)
        ss = conflict_style("conflict_selected", (92, 96, 104), .42)
        choice = mark.get("selected")
        if choice in (0, 1):
            g = ga if int(choice) == 0 else gb
            add_group_graphics(group, g["bodies"], placement_matrix(mark, int(choice)), ss[0], ss[1])
        else:
            add_group_graphics(group, ga["bodies"], placement_matrix(mark, 0), sa[0], sa[1])
            add_group_graphics(group, gb["bodies"], placement_matrix(mark, 1), sb[0], sb[1])
        draw_target_connector(group, mark)

    def thumb_for_bodies(bodies):
        keys = tuple(token(b) or str(id(b)) for b in bodies)
        if keys in thumb_cache:
            return thumb_cache[keys]
        polylines = []
        remaining = 96
        for body in bodies:
            if remaining <= 0:
                break
            try:
                take = min(int(body.edges.count), remaining)
                for i in range(take):
                    pts = m._sample_edge(body.edges.item(i), 6)
                    if len(pts) >= 2:
                        polylines.append(pts)
                remaining -= take
            except Exception:
                pass
        if not polylines:
            return []
        projected, xs, ys = [], [], []
        for poly in polylines:
            row = []
            for px, py, pz in poly:
                u = (px - py) * 0.8660254038
                v = (px + py) * 0.50 - pz
                row.append((u, v)); xs.append(u); ys.append(v)
            projected.append(row)
        xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
        w = max(xmax - xmin, 1e-6); h = max(ymax - ymin, 1e-6)
        scale = min(88.0 / w, 58.0 / h)
        ox = 50.0 - (xmin + xmax) * .5 * scale
        oy = 35.0 - (ymin + ymax) * .5 * scale
        result = [[[round(u * scale + ox, 2), round(v * scale + oy, 2)] for u, v in row]
                  for row in projected]
        thumb_cache[keys] = result
        return result

    def public(mark):
        out = old_public(mark)
        if mark.get("tool") != "compare":
            return out
        alts = []
        source = out.get("alternatives") or mark.get("alternatives") or []
        for i, alt in enumerate(source[:2]):
            row = dict(alt) if isinstance(alt, dict) else {"name": "Alternative {}".format(i + 1)}
            g = alternative_group(mark, i)
            if g["bodies"]:
                if len(g["bodies"]) > 1:
                    row["name"] = g["name"]
                row["body_count"] = len(g["bodies"])
                row["scope"] = g["kind"]
                row["thumb"] = thumb_for_bodies(g["bodies"])
            alts.append(row)
        out["alternatives"] = alts
        return out

    def accept(mark):
        if mark.get("tool") != "compare":
            return old_accept(mark)
        choice = mark.get("selected")
        if choice not in (0, 1):
            try:
                m._ui.messageBox("Choose Alternative 1 or Alternative 2 first.")
            except Exception:
                pass
            return False
        idx = int(choice)
        g = alternative_group(mark, idx)
        if not g["bodies"]:
            return False
        matrix = placement_matrix(mark, idx)
        if matrix is None:
            return False
        root = m._design().rootComponent
        placed = []
        try:
            for body in g["bodies"]:
                copy = body.copyToComponent(root)
                if copy is None:
                    raise RuntimeError("Could not copy one body in the alternative")
                placed.append(copy)
            coll = adsk.core.ObjectCollection.create()
            for body in placed:
                coll.add(body)
            moves = root.features.moveFeatures
            moves.add(moves.createInput(coll, matrix))
            for body in placed:
                try:
                    body.name = "{} / {}".format(g["name"], body.name)
                except Exception:
                    pass
            log("ACCEPT alternative={} scope={} bodies={}".format(idx + 1, g["kind"], len(placed)))
            return True
        except Exception:
            for body in placed:
                try: body.deleteMe()
                except Exception: pass
            try:
                m._ui.messageBox("FuzzyCAD couldn't place this multi-body alternative:\n{}".format(
                    m.traceback.format_exc()))
            except Exception:
                pass
            return False

    m._DRAW["compare"] = draw_compare
    m._public = public
    m._accept = accept
    m._compare_alternative_group = alternative_group
    log("READY: Compare automatically expands clicked bodies to their component/occurrence")
