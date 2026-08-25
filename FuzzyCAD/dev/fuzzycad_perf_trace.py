"""Low-overhead performance tracing for FuzzyCAD preview work.

This does not change interaction behavior.  It times the main rendering/state
paths and emits compact aggregate records to TEXT COMMANDS so complex-model lag
can be attributed before we optimize the wrong subsystem.
"""

import time


def install(m):
    old_run = m.run

    stats = {}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or m.adsk.core.Application.get()).log("[FuzzyCAD PERF] " + msg)
        except Exception:
            pass

    def record(name, ms):
        row = stats.setdefault(name, {"n": 0, "sum": 0.0, "max": 0.0})
        row["n"] += 1; row["sum"] += ms; row["max"] = max(row["max"], ms)
        # Slow individual calls are immediately useful; otherwise print a compact
        # aggregate occasionally so tracing itself stays cheap.
        if ms >= 20.0:
            log("SLOW {} {:.2f}ms active={}".format(name, ms, getattr(m, "_active_cmd", None)))
        elif row["n"] % 60 == 0:
            log("{} n={} avg={:.2f}ms max={:.2f}ms".format(
                name, row["n"], row["sum"] / row["n"], row["max"]))

    def wrap(name, fn):
        if fn is None:
            return None
        def timed(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                record(name, (time.perf_counter() - t0) * 1000.0)
        return timed

    for attr, name in (
        ("_draw_one", "draw_one"),
        ("_redraw_marks", "redraw_marks"),
        ("_refresh_ghost", "refresh_ghost"),
        ("_send_state", "send_state"),
    ):
        fn = getattr(m, attr, None)
        if fn is not None:
            setattr(m, attr, wrap(name, fn))

    m._perf_stats = stats

    def run(context):
        result = old_run(context)
        log("PERF TRACE READY: draw_one / redraw_marks / refresh_ghost / send_state")
        return result

    m.run = run
