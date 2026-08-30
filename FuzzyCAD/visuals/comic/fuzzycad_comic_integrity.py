"""Self-heal the persistent proposed comic boundary when Fusion drops its lines.

The fuzzy-boundary renderer keeps one CustomGraphics group per body and normally
reuses it across Editing -> Proposed transitions. Fusion can occasionally leave
the group object alive while some/all CustomGraphicsLines inside it disappear.
The renderer then sees an existing, visible group with the same signature and has
no reason to rebuild it, producing a badge (and sometimes fill) with an incomplete
or missing sketch boundary.

This guard validates only bodies that the central visual authority currently says
must show the Proposed comic baseline. It stores no Fusion wrappers: the group is
resolved fresh by gid, inspected, then released. If the sampled boundary exists
but the line graphics are missing or fewer than the renderer budget that created
the group, the corresponding render entry is marked dirty and the normal fuzzy
renderer rebuilds that body once. Editing visuals are intentionally untouched.

Toolbar command Destroy also schedules one post-command integrity check. The
check is deferred through a harmless custom event so Fusion has finished tearing
down its command before we validate the Proposed comic group. This is important
because a group can look complete during Destroy and lose graphics immediately
after the native command closes.
"""

import time

REPAIR_COOLDOWN = 0.75
POST_COMMAND_EVENT_ID = "FuzzyCADComicPostCommandCheck"


def install(m):
    old_sync = m._sync_comic_uncertainty
    old_redraw = m._redraw_marks
    old_run = m.run
    old_stop = m.stop
    CurrentFuzzyDestroy = getattr(m, "FuzzyDestroy", None)

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

    def expected_lines(entry):
        """Recover the line budget stored by fuzzy_boundary in its signature."""
        if not entry:
            return 0
        try:
            sig = entry.get("signature")
            if isinstance(sig, (tuple, list)) and len(sig) >= 4:
                return max(0, int(sig[3]))
        except Exception:
            pass
        return 0

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
            expected = expected_lines(entry)
            total, lines = graphics_counts(gid) if gid else (None, None)

            # Earlier guard versions only repaired lines == 0. That misses the
            # visually common failure where Fusion retains most of a comic group
            # but drops one or several boundary strokes during command teardown.
            missing = (
                entry is None or total is None or total <= 0 or lines is None or
                lines <= 0 or (expected > 0 and lines < expected)
            )
            if not missing:
                continue

            last = float(state["last_repair"].get(tok, 0.0) or 0.0)
            if now - last < REPAIR_COOLDOWN:
                continue

            entry = m._runtime_render_entry(tok, "fuzzy", True)
            entry["dirty"] = True
            state["last_repair"][tok] = now
            dirty.append((tok, gid, total, lines, expected, len(edges)))

        if not dirty:
            return False

        trace(
            "COMIC_BOUNDARY_REPAIR_BEGIN",
            "reason={} bodies={}".format(reason, len(dirty)))
        for tok, gid, total, lines, expected, edges in dirty:
            trace(
                "COMIC_BOUNDARY_MISSING",
                "token={} gid={} total={} lines={} expected={} sampled_edges={}".format(
                    tok, gid, total, lines, expected, edges))

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

    class PostCommandCheck(m.adsk.core.CustomEventHandler):
        def notify(self, args):
            # A normal Confirm leaves no active FuzzyCAD command. Clear only then;
            # if another tool already took ownership, its preview must remain.
            if getattr(m, "_active_cmd", None) is None:
                try:
                    m._clear(m.GROUP_PREVIEW)
                except Exception:
                    pass
            try:
                repair_visible("post-command")
            except Exception:
                pass

    if CurrentFuzzyDestroy is not None:
        class FuzzyDestroy(CurrentFuzzyDestroy):
            def notify(self, args):
                # Capture ownership BEFORE the legacy Destroy clears/replaces the
                # session. A stale Destroy from a superseded tool must not schedule
                # cleanup for the new command.
                session = getattr(self, "session", None)
                was_current = (
                    session is None or session is getattr(m, "_cmd_session", None))
                result = super().notify(args)
                if was_current:
                    try:
                        m._app.fireCustomEvent(POST_COMMAND_EVENT_ID, "toolbar-destroy")
                    except Exception:
                        pass
                return result

        m.FuzzyDestroy = FuzzyDestroy

    def run(context):
        result = old_run(context)
        try:
            m._app.unregisterCustomEvent(POST_COMMAND_EVENT_ID)
        except Exception:
            pass
        try:
            evt = m._app.registerCustomEvent(POST_COMMAND_EVENT_ID)
            h = PostCommandCheck()
            evt.add(h)
            m._handlers.append(h)
        except Exception:
            trace("COMIC_POST_COMMAND_REGISTER_EXCEPTION", "")
        try:
            repair_visible("startup")
        except Exception:
            pass
        return result

    def stop(context):
        try:
            m._app.unregisterCustomEvent(POST_COMMAND_EVENT_ID)
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
