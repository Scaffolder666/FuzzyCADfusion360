"""Keep Fillet live when Fusion changes the radius input without executePreview.

Observed Fusion behavior for this add-in is asymmetric: Move/Rotate/Scale usually
produce inputChanged followed by executePreview, while the Fillet distance
manipulator can emit repeated inputChanged events without any executePreview.
The old fillet implementation synchronized the proposal/card/candidate only in
executePreview, so the native arrow moved while the mark stayed at its seeded
radius and the viewport candidate never redrew.

This patch treats the native `d` inputChanged event as the authoritative live
Fillet update path.  It also initializes the kernel-measured valid radius range
immediately after edge selection so the handle is clamped before the user drags.
"""

import math

FILLET_MIN_CM = 0.01
SEARCH_STEPS = 8


def install(m):
    adsk = m.adsk
    CurrentInputChanged = m.FuzzyInputChanged
    old_run = m.run
    state = {"busy": False, "seq": 0}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD FILLET LIVE] " + msg)
        except Exception:
            pass

    def resolve_edge(mark):
        if mark is None:
            return None
        g = m._geom.get(mark.get("id"), {})
        fallback = m._entity.get(mark.get("id"))
        try:
            if fallback is not None and fallback.isValid:
                return fallback
        except Exception:
            pass
        token = g.get("edge_token")
        design = m._design()
        if design is None or not token:
            return None
        try:
            for ent in design.findEntityByToken(token):
                if isinstance(ent, adsk.fusion.BRepEdge):
                    return ent
        except Exception:
            pass
        return None

    def valid_radius(mark, radius):
        if radius < FILLET_MIN_CM * 0.5:
            return False
        edge = resolve_edge(mark)
        if edge is None:
            return False
        feat = None
        try:
            fillets = edge.body.parentComponent.features.filletFeatures
            coll = adsk.core.ObjectCollection.create()
            coll.add(edge)
            fi = fillets.createInput()
            fi.addConstantRadiusEdgeSet(
                coll, adsk.core.ValueInput.createByReal(radius), True)
            feat = fillets.add(fi)
            if feat is None:
                return False
            try:
                if feat.healthState == adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState:
                    return False
            except Exception:
                pass
            return True
        except Exception:
            return False
        finally:
            if feat is not None:
                try:
                    feat.deleteMe()
                except Exception:
                    pass

    def find_max_radius(mark):
        g = m._geom.get(mark.get("id"), {})
        existing = g.get("max_radius")
        if existing is not None:
            return existing
        if not valid_radius(mark, FILLET_MIN_CM):
            return None

        body_size = g.get("body_size", mark.get("size", 1.0))
        initial = max(mark.get("amount", 0.2), 0.2)
        cap = max(1.0, body_size * 1.5, initial * 8.0)
        lo = FILLET_MIN_CM
        hi = min(max(initial, FILLET_MIN_CM * 2.0), cap)

        if valid_radius(mark, hi):
            lo = hi
            while hi < cap:
                probe = min(cap, hi * 2.0)
                if probe <= hi + 1e-9:
                    break
                if valid_radius(mark, probe):
                    lo = probe
                    hi = probe
                    if hi >= cap:
                        g["max_radius"] = max(FILLET_MIN_CM, hi * 0.995)
                        return g["max_radius"]
                else:
                    hi = probe
                    break
            if hi == lo:
                g["max_radius"] = max(FILLET_MIN_CM, lo * 0.995)
                return g["max_radius"]

        for _ in range(SEARCH_STEPS):
            mid = (lo + hi) * 0.5
            if valid_radius(mark, mid):
                lo = mid
            else:
                hi = mid
        g["max_radius"] = max(FILLET_MIN_CM, lo * 0.995)
        return g["max_radius"]

    def install_limits(mark):
        max_r = find_max_radius(mark)
        if max_r is None:
            return None
        g = m._geom.get(mark["id"], {})
        it = m._inputs.itemById("d") if m._inputs is not None else None
        if it is not None and not g.get("input_sync_limits_installed"):
            try:
                it.minimumValue = FILLET_MIN_CM
                it.isMinimumValueInclusive = True
                it.maximumValue = max_r
                it.isMaximumValueInclusive = True
                g["input_sync_limits_installed"] = True
            except Exception as exc:
                log("could not install native radius limits mark={}: {}".format(mark["id"], exc))
        return max_r

    def redraw(mark, refresh=False):
        try:
            m._clear(m.GROUP_PREVIEW)
            group = m._group(m.GROUP_PREVIEW)
            m._draw_fillet(group, mark, *m._style(mark))
            m._refresh_ghost()
            m._send_state()
            # Selection is a discrete event, so a forced refresh is safe and makes
            # the first real candidate appear immediately.  During a drag we avoid
            # refresh because Fusion may release a manipulator on forced repaint.
            if refresh:
                m._app.activeViewport.refresh()
            return True
        except Exception as exc:
            log("redraw failed mark={}: {}".format(mark.get("id"), exc))
            return False

    def live_fillet_mark():
        try:
            mid = m._live.get("fillet")
            return m._find(mid) if mid is not None else None
        except Exception:
            return None

    def initialize_after_selection():
        mark = live_fillet_mark()
        if mark is None:
            return
        max_r = install_limits(mark)
        if max_r is None:
            log("selection mark={} could not determine a valid radius range".format(mark["id"]))
            return
        safe = min(max(mark.get("amount", FILLET_MIN_CM), FILLET_MIN_CM), max_r)
        mark["amount"] = safe
        mark["last_valid_amount"] = safe
        try:
            it = m._inputs.itemById("d")
            if it is not None and abs(it.value - safe) > 1e-9:
                it.value = safe
        except Exception:
            pass
        ok = m._compute_real(mark)
        redraw(mark, refresh=True)
        log("FILLET INIT mark={} radius_mm={:.4f} max_mm={:.4f} candidate={}".format(
            mark["id"], safe * 10.0, max_r * 10.0, bool(ok)))

    def sync_from_native_input():
        mark = live_fillet_mark()
        if mark is None:
            return
        max_r = install_limits(mark)
        if max_r is None:
            return

        raw = m._val("d")
        safe = min(max(raw, FILLET_MIN_CM), max_r)
        snapped = abs(safe - raw) > 1e-9
        if snapped:
            try:
                m._inputs.itemById("d").value = safe
            except Exception:
                pass

        mark["amount"] = safe
        candidate_ok = m._compute_real(mark)
        if not candidate_ok:
            last = min(max(mark.get("last_valid_amount", FILLET_MIN_CM), FILLET_MIN_CM), max_r)
            mark["amount"] = last
            safe = last
            snapped = True
            try:
                m._inputs.itemById("d").value = last
            except Exception:
                pass
            candidate_ok = m._compute_real(mark)

        redraw(mark, refresh=False)
        state["seq"] += 1
        log("FILLET INPUT #{:05d} raw_mm={:.4f} synced_mm={:.4f} max_mm={:.4f} snapped={} candidate={} card_mm={:.4f}".format(
            state["seq"], raw * 10.0, safe * 10.0, max_r * 10.0,
            snapped, bool(candidate_ok), mark.get("amount", 0.0) * 10.0))

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass

            super().notify(args)

            if state["busy"]:
                return
            try:
                if getattr(m, "_active_cmd", None) != "fillet":
                    return
                if cid not in ("sel", "d"):
                    return
                state["busy"] = True
                if cid == "sel":
                    initialize_after_selection()
                else:
                    sync_from_native_input()
            except Exception:
                try:
                    log("FILLET LIVE SYNC EXCEPTION\n{}".format(m.traceback.format_exc()))
                except Exception:
                    pass
            finally:
                state["busy"] = False

    m.FuzzyInputChanged = FuzzyInputChanged

    def run(context):
        result = old_run(context)
        log("FILLET LIVE INPUT SYNC READY: d inputChanged drives candidate + card")
        return result

    m.run = run
