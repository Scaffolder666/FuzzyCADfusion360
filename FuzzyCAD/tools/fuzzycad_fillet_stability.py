"""Fillet-specific uncertainty variation.

Fillet keeps its cheap local rounding scaffold/callout and throttled exact kernel
preview, but lifecycle policy comes from the central uncertainty visual authority:
- Editing: clean/live Fillet detail + exact translucent candidate.
- Proposed: common comic fill + sketch boundary, plus cheap Fillet detail.
- Resolved: no Fillet overlay.

The exact solid uses the runtime render registry and stores no long-lived Fusion
CustomGraphics wrapper objects.
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
    old_stop = m.stop
    state = {"last_exact": 0.0, "busy_exact": False, "last_amount": None,
             "candidate_epoch": {}}

    if LegacyPreview is not None:
        m.FuzzyPreview = LegacyPreview

    def visual_state(mark):
        try:
            return m._visual_state(mark)
        except Exception:
            ph = "resolved"
            try:
                ph = m._mark_phase(mark)
            except Exception:
                if mark is not None and mark.get("status", "open") == "open":
                    ph = "proposed"
            return {
                "phase": ph,
                "show_detail": ph != "resolved",
                "show_exact_fillet": ph == "editing",
            }

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
        if not visual_state(mark).get("show_detail", True):
            return
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
            if not visual_state(mark).get("show_detail", True):
                return
            g = m._geom.get(mark.get("id"), {}) or {}
            candidate = g.get("candidate_body")
            candidate_radius = g.get("candidate_radius")
            amount = float(mark.get("amount", 0.0))
            exact_fresh = (candidate is not None and candidate_radius is not None and
                           abs(float(candidate_radius) - amount) <= 1e-7)

            # Exact BRep volume is owned by the editing-only persistent visual
            # below. This GROUP_MARKS/PREVIEW renderer draws cheap line detail only.
            if candidate is not None:
                for i, poly in enumerate(g.get("candidate_edges", []) or []):
                    try:
                        m._sketchy(group, poly, rgb, amp,
                                   mark.get("id", 1) * 600 + i,
                                   weight=1, strokes=1)
                    except Exception:
                        pass
                if exact_fresh:
                    for i, poly in enumerate(g.get("fillet_edges", []) or []):
                        try:
                            m._visual_stroke(group, poly, "affected_boundary",
                                             mark.get("id", 1) * 900 + i,
                                             size=float(mark.get("size", 3.0)))
                        except Exception:
                            pass
            else:
                LegacyDrawFillet(group, mark, rgb, amp)

            draw_uncertainty(group, mark)

        m._DRAW["fillet"] = draw_fillet
        m._draw_fillet = draw_fillet

    # ---- editing-only exact Fillet visual ---------------------------------
    # Runtime registry stores mark-key/group-id/signature/visibility only. Fusion
    # CustomGraphics objects are resolved fresh and never retained in Python.
    ROLE = "fillet_exact"

    def render_key(mid):
        return "mark:{}".format(int(mid))

    def mark_from_key(key):
        try:
            return m._find(int(str(key).split(":", 1)[1]))
        except Exception:
            return None

    def bump_candidate(mid):
        try:
            mid = int(mid)
            state["candidate_epoch"][mid] = int(state["candidate_epoch"].get(mid, 0)) + 1
        except Exception:
            pass

    def build_exact_group(mark, cand, sig):
        mid = mark.get("id")
        key = render_key(mid)
        entry = m._runtime_render_entry(key, ROLE, True)
        gid = entry["gid"]
        m._runtime_delete_group(gid)
        group = m._runtime_find_group(gid, True)
        if group is None:
            return False
        try:
            cg = group.addBRepBody(cand)
            cg.color = m._solid((190, 190, 186))
            cg.setOpacity(0.26, True)
        except Exception:
            return False
        entry["signature"] = sig
        entry["visible"] = True
        entry["dirty"] = False
        return True

    def sync_fillet_solids():
        # runtime store is installed by the final visual layer before commands run
        if not hasattr(m, "_runtime_render_entry"):
            return

        marks = {}
        retained = set()
        current = {}
        for mark in list(getattr(m, "_marks", None) or []):
            try:
                if mark.get("tool") != "fillet" or mark.get("status", "open") != "open":
                    continue
                mid = int(mark.get("id"))
                key = render_key(mid)
                retained.add(key)
                marks[key] = mark
                if not visual_state(mark).get("show_exact_fillet", False):
                    continue
                g = m._geom.get(mid, {}) or {}
                cand = g.get("candidate_body")
                if cand is None:
                    continue
                radius = float(g.get("candidate_radius") or mark.get("amount", 0.0))
                epoch = int(state["candidate_epoch"].get(mid, 0))
                sig = (radius, epoch, len(g.get("candidate_edges", []) or []))
                current[key] = (mark, cand, sig)
            except Exception:
                continue

        # Resolved/deleted marks drop exact groups. Proposed marks retain them
        # hidden so reopening is a visibility toggle when the candidate is valid.
        for key in list(m._runtime_render_tokens(ROLE)):
            if key not in retained:
                m._runtime_drop_render(key, ROLE, True)
                continue
            if key not in current:
                entry = m._runtime_render_entry(key, ROLE, False)
                if entry is not None and m._runtime_group_exists(entry.get("gid")):
                    actual = m._runtime_group_visible(entry["gid"])
                    if entry.get("visible", False) or actual is True:
                        if m._runtime_set_group_visible(entry["gid"], False):
                            entry["visible"] = False

        for key, (mark, cand, sig) in current.items():
            entry = m._runtime_render_entry(key, ROLE, True)
            gid = entry["gid"]
            exists = m._runtime_group_exists(gid)
            if (not exists) or entry.get("signature") != sig or entry.get("dirty", True):
                build_exact_group(mark, cand, sig)
                continue
            actual = m._runtime_group_visible(gid)
            if not entry.get("visible", False) or actual is False:
                if m._runtime_set_group_visible(gid, True):
                    entry["visible"] = True

    m._sync_fillet_solids = sync_fillet_solids

    old_compute_real = getattr(m, "_compute_real", None)
    if old_compute_real is not None:
        def compute_real_synced(mark):
            ok = old_compute_real(mark)
            try:
                if mark is not None and mark.get("tool") == "fillet":
                    bump_candidate(mark.get("id"))
                    sync_fillet_solids()
            except Exception:
                pass
            return ok
        m._compute_real = compute_real_synced

    old_redraw_marks = m._redraw_marks
    def redraw_marks_synced(*a, **k):
        r = old_redraw_marks(*a, **k)
        try:
            sync_fillet_solids()
        except Exception:
            pass
        return r
    m._redraw_marks = redraw_marks_synced

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
                m._geom.get(mark.get("id"), {}).pop("real", None)
                maybe_refresh_exact(mark, force=False)
                draw_live(mark)
            except Exception:
                pass

    m.FuzzyInputChanged = FuzzyInputChanged

    def run(context):
        result = old_run(context)
        try:
            if hasattr(m, "_runtime_reset_graphics"):
                m._runtime_reset_graphics("FuzzyCAD_Runtime_fillet_exact_")
            sync_fillet_solids()
        except Exception:
            pass
        return result

    m.run = run

    def stop(context):
        try:
            if hasattr(m, "_runtime_reset_graphics"):
                m._runtime_reset_graphics("FuzzyCAD_Runtime_fillet_exact_")
        except Exception:
            pass
        return old_stop(context)

    m.stop = stop
