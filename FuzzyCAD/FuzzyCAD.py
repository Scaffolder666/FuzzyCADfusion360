"""FuzzyCAD entry point.

The original implementation is kept in FuzzyCAD_legacy.py. Runtime patches
replace the fillet preview/range path, guard transient live-mark teardown, and
add a live TEXT COMMANDS debug monitor for Fusion command transactions.
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
_debug = _load("fuzzycad_debug_monitor", "fuzzycad_debug_monitor.py")
_debug.install(_legacy)

run = _legacy.run
stop = _legacy.stop
