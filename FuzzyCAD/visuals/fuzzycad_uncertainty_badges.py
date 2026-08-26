"""Viewport badge visualization for FuzzyCAD uncertainty marks.

This file owns badge rendering only. Badge lifecycle comes from the central
uncertainty visual authority; this module supplies collaboration-type variation:
Need Input, Constraint/Note, and Conflict/Compare icons.
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

    m._icon_path = icon_path

    def draw_badge(group, mark):
        if mark is None:
            return
        try:
            if not bool(m._visual_state(mark).get("show_badge")):
                return
        except Exception:
            if mark.get("status", "open") != "open":
                return

        tool = mark.get("tool")
        if tool == "note":
            presentation = dict(mark)
            presentation["tool"] = "note_badge"
            presentation["mtype"] = "constraint"
            return old_draw_badge(group, presentation)

        if tool == "compare":
            presentation = dict(mark)
            presentation["mtype"] = "conflict"
            return old_draw_badge(group, presentation)

        return old_draw_badge(group, mark)

    m._draw_badge = draw_badge
