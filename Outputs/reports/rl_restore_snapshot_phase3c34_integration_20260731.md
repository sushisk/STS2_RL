# RL Restore Snapshot Phase 3C.3-3C.4 Python Integration Report

**Sections below (through "Test Results") are Codex's own account, unmodified
from its draft. A supervisor completion section was appended after
independent verification - see
`rl_restore_snapshot_phase3c34_audit_20260731.md` for the full audit.**

Date: 2026-07-31
Worktree: `C:\STS2_RL_worktrees\phase3c34-history-power-restore`
Branch: `feature/phase3c34-history-power-restore`

## Preflight

`git status --short` was clean before code changes.

Emulator DLL checked:

`C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`

SHA256:

`F79A91925C75F05BACAECDF5F614BDEA01AD6C8F65F55D0E9585EFF4D1074ECC`

`GameInstance.cs` constants were checked directly:

- `RestoreApiVersion = "phase3c.4"`
- `RestoreMilestone = "phase3c.4"`
- `SnapshotSchemaVersion = "phase3c.4"`

The schema enum was checked directly from
`C:\STS2_Emulator\docs\schemas\combat_state_snapshot.schema.json`:

- `phase2a.1`
- `phase2b.1`
- `phase2b.2`
- `phase3c.3`
- `phase3c.4`

## Changes

### `Combat/combat_state_snapshot.py`

- Updated `KNOWN_SCHEMA_VERSIONS` to include `phase3c.3` and `phase3c.4`.
- Added required `CombatHistoryEntrySnapshot.PlayerTurnNumbers`.
- Updated `CombatHistoryEntrySnapshot.from_dict()` to require and parse `PlayerTurnNumbers`.

No default was added for `PlayerTurnNumbers`; missing data is still rejected by the Python dataclass loader.

### `Combat/emulator_bridge.py`

- Added `validate_restore_snapshot_json(game, json_text)`.
- The wrapper directly calls `game.ValidateRestoreSnapshotJson(json_text)`.

### `Combat/live_combat_session.py`

- Added `LiveCombatSession.validate_restore_snapshot_json(self, json_text) -> RestoreValidationResult`.
- The method mirrors `validate_restore_snapshot()` and does not touch `_session_faulted` or `_current_frame`.

### `Combat/tests/test_restore_snapshot_phase3c1.py`

- Updated stale capability expectations to `phase3c.4`.
- Updated `supports_combat_history` expectation to `True`.
- Added assertions for the 20 `serialize_required`, 13 `safe_to_recompute`, and 2 `unsupported_unknown` power classifications exposed by capabilities.
- Added JSON dry-run and restore tests for `ValidateRestoreSnapshotJson` / `RestoreSnapshotJson`.
- Added invalid JSON required-field rejection tests that verify session/frame/legal-actions preservation and subsequent `step()` viability.
- Added CombatHistory JSON fixture coverage for all 17 entry types, including entry order, round, side, actor stable IDs, `PlayerTurnNumbers`, and `CardPlay.Resources`.
- Added Restore -> Step determinism coverage with non-empty CombatHistory.
- Added Power coverage for:
  - `FeralPower` as `serialize_required`, with exact `InternalData` round trip.
  - `AfterimagePower` as `safe_to_recompute`, with Amount/AmountOnTurnStart round trip and no internal data.
  - `VigorPower` as `unsupported_unknown`, rejected before teardown with the live session preserved.
  - Player-owned, Enemy-owned, and Pet-owned power restore paths.
- Updated stale rejection coverage from the old unconditional `combat_history_non_empty` behavior to an actual `unknown_combat_history_entry_type:SyntheticUnknownEntry` rejection.

No RL-side JSON Schema validator or JSON pre-validation layer was added. JSON validation remains Emulator-owned.

### `Combat/evaluation/online_eval/verify_snapshot_phase2b.py`

- Updated one synthetic CombatHistory entry fixture to include `PlayerTurnNumbers: {}`.
- This was necessary because `CombatHistoryEntrySnapshot.from_dict()` now correctly requires the field.

## Not Changed

- No Emulator C# source was edited.
- No legacy `Combat/battle_emulator.py` restore/apply path was edited.
- No heuristic, beam search, lookahead, or training code was edited.
- No contract v0.5/v0.6/v0.7/v0.8 document was edited.
- No manifest or audit report was created.
- No git commit, tag, branch, merge, reset, or checkout operation was run.

## Notes For Supervisor Audit

`C:\STS2_Emulator\docs\contracts\combat_state_snapshot_example.v0.8.json` was read directly. It has:

- `Metadata.SchemaVersion = "phase3c.4"`
- `CombatHistory.Entries = []`

This conflicts with the integration instruction's statement that the official JSON example covers all 17 CombatHistory entry types. The Python tests therefore use the official example for the JSON API success path, and a test-local fixture ported from the accepted Emulator C# smoke for all-17 CombatHistory round-trip coverage.

The public `RestoreCapabilities.RejectionCodes` list in current `GameInstance.cs` still includes `combat_history_non_empty` and does not include bare `unknown_combat_history_entry_type`, even though actual validation returns the suffixed `unknown_combat_history_entry_type:{type}` reason. The behavior test checks the actual returned rejection.

## Test Results

Non-Python checks run:

- `git diff --check`: PASS

Python execution could not be run in this sandbox:

