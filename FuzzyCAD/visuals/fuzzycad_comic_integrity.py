"""Self-heal the persistent proposed comic boundary when Fusion drops its lines.

The fuzzy-boundary renderer keeps one CustomGraphics group per body and normally
reuses it across Editing -> Proposed transitions. Fusion can occasionally leave
the group object alive while some/all CustomGraphicsLines inside it disappear.
The renderer then sees an existing, visible group with the same signature and has
no reason to rebuild it, producing a badge (and sometimes fill) with no sketch
boundary.

This guard validates only bodies that the central visual authority currently says
must show the Proposed comic baseline. It stores no Fusion wrappers: the group is
resolved fresh by gid, inspected, then released. If sampled body edges exist but
no line graphics remain, the corresponding render entry is marked dirty and the
normal fuzzy renderer rebuilds that body once. Editing visuals are intentionally
untouched.
"""

import time

REPAIR_COOLDOWN = 0.75


def install(m):
    old_sync = m._sync_comic_uncertainty
    old_redraw = m._redraw_marks
    old_run = m.run

    state = {
        "repairing": False,
        "last_repair": {},
    }

    def trace(event, detail=""):
        try:
            fn = getattr(m, "_crash_trace", None)
            if fn is not None:
                fn(event, detail)
                return
        except Exception:
            pass
        try:
            (m._app or m.adsk.core.Application.get()).log(
                "[FuzzyCAD COMIC INTEGRITY] {} {}".format(event, detail))
        except Exception:
            pass

    def graphics_counts(gid):
        """Return (total_items, line_items) for a freshly resolved group."""
        try:
            group = m._runtime_find_group(gid, False)
            if group is None:
                return None, None
            total = int(group.count)
            lines = 0
            for i in range(total):
                try:
                    obj = group.item(i)
                    typ = str(getattr(obj, "objectType", "") or "")
                    if "Line" in typ:
                        lines += 1
                except Exception:
                    pass
            return total, lines
        except Exception:
            return None, None

    def repair_visible(reason="sync"):
        if state["repairing"]:
            return False

        try:
            rows, _retained = m._visual_comic_subject_rows()
        except Exception:
            return False
        if not rows:
            return False

        now = time.monotonic()
        dirty = []
        for tok, body in rows:
            try:
                geometry = m._runtime_body_geometry(body)
                edges = geometry.get("edges") or []
            except Exception:
                edges = []

            # If Fusion could not sample the body at this exact command handoff,
            # runtime_store already marks that geometry incomplete and retries on a
            # later sync. Do not mistake an unavailable sample for lost graphics.
            if not edges:
                continue

            entry = m._runtime_render_entry(tok, "fuzzy", False)
            gid = entry.get("gid") if entry else None
            total, lines = graphics_counts(gid) if gid else (None, None)

            missing = entry is None or total is None or total <= 0 or lines <= 0
            if not missing:
                continue

            last = float(state["last_repair"].get(tok, 0.0) or 0.0)
            if now - last < REPAIR_COOLDOWN:
                continue

            entry = m._runtime_render_entry(tok, "fuzzy", True)
            entry["dirty"] = True
            state["last_repair"][tok] = now
            dirty.append((tok, gid, total, lines, len(edges)))

        if not dirty:
            return False

        trace(
            "COMIC_BOUNDARY_REPAIR_BEGIN",
            "reason={} bodies={}".format(reason, len(dirty)))
        for tok, gid, total, lines, edges in dirty:
            trace(
                "COMIC_BOUNDARY_MISSING",
                "token={} gid={} total={} lines={} sampled_edges={}".format(
                    tok, gid, total, lines, edges))

        state["repairing"] = True
        try:
            changed = bool(old_sync())
        except Exception:
            changed = False
        finally:
            state["repairing"] = False

        try:
            if changed and m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass
        trace("COMIC_BOUNDARY_REPAIR_DONE", "reason={} changed={}".format(reason, changed))
        return changed

    def sync():
        changed = bool(old_sync())
        repaired = repair_visible("explicit-sync")
        return changed or repaired

    m._sync_comic_uncertainty = sync
    m._repair_comic_integrity = repair_visible

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            repair_visible("redraw")
        except Exception:
            pass
        return result

    m._redraw_marks = redraw

    def run(context):
        result = old_run(context)
        try:
            repair_visible("startup")
        except Exception:
            pass
        return result

    m.run = run
