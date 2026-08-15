"""Incremental persistent rendering for FuzzyCAD collaboration marks.

The legacy renderer put every open question in one CustomGraphicsGroup. Any
numeric edit therefore deleted and rebuilt every mark, including full-BRep
Compare alternatives. This patch gives each mark its own persistent graphics
group and redraws only marks that are known to be dirty. Full redraws remain
available for startup/hydration and other structural resets.
"""

import json

PREFIX = "FuzzyCAD_Mark_"


def install(m):
    adsk = m.adsk
    old_clear = m._clear
    old_group = m._group
    old_remove_mark = m._remove_mark
    old_apply_edit = m._apply_edit
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    CurrentNoteInputChanged = getattr(m, "NoteInputChanged", None)
    old_run = m.run

    state = {
        "dirty": set(),
        "dirty_tools": {},
        "full_redraws": 0,
        "dirty_redraws": 0,
    }

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD RENDER] " + msg)
        except Exception:
            pass

    def gid(mid):
        return PREFIX + str(int(mid))

    def root_groups():
        try:
            design = m._design()
            return design.rootComponent.customGraphicsGroups if design is not None else None
        except Exception:
            return None

    def mark_group_ids():
        out = []
        groups = root_groups()
        if groups is None:
            return out
        try:
            for i in range(groups.count):
                g = groups.item(i)
                ident = str(getattr(g, "id", "") or "")
                if ident.startswith(PREFIX):
                    out.append(ident)
        except Exception:
            pass
        return out

    def has_legacy_group():
        groups = root_groups()
        if groups is None:
            return False
        try:
            for i in range(groups.count):
                if str(getattr(groups.item(i), "id", "") or "") == m.GROUP_MARKS:
                    return True
        except Exception:
            pass
        return False

    def clear_all_mark_groups():
        # Some older interaction paths still explicitly address GROUP_MARKS.
        # Clear it as well as the new per-mark namespace so the two layouts can
        # safely coexist during the migration.
        old_clear(m.GROUP_MARKS)
        for ident in mark_group_ids():
            old_clear(ident)

    def clear(gid_value):
        if gid_value == m.GROUP_MARKS:
            clear_all_mark_groups()
            return
        old_clear(gid_value)

    m._clear = clear

    def mark_dirty(mid, tool=None):
        try:
            mid = int(mid)
        except Exception:
            return
        state["dirty"].add(mid)
        if tool:
            state["dirty_tools"][mid] = tool
        else:
            mark = m._find(mid)
            if mark is not None:
                state["dirty_tools"][mid] = mark.get("tool")

    m._mark_dirty = mark_dirty

    def draw_mark(mid):
        ident = gid(mid)
        old_clear(ident)
        mark = m._find(mid)
        if mark is None or mid not in m._geom:
            return False
        group = old_group(ident)
        if group is None:
            return False
        try:
            m._draw_one(group, mark)
            return True
        except Exception:
            log("draw failed mark={}\n{}".format(mid, m.traceback.format_exc()))
            return False

    def refresh_silhouette_if_needed(tools, full=False):
        fn = getattr(m, "_redraw_view_silhouettes", None)
        if fn is None:
            return
        transform_tools = {"move", "rotate", "scale", "scale_axis", "axis_rotate"}
        if full or any(tool in transform_tools for tool in tools if tool):
            try:
                fn(False)
            except Exception:
                pass

    def redraw_marks(force=False, exclude_id=None):
        # If a compatibility path temporarily drew into the old shared group
        # (notably the existing-proposal manipulator), normalize back to per-mark
        # groups on the next redraw instead of leaving duplicate graphics.
        full = bool(force or exclude_id is not None or not state["dirty"] or has_legacy_group())
        touched_tools = []

        if full:
            clear_all_mark_groups()
            for mark in list(getattr(m, "_marks", []) or []):
                mid = mark.get("id")
                if mid is None or mid == exclude_id or mid not in m._geom:
                    continue
                draw_mark(mid)
                touched_tools.append(mark.get("tool"))
            state["dirty"].clear()
            state["dirty_tools"].clear()
            state["full_redraws"] += 1
        else:
            dirty = list(state["dirty"])
            state["dirty"].clear()
            for mid in dirty:
                touched_tools.append(state["dirty_tools"].pop(mid, None))
                draw_mark(mid)
            state["dirty_redraws"] += 1

        try:
            m._refresh_ghost()
        except Exception:
            pass
        refresh_silhouette_if_needed(touched_tools, full=full)
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass

    def redraw_mark(mid, refresh=True):
        mark_dirty(mid)
        redraw_marks()

    m._redraw_marks = redraw_marks
    m._redraw_mark = redraw_mark
    m._clear_all_mark_graphics = clear_all_mark_groups

    def remove_mark(mid):
        mark = m._find(mid)
        mark_dirty(mid, mark.get("tool") if mark else None)
        result = old_remove_mark(mid)
        # Remove this graphics group immediately. The caller's normal redraw then
        # only needs to refresh ghost/silhouette state.
        try:
            old_clear(gid(mid))
        except Exception:
            pass
        return result

    m._remove_mark = remove_mark

    def apply_edit(mark, key, value):
        # Existing-manipulator edits use GROUP_PREVIEW and intentionally avoid
        # persistent redraw until the command closes.
        if mark is not None and getattr(m, "_active_cmd", None) != "edit_existing":
            mark_dirty(mark.get("id"), mark.get("tool"))
        return old_apply_edit(mark, key, value)

    m._apply_edit = apply_edit

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
                if action == "compare_choice":
                    mark = m._find(data.get("id"))
                    if mark is not None:
                        mark_dirty(mark.get("id"), mark.get("tool"))
            except Exception:
                pass
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    if CurrentNoteInputChanged is not None:
        class NoteInputChanged(CurrentNoteInputChanged):
            def notify(self, args):
                try:
                    mid = getattr(m, "_note_live_id", None)
                    if mid is not None:
                        mark_dirty(mid, "note")
                except Exception:
                    pass
                super().notify(args)

        m.NoteInputChanged = NoteInputChanged

    def run(context):
        result = old_run(context)
        log("READY: one persistent graphics group per collaboration mark")
        return result

    m.run = run
