"""FuzzyCAD entry point.

The original implementation is kept in FuzzyCAD_legacy.py. Runtime patches
replace the fillet preview/range path, guard transient live-mark teardown,
commit accepted proposals in their own Fusion command transaction, add a live
TEXT COMMANDS debug monitor, trace manipulator values through final apply, and
flag when a sidebar Accept targets a different proposal from the active handle.
"""
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_here, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_legacy = _load("fuzzycad_legacy", "FuzzyCAD_legacy.py")
_patch = _load("fuzzycad_real_fillet", "fuzzycad_real_fillet.py")
_patch.install(_legacy)
_guard = _load("fuzzycad_sync_guard", "fuzzycad_sync_guard.py")
_guard.install(_legacy)
_commit = _load("fuzzycad_commit_bridge", "fuzzycad_commit_bridge.py")
_commit.install(_legacy)
_debug = _load("fuzzycad_debug_monitor", "fuzzycad_debug_monitor.py")
_debug.install(_legacy)
_values = _load("fuzzycad_value_trace", "fuzzycad_value_trace.py")
_values.install(_legacy)
_identity = _load("fuzzycad_accept_identity_trace", "fuzzycad_accept_identity_trace.py")
_identity.install(_legacy)

run = _legacy.run
stop = _legacy.stop
