"""Central animation ownership and timing for FuzzyCAD viewport replay.

Only one proposal animation may own the viewport at a time. Starting a new
animation always stops and clears the previous owner first, even when the new
animation belongs to a different renderer or a different card.

This controller stores pure Python state only. It never retains Fusion
CustomGraphicsGroup/BRep wrappers. Tool-specific files still draw their own
geometry, but animation ownership, timing, throttling, easing, start/frame/stop,
and supersession all go through this module.
"""

import time


_POLICIES = {
    "move": {
        "enabled": True,
        "mode": "translate_replay",
        "timing": "clock",
        "duration": 1.90,
        "frame_interval": 0.12,
        "easing": "smoothstep",
    },
    "rotate": {
        "enabled": True,
        "mode": "rotate_replay",
        "timing": "browser",
        "duration": 1.0,
        "frame_interval": 0.0,
        "easing": "linear",
    },
    "scale": {
        "enabled": True,
        "mode": "scale_replay",
        "timing": "browser",
        "duration": 1.0,
        "frame_interval": 0.0,
        "easing": "linear",
    },
    "scale_axis": {
        "enabled": True,
        "mode": "axis_scale_replay",
        "timing": "browser",
        "duration": 1.0,
        "frame_interval": 0.0,
        "easing": "linear",
    },
    "axis_rotate": {
        "enabled": True,
        "mode": "axis_rotate_replay",
        "timing": "browser",
        "duration": 1.0,
        "frame_interval": 0.0,
        "easing": "linear",
    },
    "extrude": {
        "enabled": True,
        "mode": "extrude_replay",
        "timing": "browser",
        "duration": 1.0,
        "frame_interval": 0.0,
        "easing": "linear",
    },
    "fillet": {"enabled": False, "mode": "none"},
    "hole": {"enabled": False, "mode": "none"},
    "rough": {"enabled": False, "mode": "none"},
    "compare": {"enabled": False, "mode": "none"},
    "default": {"enabled": False, "mode": "none"},
}


def install(m):
    # Installing twice is harmless and should not replace an active controller.
    if getattr(m, "_animation_controller_state", None) is not None:
        return

    state = {
        "owner": None,
        "mid": None,
        "tool": None,
        "mode": "none",
        "started": 0.0,
        "last_frame": 0.0,
        "generation": 0,
    }
    stoppers = {}
    m._animation_controller_state = state

    def log(msg):
        try:
            (m._app or m.adsk.core.Application.get()).log(
                "[FuzzyCAD ANIMATION] " + str(msg))
        except Exception:
            pass

    def policy(mark_or_tool):
        if isinstance(mark_or_tool, dict):
            tool = str(mark_or_tool.get("tool") or "default")
        else:
            tool = str(mark_or_tool or "default")
        out = dict(_POLICIES.get(tool, _POLICIES["default"]))
        out["tool"] = tool
        return out

    def clear_state():
        state["owner"] = None
        state["mid"] = None
        state["tool"] = None
        state["mode"] = "none"
        state["started"] = 0.0
        state["last_frame"] = 0.0

    def register_owner(owner, stop_callback):
        if owner and callable(stop_callback):
            stoppers[str(owner)] = stop_callback

    def is_owner(owner, mid=None):
        if state.get("owner") != str(owner):
            return False
        if mid is None:
            return True
        try:
            return int(state.get("mid")) == int(mid)
        except Exception:
            return False

    def stop_current(reason="", refresh=False):
        owner = state.get("owner")
        mid = state.get("mid")
        if owner is None:
            return False
        callback = stoppers.get(owner)

        # Clear authority BEFORE calling the renderer. This prevents a stale stop
        # callback from accidentally cancelling an animation that starts inside a
        # chained event while the previous renderer is cleaning up.
        clear_state()
        if callback is not None:
            try:
                callback(bool(refresh), True)
            except TypeError:
                try:
                    callback(bool(refresh))
                except Exception:
                    pass
            except Exception:
                pass
        log("STOP owner={} mark={} reason={}".format(owner, mid, reason or "unspecified"))
        return True

    def begin(owner, mark):
        if mark is None:
            return None
        p = policy(mark)
        if not p.get("enabled", False):
            return None
        try:
            if hasattr(m, "_mark_phase") and m._mark_phase(mark) != "proposed":
                return None
        except Exception:
            pass
        try:
            mid = int(mark.get("id"))
        except Exception:
            return None

        # The key invariant: every new start supersedes whatever was active.
        stop_current("superseded", refresh=False)

        state["generation"] = int(state.get("generation", 0)) + 1
        state["owner"] = str(owner)
        state["mid"] = mid
        state["tool"] = p.get("tool")
        state["mode"] = p.get("mode", "none")
        state["started"] = time.perf_counter()
        state["last_frame"] = 0.0
        log("START owner={} mark={} tool={} mode={}".format(
            state["owner"], mid, state["tool"], state["mode"]))
        return state["generation"]

    def apply_easing(name, t):
        t = max(0.0, min(1.0, float(t)))
        if name == "smoothstep":
            return t * t * (3.0 - 2.0 * t)
        return t

    def frame(owner, mid, browser_t=None):
        if not is_owner(owner, mid):
            return None
        p = policy(state.get("tool"))
        now = time.perf_counter()
        interval = max(0.0, float(p.get("frame_interval", 0.0) or 0.0))
        if interval > 0.0 and state.get("last_frame"):
            if now - float(state["last_frame"]) < interval:
                return None
        state["last_frame"] = now

        timing = p.get("timing", "browser")
        raw = None
        if timing == "browser" and browser_t is not None:
            try:
                raw = float(browser_t)
            except Exception:
                raw = None
        if raw is None:
            duration = max(1e-6, float(p.get("duration", 1.0) or 1.0))
            raw = (now - float(state.get("started", now))) / duration
        return apply_easing(p.get("easing", "linear"), raw)

    def end(owner, mid=None):
        if not is_owner(owner, mid):
            return False
        old_owner = state.get("owner")
        old_mid = state.get("mid")
        clear_state()
        log("END owner={} mark={}".format(old_owner, old_mid))
        return True

    def cancel(reason="cancelled", refresh=False):
        return stop_current(reason, refresh=refresh)

    m._animation_policy = policy
    m._animation_register_owner = register_owner
    m._animation_begin = begin
    m._animation_frame = frame
    m._animation_end = end
    m._animation_cancel = cancel
    m._animation_is_owner = is_owner

    log("CONTROLLER READY: single active animation owner")
