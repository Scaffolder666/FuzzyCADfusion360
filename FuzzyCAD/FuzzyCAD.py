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
if not DEV_MODE:
    _legacy._debug = lambda *_args, **_kwargs: None

# Keep references to the original lightweight proposal renderer. Fillet uses it
# continuously as the hand-drawn uncertainty layer while exact BRep candidates
# are refreshed only at a throttled cadence.
_legacy._fuzzycad_legacy_preview = _legacy.FuzzyPreview
_legacy._fuzzycad_legacy_draw_fillet = _legacy._DRAW.get("fillet")

_patch = _load("fuzzycad_real_fillet", "tools/fuzzycad_real_fillet.py")
_patch.install(_legacy)
_guard = _load("fuzzycad_sync_guard", "core/fuzzycad_sync_guard.py")
_guard.install(_legacy)
_commit = _load("fuzzycad_commit_bridge", "core/fuzzycad_commit_bridge.py")
_commit.install(_legacy)

if DEV_MODE:
    _debug = _load("fuzzycad_debug_monitor", "dev/fuzzycad_debug_monitor.py")
    _debug.install(_legacy)
    _values = _load("fuzzycad_value_trace", "dev/fuzzycad_value_trace.py")
    _values.install(_legacy)
    _identity = _load("fuzzycad_accept_identity_trace", "dev/fuzzycad_accept_identity_trace.py")
    _identity.install(_legacy)
    _handle_events = _load("fuzzycad_handle_event_trace", "dev/fuzzycad_handle_event_trace.py")
    _handle_events.install(_legacy)

_visuals = _load("fuzzycad_unified_visuals", "visuals/fuzzycad_unified_visuals.py")
_visuals.install(_legacy)
_move_hover = _load("fuzzycad_move_hover_animation", "tools/fuzzycad_move_hover_animation.py")
_move_hover.install(_legacy)
_annotations = _load("fuzzycad_note_dimensions", "visuals/fuzzycad_note_dimensions.py")
_annotations.install(_legacy)
_note_visual = _load("fuzzycad_note_no_ghost", "visuals/fuzzycad_note_no_ghost.py")
_note_visual.install(_legacy)
_next_tools = _load("fuzzycad_scale_axis_rotate_taxonomy", "tools/fuzzycad_scale_axis_rotate_taxonomy.py")
_next_tools.install(_legacy)
_direct = _load("fuzzycad_direct_interactions", "tools/fuzzycad_direct_interactions.py")
_direct.install(_legacy)
_fillet_stable = _load("fuzzycad_fillet_stability", "tools/fuzzycad_fillet_stability.py")
_fillet_stable.install(_legacy)
_scale_fix = _load("fuzzycad_scale_pending_fix", "tools/fuzzycad_scale_pending_fix.py")
_scale_fix.install(_legacy)
_scale_scope = _load("fuzzycad_directional_scale_scope", "tools/fuzzycad_directional_scale_scope.py")
_scale_scope.install(_legacy)
_move_scope = _load("fuzzycad_move_scope", "tools/fuzzycad_move_scope.py")
_move_scope.install(_legacy)
_hci = _load("fuzzycad_hci_prompts", "tools/fuzzycad_hci_prompts.py")
_hci.install(_legacy)
_move_polish = _load("fuzzycad_move_scope_polish", "tools/fuzzycad_move_scope_polish.py")
_move_polish.install(_legacy)
_contrast = _load("fuzzycad_visual_contrast", "visuals/fuzzycad_visual_contrast.py")
_contrast.install(_legacy)

_hygiene = _load("fuzzycad_startup_hygiene", "core/fuzzycad_startup_hygiene.py")
_hygiene.install(_legacy)
_store = _load("fuzzycad_persistence", "core/fuzzycad_persistence.py")
_store.install(_legacy)
_light_hydration = _load("fuzzycad_lightweight_hydration", "core/fuzzycad_lightweight_hydration.py")
_light_hydration.install(_legacy)

