"""Viewport badge semantics for FuzzyCAD uncertainty marks.

Need Input already uses a strong image badge in the 3D view. Keep that visual
language consistent for the other collaboration states as well:
- Note / Constraint uses icons/constraint.png.
- Compare / Conflict uses icons/conflict.png.

The legacy badge renderer intentionally skipped Note because the note callout was
considered sufficient. This patch re-enables the same screen-constant PNG badge
for Note without changing the existing badge geometry or fallback behavior.
Older persisted Compare marks are also normalized to the Conflict badge even if
they still carry the historical "alternative" mtype.
"""


def install(m):
    old_draw_badge = m._draw_badge

    try:
        m.MTYPE_LABEL["conflict"] = "Conflict"
        m.MTYPE_COLOR["conflict"] = (128, 90, 180)
        m.MTYPE_GLYPH["conflict"] = u"⑂"
    except Exception:
        pass

    def draw_badge(group, mark):
        if mark is None:
            return

        tool = mark.get("tool")
        if tool == "note":
            # The legacy renderer suppresses Note based on the tool name. A
            # presentation copy reuses its exact image placement and billboard
            # behavior while allowing the generated Constraint icon to render.
            presentation = dict(mark)
            presentation["tool"] = "note_badge"
            presentation["mtype"] = "constraint"
            return old_draw_badge(group, presentation)

        if tool == "compare":
            # New Compare marks already use mtype=conflict, but persisted marks
            # from older builds may still say alternative. The 3D semantics are
            # Conflict either way, so always use the fists badge.
            presentation = dict(mark)
            presentation["mtype"] = "conflict"
            return old_draw_badge(group, presentation)

        return old_draw_badge(group, mark)

    m._draw_badge = draw_badge
