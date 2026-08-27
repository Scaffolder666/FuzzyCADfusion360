"""Authoritative visual-state controller for FuzzyCAD.

This is the single persistent-render entry point.

The system used to let many wrappers independently react to `_redraw_marks()`:
comic, opacity, silhouette, reconciliation, and proposal detail each performed
part of the transition.  That made a logically valid state such as Proposed end
up visually half-Editing (for example: 0.50 opacity with no comic boundary).

The runtime contract is now deliberately simple:

    mark + phase + reveal state
        -> `_visual_state(mark)`        (desired switches; no drawing)
        -> `_visual_render()`           (apply all persistent switches together)

`_visual_render()` owns one authoritative transaction:

    1. clear idle Editing-only graphics;
    2. rebuild the persistent mark/detail group from the desired state;
    3. apply source-body opacity from the desired state;
    4. rebuild every currently-visible comic body as one complete fill+boundary;
    5. refresh the visibility-filtered silhouette layer;
    6. refresh the viewport once.

This intentionally favors determinism over a cheap persistent redraw. Global
redraws are lifecycle/reveal events, not drag frames. Move/Rotate/Scale drag
continues to update its already-created preview by transform only; slow tool
previews continue to own their temporary preview group.  Therefore rebuilding a
comic body here is acceptable and removes the fragile assumption that a Fusion
CustomGraphics group which still exists is necessarily complete.

No Fusion native wrapper is retained in controller state. Only ids, booleans,
and derived policy snapshots are stored.
"""


