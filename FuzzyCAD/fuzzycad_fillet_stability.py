"""Keep Fillet uncertainty visible continuously while throttling exact kernel work.

The research interaction needs both layers:
- a continuously visible hand-drawn/provisional rounding representation;
- periodically refreshed exact Fusion geometry so collaborators can still inspect
  the true rounded volume while dragging.

The expensive exact candidate is therefore recomputed at a coarse interval, not
on every inputChanged event.  Between exact refreshes the hand-drawn scaffold
continues to follow the native manipulator immediately.
"""

import time

FILLET_MIN_CM = 0.01
EXACT_REFRESH_SEC = 0.55
MAX_STATIONS = 10
ARC_STEPS = 8


def install(m):
    adsk = m.adsk
    BaseInputChanged = m.FuzzyInputChanged
    LegacyPreview = getattr(m, "_fuzzycad_legacy_preview", None)
    LegacyDrawFillet = getattr(m, "_fuzzycad_legacy_draw_fillet", None)
    old_run = m.run
    state = {"last_exact": 0.0, "busy_exact": False, "last_amount": None}

    if LegacyPreview is not None:
        m.FuzzyPreview = LegacyPreview

    def stroke(group, pts, role, seed, size):
        if not pts or len(pts) < 2:
            return
        try:
            m._visual_stroke(group, pts, role, seed, size=size)
            return
        except Exception:
            pass
        try:
            rgb = (225, 126, 38)
            weight = 2 if role == "affected_boundary" else 1
            m._sketchy(group, pts, rgb, 0.0, seed, weight=weight, strokes=1)
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
            text = group.addText(
                "R ≈ {:.2f} mm".format(float(mark.get("amount", 0.0)) * 10.0),
                "Arial", max(0.42, min(size * 0.09, 0.78)),
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

        edge = g.get("edge") or []
        if edge and len(edge) >= 2:
            stroke(group, edge, "affected_candidate", mid * 73001, size)

        stations = list(g.get("stations") or [])
        if stations and radius > 1e-8:
            step = max(1, int((len(stations) + MAX_STATIONS - 1) / MAX_STATIONS))
            for i, row in enumerate(stations[::step][:MAX_STATIONS]):
                try:
                    P, t1, t2 = row
                    stroke(group, quadratic_arc(P, t1, t2, radius),
                           "affected_boundary", mid * 73100 + i, size)
                except Exception:
                    continue
        elif edge and radius > 1e-8:
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

    if LegacyDrawFillet is not None:
        def draw_fillet(group, mark, rgb, amp):
            g = m._geom.get(mark.get("id"), {}) or {}
            candidate = g.get("candidate_body")
            radius = g.get("candidate_radius")
            amount = float(mark.get("amount", 0.0))
            exact_cached = (candidate is not None and radius is not None and
                            abs(float(radius) - amount) <= 1e-7)

            if exact_cached:
                try:
                    cg = group.addBRepBody(candidate)
                    cg.color = m._solid((190, 190, 186))
                    cg.setOpacity(0.26, True)
                except Exception:
                    pass
                for i, poly in enumerate(g.get("candidate_edges", []) or []):
                    try:
                        m._sketchy(group, poly, rgb, amp,
                                   mark.get("id", 1) * 600 + i,
                                   weight=1, strokes=1)
                    except Exception:
                        pass
                for i, poly in enumerate(g.get("fillet_edges", []) or []):
                    try:
                        m._visual_stroke(group, poly, "affected_boundary",
                                         mark.get("id", 1) * 900 + i,
                                         size=float(mark.get("size", 3.0)))
                    except Exception:
                        pass
            else:
                LegacyDrawFillet(group, mark, rgb, amp)

            # Hand-drawn uncertainty is always present, even on top of exact BRep.
            draw_uncertainty(group, mark)

        m._DRAW["fillet"] = draw_fillet
        m._draw_fillet = draw_fillet

    def live_mark():
        try:
            mid = m._live.get("fillet")
            return m._find(mid) if mid is not None else None
        except Exception:
            return None

    def draw_live(mark):
        if mark is None:
            return
        try:
            m._clear(m.GROUP_PREVIEW)
            group = m._group(m.GROUP_PREVIEW)
            if group is not None:
                m._draw_one(group, mark)
            m._refresh_ghost()
            m._send_state()
        except Exception:
            pass

    def maybe_refresh_exact(mark, force=False):
        if mark is None or state["busy_exact"]:
            return False
        now = time.perf_counter()
        amount = float(mark.get("amount", 0.0))
        changed = (state["last_amount"] is None or
                   abs(float(state["last_amount"]) - amount) > 1e-5)
        if not force and (not changed or now - state["last_exact"] < EXACT_REFRESH_SEC):
            return False
        state["busy_exact"] = True
        try:
            ok = bool(m._compute_real(mark))
            if ok:
                state["last_exact"] = now
                state["last_amount"] = amount
            return ok
        except Exception:
            return False
        finally:
            state["busy_exact"] = False

    class FuzzyInputChanged(BaseInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)
            if getattr(m, "_active_cmd", None) != "fillet":
                return
            try:
                if cid == "sel":
                    it = m._inputs.itemById("d") if m._inputs is not None else None
                    if it is not None:
                        try:
                            it.minimumValue = FILLET_MIN_CM
                            it.isMinimumValueInclusive = True
                        except Exception:
                            pass
                    mark = live_mark()
                    if mark is not None:
                        maybe_refresh_exact(mark, force=True)
                        draw_live(mark)
                    return
                if cid != "d":
                    return
                mark = live_mark()
                if mark is None:
                    return
                amount = max(float(m._val("d")), FILLET_MIN_CM)
                mark["amount"] = amount
                g = m._geom.get(mark.get("id"), {}) or {}
                # Old exact body remains cached but is marked stale until the
                # throttled kernel refresh catches up with the current radius.
                g["candidate_radius"] = None
                g.pop("real", None)
                maybe_refresh_exact(mark, force=False)
                draw_live(mark)
            except Exception:
                pass

    m.FuzzyInputChanged = FuzzyInputChanged

    def run(context):
        return old_run(context)

    m.run = run
