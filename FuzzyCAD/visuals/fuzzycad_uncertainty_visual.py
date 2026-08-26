"""Central visual authority for FuzzyCAD uncertainty.

This module answers one question for every visual layer: given a collaboration
mark, what should be visible right now?  Tool-specific renderers may add detail,
but they do not get to redefine the baseline lifecycle.

Core invariant for geometry-bearing uncertainty:

    proposed / inactive -> comic fill + sketchy boundary + badge
    editing             -> clean live preview/manipulator, no comic baseline
    resolved            -> no uncertainty overlay

Variations live here too.  For example, Fillet may keep additional fillet detail
visible, but its proposed state still inherits the same comic baseline.  Note and
Conflict are semantic exceptions because they are annotation/alternative views,
not a single uncertain body.
"""


# Visual variations are intentionally data, not scattered `if tool == ...`
# checks inside renderers.  A renderer can inspect `variant` or the derived flags
# below, but lifecycle policy remains centralized in visual_state().
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
        # Preserve the existing useful fillet-specific line/callout as an
        # addition.  The common proposed comic baseline is no longer skipped.
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

    def subject_bodies(mark):
        """Bodies participating in this proposal, primary first.

        Keep subject resolution here so the comic renderer, focus logic, and
        future visual layers agree on what the proposal actually owns.
        """
        if mark is None:
            return []
        out = []
        seen = set()

        def add(body):
            if body is None:
                return
            try:
                tok = str(body.entityToken)
            except Exception:
                tok = "id:{}".format(id(body))
            if tok in seen:
                return
            seen.add(tok)
            out.append(body)

        try:
            add(m._body.get(mark.get("id")))
        except Exception:
            pass

        # Move Together is currently the only multi-body proposal.  Keeping the
        # expansion here prevents renderers from drifting on which bodies count.
        if mark.get("tool") == "move" and mark.get("move_scope") == "together":
            for body in mark.get("related_bodies") or []:
                add(body)
        return out

    def detail_revealed(mark):
        if mark is None:
            return False
        v = variation(mark)
        ph = phase(mark)
        if ph == "editing":
            return True
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

        # Baseline uncertainty appearance.  This is the invariant renderers must
        # consume instead of independently checking `_live`, tool names, etc.
        proposed_geometry = bool(is_open and geometry and ph == "proposed")

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

            # Badge is the collaboration-state marker.  Existing renderers still
            # decide exact placement/icon; this authority decides lifecycle only.
            "show_badge": bool(is_open and ph != "resolved"),

            # Proposal-detail layer is orthogonal to baseline uncertainty style.
            "show_detail": bool(is_open and detail_revealed(mark)),

            # Interactive layer.
            "show_live_preview": bool(is_open and ph == "editing"),
            "show_manipulator": bool(is_open and ph == "editing"),

            # Fillet-specific addition: exact translucent BRep belongs to editing
            # only.  Proposed Fillet uses the common comic baseline plus its cheap
            # fillet-specific line/callout detail.
            "show_exact_fillet": bool(
                is_open and ph == "editing" and v.get("exact_fillet_editing", False)),
        }

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
    m._visual_subject_bodies = subject_bodies
    m._visual_state = visual_state
    m._visual_set_revealed = set_revealed
    m._visual_clear_revealed = clear_revealed

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD VISUAL STATE] " + msg)
        except Exception:
            pass

    log("UNCERTAINTY VISUAL AUTHORITY READY: proposed/editing/resolved + variations")
