# Fixed50 SlotName + Emulator revalidation report (2026-07-21)

## DLL

- Path: `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
- LastWriteTime: `2026/07/21 11:56:18`
- SHA256: `6E3D97425D629506559CE8898C8053FB1EB058FA50612B834EE3DCB204EA3FEE`
- Verification: checked in a fresh Python process after `ensure_loaded()`. No old in-process DLL was reused.

## RL changes

- Added `Combat/slot_name_inference.py`.
- `build_scenario_from_spec()` now applies encounter/order SlotName restoration before constructing `EnemyScenario`.
- `encounter_to_scenario_spec()` and `scenario_from_run()` now persist the same SlotName restoration in generated specs.
- `preflight_validate()` now compares expected/actual SlotName when a spec has one.
- `combat_scenario_input_schema.json` now documents `slot_name_manifest`.
- `battle_state_key()` already included `slotName`; no change was needed there.
- `build_scenario_from_state()` already preserved Observation `slotName`; no change was needed there.

## SlotName policy

- Explicit `enemy.slot_name`: `slot_name_source=source_history`, not overwritten.
- `PHROG_PARASITE_ELITE`:
  - `PHROG_PARASITE -> phrog`, source `encounter_definition`
  - `WRIGGLER` order -> `wriggler1..4`, source `inferred_from_order`
- `EXOSKELETONS_WEAK`: `EXOSKELETON` input order -> `first`, `second`, `third`, source `inferred_from_order`
- `EXOSKELETONS_NORMAL`: `EXOSKELETON` input order -> `first`, `second`, `third`, `fourth`, source `inferred_from_order`
- Unknown/unsupported cases remain `slot_name_source=unavailable`; no trial-and-error probing is used.

## Queen recheck

- Scenario: `fixed50:5074-46`, decision 65 next_state from the previous max100 run.
- Result: restore from next_state succeeded.
- `Sequence contains no matching element`: not reproduced.
- Final Observation contains only `QUEEN`; dead `TORCH_HEAD_AMALGAM` is absent.
- Queen state after restore: `ENRAGE_MOVE`.
- LegalActions: 5 actions available.
- One step (`End Turn`) succeeded; Queen advanced to `OFF_WITH_YOUR_HEAD_MOVE`.
- Same state + same action was deterministic.

## Relic and SlotName target recheck

All 8 previously quarantined scenarios now pass preflight with no state diffs.

- `fixed50:3315-9` (`LARGE_CAPSULE`): relic/deck/potion/HP/Stars/enemy state matched.
- `fixed50:4228-34` (`LARGE_CAPSULE`): matched.
- `fixed50:5483-41` (`NEOWS_TORMENT`, `TOUCH_OF_OROBAS`): matched at preflight.
- `fixed50:4650-48` (`DIVINE_DESTINY`, `TOUCH_OF_OROBAS`): matched.
- `fixed50:6420-19` (`RING_OF_THE_DRAKE`, `TOUCH_OF_OROBAS`): matched.
- `fixed50:2744-10` (`PHROG_PARASITE_ELITE`): `phrog`, `wriggler1`; legal actions non-empty; no `UNSET_MOVE`.
- `fixed50:2428-11` (`PHROG_PARASITE_ELITE`): `phrog`, `wriggler1`; legal actions non-empty; no `UNSET_MOVE`.
- `fixed50:5067-20` (`EXOSKELETONS_WEAK`): `first`; legal actions non-empty; initial move `SKITTER_MOVE`.

## Fixed50 rerun

- Output: `Combat/data/trajectories_fixed50_slotname_emulator_20260721_115618/`

```text
total_scenarios: 50
ok: 50
quarantined: 0
usable_complete: 33
usable_partial: 16
exclude_state_mismatch: 0
exclude_heuristic_exception: 1
illegal_action_count: 0
heuristic_exception_count: 1
emulator_step_exception_count: 0
timeout_count: 0
truncated_count: 11
cycle_detected_count: 0
no_progress_detected_count: 4
determinism: 5/5
```

Change vs previous classified fixed50:

```text
quarantined: 8 -> 0
exclude_state_mismatch: 8 -> 0
usable_complete: 29 -> 33
usable_partial: 13 -> 16
```

Truncation classification:

```text
A_normal_long_combat: 7
B_heuristic_stagnation: 4
C_state_or_implementation_loop: 0
```

## New issue

- Scenario: `fixed50:5483-41`
- Character/encounter: `DEFECT`, `MECHA_KNIGHT_ELITE`
- Preflight: OK; relic/deck/potion/HP/Stars/enemy state matched.
- Decision index: 0
- LegalActions: `End Turn`, `TORIC_TOUGHNESS`, `FTL`, `TEMPEST`, `MAYHEM`, `HOTFIX`, `SPEED_POTION`
- Heuristic context: normal selection, not forced move.
- Failure: all 7 candidate evaluations timed out in `GameInstance.Step()`.
- Captured warning: `heuristic_exception:RuntimeError:Every legal-action candidate failed to evaluate`
- Reproducibility: reproduced twice in the same fresh Python process.
- Emulator stderr root signal: `NullReferenceException` in `RunicCapacitor.AfterSideTurnStart -> OrbCmd.AddSlots`.
- Classification: new Emulator-side fix candidate or scenario restore support issue. Do not use this trajectory as a normal label.

## Tests

- `python -m py_compile ...`: passed.
- `python -m json.tool Common/schemas/combat_scenario_input_schema.json`: passed.
- `python test_scenario_v2.py`: `17 passed, 0 failed`.

## Judgment

Do not proceed to 100-combat yet. The requested eight state-mismatch cases are resolved, but the new reproducible `fixed50:5483-41` candidate-timeout/RunicCapacitor issue means the fixed50 set is not fully clean under the stated scale-up conditions.
