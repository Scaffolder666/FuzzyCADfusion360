"""Central visual authority for FuzzyCAD uncertainty.

This module answers one question for every visual layer: given a collaboration
mark, what should be visible right now? Tool-specific renderers may add detail,
but they do not get to redefine the baseline lifecycle.

Core invariant for geometry-bearing uncertainty:

    proposed / inactive -> comic fill + sketchy boundary + badge
    editing             -> clean live preview/manipulator, no comic baseline
    resolved            -> no uncertainty overlay

Variations live here too. Fillet can add fillet-specific detail, but its proposed
state still inherits the common comic baseline. Note and Conflict are semantic
exceptions because they are annotation/alternative views, not a single uncertain
body.
"""


_VARIATIONS = {
    "default": {
        "kind": "geometry",
        "retain_comic": True,
        "detail_always": False,
        "exact_fillet_editing": False,
    },
    "fillet": {
        "kind": "geometry",
        "retain_comic": True,
        "detail_always": True,
        "exact_fillet_editing": True,
    },
    "note": {
        "kind": "annotation",
        "retain_comic": False,
        "detail_always": True,
        "exact_fillet_editing": False,
    },
    "compare": {
        "kind": "conflict",
        "retain_comic": False,
        "detail_always": False,
        "exact_fillet_editing": False,
    },
}


def install(m):
    state = {
        "revealed_id": None,
        "hover_reveal_id": None,
    }
    m._uncertainty_visual_state = state

    def variation_name(mark):
        if mark is None:
            return "default"
        tool = str(mark.get("tool") or "")
        if tool in _VARIATIONS:
            return tool
        if mark.get("mtype") in ("conflict", "alternative"):
            return "compare"
        return "default"

    def variation(mark):
        name = variation_name(mark)
        out = dict(_VARIATIONS.get(name, _VARIATIONS["default"]))
        out["name"] = name
        return out

    def phase(mark):
        try:
            return str(m._mark_phase(mark))
        except Exception:
            if mark is None or mark.get("status", "open") != "open":
                return "resolved"
            return "proposed"

    def subject_token(body):
        if body is None:
            return None
        try:
            return str(body.entityToken)
        except Exception:
            return "id:{}".format(id(body))

    def subject_bodies(mark):
        """Bodies participating in this proposal, primary first."""
        if mark is None:
            return []
        out = []
        seen = set()

        def add(body):
            tok = subject_token(body)
            if body is None or tok in seen:
                return
            seen.add(tok)
            out.append(body)

        try:
            add(m._body.get(mark.get("id")))
        except Exception:
            pass

        if mark.get("tool") == "move" and mark.get("move_scope") == "together":
            for body in mark.get("related_bodies") or []:
                add(body)
        return out

    def detail_revealed(mark):
        if mark is None:
            return False
        v = variation(mark)
        if v.get("detail_always"):
            return True
        try:
            return int(mark.get("id")) == int(state.get("revealed_id"))
        except Exception:
            return False

    def visual_state(mark):
        """Return the complete derived visual policy for one mark."""
        ph = phase(mark)
        v = variation(mark)
        is_open = bool(mark is not None and mark.get("status", "open") == "open")
        geometry = v.get("kind") == "geometry"
        proposed_geometry = bool(is_open and geometry and ph == "proposed")
        persistent_detail = bool(is_open and ph == "proposed" and detail_revealed(mark))
        live_preview = bool(is_open and ph == "editing")

        return {
            "phase": ph,
            "variant": v.get("name", "default"),
            "kind": v.get("kind", "geometry"),
            "is_open": is_open,
            "is_geometry": geometry,

            # Persistent baseline uncertainty representation.
            "retain_comic": bool(is_open and geometry and v.get("retain_comic", True)),
            "show_comic_fill": proposed_geometry,
            "show_sketch_boundary": proposed_geometry,

            # Collaboration-state marker.
            "show_badge": bool(is_open and ph != "resolved"),

            # Detail has two channels. Persistent detail belongs to Proposed;
            # live detail belongs to Editing. Renderers can use show_detail when
            # they draw into either channel, while progressive visibility uses
            # show_persistent_detail specifically.
            "show_persistent_detail": persistent_detail,
            "show_detail": bool(persistent_detail or live_preview),

            # Interactive layer.
            "show_live_preview": live_preview,
            "show_manipulator": live_preview,

            # Fillet variation: exact translucent BRep is editing-only.
            "show_exact_fillet": bool(
                live_preview and v.get("exact_fillet_editing", False)),
        }

    def comic_subject_rows():
        """Aggregate mark state into body-level comic visibility.

        Returns `(visible_rows, retained_tokens)`. If any proposal on a body is
        being edited, editing wins for that body and suppresses the comic baseline.
        """
        marks = list(getattr(m, "_marks", None) or [])
        retained = set()
        editing = set()

        for mark in marks:
            vs = visual_state(mark)
            if not vs.get("retain_comic"):
                continue
            for body in subject_bodies(mark):
                tok = subject_token(body)
                if tok:
                    retained.add(tok)
                    if vs.get("phase") == "editing":
                        editing.add(tok)

        visible = []
        seen = set()

        def maybe_add(body):
            tok = subject_token(body)
            if not tok or tok in seen or tok in editing:
                return
            seen.add(tok)
            visible.append((tok, body))

        # Primary subjects first preserve the existing deterministic seed order.
        for mark in marks:
            vs = visual_state(mark)
            if not (vs.get("show_comic_fill") and vs.get("show_sketch_boundary")):
                continue
            try:
                maybe_add(m._body.get(mark.get("id")))
            except Exception:
                pass

        # Additional/group subjects follow primary subjects.
        for mark in marks:
            vs = visual_state(mark)
            if not (vs.get("show_comic_fill") and vs.get("show_sketch_boundary")):
                continue
            for body in subject_bodies(mark)[1:]:
                maybe_add(body)

        return visible, retained

    def set_revealed(mid, hover=False):
        if mid is None:
            if hover:
                state["hover_reveal_id"] = None
            state["revealed_id"] = None
            return
        try:
            mid = int(mid)
        except Exception:
            return
        state["revealed_id"] = mid
        state["hover_reveal_id"] = mid if hover else None

    def clear_revealed(mid=None, hover_only=False):
        try:
            target = int(mid) if mid is not None else None
        except Exception:
            target = None
        if hover_only:
            if target is None or state.get("hover_reveal_id") == target:
                if state.get("revealed_id") == state.get("hover_reveal_id"):
                    state["revealed_id"] = None
                state["hover_reveal_id"] = None
            return
        if target is None or state.get("revealed_id") == target:
            state["revealed_id"] = None
        if target is None or state.get("hover_reveal_id") == target:
            state["hover_reveal_id"] = None

    m._visual_variation = variation
    m._visual_subject_token = subject_token
    m._visual_subject_bodies = subject_bodies
    m._visual_state = visual_state
    m._visual_comic_subject_rows = comic_subject_rows
    m._visual_set_revealed = set_revealed
    m._visual_clear_revealed = clear_revealed

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD VISUAL STATE] " + msg)
        except Exception:
            pass

    log("UNCERTAINTY VISUAL AUTHORITY READY: lifecycle + comic invariant + variations")