def install(m):
    # All layer services are installed before this module in FuzzyCAD.py.
    # Keep the previous redraw only as an emergency fallback; normal runtime
    # rendering never delegates to the historical wrapper chain.
    previous_redraw = getattr(m, "_redraw_marks", None)

    state = {
        "rendering": False,
        "pending": False,
        "generation": 0,
        "last_snapshot": {},
        "last_reason": None,
    }
    m._visual_controller_state = state

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
                "[FuzzyCAD VISUAL CONTROLLER] {} {}".format(event, detail))
        except Exception:
            pass

    def marks():
        return list(getattr(m, "_marks", None) or [])

    def desired(mark):
        try:
            return dict(m._visual_state(mark) or {})
        except Exception:
            return {
                "phase": "proposed" if mark and mark.get("status", "open") == "open" else "resolved",
                "is_open": bool(mark and mark.get("status", "open") == "open"),
                "show_badge": bool(mark and mark.get("status", "open") == "open"),
                "show_live_preview": False,
            }

    def snapshot():
        """Pure-Python desired visual state, keyed by mark id."""
        out = {}
        for mark in marks():
            try:
                mid = int(mark.get("id"))
            except Exception:
                continue
            vs = desired(mark)
            # Store only the state switches useful for reasoning/debugging.
            out[mid] = {
                "phase": vs.get("phase"),
                "variant": vs.get("variant"),
                "show_comic_fill": bool(vs.get("show_comic_fill")),
                "show_sketch_boundary": bool(vs.get("show_sketch_boundary")),
                "show_badge": bool(vs.get("show_badge")),
                "show_persistent_detail": bool(vs.get("show_persistent_detail")),
                "show_live_preview": bool(vs.get("show_live_preview")),
                "show_manipulator": bool(vs.get("show_manipulator")),
                "source_opacity": vs.get("source_opacity"),
            }
        return out

    m._visual_snapshot = snapshot

    def active_command_running():
        return bool(getattr(m, "_active_cmd", None))

    def clear_idle_editing_graphics():
        """Remove only temporary Editing/replay groups when no tool owns them.

        Do not clear dependency/follow highlight groups here; those can be
        intentionally visible while a post-accept prompt is open.
        """
        if active_command_running():
            return False
        changed = False
        gids = [
            getattr(m, "GROUP_PREVIEW", "FuzzyCAD_Preview"),
            "FuzzyCAD_HoverAnimation",
            "FuzzyCAD_HoverDirectionArrow",
            "FuzzyCAD_OperationHover",
            "FuzzyCAD_CompareConnectorPreview",
        ]
        for gid in gids:
            try:
                # `_clear` is idempotent; no native wrapper is retained.
                m._clear(gid)
                changed = True
            except Exception:
                pass
        return changed

    def draw_persistent_marks():
        """Apply badge/detail switches into the one persistent mark group."""
        m._clear(m.GROUP_MARKS)
        group = m._group(m.GROUP_MARKS)
        if group is None:
            return

        drawer = getattr(m, "_draw_persistent_mark", None) or getattr(m, "_draw_one", None)
        if drawer is None:
            return

        geom = getattr(m, "_geom", {}) or {}
        for mark in marks():
            mid = mark.get("id")
            # Preserve the original renderer's safety rule: geometry-bearing marks
            # are drawn only once their geometry record exists.
            if mid not in geom:
                continue

            vs = desired(mark)
            if not vs.get("is_open", True):
                continue

            # An active edit owns GROUP_PREVIEW. Drawing the same live proposal in
            # GROUP_MARKS would duplicate its outline/cue. Rough is the deliberate
            # exception: it has no live preview and keeps the comic state while
            # Editing, so it remains a persistent mark.
            if vs.get("phase") == "editing" and vs.get("show_live_preview"):
                continue

            try:
                drawer(group, mark)
            except Exception:
                trace("PERSISTENT_DRAW_EXCEPTION", "id={} tool={}".format(
                    mid, mark.get("tool")))

    def current_comic_tokens():
        try:
            rows, _retained = m._visual_comic_subject_rows()
            return [(str(tok), body) for tok, body in (rows or []) if tok and body is not None]
        except Exception:
            return []

    def arm_complete_comic_rebuild():
        """Make the next comic sync rebuild each visible body completely.

        A global render is a discrete state transition, never a drag frame.  We
        deliberately do not reuse the previous comic group's signature here.
        Fill and sketch boundary are therefore created as one output every time a
        state transition asks for the Proposed comic baseline.
        """
        tokens = []
        for tok, _body in current_comic_tokens():
            try:
                entry = m._runtime_render_entry(tok, "fuzzy", True)
                entry["dirty"] = True
                entry["signature"] = None
                tokens.append(tok)
            except Exception:
                pass
        return tokens

    def apply_opacity():
        """Apply the central body's source-opacity switch and reassert reality.

        opacity_runtime keeps an `applied` cache so drag frames never rewrite the
        same Fusion opacity. A native command teardown can nevertheless change the
        *actual* display opacity behind that cache. On this non-frame transaction
        we compare the live value and reassert the desired one if needed.
        """
        try:
            recover = getattr(m, "_recover_visual_opacity", None)
            if recover is not None:
                recover()
        except Exception:
            pass

        sync = getattr(m, "_sync_visual_opacity", None)
        if sync is not None:
            try:
                sync()
            except Exception:
                trace("OPACITY_SYNC_EXCEPTION", "")

        # Reassert only bodies that currently have an explicit non-original target.
        try:
            rows = list(m._visual_opacity_subject_rows() or [])
        except Exception:
            rows = []
        for tok, body, target in rows:
            if body is None or target is None:
                continue
            try:
                wanted = float(target)
                actual = float(body.opacity)
                if abs(actual - wanted) > 0.005:
                    body.opacity = wanted
                    trace("OPACITY_REASSERT", "token={} actual={} wanted={}".format(
                        tok, round(actual, 4), round(wanted, 4)))
            except Exception:
                pass

    def apply_comic():
        sync = getattr(m, "_sync_comic_uncertainty", None)
        if sync is None:
            return False
        try:
            return bool(sync())
        except Exception:
            trace("COMIC_SYNC_EXCEPTION", "")
            return False

    def apply_silhouette():
        fn = getattr(m, "_redraw_view_silhouettes", None)
        if fn is None:
            return
        try:
            # silhouette_visibility has already replaced this hook with the
            # reveal-filtered implementation, so this remains policy-correct.
            fn(False)
        except Exception:
            pass

    def refresh_viewport():
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass

    def render_once(reason):
        state["generation"] += 1
        generation = state["generation"]

        snap = snapshot()
        phase_counts = {}
        for row in snap.values():
            ph = str(row.get("phase"))
            phase_counts[ph] = phase_counts.get(ph, 0) + 1

        trace("RENDER_BEGIN", "gen={} reason={} phases={}".format(
            generation, reason, phase_counts))

        # One state input -> one persistent output transaction.
        clear_idle_editing_graphics()
        draw_persistent_marks()
        comic_tokens = arm_complete_comic_rebuild()
        apply_opacity()
        comic_changed = apply_comic()
        apply_silhouette()
        refresh_viewport()

        state["last_snapshot"] = snap
        state["last_reason"] = str(reason)
        trace("RENDER_DONE", "gen={} comics={} comic_changed={}".format(
            generation, len(comic_tokens), comic_changed))
        return snap

    def render(*args, **kwargs):
        """Authoritative persistent render.

        Compatibility: callers may still invoke `_redraw_marks()` with arbitrary
        positional arguments. They are ignored because desired state is derived
        from the current marks/phase, not from caller-specific redraw flags.
        """
        reason = kwargs.pop("reason", None) or "redraw"
        if state["rendering"]:
            # Coalesce nested redraw requests into one extra pass. This can happen
            # when a layer notices a stale cache while the controller is applying
            # the same desired state.
            state["pending"] = True
            return state.get("last_snapshot")

        state["rendering"] = True
        try:
            result = None
            passes = 0
            while True:
                state["pending"] = False
                result = render_once(reason if passes == 0 else "coalesced")
                passes += 1
                if not state["pending"] or passes >= 2:
                    break
            return result
        except Exception:
            trace("RENDER_EXCEPTION", "")
            # Keep a last-resort escape hatch while this architecture lands. The
            # fallback is not part of normal control flow and is intentionally not
            # exposed as another public renderer.
            try:
                if previous_redraw is not None:
                    return previous_redraw()
            except Exception:
                return None
        finally:
            state["rendering"] = False

    def render_mark(mid, reason="mark-state-change"):
        """Public semantic entry point for lifecycle/reveal owners.

        Rendering remains a whole persistent transaction because GROUP_MARKS is
        still a single legacy group. Comic geometry itself is targeted per body;
        once GROUP_MARKS also moves to per-mark groups this function can become a
        true per-mark diff without changing any caller.
        """
        try:
            mid = int(mid) if mid is not None else None
        except Exception:
            mid = None
        return render(reason="{}:{}".format(reason, mid))

    m._visual_render = render
    m._visual_render_mark = render_mark

    # IMPORTANT: from this point onward `_redraw_marks` means exactly one thing:
    # apply the complete desired persistent visual state. Historical redraw
    # wrappers captured earlier remain implementation history, not runtime owners.
    m._redraw_marks = render

    trace("CONTROLLER_READY", "one state -> one persistent render transaction")
