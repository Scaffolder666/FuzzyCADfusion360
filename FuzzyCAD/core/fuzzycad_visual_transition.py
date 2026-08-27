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

    # Directional Scale and Axis Rotate are custom toolbar commands whose legacy
    # Execute/Destroy handlers used to redraw *before* clearing `_live` and
    # `_active_cmd`. The visual authority therefore saw their final card as still
    # Editing on the first Confirm. Reopened Confirm was fine because the reopen
    # owner clears edit ownership before its persistent render. Keep the same
    # lifecycle rule here: defer those two toolbar redraws until the command's
    # existing Destroy handler has fully released ownership, then render once.
    custom_finish = {
        "deferred": False,
        "command": None,
        "mark_id": None,
    }
    custom_live_key = {
        "directional_scale": "scale_axis",
        "axis_rotate": "axis_rotate",
    }
    CurrentFuzzyCommandCreated = getattr(m, "FuzzyCommandCreated", None)

    def finish_aware_redraw(*args, **kwargs):
        cmd_name = getattr(m, "_active_cmd", None)
        live_key = custom_live_key.get(cmd_name)
        if live_key is not None:
            mid = (getattr(m, "_live", None) or {}).get(live_key)
            custom_finish["deferred"] = True
            custom_finish["command"] = cmd_name
            custom_finish["mark_id"] = mid
            trace("TOOL_RENDER_DEFERRED", "tool={} id={}".format(cmd_name, mid))
            return state.get("last_snapshot")
        return render(*args, **kwargs)

    # Keep `_visual_render` as the direct authority. Only the compatibility
    # `_redraw_marks` entry point is finish-aware so the post-Destroy handler can
    # deliberately call `render()` after lifecycle ownership is gone.
    m._redraw_marks = finish_aware_redraw

    if CurrentFuzzyCommandCreated is not None:
        class PostCustomToolDestroy(m.adsk.core.CommandEventHandler):
            def __init__(self, cmd_name):
                super().__init__()
                self.cmd_name = cmd_name

            def notify(self, args):
                # Registered after the command's existing Destroy handlers, so
                # direct_interactions has already set _active_cmd=None and _live={}
                # here. Never manufacture phase state ourselves; ask the central
                # resolver what the mark is *now* and render that state.
                if getattr(m, "_active_cmd", None) == self.cmd_name:
                    trace("TOOL_POST_DESTROY_EARLY", "tool={}".format(self.cmd_name))
                    return

                mid = custom_finish.get("mark_id")
                try:
                    render(reason="{}-post-destroy".format(self.cmd_name))
                except Exception:
                    trace("TOOL_POST_DESTROY_RENDER_EXCEPTION", "tool={}".format(
                        self.cmd_name))
                try:
                    m._send_state()
                except Exception:
                    pass

                phase = None
                try:
                    mark = m._find(mid) if mid is not None else None
                    phase = m._mark_phase(mark) if mark is not None else "gone"
                except Exception:
                    pass
                trace("TOOL_POST_DESTROY_RENDERED", "tool={} id={} phase={}".format(
                    self.cmd_name, mid, phase))

                if custom_finish.get("command") == self.cmd_name:
                    custom_finish["deferred"] = False
                    custom_finish["command"] = None
                    custom_finish["mark_id"] = None

        class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
            def notify(self, args):
                super().notify(args)
                cmd_name = getattr(self, "cmd", None)
                if cmd_name not in custom_live_key:
                    return
                try:
                    handler = PostCustomToolDestroy(cmd_name)
                    args.command.destroy.add(handler)
                    m._handlers.append(handler)
                except Exception:
                    trace("TOOL_POST_DESTROY_REGISTER_EXCEPTION", "tool={}".format(
                        cmd_name))

        m.FuzzyCommandCreated = FuzzyCommandCreated

    # Reopened Extrude used the generic slow preview path: delete GROUP_PREVIEW,
    # recreate all CustomGraphics, redraw the wireframe/cue, and refresh ghost
    # state on every native distance-manipulator tick. The crash log shows a frame
    # completing in Python and Fusion disappearing immediately afterward, which is
    # the same native graphics-churn failure mode previously removed from Move and
    # Scale. Build Extrude's wireframe once, then stretch that persistent group
    # along the face normal with one Matrix3D per frame. Keep only pure Python
    # numbers in state; every graphics group is still resolved fresh by its id.
    base_clear = m._clear
    base_draw_one = m._draw_one
    base_refresh_ghost = m._refresh_ghost
    extrude_preview = {
        "mark_id": None,
        "built": False,
        "ref_amount": None,
        "pivot": None,
        "normal": None,
        "ghost_pending": False,
    }

    def active_reopened_extrude():
        if getattr(m, "_active_cmd", None) != "edit_existing":
            return None
        mid = getattr(m, "_active_edit_id", None)
        if mid is None:
            return None
        try:
            mark = m._find(mid)
        except Exception:
            mark = None
        if (mark is None or mark.get("status", "open") != "open" or
                mark.get("tool") != "extrude"):
            return None
        return mark

    def reset_extrude_preview(reason=""):
        old_mid = extrude_preview.get("mark_id")
        extrude_preview["mark_id"] = None
        extrude_preview["built"] = False
        extrude_preview["ref_amount"] = None
        extrude_preview["pivot"] = None
        extrude_preview["normal"] = None
        extrude_preview["ghost_pending"] = False
        if old_mid is not None and reason:
            trace("EXTRUDE_REOPEN_RESET", "id={} reason={}".format(old_mid, reason))

    def ensure_extrude_session(mark):
        mid = mark.get("id") if mark is not None else None
        if extrude_preview.get("mark_id") != mid:
            reset_extrude_preview("new-session")
            extrude_preview["mark_id"] = mid

    def normalized_extrude_normal(mark):
        g = (getattr(m, "_geom", None) or {}).get(mark.get("id"), {}) or {}
        raw = list(g.get("normal") or [0.0, 0.0, 1.0])
        try:
            length = (float(raw[0]) ** 2 + float(raw[1]) ** 2 + float(raw[2]) ** 2) ** 0.5
            if length < 1e-9:
                return (0.0, 0.0, 1.0)
            return (float(raw[0]) / length, float(raw[1]) / length, float(raw[2]) / length)
        except Exception:
            return (0.0, 0.0, 1.0)

    def extrude_face_pivot(mark):
        g = (getattr(m, "_geom", None) or {}).get(mark.get("id"), {}) or {}
        for loop in g.get("loops", []) or []:
            if loop:
                try:
                    p = loop[0]
                    return (float(p[0]), float(p[1]), float(p[2]))
                except Exception:
                    pass
        a = list(mark.get("anchor") or [0.0, 0.0, 0.0])
        return (float(a[0]), float(a[1]), float(a[2]))

    def extrude_transform(mark):
        ref = extrude_preview.get("ref_amount")
        pivot = extrude_preview.get("pivot")
        normal = extrude_preview.get("normal")
        if ref is None or pivot is None or normal is None:
            return m.adsk.core.Matrix3D.create()
        try:
            ref = float(ref)
            current = float(mark.get("amount", 0.0))
            if abs(ref) < 1e-9:
                return m.adsk.core.Matrix3D.create()
            scale = current / ref
            n = normal
            k = scale - 1.0
            linear = [[0.0, 0.0, 0.0] for _ in range(3)]
            for i in range(3):
                for j in range(3):
                    linear[i][j] = (1.0 if i == j else 0.0) + k * n[i] * n[j]

            mat = m.adsk.core.Matrix3D.create()
            for i in range(3):
                for j in range(3):
                    mat.setCell(i, j, linear[i][j])

            ap = [sum(linear[i][j] * pivot[j] for j in range(3)) for i in range(3)]
            mat.translation = m.adsk.core.Vector3D.create(
                pivot[0] - ap[0], pivot[1] - ap[1], pivot[2] - ap[2])
            return mat
        except Exception:
            return m.adsk.core.Matrix3D.create()

    def build_extrude_wireframe(group, mark):
        ensure_extrude_session(mark)
        g = (getattr(m, "_geom", None) or {}).get(mark.get("id"), {}) or {}
        normal = normalized_extrude_normal(mark)
        amount = float(mark.get("amount", 0.0) or 0.0)
        ref = amount
        if abs(ref) < 1e-6:
            # An open Extrude proposal is normally non-zero. Keep a safe reference
            # anyway so a typed zero can collapse/re-expand without rebuilding.
            ref = max(float(mark.get("size", 1.0)) * 0.15, 0.10)
        pivot = extrude_face_pivot(mark)
        try:
            rgb, amp = m._style(mark)
        except Exception:
            rgb, amp = (150, 150, 150), 0.0
        orange = (225, 126, 38)

        loops = list(g.get("loops", []) or [])
        for i, loop in enumerate(loops):
            if not loop:
                continue
            try:
                base = [(float(p[0]), float(p[1]), float(p[2])) for p in loop]
            except Exception:
                continue
            off = [(p[0] + normal[0] * ref,
                    p[1] + normal[1] * ref,
                    p[2] + normal[2] * ref) for p in base]

            # Orange is locus/control only: outline the selected face boundary,
            # while the candidate offset and side walls stay provisional gray.
            try:
                m._sketchy(group, base, orange, 0.0,
                           mark.get("id", 1) * 451 + i, weight=2, strokes=1)
                m._sketchy(group, off, rgb, amp,
                           mark.get("id", 1) * 452 + i, weight=1, strokes=2)
            except Exception:
                pass

            count = len(base)
            if count:
                for kidx in sorted(set((0, count // 2, count - 1))):
                    try:
                        m._sketchy(group, [base[kidx], off[kidx]], rgb, amp,
                                   mark.get("id", 1) * 453 + i * 8 + kidx,
                                   weight=1, strokes=1)
                    except Exception:
                        pass

        tip = (pivot[0] + normal[0] * ref,
               pivot[1] + normal[1] * ref,
               pivot[2] + normal[2] * ref)
        try:
            m._sketchy(group, [pivot, tip], orange, 0.0,
                       mark.get("id", 1) * 454, weight=3, strokes=1)
        except Exception:
            pass

        extrude_preview["ref_amount"] = ref
        extrude_preview["pivot"] = pivot
        extrude_preview["normal"] = normal
        extrude_preview["built"] = True
        extrude_preview["ghost_pending"] = True

        # If the first value is effectively zero, the reference geometry was built
        # at a safe non-zero depth; collapse it immediately to the actual value.
        try:
            group.transform = extrude_transform(mark)
        except Exception:
            pass
        trace("EXTRUDE_REOPEN_BASE", "id={} ref={} loops={}".format(
            mark.get("id"), round(ref, 6), len(loops)))

    def stable_clear(gid):
        if gid == getattr(m, "GROUP_PREVIEW", "FuzzyCAD_Preview"):
            mark = active_reopened_extrude()
            if mark is not None:
                ensure_extrude_session(mark)
                if extrude_preview.get("built"):
                    # Reopen draw_active calls clear every tick. Once the base is
                    # built, that clear is exactly the native churn we must avoid.
                    return
            elif extrude_preview.get("mark_id") is not None:
                # Ownership is gone (Confirm/Cancel/switch). The next real clear is
                # allowed and also resets our pure-Python session state.
                reset_extrude_preview("ownership-ended")
        return base_clear(gid)

    def stable_draw_one(group, mark):
        active = active_reopened_extrude()
        if (active is not None and mark is not None and
                mark.get("id") == active.get("id")):
            ensure_extrude_session(mark)
            if not extrude_preview.get("built"):
                build_extrude_wireframe(group, mark)
            else:
                try:
                    # Fresh wrapper each touch; only the group's transform changes.
                    group.transform = extrude_transform(mark)
                except Exception:
                    pass
            return
        return base_draw_one(group, mark)

    def stable_refresh_ghost():
        mark = active_reopened_extrude()
        if mark is not None:
            ensure_extrude_session(mark)
            if extrude_preview.get("built"):
                if extrude_preview.get("ghost_pending"):
                    extrude_preview["ghost_pending"] = False
                    return base_refresh_ghost()
                # Source opacity is phase-derived and already correct for Editing;
                # do not rescan/rewrite it on every native drag tick.
                return None
        return base_refresh_ghost()

    m._clear = stable_clear
    m._draw_one = stable_draw_one
    m._refresh_ghost = stable_refresh_ghost

    trace("CONTROLLER_READY", "one state -> one persistent render transaction")
