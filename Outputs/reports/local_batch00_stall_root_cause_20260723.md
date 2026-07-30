# Local batch_00 Stall Root Cause

## Summary

Local `teacher2000_local1500_batches_20260723/batch_00` did not produce `summary.json` or any result rows.

The batch did not simply run slowly. It exited with `exit_code=-1` after 994.38 seconds and no summary.

The immediate cause is that `run_trajectory_batch.py` only writes parent output after worker payloads return. If one or more worker scenarios do not return, `scenario_results.jsonl` remains at 0 bytes, making the batch appear completely idle.

Worker logs show two scenarios with `BEGIN` but no matching `END`:

- `4583-20`
- `1185-14`

These are the local stall candidates.

## Affected Run

- Queue root: `C:\STS2_RL\Combat\data\teacher2000_local1500_batches_20260723`
- Batch: `batch_00`
- Manifest: `C:\STS2_RL\Combat\data\teacher2000_local1500_batches_20260723\batch_00\scenario_manifest.jsonl`
- Status:
  - `exit_code`: `-1`
  - `elapsed_sec`: `994.38`
  - `has_summary`: `false`
  - `scenario_results.jsonl`: 0 bytes
  - `error_events.jsonl`: 0 bytes
  - `run_stderr.txt`: 0 bytes

## Stalled Scenarios

### `4583-20`

- Character: `SILENT`
- Encounter: `ENCOUNTER.QUEEN_BOSS`
- Floor: 15
- HP: 85 / 85
- Enemies:
  - `TORCH_HEAD_AMALGAM`, hp 211
  - `QUEEN`, hp 419
- Relics:
  - `RING_OF_THE_SNAKE`
  - `STONE_HUMIDIFIER`
  - `GORGET`
  - `STRIKE_DUMMY`
  - `PANDORAS_BOX`
  - `MERCURY_HOURGLASS`
  - `RIPPLE_BASIN`
  - `GREMLIN_HORN`
  - `JEWELRY_BOX`
  - `MUMMIFIED_HAND`
  - `STURDY_CLAMP`
  - `ODDLY_SMOOTH_STONE`
  - `BAG_OF_MARBLES`
  - `ROYAL_POISON`
- Potions:
  - `LUCKY_TONIC`
  - `FAIRY_IN_A_BOTTLE`
- Hand:
  - `ASCENDERS_BANE`
  - `LEG_SWEEP`
  - `CORROSIVE_WAVE`
  - `DEFLECT`
  - `BACKFLIP`

Worker log symptoms:

- Many candidate evaluations are logged.
- Repeated potion and card candidate evaluation appears.
- Last observed actions include Silent card chains with generated `SHIV`, `NIGHTMARE`, `BLADE_DANCE`, `CALCULATED_GAMBLE`, etc.
- No `=== END 4583-20 ===` marker was written.

### `1185-14`

- Character: `DEFECT`
- Encounter: `ENCOUNTER.DECIMILLIPEDE_ELITE`
- Floor: 11
- HP: 40 / 65
- Enemies:
  - `DECIMILLIPEDE_SEGMENT_FRONT`, hp 52
  - `DECIMILLIPEDE_SEGMENT_MIDDLE`, hp 52
  - `DECIMILLIPEDE_SEGMENT_BACK`, hp 52
- Relics:
  - `CRACKED_CORE`
  - `LEAFY_POULTICE`
  - `STRIKE_DUMMY`
  - `LIZARD_TAIL`
  - `TUNING_FORK`
  - `PAELS_TEARS`
  - `AKABEKO`
  - `BLOOD_VIAL`
  - `ORNAMENTAL_FAN`
- Potions:
  - `BLOCK_POTION`
  - `SKILL_POTION`
- Hand:
  - `SUNDER`
  - `STRIKE_DEFECT`
  - `BALL_LIGHTNING`
  - `SUBROUTINE`
  - `BOOST_AWAY`

Worker log symptoms:

- Repeated candidate evaluation involving:
  - `SKILL_POTION`
  - `SKIM`
  - `HOLOGRAM`
  - `BOOST_AWAY`
  - `WHITE_NOISE`
  - `RAINBOW`
  - repeated multi-target `BALL_LIGHTNING` / `STRIKE_DEFECT`
- No `=== END 1185-14 ===` marker was written.

## Additional Confounder

An older unbatched local run, `teacher2000_local1500_20260723_w4`, had orphaned worker processes consuming CPU at the same time.

Those orphaned processes were stopped. They were not part of the intended 100-scenario batch queue.

## Current Diagnosis

The local stall is not caused by missing manifests or Azure interaction.

Most likely cause:

- Candidate evaluation for specific high-branching / action-continuation-heavy scenarios can run for a very long time or fail to return.
- The batch driver has no per-scenario hard wall-clock timeout at the parent `Future` level.
- Parent output is only written after all worker payloads return, so one stuck scenario prevents partial results for the whole 100-scenario batch.

## Recommended Next Action

Do not rerun `batch_00` as one 100-scenario unit.

Recommended:

1. Split `batch_00` into smaller chunks, e.g. 10-scenario or 1-scenario probes.
2. Isolate `4583-20` and `1185-14`.
3. Run the remaining non-suspect `batch_00` scenarios normally.
4. Treat `4583-20` and `1185-14` as suspect cases until reproduced individually.
5. Add a parent-side per-scenario timeout or per-Future timeout before large-scale generation continues.

