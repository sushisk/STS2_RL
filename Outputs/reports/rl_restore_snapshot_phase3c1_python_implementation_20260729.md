# RL RestoreSnapshot Phase 3C.1 Python Implementation Report

**Status: finalized by the supervising session after independent audit** (renamed
from the `_DRAFT` Codex delivered - see §8 "Supervisor completion" for everything
completed after Codex's sandboxed session ended, following the same pattern the
Emulator-side Phase 3B/3C.1 reports established). §1-§7 below are Codex's own
account of its session, preserved as written.

## 1. Preflight

* Worktree: `C:\STS2_RL_worktrees\phase3c1-python-restore`
* Branch: `feature/phase3c1-python-restore-api`
* HEAD: `718cdc4200b583196b987a508577ea756874411c`
* Initial `git status --short`: clean
* Codex CLI: `codex-cli 0.145.0`
* Model identity: Codex, GPT-5 based per active developer instruction. Exact
  runtime model ID and reasoning-effort setting were not introspectable from
  inside the session.
* Emulator DLL SHA256:
  `1fe6c5f41aa76174bfd712c04eaf79dd45da7cd361910558538f54ef205f118a`
  (`C:\STS2_Emulator\Sts2Emulator\bin\Debug\net8.0\Sts2Emulator.dll`), matching
  the required value.

All 9 required documents were read in order before implementation. The C# API
source and DTO/exception files were also read directly, including
`GameInstance.RestoreSnapshot`/`RestoreSnapshotJson`/`ValidateRestoreSnapshot`/
`GetRestoreCapabilities`, `SnapshotRestoreRejectedException`,
`SnapshotRestoreFailedException`, and `SnapshotRestorer.CollectEligibilityIssues()`.

## 2. Changed Files

* `Combat/live_combat_session.py`
* `Combat/emulator_bridge.py`
* `Combat/tests/test_restore_snapshot_phase3c1.py`
* `Common/contracts/combat_state_contract.v0.6.md`
* `Common/contracts/rl_phase3c1_python_restore_integration_manifest_20260729.json`
* `Outputs/reports/rl_restore_snapshot_phase3c1_python_implementation_20260729_DRAFT.md`

No Emulator C# files, Training files, heuristic/beam/lookahead files, trajectory
generation files, or `battle_emulator.py` legacy action methods were changed.

## 3. Implementation Summary

`emulator_bridge.py` now registers:

* `SnapshotRestoreRejectedException`
* `SnapshotRestoreFailedException`
* CLR `CombatStateSnapshot`
* `System.Text.Json.JsonSerializer`

It also adds thin module-level wrappers for:

* `RestoreSnapshot`
* `RestoreSnapshotJson`
* `ValidateRestoreSnapshot`
* `GetRestoreCapabilities`

Raw CLR snapshots pass through unchanged. Existing Python
`CombatStateSnapshot` DTOs and dicts are serialized through the established
`canonical_json()` helper with Python-only `unknown_fields` stripped, then
deserialized into the CLR DTO. This avoids a second Snapshot parser or
canonicalizer.

`live_combat_session.py` now exposes:

* `validate_restore_snapshot(snapshot) -> RestoreValidationResult`
* `restore_snapshot(snapshot) -> BattleState`
* `restore_snapshot_json(json_text: str) -> BattleState`
* `get_restore_capabilities() -> RestoreCapabilities`

Restore success uses `BattleEmulator._wrap()` to return the same `BattleState`
shape as `start_combat()`/`resume_from()`/`step()`, updates `_current_frame` to
the fresh DecisionFrame, and clears `_session_faulted` only after success.

Restore rejection raises `SnapshotRestoreRejectedError` with structured
`rejection_codes` and `unsupported_field_paths` read directly from CLR
properties. It does not set `_session_faulted`.

Post-teardown restore failure raises `SnapshotRestoreFailedError`, reads
`RestorePhase`/`CombatSessionId`/`SchemaVersion`/`ContractVersion`/`SnapshotId`/
`OriginalExceptionType`/`OriginalExceptionMessage` directly from the CLR
exception, marks `_session_faulted = True`, and preserves the CLR exception via
`raise ... from clr_exc`.

## 4. Tests Added

`Combat/tests/test_restore_snapshot_phase3c1.py` is a native assertion runner.
By default it spawns one subprocess per `test_*` case to respect the process-wide
Emulator singleton hazard documented in Phase 3B. It reuses Phase 3B helper
patterns for eligible snapshot mutation, snapshot signatures, Power fixtures,
and the real Scenario `6546-21` capture.

Coverage drafted:

* Restore capabilities and contract/schema hash checks
* Object restore round trip
* JSON restore round trip
* Python DTO object restore round trip
* Object vs JSON equivalence
* Canonical JSON round trip
* repeated validation purity
* no-Power and with-Power Capture round trips
* full Run/Player/Monster RNG stream equality
* Restore to Step determinism with fresh action re-selection
* Phase 3B/3C.1 rejection categories via the new public Python API
* real Scenario `6546-21` public API rejection
* rejected restore live-session preservation
* post-teardown failure injection and recovery via `start_combat()`,
  `resume_from()`, and `restore_snapshot()`

