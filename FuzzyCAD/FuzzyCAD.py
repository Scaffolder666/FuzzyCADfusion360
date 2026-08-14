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
lightweight active-candidate size frame, keep Note annotation-only without
fading its referenced body, separate the collaboration taxonomy, preserve the
direct body->manipulator interaction flow for scale and axis rotation, restore
the missing Uniform Scale body-selection pending state, make directional scale
one-sided by default, prototype relationship-aware Move scope, render interaction
choices in FuzzyCAD's own HTML UI instead of Fusion's native option controls,
keep related-part Move previews synchronized directly from the native handle,
strengthen original/proposed visual contrast, persist open collaboration state
inside the Fusion design for reopen/handoff workflows, render non-Fillet
proposals as edge-only geometry, emphasize the actual rounded Fillet region,
treat multi-body Move as one proposal group, expose procedural stage feedback in
a left-side tool rail, profile the main preview paths, clear stale runtime
graphics before persistence rehydration, keep sketch strokes light, add sparse
surface scaffolds when smooth bodies have too few topology edges to communicate
their 3D volume, add view-dependent apparent contours, add operation-specific
motion rulers and lightweight card-hover replays, and restore the preferred
FuzzyCAD panel layout on every add-in start.
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
_note_visual = _load("fuzzycad_note_no_ghost", "fuzzycad_note_no_ghost.py")
_note_visual.install(_legacy)
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
_move_polish = _load("fuzzycad_move_scope_polish", "fuzzycad_move_scope_polish.py")
_move_polish.install(_legacy)
_contrast = _load("fuzzycad_visual_contrast", "fuzzycad_visual_contrast.py")
_contrast.install(_legacy)
_hygiene = _load("fuzzycad_startup_hygiene", "fuzzycad_startup_hygiene.py")
_hygiene.install(_legacy)
_store = _load("fuzzycad_persistence", "fuzzycad_persistence.py")
_store.install(_legacy)
_outline = _load("fuzzycad_outline_only_candidates", "fuzzycad_outline_only_candidates.py")
_outline.install(_legacy)
_fillet_color = _load("fuzzycad_fillet_highlight", "fuzzycad_fillet_highlight.py")
_fillet_color.install(_legacy)
_groups = _load("fuzzycad_proposal_groups", "fuzzycad_proposal_groups.py")
_groups.install(_legacy)
_stages = _load("fuzzycad_stage_ui", "fuzzycad_stage_ui.py")
_stages.install(_legacy)
_perf = _load("fuzzycad_perf_trace", "fuzzycad_perf_trace.py")
_perf.install(_legacy)
_tuning = _load("fuzzycad_sketch_tuning", "fuzzycad_sketch_tuning.py")
_tuning.install(_legacy)
_scaffold = _load("fuzzycad_surface_scaffold", "fuzzycad_surface_scaffold.py")
_scaffold.install(_legacy)
_silhouette = _load("fuzzycad_view_silhouette", "fuzzycad_view_silhouette.py")
_silhouette.install(_legacy)
_cues = _load("fuzzycad_operation_cues", "fuzzycad_operation_cues.py")
_cues.install(_legacy)
_op_hover = _load("fuzzycad_operation_hover_animation", "fuzzycad_operation_hover_animation.py")
_op_hover.install(_legacy)
_layout = _load("fuzzycad_layout_lock", "fuzzycad_layout_lock.py")
_layout.install(_legacy)

run = _legacy.run
stop = _legacy.stop
