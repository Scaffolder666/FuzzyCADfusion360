"""Make FuzzyCAD proposals read unmistakably as before -> proposed geometry.

Candidate sketch strokes are darker/thicker/more hand-drawn, while the original
geometry gets a quiet single-stroke outline.  Move and Rotate also receive small
screen-facing Original / Proposed labels at corresponding reference points.
"""


def install(m):
    adsk = m.adsk
    old_sketchy = m._sketchy
    old_run = m.run

    BEFORE_RGB = (145, 145, 142)
    ORIGINAL_LABEL_RGB = (112, 118, 124)
    PROPOSED_LABEL_RGB = (55, 62, 68)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD CONTRAST] " + msg)
        except Exception:
            pass

    # A proposal should visually separate from the original body.  This is the
    # same low original opacity used by the FeatureScript reference language.
    m.GHOST_OPACITY = 0.08

    def sketchy(group, pts, rgb, amp, seed, weight=2, strokes=2):
        # Multi-stroke geometry is the proposal sketch. Strengthen it without
        # thickening dimension leaders, warning marks, or axis guides.
        if strokes >= 2 and amp > 0:
            amp = amp * 1.65
            weight = max(2, weight)
        return old_sketchy(group, pts, rgb, amp, seed, weight=weight, strokes=strokes)

    m._sketchy = sketchy

    def original_polys(mark):
        g = m._geom.get(mark.get("id"), {})
        if g.get("edges"):
            return g.get("edges")
        if g.get("loops"):
            return g.get("loops")
        if g.get("edge"):
            return [g.get("edge")]
        return []

    def draw_original(group, mark):
        for i, poly in enumerate(original_polys(mark)):
            if poly and len(poly) >= 2:
                old_sketchy(group, poly, BEFORE_RGB, 0.0,
                            mark.get("id", 1) * 15001 + i,
                            weight=1, strokes=1)

    def farthest_point(mark):
        a = mark.get("anchor") or [0.0, 0.0, 0.0]
        best = None
        best_d = -1.0
        for poly in original_polys(mark):
            for p in poly:
                d = ((p[0]-a[0])**2 + (p[1]-a[1])**2 + (p[2]-a[2])**2)
                if d > best_d:
                    best_d = d; best = p
        return tuple(best or a)

    def transformed_ref(mark, p0):
        tool = mark.get("tool")
        try:
            if tool in ("move", "rotate", "axis_rotate"):
                mat = m._op_matrix(mark)
            elif tool == "scale":
                f = max(0.05, float(mark.get("factor", 1.0)))
                a = mark.get("anchor") or [0.0, 0.0, 0.0]
                mat = adsk.core.Matrix3D.create()
                mat.setCell(0, 0, f); mat.setCell(1, 1, f); mat.setCell(2, 2, f)
                mat.translation = adsk.core.Vector3D.create(
                    a[0]*(1-f), a[1]*(1-f), a[2]*(1-f))
            else:
                return None
            p = adsk.core.Point3D.create(*p0)
            p.transformBy(mat)
            return (p.x, p.y, p.z)
        except Exception:
            return None

    def label(group, xyz, text, rgb, size):
        try:
            p = adsk.core.Point3D.create(*xyz)
            t = group.addText(text, "Arial", max(0.38, min(size * 0.075, 0.62)),
                              m._label_transform(p))
            t.color = m._solid(rgb)
            m._apply_billboard(t, p)
        except Exception:
            pass

    def add_before_after_labels(group, mark):
        if mark.get("tool") not in ("move", "rotate"):
            return
        p0 = farthest_point(mark)
        p1 = transformed_ref(mark, p0)
        if p1 is None:
            return
        d2 = sum((p1[i]-p0[i])**2 for i in range(3))
        if d2 < 1e-5:
            return
        size = mark.get("size", 3.0)
        label(group, p0, "Original", ORIGINAL_LABEL_RGB, size)
        label(group, p1, "Proposed", PROPOSED_LABEL_RGB, size)

    # Wrap current renderers last so all existing exact BRep / related-body
    # behavior remains intact.
    for tool in ("move", "rotate", "scale", "scale_axis", "axis_rotate", "extrude", "fillet"):
        previous = m._DRAW.get(tool)
        if previous is None:
            continue
        def make_draw(prev, tool_name):
            def draw(group, mark, rgb, amp):
                draw_original(group, mark)
                prev(group, mark, rgb, amp)
                if tool_name in ("move", "rotate"):
                    add_before_after_labels(group, mark)
            return draw
        m._DRAW[tool] = make_draw(previous, tool)

    def run(context):
        result = old_run(context)
        log("VISUAL CONTRAST READY: original single-line + stronger proposed sketch")
        return result

    m.run = run
