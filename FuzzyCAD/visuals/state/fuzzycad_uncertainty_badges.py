"""Viewport badge visualization for FuzzyCAD uncertainty marks.

This file owns badge rendering only. Badge lifecycle comes from the central
uncertainty visual authority; this module supplies collaboration-type variation:
Need Input, Constraint/Note, and Conflict/Compare icons.

Persistent viewport text is deliberately avoided here. Fusion can reopen saved
CustomGraphicsText as white placeholder quads, so notes use a vector leader plus
the constraint badge; the full note text remains available in the side panel.
"""

import os


def install(m):
    old_draw_badge = m._draw_badge
    old_icon_path = m._icon_path

    # Persistent CustomGraphicsText/PNG billboards are not reload-safe in Fusion.
    # Keep the shared switch off for the saved-document renderer. Interactive HTML
    # cards remain the source for names, values, and full note text.
    m._VIEWPORT_LABELS = False

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

    # fuzzycad_note_dimensions originally renders note text through addText().
    # That text can turn into a white rectangle after reopening an .f3d. Replace
    # only the persistent note renderer here, after note_dimensions has installed,
    # with texture-free line graphics. _draw_one still adds the constraint badge,
    # so the note remains visible and locatable in the viewport.
    def draw_note_reload_safe(group, mark, rgb, amp):
        try:
            a = mark.get("anchor") or [0.0, 0.0, 0.0]
            s = float(mark.get("size", 3.0) or 3.0)
            (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
            off = max(1.0, min(s * 0.9, 3.2))
            tip = (
                a[0] + (0.22 * xx + 0.94 * yx) * off,
                a[1] + (0.22 * xy + 0.94 * yy) * off,
                a[2] + (0.22 * xz + 0.94 * yz) * off,
            )
            m._sketchy(group, [tuple(a), tip], rgb, max(0.01, amp),
                       mark["id"] * 3001, weight=2, strokes=1)
        except Exception:
            pass

    try:
        m._DRAW["note"] = draw_note_reload_safe
    except Exception:
        pass

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
