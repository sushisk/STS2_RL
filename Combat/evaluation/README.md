# Combat/evaluation

- `benchmark_states/` - the fixed 50-scenario regression/comparison set
  (`fixed_50_scenarios.json`, generated+validated by `generate_fixed_50.py`). Used by
  `Combat/data/run_trajectory_batch.py --source fixed50` and intended to stay the
  standard quick regression check for the Emulator bridge, Heuristic, and teacher-data
  pipeline going forward - see `STS2_RL/docs/RL_HANDOFF.md` section 12 for its current
  (incomplete) status.
- `reports/emulator_hang/` - **historical investigation record**, not current
  documentation. This is the raw evidence trail from finding the `LEAD_PAPERWEIGHT`/
  `CLAWS`/`LOST_COFFER`-class Emulator hang bug, captured *before* the Emulator-side fix
  landed. For the current, fixed state of the Emulator, see
  `Outputs/reports/emulator_fix_revalidation_report.md` and
  `STS2_RL/docs/RL_HANDOFF.md` section 5.6 instead.
