# RL Phase 3C.2 Python Restore Pet Integration Report (2026-07-30)

**Sections 1-7 below are Codex's own account, unmodified from its draft. Section
8 was added by the current RL担当 (supervisor) after independent verification -
see `rl_restore_snapshot_phase3c2_audit_20260730.md` for the full audit.**

## 1. Scope

Branch/worktree:

- `feature/phase3c2-python-pet-restore`
- cwd: `C:\STS2_RL_worktrees\phase3c2-python-pet-restore`
- baseline described by supervisor: `2ce4912` / `rl-phase3c1-python-accepted-20260729`

Emulator DLL used for preflight:

- `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
- SHA256: `FFD10AB5607F5683CE507CA849CF9E09BF671311B58F65AFD8530F4D06E6DAF1`

This round integrates the already-accepted Emulator Phase 3C.2 Pet restore behavior into
the existing Python Restore API tests. No new Python Restore API surface was added.

## 2. Required Reading Summary

Read before edits:

- Emulator Phase 3C.2 design report.
- Emulator Phase 3C.2 implementation report.
- Emulator Phase 3C.2 audit report, especially section 7.
- `Common/contracts/combat_state_contract.v0.6.md`.
- `Outputs/reports/rl_restore_snapshot_phase3c1_audit_20260729.md`.
- Emulator C# source for `GameInstance.GetRestoreCapabilities()` and `SnapshotRestorer`.
- Emulator smoke test `smoke_restore_snapshot_phase3c2_pet_20260730.cs`.
- RL Python files `emulator_bridge.py`, `live_combat_session.py`,
  `test_restore_snapshot_phase3c1.py`, `verify_restore_bootstrap_phase3b.py`,
  and `combat_state_snapshot.py`.

Key findings:

- `GetRestoreCapabilities()` now reports `RestoreApiVersion = "phase3c.2"`,
  `Milestone = "phase3c.2"`, and `SupportsPets = true`.
- The pythonnet empty `AppContext.BaseDirectory` bug is fixed in the Emulator DLL.
- The old `pet_count` rejection gate is removed.
- Pets use existing `CreatureSnapshot` and `PowerSnapshot` DTOs; no Python schema or
  wrapper change is needed.
- Pet powers are restored and validated through the existing Power path.

## 3. Changed Files

Changed:

- `Combat/tests/test_restore_snapshot_phase3c1.py`
- `Outputs/reports/rl_restore_snapshot_phase3c2_python_integration_20260730_DRAFT.md`

Not changed:

- `Combat/emulator_bridge.py`
- `Combat/live_combat_session.py`
- Emulator C# source under `C:\STS2_Emulator\**`
- `Combat/battle_emulator.py`
- `Combat/heuristic_agent.py`
- `Combat/beam_search.py`
- `Combat/lookahead.py`
- `Training/**`
- `Common/contracts/combat_state_contract.v0.5.md`
- Contract v0.6/v0.7 documents and manifests

## 4. Implementation Details

Updated stale Phase 3C.1 expectations in `test_get_restore_capabilities_hashes`:

- `restore_api_version`: `phase3c.1` -> `phase3c.2`
- `milestone`: `phase3c.1` -> `phase3c.2`
- `supports_pets`: `False` -> `True`

Updated `test_rejection_categories_via_public_python_api`:

- Removed the obsolete `_assert_rejected(pet_snapshot, "pet_count")`.
- Kept a light Scenario `6546-21` check in that function: validation must still be
  ineligible, must include `reference_integrity`, and must not include `pet_count`.
- Left `test_real_6546_21_rejected_via_public_api` unchanged as the full coverage for
  the scenario-specific rejection.

Added Pet-specific tests in the existing Phase 3C.1 Restore test file:

- `test_pet_object_restore_round_trip`
- `test_pet_json_restore_round_trip_matches_object_restore`
- `test_pet_canonical_json_round_trip`
- `test_pet_restore_step_determinism_reselects_fresh_action`

Fixture choice:

- Used a real `BOUND_PHYLACTERY` scenario through `LiveCombatSession.start_combat()`.
- Captured the live Osty Pet and then reused `_make_eligible()` to clear
  `CombatHistory.Entries` and normalize metadata for Restore eligibility.
- Did not use a synthetic Pet fallback because the Emulator audit independently verified
  the real summon path, and this is the fixture construction requested for first attempt.

The Pet comparison helpers are deliberately narrow. They compare the restored Pet's
`InstanceId`, `Kind`, `MonsterId`, `OwnerInstanceId`, `CombatId`, `Hp`, `MaxHp`,
`Block`, `IsAlive`, and Power signature. They do not parse or canonicalize snapshots;
they sit alongside the existing `_snapshot_sig()` checks from
`verify_restore_bootstrap_phase3b.py`.

## 5. Wrapper Decision

No changes were made to `Combat/emulator_bridge.py` or `Combat/live_combat_session.py`.

Reason: Phase 3C.1 already routed Python `CombatStateSnapshot` dataclasses/plain dicts
through `canonical_json()` and the CLR `CombatStateSnapshot` deserializer. `Player.Pets`
and `CreatureSnapshot` already exist in the Python DTO schema, and
`RestoreCapabilities.supports_pets` already exists in the Python dataclass.

Adding Pet-specific wrapper code would duplicate an existing generic path and would be
outside the Phase 3C.2 API shape.

## 6. Verification

Preflight:

- `git status --short`: clean before edits.
- Emulator DLL hash computed successfully: `FFD10AB5607F5683CE507CA849CF9E09BF671311B58F65AFD8530F4D06E6DAF1`.

Static checks completed:

- `git diff --check`: exit 0. It printed only the existing-style warning that LF in
  `Combat/tests/test_restore_snapshot_phase3c1.py` will be replaced by CRLF next time
  Git touches it.
- Manual diff review confirmed only the intended test/report files changed.
- `rg` confirmed no remaining `phase3c.1` capability literals in the test file and no
  active `pet_count` assertion.

Attempted runtime tests:

- `python Combat\tests\test_restore_snapshot_phase3c1.py --case test_pet_object_restore_round_trip`
  - Not executed. `python.exe` failed to launch with Windows logon-session error:
    "The specified logon session does not exist. It may already have been terminated."

Required regression command outcomes in this sandbox:

- `pytest C:\STS2_RL\Combat\tests\ -q`
  - Not executed: `pytest` is not recognized on PATH.
- `python Combat/evaluation/online_eval/verify_live_combat_session_6546_21.py`
  - Not executed: `python.exe` failed with the Windows logon-session error.
- `python Combat/evaluation/online_eval/verify_snapshot_phase2b.py`
  - Not executed: `python.exe` failed with the Windows logon-session error.
- `python Combat/tests/test_choice_semantics.py`
  - Not executed: `python.exe` failed with the Windows logon-session error.
- `python Combat/tests/test_scenario_v2.py`
  - Not executed: `python.exe` failed with the Windows logon-session error.
- `python Combat/evaluation/online_eval/qb_repro_driver_rl.py --order forward --iterations 15`
  - Not executed: `python.exe` failed with the Windows logon-session error.

## 7. Open Questions And Audit Risks

- The new tests could not be executed in this sandbox. Supervisor should run the full
  requested regression list in an environment where Python and pytest can launch.
- The real `BOUND_PHYLACTERY` Pet fixture depends on combat-start behavior continuing to
  summon exactly one Osty. This is the intended Phase 3C.2 fixture and was independently
  verified by the Emulator audit, but this draft could not re-verify it locally.
- `test_restore_snapshot_phase3c1.py` keeps its historical filename. The API surface is
  still the same Restore API, so the Pet tests were intentionally added there rather than
  split into a Phase 3C.2 file.

## 8. Supervisor Completion (independent verification, added after Codex's session ended)

Everything Codex's sandbox could not do was completed independently by the current
RL担当 (supervisor):

1. **Ran the full updated `test_restore_snapshot_phase3c1.py` suite** (19 test functions,
   including Codex's 4 new Pet tests): initially 18 passed, 1 failed
   (`test_get_restore_capabilities_hashes`).
2. **Root-caused the one failure**: it was a genuine, but modest, pre-existing test-fixture
   issue - the test hashes its own local worktree checkout of `combat_state_contract.v0.5.md`
   with raw bytes, but this worktree's copy has CRLF line endings (`core.autocrlf=true`)
   while the Emulator's reported `ContractSha256` reflects the canonical LF git-blob content.
   This was never previously reachable/exercised because the Phase 3C.1-era
   `AppContext.BaseDirectory` bug (contract v0.6 §9-B) always threw before this specific
   assertion was ever reached in any prior round - this round's Emulator-side fix to that
   bug is precisely what let the test progress far enough to expose it. Classified as a
   test-fixture/environment-robustness issue (not an implementation defect) per the
   established audit permission boundary, and fixed directly: the fix normalizes `\r\n` to
   `\n` on the locally-read bytes before hashing. Documented as a standing rule in
   `combat_state_contract.v0.7.md` §11 (Artifact Hash Resolution Rules).
3. **Re-ran the suite after the fix**: 19 passed, 0 failed.
4. **Full regression, all independently executed** (none of this was possible in Codex's
   sandbox, which cannot launch `python.exe` at all):
   - `python -m pytest Combat/tests/ -q`: 79 passed, 1 failed (the known, pre-existing
     WRIGGLER quarantine reason-string mismatch, unrelated to Phase 3C.2).
   - `verify_live_combat_session_6546_21.py`: PASS, 49 decisions, victory, 0
     `QuiescentBoundaryViolation`.
   - `verify_snapshot_phase2b.py`: PASS, 0 failing checks.
   - `test_choice_semantics.py`: 20 passed, 0 failed.
   - `test_scenario_v2.py`: 31 passed, 1 failed (same known WRIGGLER failure as above).
   - `qb_repro_driver_rl.py --order forward --iterations 15`: 780 total test executions, 0
     `QuiescentBoundaryViolation`, 15 known WRIGGLER, 0 unexpected failures.
5. **Confirmed Codex's fixture-construction decision** (real `BOUND_PHYLACTERY` summon
   rather than a synthetic fallback) worked correctly on the first independent run - no
   fixture rework was needed.
6. **Authored `combat_state_contract.v0.7.md`** (Codex was explicitly instructed not to
   write this - the supervisor writes it after independent verification, per Phase 3C.1
   precedent) and
   `rl_phase3c2_pet_restore_integration_manifest_20260730.json`, and this section.
7. **Discarded two non-deterministic diagnostic-output diffs**
   (`snapshot_phase2b_five_case_results.json`/`snapshot_phase2b_sample.json`, regenerated
   with fresh `SnapshotId`/`CapturedAtUtc`/`CombatSessionId` values by
   `verify_snapshot_phase2b.py` on every run) rather than committing them - they carry no
   meaningful content change.
8. **Commit split**: performed by the supervisor after independent verification passed -
   see `rl_restore_snapshot_phase3c2_audit_20260730.md` for the exact commit hashes.

Working tree is clean as of the supervisor's commits.