_outline = _load("fuzzycad_outline_only_candidates", "visuals/fuzzycad_outline_only_candidates.py")
_outline.install(_legacy)
_fillet_color = _load("fuzzycad_fillet_highlight", "tools/fuzzycad_fillet_highlight.py")
_fillet_color.install(_legacy)
_groups = _load("fuzzycad_proposal_groups", "visuals/fuzzycad_proposal_groups.py")
_groups.install(_legacy)
_opacity_runtime = _load("fuzzycad_opacity_runtime", "core/fuzzycad_opacity_runtime.py")
_opacity_runtime.install(_legacy)
_stages = _load("fuzzycad_stage_ui", "core/fuzzycad_stage_ui.py")
_stages.install(_legacy)

if DEV_MODE:
    _perf = _load("fuzzycad_perf_trace", "dev/fuzzycad_perf_trace.py")
    _perf.install(_legacy)

_tuning = _load("fuzzycad_sketch_tuning", "visuals/fuzzycad_sketch_tuning.py")
_tuning.install(_legacy)
_scaffold = _load("fuzzycad_surface_scaffold", "visuals/fuzzycad_surface_scaffold.py")
_scaffold.install(_legacy)
_silhouette = _load("fuzzycad_view_silhouette", "visuals/fuzzycad_view_silhouette.py")
_silhouette.install(_legacy)
_silhouette_stable = _load("fuzzycad_silhouette_stability", "visuals/fuzzycad_silhouette_stability.py")
_silhouette_stable.install(_legacy)
_cues = _load("fuzzycad_operation_cues", "visuals/fuzzycad_operation_cues.py")
_cues.install(_legacy)
_op_hover = _load("fuzzycad_operation_hover_animation", "visuals/fuzzycad_operation_hover_animation.py")
_op_hover.install(_legacy)
_visual_system = _load("fuzzycad_visual_system", "visuals/fuzzycad_visual_system.py")
_visual_system.install(_legacy)
_reopen = _load("fuzzycad_card_manipulator_reopen", "visuals/fuzzycad_card_manipulator_reopen.py")
_reopen.install(_legacy)

_compare = _load("fuzzycad_compare_stable", "compare/fuzzycad_compare_stable.py")
_compare.install(_legacy)
_compare_orientation = _load(
    "fuzzycad_compare_preserve_orientation",
    "compare/fuzzycad_compare_preserve_orientation.py")
_compare_orientation.install(_legacy)
_compare_full = _load(
    "fuzzycad_compare_full_preview",
    "compare/fuzzycad_compare_full_preview.py")
_compare_full.install(_legacy)
# In-place Compare: two options already built at the same spot; keep one, drop
# the other. Loaded after the target-aligned Compare so its _accept/_DRAW
# wrappers sit outermost and delegate non-in-place marks down.
_compare_here = _load("fuzzycad_compare_inplace", "compare/fuzzycad_compare_inplace.py")
_compare_here.install(_legacy)
_badges = _load("fuzzycad_uncertainty_badges", "visuals/fuzzycad_uncertainty_badges.py")
_badges.install(_legacy)

_hydrate = _load("fuzzycad_persistence_hydration", "core/fuzzycad_persistence_hydration.py")
_hydrate.install(_legacy)
_panel_resync = _load("fuzzycad_panel_state_resync", "core/fuzzycad_panel_state_resync.py")
_panel_resync.install(_legacy)
_reference_warning = _load("fuzzycad_reference_warning", "references/fuzzycad_reference_warning.py")
_reference_warning.install(_legacy)
_hover_guard = _load("fuzzycad_hover_guard", "references/fuzzycad_hover_guard.py")
_hover_guard.install(_legacy)
_compare_focus = _load(
    "fuzzycad_compare_card_focus",
    "compare/fuzzycad_compare_card_focus.py")
