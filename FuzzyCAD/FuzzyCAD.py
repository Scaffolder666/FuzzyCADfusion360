"""FuzzyCAD entry point.

The original implementation is kept in FuzzyCAD_legacy.py. Runtime patches
replace the fillet preview/range path, guard transient live-mark teardown,
commit accepted proposals in their own Fusion command transaction, add a live
TEXT COMMANDS debug monitor, trace manipulator values through final apply, flag
when a sidebar Accept targets a different proposal from the active handle, trace
the native manipulator inputChanged/executePreview events directly, keep the
Fillet candidate/card synchronized even when Fusion emits inputChanged without
executePreview, unify Move/Rotate/Scale/Extrude candidate visualization with
the Fillet visual language, replay Move proposals as a one-way animation while
their sidebar card is hovered, keep notes screen-facing while showing a
lightweight active-candidate size frame, separate the collaboration taxonomy,
preserve the direct body->manipulator interaction flow for scale and axis
rotation, restore the missing Uniform Scale body-selection pending state, make
directional scale one-sided by default, prototype relationship-aware Move scope,
and render interaction choices in FuzzyCAD's own HTML UI instead of Fusion's
native option controls.
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
_handle_events = _load("fuzzycad_handle_event_trace", "fuzzycad_handle_event_trace.py")
_handle_events.install(_legacy)
_fillet_live = _load("fuzzycad_fillet_input_sync", "fuzzycad_fillet_input_sync.py")
_fillet_live.install(_legacy)
_visuals = _load("fuzzycad_unified_visuals", "fuzzycad_unified_visuals.py")
_visuals.install(_legacy)
_move_hover = _load("fuzzycad_move_hover_animation", "fuzzycad_move_hover_animation.py")
_move_hover.install(_legacy)
_annotations = _load("fuzzycad_note_dimensions", "fuzzycad_note_dimensions.py")
_annotations.install(_legacy)
_next_tools = _load("fuzzycad_scale_axis_rotate_taxonomy", "fuzzycad_scale_axis_rotate_taxonomy.py")
_next_tools.install(_legacy)
_direct = _load("fuzzycad_direct_interactions", "fuzzycad_direct_interactions.py")
_direct.install(_legacy)
_scale_fix = _load("fuzzycad_scale_pending_fix", "fuzzycad_scale_pending_fix.py")
_scale_fix.install(_legacy)
_scale_scope = _load("fuzzycad_directional_scale_scope", "fuzzycad_directional_scale_scope.py")
_scale_scope.install(_legacy)
_move_scope = _load("fuzzycad_move_scope", "fuzzycad_move_scope.py")
_move_scope.install(_legacy)
_hci = _load("fuzzycad_hci_prompts", "fuzzycad_hci_prompts.py")
_hci.install(_legacy)

run = _legacy.run
stop = _legacy.stop
