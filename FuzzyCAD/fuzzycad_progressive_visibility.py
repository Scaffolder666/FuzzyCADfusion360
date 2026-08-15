"""Progressive viewport visibility for persistent FuzzyCAD uncertainty.

The persistent overview should stay legible when many unresolved decisions exist:
- Move/Rotate/Scale/Axis Rotate/Extrude and Compare collapse to their 3D badge.
- Fillet remains visible because its local cue overlaps the source geometry.
- Note remains visible because the annotation itself is the information.
- Clicking/editing a card reveals that mark; revealing another card collapses the
  previous non-local proposal.
- Confirm or switching creation tools returns the viewport to the overview.

Live command previews are unchanged because they render in GROUP_PREVIEW. The
existing opacity layer remains authoritative, so unresolved source bodies stay
semi-transparent even while their proposal sketch is collapsed.
"""


def install(m):
    adsk = m.adsk
    old_draw_one = m._draw_one
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    ALWAYS_VISIBLE = {"note", "fillet"}
    state = {"revealed_id": None}

    def is_revealed(mark):
        if mark is None:
            return False
        if mark.get("tool") in ALWAYS_VISIBLE:
            return True
        return mark.get("id") == state.get("revealed_id")

    def is_persistent_group(group):
        try:
            return group is not None and group.id == m.GROUP_MARKS
        except Exception:
            return False

    def draw_one(group, mark):
        # Preview groups keep the full proposal. Only the persistent overview
        # collapses non-local geometry.
        if not is_persistent_group(group) or is_revealed(mark):
            return old_draw_one(group, mark)

        # Badge-only overview. Do not touch opacity here.
        if mark.get("status", "open") == "open":
            try:
                m._draw_badge(group, mark)
            except Exception:
                pass

    m._draw_one = draw_one
    m._draw_persistent_mark = draw_one
    m._is_mark_revealed = is_revealed

    def reveal(mid, redraw=True):
        try:
            mid = int(mid)
        except Exception:
            return False
        mark = m._find(mid)
        if mark is None:
            return False
        changed = state.get("revealed_id") != mid
        state["revealed_id"] = mid
        if redraw and changed:
            try:
                m._redraw_marks()
            except Exception:
                pass
        return True

    def collapse(redraw=True):
        if state.get("revealed_id") is None:
            return
        state["revealed_id"] = None
        if redraw:
            try:
                m._redraw_marks()
            except Exception:
                pass

    m._reveal_mark = reveal
    m._collapse_revealed_mark = collapse

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                import json
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            # Explicit inspection/edit means "show me this uncertainty." A
            # single revealed_id guarantees that opening one card collapses the
            # previously inspected non-local proposal.
            if action in ("focus", "editManipulator", "edit", "compare_choice"):
                reveal(data.get("id"), True)

            # Starting another creation tool returns to the overview. The new
            # tool still renders fully in GROUP_PREVIEW while it is being made.
            elif action == "tool":
                collapse(True)

            # Confirm terminates the current edit/creation command. Clear the
            # reveal state first; the command destroy path performs the redraw,
            # so we avoid an extra viewport refresh while a manipulator is live.
            elif action == "confirm":
                collapse(False)

            # Downstream accept/reject removes the mark and redraws the model.
            elif action in ("accept", "reject"):
                try:
                    mid = int(data.get("id"))
                    if state.get("revealed_id") == mid:
                        state["revealed_id"] = None
                except Exception:
                    pass

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
