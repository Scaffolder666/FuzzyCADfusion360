"""Strengthen the Fillet visual language without changing other tools.

The exact fillet candidate already exists as a transient BRep. This patch uses
its cached fillet-boundary edges to identify corresponding candidate faces and
overlays those rounded surfaces. Color/opacity/boundary styling are supplied by
the centralized visual system.
"""


def install(m):
    adsk = m.adsk
    previous_draw = m._DRAW.get("fillet")
    old_run = m.run

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD FILLET COLOR] " + msg)
        except Exception:
            pass

    def vstyle(role, fallback):
        try:
            return m._visual_style(role)
        except Exception:
            return fallback

    def point_key(p, digits=4):
        return (round(float(p[0]), digits),
                round(float(p[1]), digits),
                round(float(p[2]), digits))

    def poly_signature(poly):
        if not poly or len(poly) < 2:
            return None
        a = point_key(poly[0])
        b = point_key(poly[-1])
        return tuple(sorted((a, b)))

    def target_signatures(g):
        out = set()
        for poly in g.get("fillet_edges", []) or []:
            sig = poly_signature(poly)
            if sig is not None:
                out.add(sig)
        return out

    def find_fillet_face_bodies(mark):
        g = m._geom.get(mark.get("id"), {})
        radius = g.get("candidate_radius")
        cached_radius = g.get("fillet_color_radius")
        cached = g.get("fillet_face_bodies")
        if cached is not None and cached_radius is not None and radius is not None:
            if abs(float(cached_radius) - float(radius)) <= 1e-7:
                return cached

        candidate = g.get("candidate_body")
        targets = target_signatures(g)
        if candidate is None or not targets:
            g["fillet_face_bodies"] = []
            g["fillet_color_radius"] = radius
            return []

        mgr = adsk.fusion.TemporaryBRepManager.get()
        found = []
        best = []
        try:
            for i in range(candidate.faces.count):
                face = candidate.faces.item(i)
                matched = 0
                total = 0
                try:
                    for j in range(face.edges.count):
                        edge = face.edges.item(j)
                        poly = m._sample_edge(edge)
                        sig = poly_signature(poly)
                        if sig is None:
                            continue
                        total += 1
                        if sig in targets:
                            matched += 1
                except Exception:
                    continue
                if matched:
                    best.append((matched, total, face))

            if best:
                max_match = max(row[0] for row in best)
                for matched, total, face in best:
                    strong = matched >= 2 and matched >= max(2, total // 2)
                    strongest = matched == max_match and max_match >= 2
                    if not (strong or strongest):
                        continue
                    try:
                        fb = mgr.copy(face)
                        if fb is not None:
                            found.append(fb)
                    except Exception:
                        pass
        except Exception:
            found = []

        g["fillet_face_bodies"] = found
        g["fillet_color_radius"] = radius
        log("mark={} colored_faces={} radius_mm={:.3f}".format(
            mark.get("id"), len(found), float(mark.get("amount", 0.0)) * 10.0))
        return found

    def draw_colored_region(group, mark):
        face_style = vstyle("affected_surface", {"rgb": (235, 132, 42), "opacity": 0.46})
        face_rgb = tuple(face_style.get("rgb", (235, 132, 42)))
        face_opacity = float(face_style.get("opacity", 0.46))
        for body in find_fillet_face_bodies(mark):
            try:
                cg = group.addBRepBody(body)
                cg.color = m._solid(face_rgb)
                cg.setOpacity(face_opacity, True)
                try:
                    cg.depthPriority = 5
                except Exception:
                    pass
            except Exception:
                pass

        size = float(mark.get("size", 3.0))
        g = m._geom.get(mark.get("id"), {})
        for i, poly in enumerate(g.get("fillet_edges", []) or []):
            try:
                m._visual_stroke(group, poly, "affected_boundary",
                                 mark.get("id", 1) * 19001 + i, size=size)
            except Exception:
                m._sketchy(group, poly, (245, 118, 24), 0.0,
                           mark.get("id", 1) * 19001 + i,
                           weight=2, strokes=1)

    def draw_fillet(group, mark, rgb, amp):
        if previous_draw is not None:
            previous_draw(group, mark, rgb, amp)
        draw_colored_region(group, mark)

    m._DRAW["fillet"] = draw_fillet
    m._draw_fillet = draw_fillet

    def run(context):
        result = old_run(context)
        log("FILLET COLOR READY: affected surface/boundary use central visual tokens")
        return result

    m.run = run
