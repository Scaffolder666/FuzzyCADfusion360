"""Keep Fillet direct manipulation live without rebuilding real Fusion features per frame.

The exact-candidate patch can build/delete a temporary fillet to validate and
capture geometry. Doing that from inputChanged/executePreview is unsafe on large
or imported models and can destabilize Fusion. This patch restores the legacy
lightweight rolling-ball sketch during drag. Exact BRep computation remains
available at discrete settle points (command execute, card edit, manipulator
close) through m._compute_real.
"""

FILLET_MIN_CM = 0.01


def install(m):
    adsk = m.adsk
    BaseInputChanged = m.FuzzyInputChanged
    LegacyPreview = getattr(m, "_fuzzycad_legacy_preview", None)
    LegacyDrawFillet = getattr(m, "_fuzzycad_legacy_draw_fillet", None)
    old_run = m.run

    if LegacyPreview is not None:
        m.FuzzyPreview = LegacyPreview

    if LegacyDrawFillet is not None:
        def draw_fillet(group, mark, rgb, amp):
            g = m._geom.get(mark.get("id"), {}) or {}
            # If an exact candidate was computed at a discrete settle point,
            # render the cached TemporaryBRep. Never compute it from draw().
            candidate = g.get("candidate_body")
            radius = g.get("candidate_radius")
            amount = float(mark.get("amount", 0.0))
            if candidate is not None and radius is not None and abs(float(radius) - amount) <= 1e-7:
                try:
                    cg = group.addBRepBody(candidate)
                    cg.color = m._solid((190, 190, 186))
                    cg.setOpacity(0.30, True)
                except Exception:
                    pass
                for i, poly in enumerate(g.get("candidate_edges", []) or []):
                    try:
                        m._sketchy(group, poly, rgb, amp,
                                   mark.get("id", 1) * 600 + i,
                                   weight=1, strokes=2)
                    except Exception:
                        pass
                for i, poly in enumerate(g.get("fillet_edges", []) or []):
                    try:
                        m._sketchy(group, poly, (225, 126, 38), amp * 0.35,
                                   mark.get("id", 1) * 900 + i,
                                   weight=2, strokes=1)
                    except Exception:
                        pass
                return
            # During drag, use the original lightweight geometric approximation.
            LegacyDrawFillet(group, mark, rgb, amp)

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
            # Do not force activeViewport.refresh() in the drag path. Fusion's
            # command preview cycle owns repainting and retains the manipulator.
        except Exception:
            pass

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
                    return
                if cid != "d":
                    return
                mark = live_mark()
                if mark is None:
                    return
                amount = max(float(m._val("d")), FILLET_MIN_CM)
                mark["amount"] = amount
                g = m._geom.get(mark.get("id"), {}) or {}
                # A settled exact candidate is stale once the handle moves.
                g["candidate_radius"] = None
                g.pop("real", None)
                draw_live(mark)
            except Exception:
                pass

    m.FuzzyInputChanged = FuzzyInputChanged

    def run(context):
        result = old_run(context)
        return result

    m.run = run
