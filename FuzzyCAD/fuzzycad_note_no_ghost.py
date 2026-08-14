"""Keep Note as a pure annotation instead of a geometry proposal.

A Note/Constraint does not propose a geometric change, so the referenced body
should stay at its normal Fusion opacity.  Other open geometry-changing marks
still ghost their source bodies exactly as before.
"""


def install(m):
    old_refresh_ghost = m._refresh_ghost
    old_run = m.run

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD NOTE] " + msg)
        except Exception:
            pass

    def refresh_ghost():
        # Re-implement the small ghost-state pass with one semantic difference:
        # Note is annotation-only, so its referenced body never enters `want`.
        want = {}
        for mark in list(getattr(m, "_marks", []) or []):
            if mark.get("tool") == "note":
                continue
            body = m._body.get(mark.get("id"))
            if body is None:
                continue
            try:
                want[body.entityToken] = body
            except Exception:
                pass

        # Fade bodies that participate in geometry-changing open proposals.
        for tok, body in want.items():
            try:
                body.opacity = m.GHOST_OPACITY
            except Exception:
                pass

        # Anything previously ghosted only because of a Note is restored now.
        for tok, body in list(getattr(m, "_ghosted", {}).items()):
            if tok not in want:
                try:
                    body.opacity = 1.0
                except Exception:
                    pass

        m._ghosted = want

    m._refresh_ghost = refresh_ghost

    def run(context):
        result = old_run(context)
        try:
            # Immediately correct any stale Note-only ghosting from an older run.
            refresh_ghost()
        except Exception:
            pass
        log("NOTE VISUAL READY: annotation only; referenced body remains opaque")
        return result

    m.run = run