_compare_focus.install(_legacy)
_card_focus = _load(
    "fuzzycad_card_focus_zoom",
    "visuals/fuzzycad_card_focus_zoom.py")
_card_focus.install(_legacy)
_visibility = _load(
    "fuzzycad_progressive_visibility",
    "visuals/fuzzycad_progressive_visibility.py")
_visibility.install(_legacy)
_silhouette_visibility = _load(
    "fuzzycad_silhouette_visibility",
    "visuals/fuzzycad_silhouette_visibility.py")
_silhouette_visibility.install(_legacy)
_layout = _load("fuzzycad_layout_lock", "core/fuzzycad_layout_lock.py")
_layout.install(_legacy)

# Hole tool: diameter and depth are Need Inputs. Loaded before the dependency
# layer so its _accept handles the hole directly.
_hole = _load("fuzzycad_hole", "tools/fuzzycad_hole.py")
_hole.install(_legacy)

# Dependent follow: accepting a Move/Rotate on a fuzzy part carries the parts
# built on it (detected by a shared coincident face) along, after the user
# confirms. Wraps _accept outermost so it decides before the change is applied.
_follow = _load("fuzzycad_dependent_follow", "tools/fuzzycad_dependent_follow.py")
_follow.install(_legacy)

# Scale scope: mirror Move's selection-time question for the Scale command. Asks
# once whether touching parts stay attached; the FLEX path in dependent_follow
# then translates them at accept (position only, never resized). Loaded after
# dependent_follow so m._follow_detect_dependents is available.
_scale_scope = _load("fuzzycad_scale_scope", "tools/fuzzycad_scale_scope.py")
_scale_scope.install(_legacy)

# Dependency check: after a Scale/Extrude is accepted, raise an OK messageBox --
# "do the nearby parts still fit / any overlap?" -- highlighting them while it is
# open. Awareness only; nothing is changed. Loaded AFTER dependent_follow so its
# _accept sits outermost and the nudge appears once the whole operation (the
# scale/extrude plus any dependent parts that followed) has finished.
_dependency = _load("fuzzycad_dependency_prompts", "tools/fuzzycad_dependency_prompts.py")
_dependency.install(_legacy)

# State reconciliation: outermost redraw wrapper. Reclaims ghost bodies left
# semi-transparent after a rebuild invalidated their restore proxy, and clears
# stray idle PREVIEW graphics, keeping the 3D view in sync with the open marks.
_reconcile = _load("fuzzycad_state_reconcile", "core/fuzzycad_state_reconcile.py")
_reconcile.install(_legacy)

# "Clear all" panel action: permanently delete this document's stored FuzzyCAD
# uncertainty (the persistence attributes + overlays + in-memory marks). Loaded
# last so its palette handler is outermost and intercepts the action first.
_clear_all = _load("fuzzycad_clear_all", "core/fuzzycad_clear_all.py")
_clear_all.install(_legacy)

# Inspector: a fallback admin surface in the palette -- a live status snapshot
# (open marks by type, ghosted bodies, stray graphics) and a one-click Repair
# that runs the visual authority (sweep + restore + redraw) by hand.
_inspector = _load("fuzzycad_inspector", "core/fuzzycad_inspector.py")
_inspector.install(_legacy)

# EXPERIMENT — fuzzy boundary as the ghost replacement. Keeps the questioned body
# mostly visible and draws its edges as an offset hand-drawn blur ("the true edge
# isn't pinned"), moving the "unsettled" signal off transparency. Loaded last so
# it softens the fade after opacity_runtime and draws after every other layer.
# To revert to the classic ghost: set _legacy._FUZZY_BOUNDARY = False, or remove
# these two lines.
_fuzzy = _load("fuzzycad_fuzzy_boundary", "visuals/fuzzycad_fuzzy_boundary.py")
_fuzzy.install(_legacy)

run = _legacy.run
stop = _legacy.stop
