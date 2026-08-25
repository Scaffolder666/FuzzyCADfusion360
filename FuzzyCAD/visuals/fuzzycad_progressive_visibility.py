"""Progressive viewport visibility for persistent FuzzyCAD uncertainty.

The persistent overview stays compact, but hovering a proposal temporarily reveals
its full proposed wireframe while the lightweight replay animation runs. This
keeps three states readable at once: current geometry, the proposed destination,
and the moving line-art replay between them.
"""


def install(m):
    adsk = m.adsk
    old_draw_one = m._draw_one
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    ALWAYS_VISIBLE = {"note", "fillet"}
    state = {"revealed_id": None, "hover_reveal_id": None}

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
        if not is_persistent_group(group) or is_revealed(mark):
            return old_draw_one(group, mark)
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
        state["hover_reveal_id"] = None
        if redraw:
            try:
                m._redraw_marks()
            except Exception:
                pass

    def hover_reveal(mid):
        try:
            mid = int(mid)
        except Exception:
            return
        # Only remember it as hover-owned when hover itself opened the proposal.
        if state.get("revealed_id") != mid:
            state["hover_reveal_id"] = mid
            reveal(mid, True)
        else:
            state["hover_reveal_id"] = None

    def hover_collapse(mid):
        try:
            mid = int(mid)
        except Exception:
            return
        if state.get("hover_reveal_id") == mid and state.get("revealed_id") == mid:
            state["hover_reveal_id"] = None
            state["revealed_id"] = None
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

            # Hover animation should show the actual proposed destination as a
            # stationary sketch while a temporary sketch copy travels toward it.
            if action in ("hoverMoveStart", "hoverOpStart"):
                hover_reveal(data.get("id"))

            elif action in ("hoverMoveEnd", "hoverOpEnd"):
                hover_collapse(data.get("id"))

            # Click/inspect/edit owns the reveal after hover ends.
            elif action in ("focus", "editManipulator", "edit", "compare_choice"):
                state["hover_reveal_id"] = None
                reveal(data.get("id"), True)

            elif action == "tool":
                collapse(True)

            elif action == "confirm":
                collapse(False)

            elif action in ("accept", "reject"):
                try:
                    mid = int(data.get("id"))
                    if state.get("revealed_id") == mid:
                        state["revealed_id"] = None
                    if state.get("hover_reveal_id") == mid:
                        state["hover_reveal_id"] = None
                except Exception:
                    pass

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
