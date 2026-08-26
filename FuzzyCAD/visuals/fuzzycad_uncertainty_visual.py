"""Central visual authority for FuzzyCAD uncertainty.

`VISUAL_STATE_SPEC.md` is the design contract. This module is the runtime source
of truth that turns a mark + interaction phase into a complete visual policy.
Individual renderer files draw one layer; they do not redefine lifecycle state.

The shared Proposed baseline for an unresolved geometry-bearing decision is:

    paper/white comic fill + sketchy boundary + badge

Editing is normally a clean CAD proposal view. Tool variations are explicit data
here: Fillet/Hole use a semi-transparent source body, Rough keeps the comic look
while Editing, and Compare reveals alternatives only on explicit focus/compare.
"""

COMIC_SOURCE_OPACITY = 0.02
SEMITRANSPARENT_SOURCE_OPACITY = 0.50


def _cfg(**overrides):
    row = {
        "kind": "geometry",
        "comic_capable": True,
        "comic_proposed": True,
        "comic_editing": False,
        "editing_source_opacity": None,   # None = preserve original opacity
        "editing_detail": True,
        "editing_preview": True,
        "editing_manipulator": True,
        # proposed_detail: reveal = hover/focus; focus = explicit focus only;
        # none = Proposed never expands beyond comic baseline + badge.
        "proposed_detail": "reveal",
        "exact_fillet_editing": False,
    }
    row.update(overrides)
    return row


