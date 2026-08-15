"""Keep Fillet's uncertainty representation visible at all times.

FuzzyCAD's research contribution depends on provisional geometry looking
provisional.  Exact kernel candidates are useful, but they must not be the only
way the rounding intent becomes visible.  This layer therefore adds a cheap,
hand-drawn local rounding scaffold on every Fillet draw.  It uses only the
already-sampled edge/station geometry and never creates or deletes Fusion
features.

During direct manipulation:
    original edge locus + sparse orange rounding arcs + radius callout
At a settle point, if an exact TemporaryBRep candidate is already cached:
    the same hand-drawn scaffold remains on top of the exact local surface

This keeps the uncertainty visual continuous while the expensive modeling
kernel runs only at discrete settle points.
"""


def install(m):
    adsk = m.adsk
    previous_draw = m._DRAW.get("fillet")
    old_run = m.run

    MAX_STATIONS = 10
    ARC_STEPS = 8

    def stroke(group, pts, role, seed, size):
        if not pts or len(pts) < 2:
            return
        try:
            m._visual_stroke(group, pts, role, seed, size=size)
        except Exception:
            rgb = (225, 126, 38)
            weight = 2 if role == "affected_boundary" else 1
            try:
                m._sketchy(group, pts, rgb, 0.0, seed,
                           weight=weight, strokes=1)
            except Exception:
                pass

    def quadratic_arc(P, t1, t2, radius):
        a = (P[0] + t1[0] * radius,
             P[1] + t1[1] * radius,
             P[2] + t1[2] * radius)
        b = (P[0] + t2[0] * radius,
             P[1] + t2[1] * radius,
             P[2] + t2[2] * radius)
        pts = []
        for k in range(ARC_STEPS + 1):
            u = k / float(ARC_STEPS)
            v = 1.0 - u
            pts.append((v * v * a[0] + 2.0 * v * u * P[0] + u * u * b[0],
                        v * v * a[1] + 2.0 * v * u * P[1] + u * u * b[1],
                        v * v * a[2] + 2.0 * v * u * P[2] + u * u * b[2]))
        return pts

    def radius_callout(group, mark):
        try:
            anchor = list(mark.get("anchor") or [0.0, 0.0, 0.0])
            size = float(mark.get("size", 3.0))
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            d = max(0.75, min(size * 0.25, 2.2))
            tip = (anchor[0] + (0.78 * xx + 0.45 * yx) * d,
                   anchor[1] + (0.78 * xy + 0.45 * yy) * d,
                   anchor[2] + (0.78 * xz + 0.45 * yz) * d)
            stroke(group, [tuple(anchor), tip], "operation_cue",
                   mark.get("id", 1) * 73103, size)
            p = adsk.core.Point3D.create(*tip)
            txt = "R ≈ {:.2f} mm".format(float(mark.get("amount", 0.0)) * 10.0)
            text = group.addText(
                txt, "Arial", max(0.42, min(size * 0.09, 0.78)),
                m._label_transform(p))
            try:
                text.color = m._solid(m._visual_color("operation_cue"))
            except Exception:
                text.color = m._solid((225, 126, 38))
            m._apply_billboard(text, p)
        except Exception:
            pass

    def draw_uncertainty(group, mark):
        mid = mark.get("id")
        g = m._geom.get(mid, {}) or {}
        size = float(mark.get("size", 3.0))
        radius = max(0.0, float(mark.get("amount", 0.0)))

        # The selected edge remains the change locus even when an exact candidate
        # is cached.  This preserves the visual distinction between current shape
        # and the local decision being negotiated.
        edge = g.get("edge") or []
        if edge and len(edge) >= 2:
            stroke(group, edge, "affected_candidate", mid * 73001, size)

        stations = list(g.get("stations") or [])
        if stations and radius > 1e-8:
            step = max(1, int((len(stations) + MAX_STATIONS - 1) / MAX_STATIONS))
            shown = stations[::step][:MAX_STATIONS]
            for i, row in enumerate(shown):
                try:
                    P, t1, t2 = row
                    arc = quadratic_arc(P, t1, t2, radius)
                    stroke(group, arc, "affected_boundary",
                           mid * 73100 + i, size)
                except Exception:
                    continue
        elif edge and radius > 1e-8:
            # Curved/non-station edge fallback: keep the locus visibly unresolved
            # without inventing an expensive surface. Sparse short ticks communicate
            # that the edge is being rounded while the exact surface waits for settle.
            n = len(edge)
            if n >= 2:
                try:
                    (xx, xy, xz), _ = m._camera_xy()
                    tick = max(0.08, min(radius * 0.30, size * 0.035, 0.30))
                    picks = sorted(set([0, n // 4, n // 2, (3 * n) // 4, n - 1]))
                    for i, idx in enumerate(picks):
                        p = edge[min(max(idx, 0), n - 1)]
                        a = (p[0] - xx * tick, p[1] - xy * tick, p[2] - xz * tick)
                        b = (p[0] + xx * tick, p[1] + xy * tick, p[2] + xz * tick)
                        stroke(group, [a, b], "affected_boundary",
                               mid * 73200 + i, size)
                except Exception:
                    pass

        radius_callout(group, mark)

    def draw_fillet(group, mark, rgb, amp):
        if previous_draw is not None:
            previous_draw(group, mark, rgb, amp)
        draw_uncertainty(group, mark)

    m._DRAW["fillet"] = draw_fillet
    m._draw_fillet = draw_fillet

    def run(context):
        return old_run(context)

    m.run = run
