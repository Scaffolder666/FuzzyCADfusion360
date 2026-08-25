"""Viewport badge semantics for FuzzyCAD uncertainty marks.

Need Input already uses a strong image badge in the 3D view. Keep that visual
language consistent for the other collaboration states as well:
- Note / Constraint uses icons/constraint.png.
- Compare / Conflict uses icons/conflict.png.

All badge images live inside the add-in's icons directory. Fusion's
UserDefinedCustomGraphicsPointType is most reliable when the image path belongs
to the loaded add-in package, so Note intentionally follows the exact same path
resolution mechanism as the working Need Input badge.
"""

import os


def install(m):
    old_draw_badge = m._draw_badge
    old_icon_path = m._icon_path

    try:
        m.MTYPE_LABEL["conflict"] = "Conflict"
        m.MTYPE_COLOR["conflict"] = (128, 90, 180)
        m.MTYPE_GLYPH["conflict"] = u"⑂"
    except Exception:
        pass

    def icon_path(mtype):
        # Resolve Note from the packaged icon directory, exactly like
        # need_input.png. Avoid temp-file sprites because Fusion can render an
        # unresolved/late-loaded image as a blank white point sprite.
        if mtype == "constraint":
            try:
                icon_dir = getattr(m, "_ICON_DIR", None)
                if icon_dir:
                    p = os.path.join(icon_dir, "constraint.png")
                    if os.path.exists(p):
                        return p
            except Exception:
                pass
        return old_icon_path(mtype)

    # _draw_badge resolves this global through the legacy module at call time.
    m._icon_path = icon_path

    def draw_badge(group, mark):
        if mark is None:
            return

        tool = mark.get("tool")
        if tool == "note":
            # The legacy renderer suppresses Note based on the tool name. A
            # presentation copy reuses its exact image placement and billboard
            # behavior while allowing the notebook image to render.
            presentation = dict(mark)
            presentation["tool"] = "note_badge"
            presentation["mtype"] = "constraint"
            return old_draw_badge(group, presentation)

        if tool == "compare":
            # New Compare marks already use mtype=conflict, but persisted marks
            # from older builds may still say alternative. Always show Conflict.
            presentation = dict(mark)
            presentation["mtype"] = "conflict"
            return old_draw_badge(group, presentation)

        return old_draw_badge(group, mark)

    m._draw_badge = draw_badge
