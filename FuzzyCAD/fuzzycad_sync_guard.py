"""Defensive guards for transient FuzzyCAD command state.

Fusion can deliver late executePreview events after a mark is removed, and a
palette Accept/Reject can resolve a mark while its Fusion command is still
active. This module keeps those two transient states from leaking back into the
viewport.
"""


def install(m):
    def sync_category(cat):
        """Create/update a live mark, but tolerate a stale _live id."""
        op = m._category_raw(cat)
        mid = m._live.get(cat)

        if mid is not None:
            mark = m._find(mid)
            if mark is None:
                # A preview event raced command teardown/removal. Do not recreate
                # the mark from the old non-zero manipulator value.
                return None
            mark.update(op)
            return mark

        if m._is_default(cat, op):
            return None

        mid = m._next_id
        m._next_id += 1
        mark = m._make_mark(cat, op)
        mark["id"] = mid
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        m._marks.append(mark)
        m._live[cat] = mid
        m._send_state()
        return mark

    def seed_single(cat, amount):
        """Seed extrude/fillet safely if a stale live id survives teardown."""
        mid = m._live.get(cat)
        if mid is not None:
            mark = m._find(mid)
            if mark is None:
                return None
            mark["amount"] = amount
            if cat == "fillet":
                mark["last_valid_amount"] = amount
            return mark

        mid = m._next_id
        m._next_id += 1
        mark = m._make_mark(cat, {"amount": amount})
        mark["id"] = mid
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        m._marks.append(mark)
        m._live[cat] = mid
        if cat == "fillet":
            mark["last_valid_amount"] = amount
        m._send_state()
        return mark

    m._sync_category = sync_category
    m._seed_single = seed_single

    # Accept/Reject happens from the HTML palette, not from Fusion's command OK
    # button. The old code removed the mark but left the command itself alive.
    # Its DistanceValueCommandInput therefore kept drawing the arrow/value and
    # GROUP_PREVIEW kept the warning badge/candidate. The stale handle could move
    # even though it no longer had a mark to update.
    OriginalPaletteHTMLHandler = m.PaletteHTMLHandler

    def dismiss_resolved_live_session(mid):
        live = any(v == mid for v in m._live.values())
        note_live = getattr(m, "_note_live_id", None) == mid
        if not (live or note_live):
            return

        # Hide every command-owned manipulator immediately. We intentionally do
        # not terminate the command here because Accept may just have created a
        # real Fusion feature in the same event transaction. The next tool launch
        # already terminates any old command cleanly via LaunchHandler.
        inputs = m._inputs
        if inputs is not None:
            for cid in ("mX", "mY", "mZ", "rX", "rY", "rZ", "sc", "d"):
                try:
                    it = inputs.itemById(cid)
                    if it is not None:
                        it.isVisible = False
                        it.isEnabled = False
                except Exception:
                    pass
            try:
                sel = inputs.itemById("sel")
                if sel is not None:
                    sel.clearSelection()
            except Exception:
                pass

        # Remove the transient candidate/badge layer. _redraw_marks has already
        # removed the persistent mark graphics after Accept/Reject.
        try:
            m._clear(m.GROUP_PREVIEW)
        except Exception:
            pass

        # Detach the active command from the resolved proposal so any late
        # executePreview/inputChanged callback becomes a no-op.
        m._live = {k: v for k, v in m._live.items() if v != mid}
        if live:
            m._pending = None
            m._inputs = None
            m._active_cmd = None
        if note_live:
            m._note_live_id = None
            m._note_inputs = None

        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    class PaletteHTMLHandler(m.adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = OriginalPaletteHTMLHandler()

        def notify(self, args):
            action = None
            mid = None
            try:
                import json
                e = m.adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                if action in ("accept", "reject"):
                    data = json.loads(e.data) if e.data else {}
                    mid = data.get("id")
            except Exception:
                pass

            # Let the existing handler perform the actual apply/remove/redraw.
            self._delegate.notify(args)

            # Only clean up when resolution really succeeded. If Accept failed,
            # the mark is still present and the user should keep the live handle.
            if action in ("accept", "reject") and mid is not None:
                try:
                    if m._find(mid) is None:
                        dismiss_resolved_live_session(mid)
                except Exception:
                    pass

    m.PaletteHTMLHandler = PaletteHTMLHandler