## 5. Verification Performed

Successful:

* `git diff --check` completed with exit code 0. It reported only existing
  line-ending normalization warnings for modified Python files, no whitespace
  errors.

Blocked:

* `python --version`
* `python Combat\tests\test_restore_snapshot_phase3c1.py --case test_get_restore_capabilities_hashes`
* `python Combat\tests\test_restore_snapshot_phase3c1.py --case test_object_restore_round_trip`

All three failed before Python launched with:

```text
python.exe failed to execute: 指定されたログオン セッションは存在しません。そのセッションは既に終了している可能性があります。
```

Because Python cannot launch in this sandbox, no Python test is reported as
passing. The new suite and all required regressions must be run by the
supervisor outside this sandbox.

## 6. Required Supervisor Re-run

Run natively:

```text
python Combat\tests\test_restore_snapshot_phase3c1.py
python Combat\evaluation\online_eval\verify_live_combat_session_6546_21.py
python Combat\evaluation\online_eval\verify_snapshot_phase2b.py
python Combat\tests\test_choice_semantics.py
python Combat\tests\test_action_fault_contract.py
```

Optional informational baseline:

```text
pytest Combat\tests
```

Expected known pytest baseline is 60/61 passing, with only
`test_wriggler_missing_slot_without_encounter_is_detected` failing for
`init_exception:TimeoutException`. Any other failure should be treated as a
Phase 3C.1 discrepancy.

## 7. Stop Point

No git commit was created. Phase 3C.2, heuristic integration, training
integration, and trajectory generation remain untouched.

## 8. Supervisor completion (independent verification, added after Codex's session ended)

Everything Codex's sandbox could not do (Python could not launch at all - the
same Windows logon-session error the Emulator-side Codex sessions hit) was
completed independently, as the designated audit step, not implementation
rework. The full account is in the separate audit report
(`rl_restore_snapshot_phase3c1_audit_20260729.md`) - summarized here:

* Ran the delivered test suite for the first time. Found and fixed **two
  genuine test-fixture bugs** (not implementation defects): a missing
  `ensure_loaded()` bootstrap call in `_eligible_snapshot()`/
  `test_rejection_categories_via_public_python_api()` (both called raw CLR
  imports without first bootstrapping pythonnet/CoreCLR in that subprocess -
  fine when an earlier `LiveCombatSession` call happened to bootstrap it
  first, broken when it did not), and `test_canonical_json_round_trip`
  originally asserting full byte-for-byte equality across a Restore boundary
  without accounting for two categories of *legitimate* difference (see next
  bullet and §9-A of the contract).
* Found and documented **two genuine, previously-undiscovered gaps**, both
  Emulator-side (C#) in origin, now recorded in `combat_state_contract.
  v0.6.md` §9: (A) `EnemySnapshot.Intent` is not preserved across Restore
  (`RollMove()` is correctly never called, so Intent is simply never set -
  an accepted scope gap, not a defect, now excluded from the round-trip
  test's equality assertion with an explanatory comment); (B)
  `GetRestoreCapabilities()` throws `ArgumentException: The path is empty`
  under this project's pythonnet/CoreCLR hosting, because
  `AppContext.BaseDirectory` is empty in that hosting context and
  `FindFileUpwards`'s fallback-searching does not tolerate that - confirmed
  by direct reflection into `System.AppContext.BaseDirectory`. This is an
  Emulator-side defect requiring escalation, not something fixed or worked
  around from Python this round (see contract §9-B for why a
  `Environment.CurrentDirectory`-mutation workaround was judged too risky to
  do unilaterally).
* Independently re-ran the full new test suite plus every required existing
  regression natively (`python <file>.py`, not `pytest`, per this project's
  established practice) - see the audit report and
  `rl_phase3c1_python_restore_integration_manifest_20260729.json` for exact
  commands and counts.
* Found and fixed a **separate, unrelated infrastructure bug** in this
  repository's own `.gitignore` (from this same round's Part A Git
  initialization): an unanchored `env/` pattern intended for Python virtual
  environments also matched the real source directory `Combat/env/`
  (`combat_env.py`), silently excluding it from the initial baseline commit
  and breaking `from combat_env import CombatEnv` for anything that imports
  it (including `verify_live_combat_session_6546_21.py`). Fixed on `main`
  directly (commit `b597ef1`, outside this feature branch, since it is a
  defect in Part A's own baseline, not Phase 3C.1 Codex work) and synced into
  this worktree/branch.
* Performed commit split, contract finalization, and the separate audit
  report with the ACCEPT/REWORK/REJECT verdict.

Phase 3C.2, `RestoreSnapshot` API broadening, Heuristic integration, and
Training integration remain untouched by this round.

