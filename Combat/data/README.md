# Combat/data

Data pipeline: raw human-run data -> canonical ID validation -> per-floor state
reconstruction -> real-Emulator validation -> Heuristic-driven teacher-data trajectories.

**Full narrative, current numbers, and next steps: `STS2_RL/docs/RL_HANDOFF.md`
(sections 4, 6, 9, 10, 12).** This file only orients which script does what.

## Pipeline scripts (run in this rough order)

- `audit_runs_dataset.py` - initial Phase 0 audit of the raw 6,796-run dataset.
- `scenario_from_runs.py` - final-run-state scenario generator (superseded in fidelity
  by `reconstruct_floor_state.py` below, kept for its `CHARACTER_STARTING_HP` etc.
  constants and as a simpler reference implementation).
- `reconstruct_floor_state.py` - the core per-floor state replay engine
  (`ReplayState`, `reconstruct_encounters_for_run()`, `encounter_to_scenario_spec()`,
  `validate_run_reconstruction()`). Read this file's module docstring first.
- `reconstruct_all_runs.py` - runs the above across all 5,997 usable runs, writes
  `full_reconstruction/`.
- `validate_reconstruction_staged.py` - offline (no Emulator) v8/v9/mix/50-run
  validation of the reconstruction's own internal consistency.
- `validate_reconstructed_scenarios_live.py` / `validate_reconstructed_scenarios_at_scale.py`
  - real-Emulator validation of reconstructed scenarios (small sample / at-scale
    stratified sample respectively).
- `revalidate_lostcoffer_fix.py` - targeted re-validation script written for one
  specific Emulator bug fix; kept as a template for future targeted re-validations.
- `preflight_validate.py` - **used by the production trajectory pipeline**: validates
  a scenario against the just-initialized Emulator state before any Heuristic decision
  runs from it.
- `generate_heuristic_trajectories.py` / `run_trajectory_batch.py` - **the teacher-data
  generation pipeline itself**. See `RL_HANDOFF.md` section 9 for schema and section 12
  for the fixed-50 completion criteria that must pass before scaling up.
- `repro_from_batch_run.py` - replays one saved batch scenario by `trajectory_id`
  using the saved `scenario_manifest.jsonl`; useful as the shared entrypoint behind
  generated repro wrappers.
- `summarize_batch_outputs.py` - offline summary over a saved run directory
  (`scenario_results.jsonl`, `error_events.jsonl`), no combat execution required.
- `generate_repro_wrappers.py` - offline helper that scans `error_events.jsonl` and
  emits one small wrapper script per affected `trajectory_id` under
  `generated_repros/`.

## Output directories

- `full_reconstruction/` - production output of `reconstruct_all_runs.py` (current).
- `full_reconstruction_PRE_ID_FIX/` - snapshot from before the ID-dictionary
  transitive-inheritance fix, kept for before/after comparison only.
- `trajectories_fixed50/`, `trajectories_fixed10_smoke/` - trajectory-generation trial
  outputs (see `RL_HANDOFF.md` section 10 for their status - not yet a clean full run).
- `raw/`, `converted/`, `heuristic/` - empty placeholders from the plan's originally
  recommended structure, unused so far.

## Operational notes

`run_trajectory_batch.py` flushes `trajectories.jsonl`, `trajectory_meta.jsonl`, and
`quarantine.jsonl` after each write, so line counts are usable for rough progress checks
while a long batch is running. Treat `summary.json` as authoritative only after the
process exits.

`run_trajectory_batch.py --resume` now prefers the saved `scenario_manifest.jsonl`
inside the output directory. For reconstructed samples, this matters: it prevents a
resume from silently re-sampling a different 100-scenario set.

Additional batch assets written by the current runner:

- `scenario_manifest.jsonl` - fixed input manifest for the run; required for safe
  resume and for later repro.
- `scenario_results.jsonl` - one row per scenario, including elapsed time, stderr
  excerpt path, quality classification, saved result payload, and extracted error
  events.
- `error_events.jsonl` - flattened error/warning index keyed by `trajectory_id`,
  intended for quick triage and downstream repro generation.
- `error_summary.json` - offline aggregation by error kind/type.
- `generated_repros/` - per-scenario wrapper scripts pointing at
  `repro_from_batch_run.py`.

## teacher2000_20260723 (2026-07-23 large-scale generation)

Separate from the `trajectories_*` trial runs above, `teacher2000_*` is the 2000-scenario
production batch (1500 local + 500 Azure) generated on 2026-07-23. Its raw run history involved
several parallel/retried attempts (some failed or abandoned) across a local PC and an Azure Spot VM;
these have been reconciled and deduplicated into three directories:

- `teacher2000_20260723_manifests/` - input scenario manifests (parent 2000-set and all
  local/Azure/batch splits actually used).
- `teacher2000_20260723_dataset/` - **the consolidated, dedup'd, RL-ready output.** One winning
  trajectory per scenario ID, plus `quarantine_report.jsonl` (36 scenarios that never succeeded
  anywhere), `provenance_manifest.jsonl` (audit trail of which run each scenario ID came from and
  why), and merged `error_events.jsonl`/`error_summary.json`/`dataset_summary.json`. See its own
  `README.md` for the full reconciliation methodology.
- `teacher2000_20260723_archive/` - raw per-batch outputs from the runs that actually produced
  usable data (`local_batches100/`, `azure_500/`, `azure_batches100/`, `azure_extra1450/`), kept for
  provenance/reproducibility. Fully-failed and abandoned attempts (a crashed single-shot run and a
  25-per-batch attempt superseded by the 100-per-batch run) were deleted rather than archived.

## Dynamic state collection notes

When reconstructing or persisting scenario inputs for future teacher-data generation,
keep the following per-instance fields; do not infer them later from card text or
power ids:

- `hand_cards` / `draw_pile_cards` / `discard_pile_cards` / `exhaust_pile_cards`
  `MAD_SCIENCE` entries must preserve both `tinker_time_type` and
  `tinker_time_rider`. If either value is unavailable, quarantine as
  `missing_mad_science_state`.
- `player_powers[].associated_card` must be stored for powers that require private
  card state, currently `NIGHTMARE_POWER`. If missing, quarantine as
  `missing_associated_card` instead of guessing.
