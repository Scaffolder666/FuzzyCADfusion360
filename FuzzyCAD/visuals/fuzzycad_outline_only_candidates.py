"""Render proposed geometry as outline-only for clear before/after comparison.

This patch now owns only geometry policy: candidate BRep surfaces are suppressed
for Move/Rotate/Scale/Axis Rotate/Extrude. It no longer changes line weight or
sketch character; those belong exclusively to fuzzycad_visual_system.py.
"""


def install(m):
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
            return NullGraphic()

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
        log("OUTLINE-ONLY CANDIDATES READY: geometry-only; visual styling delegated centrally")
        return result

    m.run = run