# Explicitly encode every current tool. Similar tools intentionally repeat the
# same values so the agreed matrix is readable in code instead of hidden behind
# ad-hoc renderer conditionals.
_TOOL_VISUALS = {
    "default": _cfg(),
    "move": _cfg(),
    "rotate": _cfg(),
    "scale": _cfg(),
    "scale_axis": _cfg(),
    "axis_rotate": _cfg(),
    "extrude": _cfg(),

    "fillet": _cfg(
        editing_source_opacity=SEMITRANSPARENT_SOURCE_OPACITY,
        proposed_detail="none",
        exact_fillet_editing=True,
    ),
    "hole": _cfg(
        editing_source_opacity=SEMITRANSPARENT_SOURCE_OPACITY,
        proposed_detail="none",
    ),
    "rough": _cfg(
        comic_editing=True,
        editing_detail=False,
        editing_preview=False,
        editing_manipulator=False,
        proposed_detail="none",
    ),
    "compare": _cfg(
        kind="conflict",
        proposed_detail="focus",
    ),

    # Backward compatibility only. Note is not part of the current tool matrix.
    "_legacy_note": _cfg(
        kind="annotation",
        comic_capable=False,
        comic_proposed=False,
        comic_editing=False,
        editing_detail=False,
        editing_preview=False,
        editing_manipulator=False,
        proposed_detail="none",
    ),
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
        if tool == "note":
            return "_legacy_note"
        if tool in _TOOL_VISUALS:
            return tool
        if mark.get("mtype") in ("conflict", "alternative"):
            return "compare"
        return "default"

    def variation(mark):
        name = variation_name(mark)
        out = dict(_TOOL_VISUALS.get(name, _TOOL_VISUALS["default"]))
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

    def resolve_body_token(tok):
        if not tok or str(tok).startswith("id:"):
            return None
        try:
            design = m._design()
            if design is None:
                return None
            for ent in design.findEntityByToken(str(tok)):
                if isinstance(ent, m.adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

    def entity_body(ent):
        if ent is None:
            return None
        try:
            if isinstance(ent, m.adsk.fusion.BRepBody):
                return ent
        except Exception:
            pass
        try:
            return m._entity_body(ent)
        except Exception:
            pass
        try:
            return ent.body
        except Exception:
            return None

    def subject_bodies(mark):
        """Bodies owned by the decision, primary first.

        Subject ownership belongs to the visual authority so comic, opacity and
        future render layers cannot disagree about which bodies a proposal owns.
        """
        if mark is None:
            return []
        out = []
        seen = set()

        def add(body):
            tok = subject_token(body)
            if body is None or not tok or tok in seen:
                return
            seen.add(tok)
            out.append(body)

        mid = mark.get("id")
        try:
            add(m._body.get(mid))
        except Exception:
            pass

        # Some marks (notably target-aligned Compare) store an entity but no body.
        if not out:
            try:
                add(entity_body(m._entity.get(mid)))
            except Exception:
                pass

        # Move Together is one proposal with several body subjects.
        if mark.get("tool") == "move" and mark.get("move_scope") == "together":
            for body in mark.get("related_bodies") or []:
                add(body)

        # In-place Compare has no target body. Use the currently shown option as
        # the compact conflict subject; explicit Compare/Focus reveals both.
        if mark.get("tool") == "compare" and mark.get("inplace") and not out:
            alts = mark.get("alternatives") or []
            idx = mark.get("selected") if mark.get("selected") in (0, 1) else 0
            if 0 <= idx < len(alts):
                for tok in alts[idx].get("body_tokens") or []:
                    add(resolve_body_token(tok))

        return out

    def reveal_owned(mark):
        if mark is None:
            return False
        try:
            mid = int(mark.get("id"))
            revealed = int(state.get("revealed_id"))
        except Exception:
            return False
        if mid != revealed:
            return False

        mode = variation(mark).get("proposed_detail", "reveal")
        if mode == "none":
            return False
        if mode == "focus":
            # Compare is deliberately click/focus-to-expand, never hover-to-expand.
            try:
                return int(state.get("hover_reveal_id")) != mid
            except Exception:
                return True
        return True

    def visual_state(mark):
        """Return the complete derived visual policy for one mark."""
        ph = phase(mark)
        v = variation(mark)
        is_open = bool(mark is not None and mark.get("status", "open") == "open")

        comic_visible = bool(
            is_open and v.get("comic_capable", True) and (
                (ph == "proposed" and v.get("comic_proposed", True)) or
                (ph == "editing" and v.get("comic_editing", False))
            )
        )
        persistent_detail = bool(is_open and ph == "proposed" and reveal_owned(mark))
        editing = bool(is_open and ph == "editing")

        source_opacity = None
        if comic_visible:
            source_opacity = COMIC_SOURCE_OPACITY
        elif editing and v.get("editing_source_opacity") is not None:
            source_opacity = float(v.get("editing_source_opacity"))

        return {
            "phase": ph,
            "variant": v.get("name", "default"),
            "kind": v.get("kind", "geometry"),
            "is_open": is_open,
            "is_geometry": bool(v.get("kind") in ("geometry", "conflict")),

            # Shared persistent uncertainty baseline.
            "retain_comic": bool(is_open and v.get("comic_capable", True)),
            "show_comic_fill": comic_visible,
            "show_sketch_boundary": comic_visible,
            "show_badge": bool(is_open and ph != "resolved"),

            # Proposal detail is orthogonal to the baseline. Fillet/Hole/Rough
            # explicitly return False here in Proposed; Compare requires focus.
            "show_persistent_detail": persistent_detail,
            "show_detail": bool(
                persistent_detail or (editing and v.get("editing_detail", True))),

            # Interactive Editing layer.
            "show_live_preview": bool(editing and v.get("editing_preview", True)),
            "show_manipulator": bool(editing and v.get("editing_manipulator", True)),

            # Source body presentation. None means restore/preserve its original
            # Fusion opacity; numeric values are consumed by opacity_runtime.
            "source_opacity": source_opacity,

            # Tool-specific additions remain centrally authorized.
            "show_exact_fillet": bool(
                editing and v.get("exact_fillet_editing", False)),
        }

    def body_visual_states():
        """Aggregate mark policies into one authoritative state per body.

        A non-comic Editing mark wins over Proposed comic marks on the same body.
        Rough is the intentional exception because its Editing policy itself asks
        for the comic baseline. This same aggregation drives both comic graphics
        and body opacity so those layers cannot drift apart.
        """
        marks = list(getattr(m, "_marks", None) or [])
        rows = {}
        order = []

        for mark in marks:
            vs = visual_state(mark)
            if not vs.get("is_open"):
                continue
            for body in subject_bodies(mark):
                tok = subject_token(body)
                if not tok:
                    continue
                if tok not in rows:
                    rows[tok] = {
                        "token": tok,
                        "body": body,
                        "retained": False,
                        "wants_comic": False,
                        "suppress_comic": False,
                        "editing_opacity": None,
                        "mark_ids": [],
                    }
                    order.append(tok)
                row = rows[tok]
                row["body"] = body
                row["mark_ids"].append(mark.get("id"))

                if vs.get("retain_comic"):
                    row["retained"] = True

                if vs.get("phase") == "editing" and not vs.get("show_comic_fill"):
                    row["suppress_comic"] = True
                    if vs.get("source_opacity") is not None:
                        row["editing_opacity"] = float(vs.get("source_opacity"))

                if vs.get("show_comic_fill") and vs.get("show_sketch_boundary"):
                    row["wants_comic"] = True

        out = []
        for tok in order:
            row = rows[tok]
            row["comic_visible"] = bool(
                row.get("wants_comic") and not row.get("suppress_comic"))
            if row["comic_visible"]:
                row["source_opacity"] = COMIC_SOURCE_OPACITY
            else:
                row["source_opacity"] = row.get("editing_opacity")
            out.append(row)
        return out

    def comic_subject_rows():
        states = body_visual_states()
        visible = [(row["token"], row["body"]) for row in states
                   if row.get("comic_visible")]
        retained = set(row["token"] for row in states if row.get("retained"))
        return visible, retained

    def opacity_subject_rows():
        """Return bodies that currently require a non-original source opacity."""
        return [(row["token"], row["body"], row.get("source_opacity"))
                for row in body_visual_states()
                if row.get("source_opacity") is not None]

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
    m._visual_body_states = body_visual_states
    m._visual_comic_subject_rows = comic_subject_rows
    m._visual_opacity_subject_rows = opacity_subject_rows
    m._visual_set_revealed = set_revealed
    m._visual_clear_revealed = clear_revealed
    m._VISUAL_COMIC_SOURCE_OPACITY = COMIC_SOURCE_OPACITY
    m._VISUAL_SEMITRANSPARENT_SOURCE_OPACITY = SEMITRANSPARENT_SOURCE_OPACITY

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log(
                "[FuzzyCAD VISUAL STATE] " + msg)
        except Exception:
            pass

    log("VISUAL AUTHORITY READY: explicit tool x moment matrix + body aggregation")
