# 5362-18 candidate evaluation timeout repro

## Purpose

Emulator担当向けの最小再現スクリプト:

- [probe_5362_18_candidate_timeout.py](C:/STS2_RL/Combat/data/probe_5362_18_candidate_timeout.py)

## Expected behavior

- trajectory: `5362-18`
- expected warning: `step_exception:TimeoutException:candidate_evaluation`
- expected failure decision index: `30`
- last committed decision before the timeout: `29`
- last selected action before the timeout: `TYRANNY`

## How to run

```powershell
python C:\STS2_RL\Combat\data\probe_5362_18_candidate_timeout.py
```

## Saved-run context

- run dir: `C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4`
- source_run_id: `5362`
- source_combat_index: `18`
- data_usage: `exclude_emulator_issue`
