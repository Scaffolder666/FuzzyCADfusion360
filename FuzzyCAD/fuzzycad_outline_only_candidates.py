"""Render proposed geometry as outline-only for clear before/after comparison.

Move, Rotate, Scale, directional Scale, Axis Rotate, and Extrude candidates keep
real BRep geometry for positioning and exact edge extraction, but their candidate
fill is fully transparent. Fillet is intentionally excluded because its local
surface change benefits from the existing filled candidate treatment.
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

    class GraphicProxy:
        def __init__(self, obj):
            object.__setattr__(self, "_obj", obj)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_obj"), name)

        def __setattr__(self, name, value):
            setattr(object.__getattribute__(self, "_obj"), name, value)

        def setOpacity(self, opacity, is_through):
            # Geometry stays available for transform/reference, but faces vanish.
            return object.__getattribute__(self, "_obj").setOpacity(0.0, is_through)

    class GroupProxy:
        def __init__(self, group):
            object.__setattr__(self, "_group", group)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_group"), name)

        def addBRepBody(self, body):
            obj = object.__getattribute__(self, "_group").addBRepBody(body)
            return GraphicProxy(obj)

    # Proposed outlines should remain visibly sketch-like after the fill is gone.
    # Only multi-stroke sketch geometry is thickened; dimensions/axis leaders stay
    # at their existing weights.
    def sketchy(group, pts, rgb, amp, seed, weight=2, strokes=2):
        if strokes >= 2 and amp > 0:
            weight = max(3, weight)
        return old_sketchy(group, pts, rgb, amp, seed, weight=weight, strokes=strokes)

    m._sketchy = sketchy

    # Wrap the final renderer chain so any candidate BRep created by earlier
    # patches becomes transparent without duplicating their exact geometry logic.
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
        log("OUTLINE-ONLY CANDIDATES READY: transparent fill + stronger proposed sketch; Fillet unchanged")
        return result

    m.run = run
