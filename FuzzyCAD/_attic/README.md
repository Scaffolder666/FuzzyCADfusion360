# _attic — superseded modules (not loaded)

These files are earlier iterations that FuzzyCAD.py no longer loads. They are
kept out of the live patch stack but retained for reference. Superseded by:

- compare_* (component_groups, conflict, connectors, face_connectors,
  interaction_fix) → compare/fuzzycad_compare_stable.py + compare/fuzzycad_compare_inplace.py
- fillet_input_sync → tools/fuzzycad_fillet_stability.py
- opacity_guard, opacity_finalize → core/fuzzycad_opacity_runtime.py + core/fuzzycad_state_reconcile.py
- reference_health, reference_relink → references/fuzzycad_reference_warning.py
- incremental_render → superseded by the throttled candidate refresh in the fillet path

Nothing here is imported. Safe to delete permanently once no longer needed.
