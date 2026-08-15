"""FuzzyCAD entry point.

The original implementation is kept in FuzzyCAD_legacy.py. Runtime patches
layer collaboration UI, persistence, manipulators, proposal visuals, and
operation-specific behavior onto the legacy implementation.

DEV_MODE is intentionally False for the study/runtime build. Heavy lifecycle,
value, handle, identity, and performance tracing stay available in the repo but
are not installed unless development diagnostics are explicitly enabled.
"""
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
DEV_MODE = False


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_here, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_legacy = _load("fuzzycad_legacy", "FuzzyCAD_legacy.py")
_legacy.DEV_MODE = DEV_MODE

_patch = _load("fuzzycad_real_fillet", "fuzzycad_real_fillet.py")
_patch.install(_legacy)
_guard = _load("fuzzycad_sync_guard", "fuzzycad_sync_guard.py")
_guard.install(_legacy)
_commit = _load("fuzzycad_commit_bridge", "fuzzycad_commit_bridge.py")
_commit.install(_legacy)

if DEV_MODE:
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

# Install exact opacity preservation inside startup hygiene/persistence so an
# interrupted previous session is restored before saved marks are rehydrated.
_opacity = _load("fuzzycad_opacity_guard", "fuzzycad_opacity_guard.py")
_opacity.install(_legacy)
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
_opacity_final = _load("fuzzycad_opacity_finalize", "fuzzycad_opacity_finalize.py")
_opacity_final.install(_legacy)
_stages = _load("fuzzycad_stage_ui", "fuzzycad_stage_ui.py")
_stages.install(_legacy)

if DEV_MODE:
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
_visual_system = _load("fuzzycad_visual_system", "fuzzycad_visual_system.py")
_visual_system.install(_legacy)
_reopen = _load("fuzzycad_card_manipulator_reopen", "fuzzycad_card_manipulator_reopen.py")
_reopen.install(_legacy)

_compare = _load("fuzzycad_compare_stable", "fuzzycad_compare_stable.py")
_compare.install(_legacy)
_compare_orientation = _load(
    "fuzzycad_compare_preserve_orientation",
    "fuzzycad_compare_preserve_orientation.py")
_compare_orientation.install(_legacy)
_compare_full = _load(
    "fuzzycad_compare_full_preview",
    "fuzzycad_compare_full_preview.py")
_compare_full.install(_legacy)

_incremental = _load("fuzzycad_incremental_render", "fuzzycad_incremental_render.py")
_incremental.install(_legacy)

_hydrate = _load("fuzzycad_persistence_hydration", "fuzzycad_persistence_hydration.py")
_hydrate.install(_legacy)
_panel_resync = _load("fuzzycad_panel_state_resync", "fuzzycad_panel_state_resync.py")
_panel_resync.install(_legacy)
_reference = _load("fuzzycad_reference_relink", "fuzzycad_reference_relink.py")
_reference.install(_legacy)
_reference_health = _load("fuzzycad_reference_health", "fuzzycad_reference_health.py")
_reference_health.install(_legacy)
_hover_guard = _load("fuzzycad_hover_guard", "fuzzycad_hover_guard.py")
_hover_guard.install(_legacy)
_compare_focus = _load(
    "fuzzycad_compare_card_focus",
    "fuzzycad_compare_card_focus.py")
_compare_focus.install(_legacy)
_layout = _load("fuzzycad_layout_lock", "fuzzycad_layout_lock.py")
_layout.install(_legacy)

run = _legacy.run
stop = _legacy.stop
