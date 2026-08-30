"""Single source of truth for a mark's visualization phase.

Every tool used to decide "what to draw" with its own ad-hoc `is_live` check,
which is why the fillet preview / x-ray / performance behaviour drifted apart.
This centralises the lifecycle into one resolver, `m._mark_phase(mark)`:

    "editing"   -- the proposal is being actively adjusted right now: either the
                   original command is live (m._live) or its card was reopened
                   through the edit-manipulator (m._active_edit_id).
    "proposed"  -- an open mark awaiting Accept / Reject (the persistent
                   Need Input state), not currently being edited.
    "resolved"  -- accepted or rejected; nothing to overlay.

("drafting" -- a tool open with no mark yet -- is a pre-mark, tool-level state,
so it has no mark to resolve and is not returned here.)

Layers should be switched off this phase, not off scattered per-tool flags. See
the phase x layer policy in the design notes.
"""


def install(m):
    def mark_phase(mark):
        if mark is None:
            return "resolved"
        try:
            if mark.get("status", "open") != "open":
                return "resolved"
            mid = mark.get("id")
            live_ids = set((getattr(m, "_live", None) or {}).values())
            if mid in live_ids or getattr(m, "_active_edit_id", None) == mid:
                return "editing"
            return "proposed"
        except Exception:
            return "proposed"

    m._mark_phase = mark_phase

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD PHASE] " + msg)
        except Exception:
            pass

    log("MARK PHASE READY: editing / proposed / resolved")
