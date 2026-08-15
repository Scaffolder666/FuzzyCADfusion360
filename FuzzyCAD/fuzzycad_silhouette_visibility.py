"""Keep view-dependent silhouette overlays aligned with proposal visibility.

The silhouette renderer lives in its own CustomGraphics group, so collapsing a
proposal to its badge did not collapse its apparent-contour curves. On assemblies
with cylindrical parts those orphaned contours can read as isolated vertical or
horizontal strokes far away from the currently inspected proposal.

This patch keeps the silhouette layer useful for the proposal being inspected or
hovered while removing it from the compact overview.
"""


def install(m):
    old_silhouette_redraw = getattr(m, "_redraw_view_silhouettes", None)
    old_redraw_marks = m._redraw_marks
    old_run = m.run
    old_stop = m.stop

    if old_silhouette_redraw is None:
        return

    def visible_marks():
        marks = list(getattr(m, "_marks", None) or [])
        checker = getattr(m, "_is_mark_revealed", None)
        if checker is None:
            return marks
        out = []
        for mark in marks:
            try:
                if checker(mark):
                    out.append(mark)
            except Exception:
                pass
        return out

    def redraw_visible_silhouettes(force=False):
        """Run the existing silhouette renderer against revealed marks only."""
        original = getattr(m, "_marks", None)
        if original is None:
            return old_silhouette_redraw(force)
        try:
            m._marks = visible_marks()
            return old_silhouette_redraw(force)
        finally:
            m._marks = original

    # Camera-driven silhouette refreshes go through this public hook.
    m._redraw_view_silhouettes = redraw_visible_silhouettes

    def redraw_marks(*args, **kwargs):
        # Earlier wrappers redraw persistent geometry and may also redraw the
        # legacy silhouette group. Finish by replacing that group with the
        # visibility-filtered version. This path is not animation-frame hot.
        result = old_redraw_marks(*args, **kwargs)
        try:
            redraw_visible_silhouettes(False)
        except Exception:
            pass
        return result

    m._redraw_marks = redraw_marks

    def run(context):
        result = old_run(context)
        try:
            redraw_visible_silhouettes(True)
        except Exception:
            pass
        return result

    def stop(context):
        try:
            m._clear("FuzzyCAD_Silhouette")
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
