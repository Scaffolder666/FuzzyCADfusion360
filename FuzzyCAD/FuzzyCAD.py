"""FuzzyCAD entry point.

The original implementation is kept in FuzzyCAD_legacy.py. The runtime patch
replaces only the fillet preview/range path so it is easy to test or revert
without disturbing the rest of the add-in.
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

run = _legacy.run
stop = _legacy.stop
