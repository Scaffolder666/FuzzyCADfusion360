"""Decision archive -- a minimal, Overleaf-style history of resolved cards.

When a decision card is Accepted or Rejected it disappears from the live list.
This module keeps a compact record of what was there so a downstream collaborator
can still see which decisions were made and read the discussion that led to them.
It intentionally stores ONLY the card identity, its resolution, and its comments
-- not geometry, field history, or images. It is a read-only trail, not undo.

Design boundary (ARCHITECTURE.md §3): every archived row is JSON-safe
(strings/numbers/lists only). No BRepBody, Canvas, or CommandInput is retained.

How it hooks in, all through owners that already exist:

- Accept and Reject both resolve a card by calling ``m._remove_mark(mid)`` -- the
  legacy palette handler and ``core/fuzzycad_safe_confirm.resolve_terminal`` alike.
  We wrap the palette handler (outermost, installed after safe_confirm) to record
  the *reason* for the removal, keyed by the target id, then wrap
  ``m._remove_mark`` (outermost, installed after persistence) to snapshot the card
  the instant before persistence drops it.
- The archive travels with the document exactly like the marks do: it saves and
  loads on the same lifecycle as ``core/fuzzycad_persistence`` by wrapping
  ``m._persist_state`` / ``m._reload_persisted_state``. It uses its own document
  attribute, so it never entangles the mark-snapshot logic.
- Every ``_send_state`` also pushes the archive to the palette as a separate
  ``archive`` message, so ``palette/panel_archive.js`` can render the resolved
  section without touching the main card render or the incremental-patch path.

Removals that are NOT resolutions (Clear all, deleting an unresolved card) never
set a reason for that id, so they are not archived.
"""

import json
import time

ATTR_GROUP = "FuzzyCAD"
ATTR_NAME = "decision_archive_v1"
MAX_ROWS = 300   # bound document growth; oldest rows fall off the front


def install(m):
    adsk = m.adsk

    if not hasattr(m, "_archive"):
        m._archive = []
    # id -> "accepted" | "rejected", set when a resolution action arrives and
    # consumed by the _remove_mark wrapper. Not persisted.
    m._archive_pending = {}
    _loaded = {"done": False}

    def log(msg):
        try:
            ct = getattr(m, "_crash_trace", None)
            if ct is not None:
                ct("ARCHIVE", str(msg))
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD ARCHIVE] " + msg)
        except Exception:
            pass

    def design():
        try:
            return m._design()
        except Exception:
            return None

    # ---- document attribute I/O (own attribute, JSON-safe rows) -----------
    def save_archive():
        des = design()
        if des is None:
            return
        try:
            text = json.dumps({"archive": m._archive},
                              separators=(",", ":"), ensure_ascii=False)
            des.attributes.add(ATTR_GROUP, ATTR_NAME, text)
        except Exception:
            log("save failed\n{}".format(m.traceback.format_exc()))

    def load_archive():
        des = design()
        if des is None:
            return
        try:
            attr = des.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
            payload = json.loads(attr.value) if attr and attr.value else {}
            rows = payload.get("archive")
            m._archive = rows if isinstance(rows, list) else []
            _loaded["done"] = True
            log("loaded rows={}".format(len(m._archive)))
        except Exception:
            m._archive = []
            log("load failed\n{}".format(m.traceback.format_exc()))

    # ---- build a compact, JSON-safe row from a live mark ------------------
    def snapshot_row(mark, reason):
        tool = mark.get("tool", "")
        try:
            summary = m._summary(mark)
        except Exception:
            summary = tool
        comments = []
        for c in (mark.get("comments") or []):
            try:
                comments.append({"text": str(c.get("text", ""))})
            except Exception:
                pass
        return {
            "id": mark.get("id"),
            "tool": tool,
            "num": mark.get("num", 1),
            "title": "{} {}".format(tool.capitalize(), mark.get("num", 1)),
            "mtype": mark.get("mtype", "need_input"),
            "summary": summary,
            "resolution": reason,       # "accepted" | "rejected"
            "comments": comments,
            "ts": time.time(),
        }

    # ---- wrap _remove_mark: snapshot the card just before it is dropped ----
    old_remove_mark = m._remove_mark

    def remove_mark(mid):
        reason = m._archive_pending.pop(mid, None)
        if reason:
            try:
                mark = m._find(mid)
                if mark is not None:
                    m._archive.append(snapshot_row(mark, reason))
                    if len(m._archive) > MAX_ROWS:
                        del m._archive[:len(m._archive) - MAX_ROWS]
                    log("archived id={} tool={} reason={}".format(
                        mid, mark.get("tool"), reason))
            except Exception:
                log("snapshot failed\n{}".format(m.traceback.format_exc()))
        return old_remove_mark(mid)

    m._remove_mark = remove_mark

    # ---- wrap persistence save/load so the archive rides the same lifecycle -
    old_persist = getattr(m, "_persist_state", None)
    if callable(old_persist):
        def persist_state(reason="state"):
            result = old_persist(reason)
            try:
                save_archive()
            except Exception:
                pass
            return result
        m._persist_state = persist_state

    old_reload = getattr(m, "_reload_persisted_state", None)
    if callable(old_reload):
        def reload_persisted_state(*a, **k):
            result = old_reload(*a, **k)
            try:
                load_archive()
            except Exception:
                pass
            try:
                send_archive()
            except Exception:
                pass
            return result
        m._reload_persisted_state = reload_persisted_state

    # ---- push the archive to the palette alongside every state send -------
    def send_archive():
        try:
            palette = m._ui.palettes.itemById(m.PALETTE_ID)
        except Exception:
            palette = None
        if not palette:
            return
        if not _loaded["done"]:
            # First push of the session: make sure we reflect what the document
            # already carried before this add-in session started.
            load_archive()
        try:
            palette.sendInfoToHTML(
                "archive",
                json.dumps({"archive": m._archive}, ensure_ascii=False))
        except Exception:
            pass

    old_send_state = m._send_state

    def send_state():
        result = old_send_state()
        try:
            send_archive()
        except Exception:
            pass
        return result

    m._send_state = send_state

    # ---- record the resolution reason at the palette action edge ----------
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler

    class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            try:
                event = adsk.core.HTMLEventArgs.cast(args)
                action = event.action if event is not None else None
                if action in ("accept", "reject"):
                    data = json.loads(event.data) if event.data else {}
                    mid = data.get("id")
                    if mid is not None:
                        m._archive_pending[mid] = (
                            "accepted" if action == "accept" else "rejected")
            except Exception:
                pass
            # Always fall through to the real handler chain, which performs the
            # resolution (and the _remove_mark that triggers the snapshot).
            self._delegate.notify(args)

    m.PaletteHTMLHandler = PaletteHTMLHandler

    # Initial load for a document already open when the add-in starts.
    try:
        load_archive()
    except Exception:
        pass

    log("DECISION ARCHIVE READY rows={}".format(len(m._archive)))
