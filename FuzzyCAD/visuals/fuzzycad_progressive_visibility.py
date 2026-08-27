"""Progressive proposal-detail visibility.

This file owns one visualization layer: optional persistent proposal detail
(wireframe / operation cue shown after an explicit focus). Baseline comic
uncertainty is independent and comes from the central visual authority.

The authority also decides the reveal mode:
- `reveal`: explicit focus may expand detail; hover replay itself stays temporary
- `focus`: explicit focus may expand, hover must not (Compare)
- `none`: Proposed never expands (Fillet / Hole / Rough)

Hover animation is deliberately NOT represented by a persistent reveal state.
Move / operation animation renderers already own temporary CustomGraphics groups,
so rebuilding all persistent marks on hover start/end only duplicates work and
makes card controls feel sluggish.
"""


def install(m):
    adsk = m.adsk
    old_draw_one = m._draw_one
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    fallback = {"revealed_id": None, "hover_reveal_id": None}

    def shared_state():
        return getattr(m, "_uncertainty_visual_state", None) or fallback

    def detail_mode(mark):
        try:
            return str(m._visual_variation(mark).get("proposed_detail", "reveal"))
        except Exception:
            return "reveal"

    def is_revealed(mark):
        if mark is None:
            return False
        try:
            return bool(m._visual_state(mark).get("show_persistent_detail"))
        except Exception:
            return mark.get("id") == shared_state().get("revealed_id")

    def is_persistent_group(group):
        try:
            return group is not None and group.id == m.GROUP_MARKS
        except Exception:
            return False

    def draw_one(group, mark):
        # This layer only gates DETAIL inside GROUP_MARKS. The comic paper fill and
        # sketch boundary are persistent groups owned by the comic renderer.
        if not is_persistent_group(group) or is_revealed(mark):
            return old_draw_one(group, mark)
        if mark.get("status", "open") == "open":
            try:
                if bool(m._visual_state(mark).get("show_badge")):
                    m._draw_badge(group, mark)
            except Exception:
                try:
                    m._draw_badge(group, mark)
                except Exception:
                    pass

    m._draw_one = draw_one
    m._draw_persistent_mark = draw_one
    m._is_mark_revealed = is_revealed

    def set_revealed(mid, hover=False):
        try:
            fn = getattr(m, "_visual_set_revealed", None)
            if fn:
                fn(mid, hover=hover)
                return
        except Exception:
            pass
        try:
            mid = int(mid) if mid is not None else None
        except Exception:
            return
        fallback["revealed_id"] = mid
        fallback["hover_reveal_id"] = mid if hover else None

    def clear_revealed(mid=None, hover_only=False):
        try:
            fn = getattr(m, "_visual_clear_revealed", None)
            if fn:
                fn(mid, hover_only=hover_only)
                return
        except Exception:
            pass
        if hover_only:
            if mid is None or fallback.get("hover_reveal_id") == mid:
                if fallback.get("revealed_id") == fallback.get("hover_reveal_id"):
                    fallback["revealed_id"] = None
                fallback["hover_reveal_id"] = None
            return
        if mid is None or fallback.get("revealed_id") == mid:
            fallback["revealed_id"] = None
        if mid is None or fallback.get("hover_reveal_id") == mid:
            fallback["hover_reveal_id"] = None

    def reveal(mid, redraw=True, hover=False):
        try:
            mid = int(mid)
        except Exception:
            return False
        mark = m._find(mid)
        if mark is None:
            return False

        mode = detail_mode(mark)
        if mode == "none" or (hover and mode != "reveal"):
            # Fillet/Hole/Rough remain baseline-only. Compare is click-to-expand.
            return False

        old = shared_state().get("revealed_id")
        set_revealed(mid, hover=hover)
        if redraw and old != mid:
            try:
                m._redraw_marks()
            except Exception:
                pass
        return True

    def collapse(redraw=True):
        s = shared_state()
        if s.get("revealed_id") is None and s.get("hover_reveal_id") is None:
            return
        clear_revealed(None, hover_only=False)
        if redraw:
            try:
                m._redraw_marks()
            except Exception:
                pass

    # Kept as compatibility helpers for any older caller, but the current HTML
    # hover path intentionally bypasses them. Hover replay uses temporary groups.
    def hover_reveal(mid):
        try:
            mid = int(mid)
        except Exception:
            return
        mark = m._find(mid)
        if mark is None or detail_mode(mark) != "reveal":
            return
        s = shared_state()
        if s.get("revealed_id") != mid:
            reveal(mid, True, hover=True)
        else:
            try:
                s["hover_reveal_id"] = None
            except Exception:
                pass

    def hover_collapse(mid):
        try:
            mid = int(mid)
        except Exception:
            return
        s = shared_state()
        if s.get("hover_reveal_id") == mid and s.get("revealed_id") == mid:
            clear_revealed(mid, hover_only=True)
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

            if action in ("hoverMoveStart", "hoverOpStart", "hoverMoveEnd", "hoverOpEnd"):
                # Performance invariant: hover replay is ephemeral. The dedicated
                # animation renderer will create/transform/clear its own temporary
                # group, so do not mutate persistent reveal state and do not call
                # _redraw_marks() here.
                pass

            elif action in ("focus", "compare_choice"):
                # These are explicit Proposed-detail changes, so the viewport must
                # update immediately.
                reveal(data.get("id"), True, hover=False)

            elif action == "editManipulator":
                # Entering Editing owns its own viewport transition. Record the
                # reveal state without first redrawing the Proposed scene; otherwise
                # a card click redraws every mark immediately before the native edit
                # command redraws again.
                reveal(data.get("id"), False, hover=False)

            elif action == "edit":
                # Numeric card edit is applied by the delegated handler, which then
                # performs the single authoritative redraw with the NEW value. Set
                # reveal first but do not redraw the OLD value here.
                reveal(data.get("id"), False, hover=False)

            elif action == "tool":
                collapse(True)

            elif action == "confirm":
                collapse(False)

            elif action in ("accept", "reject"):
                clear_revealed(data.get("id"), hover_only=False)

            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler
