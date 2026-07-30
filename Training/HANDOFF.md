# Training Handoff

## Purpose

This folder contains the initial learning-side contract and export tooling for the local 500-scenario teacher-data trial.

Use these artifacts without starting Emulator or `pythonnet`.

## Primary Files

- `DATA_CONTRACT.md`
- `schemas/training_decision.schema.json`
- `schemas/split_manifest.schema.json`
- `schemas/id_dictionaries.schema.json`
- `schemas/export_metadata.schema.json`
- `export_training_dataset.py`
- `validate_export.py`
- `sample_read_export.py`

## Export Policy

- Primary supervised dataset:
  - `usable_complete` only
- Separate retained dataset:
  - `usable_partial`
- Excluded from initial supervised export:
  - state mismatch
  - cycle
  - heuristic exception
  - emulator issue
  - missing Mad Science state
  - missing associated card

## Split Policy

- Split unit: trajectory / Scenario
- Ratio: 80 / 10 / 10
- Seed: fixed in export metadata
- Split manifests are written and reproducible

## Action-ID Rule

`action_id` is a per-state ephemeral identifier for replay/debugging only.

Do not use it as a global supervised class id.

## Residual 7-Case Classification

- `6304-18`: RL側修正
- `787-23`: RL側修正
- `2365-21`: RL側修正
- `4419-24`: RL側修正
- `5362-18`: Emulator側調査依頼
- `7678-9`: データ不足による隔離
- `6588-3`: RL側修正

See exported `quality_report.json` for the machine-readable form.
