"""Render proposed geometry as outline-only for clear before/after comparison.

Move, Rotate, Scale, directional Scale, Axis Rotate, and Extrude candidates use
cached/sampled edge geometry for their visible preview.  Because their faces are
intentionally invisible, this patch does not create a CustomGraphics BRep body
at all.  That removes a surprisingly expensive invisible object from the live
drag path.  Fillet is intentionally excluded because its local surface change
benefits from the existing filled candidate treatment.
"""


def install(m):
    old_sketchy = m._sketchy
    old_run = m.run

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD OUTLINE] " + msg)
        except Exception:
            pass

    class NullGraphic:
        """No-op stand-in for an intentionally invisible candidate BRep."""
        def __init__(self):
            self.transform = None
            self.color = None

        def setOpacity(self, opacity, is_through):
            return None

    class GroupProxy:
        def __init__(self, group):
            object.__setattr__(self, "_group", group)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_group"), name)

        def addBRepBody(self, body):
            # The visible candidate is the sketch outline.  Earlier builds still
            # created the full BRep and merely set opacity=0, which means Fusion
            # paid the BRep graphics cost even though the user could not see it.
            return NullGraphic()

    # Proposed outlines should remain visibly sketch-like after the fill is gone.
    # Only multi-stroke sketch geometry is thickened; dimensions/axis leaders stay
    # at their existing weights.
    def sketchy(group, pts, rgb, amp, seed, weight=2, strokes=2):
        if strokes >= 2 and amp > 0:
            weight = max(3, weight)
        return old_sketchy(group, pts, rgb, amp, seed, weight=weight, strokes=strokes)

    m._sketchy = sketchy

    # Wrap the final renderer chain so candidate BRep requests from earlier
    # patches become no-ops while all exact edge/callout logic remains intact.
    for tool in ("move", "rotate", "scale", "scale_axis", "axis_rotate", "extrude"):
        prev = m._DRAW.get(tool)
        if prev is None:
            continue

        def make_draw(previous):
            def draw(group, mark, rgb, amp):
                return previous(GroupProxy(group), mark, rgb, amp)
            return draw

        m._DRAW[tool] = make_draw(prev)

    def run(context):
        result = old_run(context)
        log("OUTLINE-ONLY CANDIDATES READY: no invisible candidate BRep + stronger proposed sketch; Fillet unchanged")
        return result

    m.run = run
