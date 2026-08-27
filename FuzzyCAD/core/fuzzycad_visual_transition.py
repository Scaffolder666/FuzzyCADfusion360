"""Authoritative visual handoff for FuzzyCAD lifecycle transitions.

`fuzzycad_uncertainty_visual.py` decides what each phase means. This module owns
only the *handoff* between those derived states. In particular, leaving Editing
for Proposed is a discrete transaction, not a frame update:

    clear temporary Editing graphics
    -> reassert the centrally-derived source opacity
    -> rebuild the persistent comic baseline for the affected body
    -> one viewport refresh

Fusion can mutate display state while a native command is being torn down. The
opacity runtime intentionally caches values to avoid per-drag writes, and the
comic renderer intentionally reuses persistent groups. Those optimizations are
correct during Editing, but they must not let a command teardown leave a
half-Editing / half-Proposed viewport.

This module stores only Python ids/tokens/flags. It never retains Fusion native
wrappers across events.
"""


def install(m):
    old_sync_opacity = getattr(m, "_sync_visual_opacity", None)
    old_sync_comic = getattr(m, "_sync_comic_uncertainty", None)
    old_repair = getattr(m, "_repair_comic_integrity", None)
    CurrentFuzzyDestroy = getattr(m, "FuzzyDestroy", None)

    if old_sync_opacity is None or old_sync_comic is None:
        return

    state = {
        "comic_tokens": set(),
        "opacity_targets": {},
        "syncing_comic": False,
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
                "[FuzzyCAD VISUAL TRANSITION] {} {}".format(event, detail))
        except Exception:
            pass

    def token_of(body):
        try:
            return str(m._visual_subject_token(body))
        except Exception:
            try:
                return str(body.entityToken)
            except Exception:
                return None

    def comic_rows():
        try:
            rows, retained = m._visual_comic_subject_rows()
            return list(rows or []), set(retained or [])
        except Exception:
            return [], set()

    def opacity_rows():
        out = {}
        try:
            for tok, body, target in m._visual_opacity_subject_rows():
                if tok and body is not None and target is not None:
                    out[str(tok)] = (body, float(target))
        except Exception:
            pass
        return out

    def mark_fuzzy_dirty(tokens, reason):
        changed = False
        clean_tokens = set(str(t) for t in (tokens or []) if t)
        for tok in clean_tokens:
            try:
                entry = m._runtime_render_entry(tok, "fuzzy", True)
                entry["dirty"] = True
                entry["signature"] = None
                changed = True
            except Exception:
                pass
        if changed:
            trace(
                "COMIC_REBUILD_ARMED",
                "reason={} tokens={}".format(reason, len(clean_tokens)))
        return changed

    def reassert_opacity(tokens=None, reason="transition"):
        """Write authoritative opacity once, outside live drag rendering.

        opacity_runtime deliberately skips redundant writes using a Python cache.
        Native command teardown can nevertheless disturb Fusion's display state.
        A lifecycle handoff is rare and safe to reassert once.
        """
        wanted = opacity_rows()
        selected = set(str(t) for t in (tokens or []) if t)
        if not selected:
            selected = set(wanted.keys())

        wrote = 0
        for tok in selected:
            row = wanted.get(tok)
            if row is None:
                continue
            body, target = row
            try:
                if not bool(body.isValid):
                    continue
            except Exception:
                pass
            try:
                actual = float(body.opacity)
            except Exception:
                actual = None
            try:
                # On an explicit transition write once even if the numeric value
                # already matches. This also refreshes Fusion's display-side state.
                body.opacity = float(target)
                wrote += 1
                if actual is None or abs(actual - float(target)) > 1e-4:
                    trace(
                        "OPACITY_REASSERT",
                        "reason={} token={} actual={} target={}".format(
                            reason, tok, actual, target))
            except Exception:
                pass
        return wrote

    def sync_comic(force_tokens=None, reason="sync"):
        rows, _retained = comic_rows()
        visible = set(str(tok) for tok, _body in rows if tok)
        entering = visible - set(state.get("comic_tokens") or set())
        forced = set(str(t) for t in (force_tokens or []) if t)
        dirty = (entering | forced) & visible

        if dirty:
            mark_fuzzy_dirty(dirty, reason)

        state["syncing_comic"] = True
        try:
            result = old_sync_comic()
        finally:
            state["syncing_comic"] = False

        rows_after, _ = comic_rows()
        state["comic_tokens"] = set(str(tok) for tok, _body in rows_after if tok)
        return result

    # Install comic wrapper first. opacity_runtime calls the comic service
    # dynamically on body-level phase changes, so it should see this wrapper.
    m._sync_comic_uncertainty = sync_comic

    def sync_opacity(force_tokens=None, reason="sync"):
        before = dict(state.get("opacity_targets") or {})
        result = old_sync_opacity()

        wanted = opacity_rows()
        after = {tok: round(float(target), 6)
                 for tok, (_body, target) in wanted.items()}
        changed = set(tok for tok, target in after.items()
                      if before.get(tok) != target)
        forced = set(str(t) for t in (force_tokens or []) if t)

        if changed or forced:
            reassert_opacity(changed | forced, reason)

        state["opacity_targets"] = after
        return result

    m._sync_visual_opacity = sync_opacity

    def prepare_visual_exit(mid=None, reason="finish"):
        """Clear Editing-only layers before a native command hands off."""
        try:
            cancel = getattr(m, "_animation_cancel", None)
            if cancel is not None:
                cancel("visual-transition:" + str(reason), refresh=False)
        except Exception:
            pass
        try:
            clear = getattr(m, "_visual_clear_revealed", None)
            if clear is not None:
                clear(mid, hover_only=False)
        except Exception:
            pass
        try:
            m._clear(m.GROUP_PREVIEW)
        except Exception:
            pass

    def subject_tokens(mark):
        out = []
        seen = set()
        try:
            bodies = m._visual_subject_bodies(mark)
        except Exception:
            bodies = []
        for body in bodies or []:
            tok = token_of(body)
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
        return out

    def force_proposed_visual(mid, reason="confirm", refresh=True):
        """Establish one open mark's complete Proposed baseline immediately."""
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if mark is None:
            return False
        try:
            if m._mark_phase(mark) != "proposed":
                return False
        except Exception:
            return False

        tokens = subject_tokens(mark)
        prepare_visual_exit(mid, reason)
        mark_fuzzy_dirty(tokens, reason)

        try:
            sync_opacity(tokens, reason)
        except Exception:
            pass
        try:
            # If opacity_runtime already synchronized comic on its phase change,
            # this is a cheap no-op. Otherwise the entering-token detector performs
            # the rebuild now. Do not force-dirty twice.
            sync_comic(None, reason)
        except Exception:
            pass

        # Integrity remains a fallback after the authoritative rebuild.
        if old_repair is not None:
            try:
                old_repair("transition:" + str(reason))
            except Exception:
                pass

        if refresh:
            try:
                if m._app and m._app.activeViewport:
                    m._app.activeViewport.refresh()
            except Exception:
                pass

        trace(
            "PROPOSED_READY",
            "reason={} id={} tool={} tokens={}".format(
                reason, mid, mark.get("tool"), len(tokens)))
        return True

    m._prepare_visual_exit = prepare_visual_exit
    m._force_proposed_visual = force_proposed_visual
    m._force_visual_opacity = reassert_opacity
    m._force_comic_sync = lambda tokens=None, reason="force": sync_comic(tokens, reason)

    # The normal toolbar command has its own FuzzyDestroy path (separate from the
    # reopened-card EditDestroy). Capture which marks were Editing before Destroy,
    # let the existing lifecycle finish, then establish Proposed only for those
    # marks that actually transitioned. This is targeted: no second global redraw.
    if CurrentFuzzyDestroy is not None:
        class FuzzyDestroy(CurrentFuzzyDestroy):
            def notify(self, args):
                editing = []
                for mark in list(getattr(m, "_marks", None) or []):
                    try:
                        if m._mark_phase(mark) == "editing":
                            editing.append(mark.get("id"))
                    except Exception:
                        pass

                result = super().notify(args)

                changed = False
                for mid in editing:
                    try:
                        changed = force_proposed_visual(
                            mid, "toolbar-destroy", refresh=False) or changed
                    except Exception:
                        pass
                if changed:
                    try:
                        if m._app and m._app.activeViewport:
                            m._app.activeViewport.refresh()
                    except Exception:
                        pass
                return result

        m.FuzzyDestroy = FuzzyDestroy

    # Seed signatures without touching graphics. Future syncs can then identify
    # real phase transitions rather than treating the whole existing document as
    # newly Proposed.
    rows, _ = comic_rows()
    state["comic_tokens"] = set(str(tok) for tok, _body in rows if tok)
    state["opacity_targets"] = {
        tok: round(float(target), 6)
        for tok, (_body, target) in opacity_rows().items()
    }

    trace(
        "VISUAL_TRANSITION_READY",
        "Editing->Proposed is one targeted lifecycle handoff")
