"""Render Compare alternatives as full BRep custom graphics.

Compare normally deals with only two alternatives, so preserve the actual model
geometry in the viewport instead of reducing each alternative to sampled edge
polylines.  The placement semantics are intentionally unchanged: the
preserve-orientation patch has already defined Compare as translation-only.

CustomGraphicsGroup.addBRepBody makes its own graphics copy of the source body.
We apply the translation as CustomGraphicsBRepBody.transform, avoiding a second
TemporaryBRepManager copy while still showing the complete BRep.
"""


def install(m):
    adsk = m.adsk

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
                "[FuzzyCAD COMPARE FULL] " + msg)
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

    def style(name, rgb, opacity):
        try:
            st = m.VISUAL_TOKENS.get(name, {})
            return tuple(st.get("rgb", rgb)), float(st.get("opacity", opacity))
        except Exception:
            return rgb, opacity

    def add_full_alt(group, mark, index, rgb, opacity):
        matrix = translation_matrix(mark, index)
        bodies = alt_bodies(mark, index)
        if matrix is None or not bodies:
            return
        for body in bodies:
            try:
                cg = group.addBRepBody(body)
                if cg is None:
                    continue
                cg.transform = matrix
                cg.color = m._solid(rgb)
                cg.setOpacity(opacity, True)
            except Exception:
                log("could not draw full BRep body for alt {}".format(index + 1))

    def draw_target_marker(group, mark):
        # Reuse the same minimal target marker language used by Compare, while
        # the alternatives themselves remain unsimplified BReps.
        vals = mark.get("target_frame")
        if not isinstance(vals, (list, tuple)) or len(vals) != 16:
            return
        try:
            import math
            o = [float(vals[3]), float(vals[7]), float(vals[11])]
            x = [float(vals[0]), float(vals[4]), float(vals[8])]
            y = [float(vals[1]), float(vals[5]), float(vals[9])]
            z = [float(vals[2]), float(vals[6]), float(vals[10])]
            size = float(mark.get("size", 3.0) or 3.0)
            r = float(mark.get("target_connector_radius") or max(size * 0.06, 0.2))
            r = max(0.08, min(r, max(0.2, size * 0.25)))

            def P(axis, scale):
                return tuple(o[i] + axis[i] * scale for i in range(3))

            if hasattr(m, "_visual_stroke"):
                m._visual_stroke(group, [P(z, -r * .35), P(z, r * .75)],
                                 "conflict_marker", mark["id"] * 950001, size=size)
                ring = []
                for i in range(25):
                    a = math.pi * 2.0 * i / 24.0
                    ring.append(tuple(
                        o[j] + (x[j] * math.cos(a) + y[j] * math.sin(a)) * r
                        for j in range(3)))
                m._visual_stroke(group, ring, "conflict_marker",
                                 mark["id"] * 950002, size=size)
        except Exception:
            pass

    def draw_compare(group, mark, rgb, amp):
        a_rgb, a_opacity = style("conflict_alt_a", (126, 104, 180), .18)
        b_rgb, b_opacity = style("conflict_alt_b", (92, 118, 170), .18)
        s_rgb, s_opacity = style("conflict_selected", (72, 76, 80), .42)
        choice = mark.get("selected")
        if choice in (0, 1):
            add_full_alt(group, mark, int(choice), s_rgb, s_opacity)
        else:
            add_full_alt(group, mark, 0, a_rgb, a_opacity)
            add_full_alt(group, mark, 1, b_rgb, b_opacity)
        draw_target_marker(group, mark)

    m._DRAW["compare"] = draw_compare
    m._compare_full_brep_preview = True
    log("READY: Compare shows complete BRep alternatives")