- `python -m py_compile ...`: failed before Python startup with PowerShell error: `specified logon session does not exist`
- `py -3 -m py_compile ...`: failed with `No installed Python found!`

Required regression command outcomes:

- `pytest C:\STS2_RL\Combat\tests\ -q`: failed before running tests, `pytest` not found on PATH.
- `python Combat/evaluation/online_eval/verify_live_combat_session_6546_21.py`: failed before Python startup with `specified logon session does not exist`.
- `python Combat/evaluation/online_eval/verify_snapshot_phase2b.py`: failed before Python startup with `specified logon session does not exist`.
- `python Combat/tests/test_choice_semantics.py`: failed before Python startup with `specified logon session does not exist`.
- `python Combat/tests/test_scenario_v2.py`: failed before Python startup with `specified logon session does not exist`.
- `python Combat/evaluation/online_eval/qb_repro_driver_rl.py --order forward --iterations 15`: failed before Python startup with `specified logon session does not exist`.

Supervisor should run the full Python regression list in an environment where Python and pythonnet can start.

## Supervisor Completion (independent verification, added after Codex's session ended)

1. **Reviewed all five changed source files' diffs in full**: `combat_state_snapshot.py`
   (`KNOWN_SCHEMA_VERSIONS`/`PlayerTurnNumbers` fixes), `emulator_bridge.py`/
   `live_combat_session.py` (`validate_restore_snapshot_json` wrapper, mirrors the existing
   `validate_restore_snapshot` exactly), `verify_snapshot_phase2b.py` (one-line fixture fix).
   All matched the governing instruction's identified gaps exactly, with no speculative
   changes beyond what was asked.
2. **Ran the full updated `test_restore_snapshot_phase3c1.py` suite** (28 test functions):
   initially 24 passed, 4 failed.
3. **Root-caused all 4 failures to a single shared cause**: `_model_fixtures()` (a new test
   helper Codex wrote) called `ensure_loaded()` then immediately iterated `ModelDb.AllPowers`
   without first constructing a `GameInstance` - `ModelDb` is not populated until a
   `GameInstance` exists (reproduced directly: iterating `ModelDb.AllPowers` before any
   `GameInstance` construction throws `TypeError: Exception has been thrown by the target
   of an invocation.`; calling `shared_game_instance()` first fixes it, yielding 281
   powers). Classified as a test-fixture/test-isolation bug (the same class of issue found
   and fixed directly in Phase 3C.1's audit), not an implementation defect - fixed directly
   by adding `shared_game_instance()` as the first statement in `_model_fixtures()`.
4. **Re-ran the suite after the fix**: 28 passed, 0 failed - including all 5
   CombatHistory/Power/JSON-validation tests whose hand-built fixture field names (ported
   from the accepted Emulator C# smoke test, e.g. `wasEthereal`, `creatorInstanceId`,
   `receiverInstanceId`, `stolenStrength`) had never actually been executed before this
   independent run, since Codex's sandbox cannot launch Python at all.
5. **Independently reproduced and confirmed both of Codex's own flagged discrepancies**:
   the official JSON example (`combat_state_snapshot_example.v0.8.json`) has
   `SchemaVersion="phase3c.4"` but an empty `CombatHistory.Entries` array (confirmed via
   direct `json.load` inspection); the public `RestoreRejectionCodes` C# list contains
   `combat_history_non_empty`/`fresh_combat_history_written` but not a bare
   `unknown_combat_history_entry_type` entry (confirmed via direct grep of
   `GameInstance.cs`), even though actual validation returns the suffixed form. Both are
   real, now documented in contract v0.8 §9-D/§9-E as follow-up items for a future
   Emulator-side round, not blockers for this one.
6. **Full regression, all independently executed** (none of this was possible in Codex's
   sandbox):
   - `python -m pytest Combat/tests/ -q`: 88 passed, 1 failed (the known, pre-existing
     WRIGGLER quarantine reason-string mismatch, unrelated to this round).
   - `verify_snapshot_phase2b.py`: PASS, 0 failing checks - notably, this script's own
     `KNOWN_SCHEMA_VERSIONS`-vs-schema-enum assertion was broken before this round's fix
     and passes after it, direct evidence the fix addressed the stated Python-side gap.
   - `verify_live_combat_session_6546_21.py`: PASS, 49 decisions, victory, 0
     `QuiescentBoundaryViolation`.
   - `test_choice_semantics.py`: 20 passed, 0 failed.
   - `test_scenario_v2.py`: 31 passed, 1 failed (same known WRIGGLER failure as above).
   - `qb_repro_driver_rl.py --order forward --iterations 15`: 780 total test executions, 0
     `QuiescentBoundaryViolation`, 15 known WRIGGLER, 0 unexpected failures.
7. **Authored `combat_state_contract.v0.8.md`** (Codex was explicitly instructed not to
   write this) and
   `rl_phase3c34_history_power_integration_manifest_20260731.json`, and this section.
8. **Discarded two non-deterministic diagnostic-output diffs**
   (`snapshot_phase2b_five_case_results.json`/`snapshot_phase2b_sample.json`, regenerated
   with fresh identifiers by `verify_snapshot_phase2b.py` on every run) rather than
   committing them.
9. **Commit split**: performed by the supervisor after independent verification passed -
   see `rl_restore_snapshot_phase3c34_audit_20260731.md` for the exact commit hashes.

Working tree is clean as of the supervisor's commits.
