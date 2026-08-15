"""Keep Compare alternatives in their current orientation.

The connector click should identify *where* an alternative connects, not silently
redefine its roll/orientation.  The stable Compare command still records face/edge
frames for the marker and persistence, but placement uses connector centers only:
translate source connector center onto target connector center and preserve the
alternative's existing world orientation exactly.

This patch does not own/register the Compare command.  It only replaces Compare
preview/commit placement semantics after fuzzycad_compare_stable is installed.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept
    preview_cache = {}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log(
                "[FuzzyCAD COMPARE ORIENTATION] " + msg)
        except Exception:
            pass

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

    def alt_bodies(mark, index):
        try:
            alt = (mark.get("alternatives") or [])[index]
        except Exception:
            return []
        toks = alt.get("body_tokens") or []
        bodies = [resolve_body(t) for t in toks]
        bodies = [b for b in bodies if b is not None]
        if bodies:
            return bodies
        clicked = resolve_body(alt.get("token"))
        return [clicked] if clicked is not None else []

    def translation_values(mark, index):
        """Identity rotation + source-connector-center -> target-center translation."""
        try:
            target = list(mark.get("target_frame") or [])
            source = list((mark.get("alternatives") or [])[index].get("connector_frame") or [])
        except Exception:
            return None
        if len(target) != 16 or len(source) != 16:
            return None
        dx = float(target[3]) - float(source[3])
        dy = float(target[7]) - float(source[7])
        dz = float(target[11]) - float(source[11])
        return [1.0, 0.0, 0.0, dx,
                0.0, 1.0, 0.0, dy,
                0.0, 0.0, 1.0, dz,
                0.0, 0.0, 0.0, 1.0]

    def translation_matrix(mark, index):
        vals = translation_values(mark, index)
        if vals is None:
            return None
        mat = adsk.core.Matrix3D.create()
        k = 0
        try:
            for r in range(4):
                for c in range(4):
                    mat.setCell(r, c, float(vals[k]))
                    k += 1
            return mat
        except Exception:
            return None

    def moved_point(vals, p):
        return (p[0] + vals[3], p[1] + vals[7], p[2] + vals[11])

    def body_key(body):
        try:
            return body.entityToken
        except Exception:
            return str(id(body))

    def sample_group(bodies, max_edges=140):
        key = tuple(body_key(b) for b in bodies)
        cached = preview_cache.get(key)
        if cached is not None:
            return cached
        rows = []
        remaining = int(max_edges)
        for body in bodies:
            if remaining <= 0:
                break
            try:
                take = min(int(body.edges.count), remaining)
                for i in range(take):
                    pts = m._sample_edge(body.edges.item(i), 6)
                    if len(pts) >= 2:
                        rows.append(pts)
                remaining -= take
            except Exception:
                pass
        preview_cache[key] = rows
        return rows

    def style(name, rgb):
        try:
            return tuple(m.VISUAL_TOKENS.get(name, {}).get("rgb", rgb))
        except Exception:
            return rgb

    def draw_alt(group, mark, index, rgb, weight):
        vals = translation_values(mark, index)
        bodies = alt_bodies(mark, index)
        if vals is None or not bodies:
            return
        for j, poly in enumerate(sample_group(bodies)):
            try:
                pts = [moved_point(vals, p) for p in poly]
                m._sketchy(group, pts, rgb, 0.0,
                            mark.get("id", 1) * 930007 + index * 5003 + j,
                            weight=weight, strokes=1)
            except Exception:
                pass

    def draw_target_marker(group, mark):
        vals = mark.get("target_frame")
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return
        o = [float(vals[3]), float(vals[7]), float(vals[11])]
        x = [float(vals[0]), float(vals[4]), float(vals[8])]
        y = [float(vals[1]), float(vals[5]), float(vals[9])]
        z = [float(vals[2]), float(vals[6]), float(vals[10])]
        size = float(mark.get("size", 3.0) or 3.0)
        r = float(mark.get("target_connector_radius") or max(size * 0.06, 0.2))
        r = max(0.08, min(r, max(0.2, size * 0.25)))

        def P(axis, scale):
            return tuple(o[i] + axis[i] * scale for i in range(3))

        try:
            if hasattr(m, "_visual_stroke"):
                m._visual_stroke(group, [P(z, -r * .35), P(z, r * .75)],
                                 "conflict_marker", mark["id"] * 940001,
                                 size=size)
                ring = []
                for i in range(25):
                    a = math.pi * 2.0 * i / 24.0
                    ring.append(tuple(
                        o[j] + (x[j] * math.cos(a) + y[j] * math.sin(a)) * r
                        for j in range(3)))
                m._visual_stroke(group, ring, "conflict_marker",
                                 mark["id"] * 940002, size=size)
        except Exception:
            pass

    def draw_compare(group, mark, rgb, amp):
        a_rgb = style("conflict_alt_a", (126, 104, 180))
        b_rgb = style("conflict_alt_b", (92, 118, 170))
        s_rgb = style("conflict_selected", (72, 76, 80))
        choice = mark.get("selected")
        if choice in (0, 1):
            draw_alt(group, mark, int(choice), s_rgb, 2)
        else:
            draw_alt(group, mark, 0, a_rgb, 1)
            draw_alt(group, mark, 1, b_rgb, 1)
        draw_target_marker(group, mark)

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
        bodies = alt_bodies(mark, idx)
        matrix = translation_matrix(mark, idx)
        if not bodies or matrix is None:
            return False
        root = m._design().rootComponent
        placed = []
        try:
            for body in bodies:
                cp = body.copyToComponent(root)
                if cp is None:
                    raise RuntimeError("Could not copy one body in the selected alternative")
                placed.append(cp)
            coll = adsk.core.ObjectCollection.create()
            for body in placed:
                coll.add(body)
            moves = root.features.moveFeatures
            moves.add(moves.createInput(coll, matrix))
            log("ACCEPT preserve-orientation choice={} bodies={}".format(
                idx + 1, len(placed)))
            return True
        except Exception:
            for body in placed:
                try:
                    body.deleteMe()
                except Exception:
                    pass
            try:
                m._ui.messageBox(
                    "FuzzyCAD couldn't place this alternative:\n{}".format(
                        m.traceback.format_exc()))
            except Exception:
                pass
            return False

    m._DRAW["compare"] = draw_compare
    m._accept = accept
    m._compare_preserve_orientation = True
    log("READY: Compare connector clicks translate only; current orientation is preserved")
