"""Viewport badge semantics for FuzzyCAD uncertainty marks.

Need Input already uses a strong image badge in the 3D view. Keep that visual
language consistent for the other collaboration states as well:
- Note / Constraint uses icons/constraint.png.
- Compare / Conflict uses icons/conflict.png.

The legacy badge renderer intentionally skipped Note because the note callout was
considered sufficient. This patch re-enables the same screen-constant PNG badge
for Note without changing the existing badge geometry or fallback behavior.
"""


def install(m):
    old_draw_badge = m._draw_badge

    # Compare now uses the explicit "conflict" mtype. Add matching fallback
    # semantics in case an icon ever fails to load.
    try:
        m.MTYPE_LABEL["conflict"] = "Conflict"
        m.MTYPE_COLOR["conflict"] = (128, 90, 180)
        m.MTYPE_GLYPH["conflict"] = u"⑂"
    except Exception:
        pass

    def draw_badge(group, mark):
        if mark is None:
            return
        if mark.get("tool") == "note":
            # The legacy renderer only suppresses Note based on the tool name.
            # A shallow presentation copy lets it reuse the exact same image
            # placement, screen-facing point sprite, scale, and fallback logic.
            presentation = dict(mark)
            presentation["tool"] = "note_badge"
            presentation["mtype"] = "constraint"
            return old_draw_badge(group, presentation)
        return old_draw_badge(group, mark)

    m._draw_badge = draw_badge
