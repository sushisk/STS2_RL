# Canonical CombatStateSnapshot Contract v0.6

**Status: Phase 3C.1 Python Restore integration draft (2026-07-29).**

This document supersedes `combat_state_contract.v0.5.md` for RL-side Python
Restore API integration. v0.5 remains unchanged as the Phase 3A.4 historical
contract. The underlying Emulator Restore capability still reports C# contract
version `0.5`; v0.6 records the Python wrapper contract layered on top of that
public API.

## 1. Python Restore API Surface

`Combat/live_combat_session.py::LiveCombatSession` exposes:

```text
validate_restore_snapshot(snapshot) -> RestoreValidationResult
restore_snapshot(snapshot) -> BattleState
restore_snapshot_json(json_text: str) -> BattleState
get_restore_capabilities() -> RestoreCapabilities
```

`restore_snapshot()` accepts a raw CLR `CombatStateSnapshot` or the existing
Python `combat_state_snapshot.CombatStateSnapshot` DTO. Python DTO inputs are
serialized with the existing canonical Snapshot JSON helper and converted by the
CLR DTO deserializer; no second Python Snapshot parser or canonicalizer is added.

`restore_snapshot()` and `restore_snapshot_json()` return the same `BattleState`
type as `start_combat()`, `resume_from()`, and `step()`, built through the
existing `BattleEmulator._wrap()` helper.

## 2. Validation And Rejection

`validate_restore_snapshot()` is a side-effect-free dry run. Repeated validation
of the same snapshot must return the same result and must not change the active
session id, `DecisionFrame`, Observation, LegalActions, RNG, transition log, or
fault state.

The Emulator validates before destroying the previous live session. Therefore a
rejected `restore_snapshot()` or `restore_snapshot_json()` call never touches the
current live session. Python raises `SnapshotRestoreRejectedError`, preserving:

```text
rejection_codes
unsupported_field_paths
clr_exception_type
raw_message
```

This is an input rejection, not a session fault. It does not set
`_session_faulted = True`; if the session was healthy before the call, a
subsequent `step()` on the original session must still work.

## 3. Success Semantics

A successful Restore establishes a new live combat session identity:

* new `combatSessionId`
* new `DecisionFrame`
* fresh Observation and LegalActions from the restored state
* `_session_faulted = False`, cleared only after the restore succeeds

Any old action-id cache tied to the previous session/frame is invalid after
Restore. Callers must reselect an action from the freshly returned LegalActions
by logical identity, such as `action_type` plus card id, never by reusing a raw
pre-Restore `action_id` integer.

Restore is not a step. It records no transition and creates no trajectory entry.

## 4. Post-Teardown Failure

If validation has passed, the old session has been torn down, and construction
then fails, Python raises `SnapshotRestoreFailedError` and marks the existing
`LiveCombatSession` faulted using the same `_session_faulted` flag as the Phase
3A.3 action-fault contract.

The error context is read from structured CLR properties:

```text
restore_phase
combat_session_id
schema_version
contract_version
snapshot_id
original_exception_type
original_exception_message
clr_exception_type
raw_message
```

The CLR exception is preserved with Python exception chaining (`raise ... from
clr_exc`). After this failure, `step()`, `get_observation()`,
`get_legal_actions()`, and `capture_snapshot()` must raise the existing
`FaultedCombatSessionError`.

## 5. Fault Recovery

A faulted `LiveCombatSession` may recover only through a subsequent successful:

```text
start_combat()
resume_from()
restore_snapshot()
restore_snapshot_json()
```

Attempted but failed recovery does not clear `_session_faulted`.

## 6. Capabilities

`get_restore_capabilities()` returns the live CLR `RestoreCapabilities` contract
as a Python dataclass. It reports API/milestone versions, C# contract and schema
versions plus SHA256 hashes, supported completeness values, rejection codes,
Power scope, and transaction flags:

```text
transaction_model = validate_before_destroy
rollback_after_teardown = false
issues_new_combat_session = true
preserves_stable_ids = true
```

## 7. Phase 3C.1 Scope

Supported:

```text
clean, complete, normal_player_decision snapshots
no CombatHistory
no Pets
no Pending Choice/Target
no Action continuation
no unsupported broader Power internalData
single enemy
valid stable references
```

Unsupported and rejected in Phase 3C.1:

```text
CombatHistory
Pets
Pending Choice
Pending Target
Action continuation
Orbs
Relic SavedProperties
Power AssociatedCard
Power amount 0
broader Power internalData
duplicate or dangling stable IDs
unknown schema versions
```

The corresponding capability flags remain `False`. These items are deferred to
Phase 3C.2 and later; this Python integration does not broaden Emulator Restore
semantics.

## 8. Restore To Step Determinism

For a supported snapshot, two independent restores of the same snapshot followed
by the same logical action, reselected from each fresh LegalActions list, must
produce equivalent resulting state. Stale integer action ids from before either
Restore are outside the contract and must not be reused.

## 9. Known Limitations (found during RL-side independent audit, 2026-07-29)

Both items below are genuine, previously-undocumented gaps discovered while
independently running the Python integration tests (Codex's own sandbox could
not execute Python at all - see the Phase 3C.1 Python integration report). Both
are Emulator-side (C#) in origin, not defects in this Python wrapper, and are
out of this round's authority to fix directly.

### 9-A. `EnemySnapshot.Intent` is not preserved across Restore

`SnapshotRestorer.cs` never sets `Intent` (confirmed by direct source
inspection - zero occurrences of `Intent`/`RollMove` in that file). Intent is
derived state that only `RollMove()` computes, and `RollMove()` is one of the
explicitly-forbidden fresh-start hooks Restore must never call (Phase 3B
design - calling it would be a fresh-start side effect). Consequently, a
Capture -> Restore -> Capture round trip legitimately changes a live enemy's
`Intent` from whatever it was (e.g. `{"intentTypes":["Buff"],"stateId":
"INCANTATION_MOVE"}`) to `{"intentTypes":[],"stateId":"UNSET_MOVE"}` - full
byte-for-byte Snapshot equality across a Restore boundary does NOT hold for
this one field. No prior round-trip verification on either side (Emulator's
own smoke/audit tests, or RL's Phase 3B `verify_restore_bootstrap_phase3b.py`)
ever actually compared `Intent`, since all of them used a narrower field
signature rather than a full canonical-JSON comparison - this was first
caught by this round's `test_canonical_json_round_trip` test. Recorded here as
an accepted, understood scope gap (Restore does not attempt to reconstruct
derived AI-intent state); the corresponding test excludes `Intent` from its
equality assertion with a comment explaining why, rather than silently
weakening the check without explanation.

### 9-B. `get_restore_capabilities()` currently throws under the pythonnet/CoreCLR hosting this project uses

`GameInstance.GetRestoreCapabilities()` calls `FindFileUpwards(...)` to locate
`combat_state_snapshot.schema.json` for its reported hash, falling back to a
hardcoded constant only if the file search returns `null`. `FindFileUpwards`'s
`CandidateRoots()` tries `Environment.CurrentDirectory` first (valid in this
hosting context, but does not lead to the schema file since the Python
process's working directory is under `C:\STS2_RL`, nowhere near
`C:\STS2_Emulator`), then `AppContext.BaseDirectory` as a second candidate.
**`AppContext.BaseDirectory` is confirmed empty (`""`) when CoreCLR is loaded
via `pythonnet.load("coreclr", ...)`** (verified directly:
`str(System.AppContext.BaseDirectory)` returns `''` in this process) -
`new DirectoryInfo("")` then throws `ArgumentException: The path is empty`
instead of returning `null` and letting the intended `?? KnownSnapshotSchemaSha256`
fallback take over. The method never returns; every call from this project's
Python integration currently fails with an unhandled CLR exception, not a
Python-catchable rejection. This affects ONLY `GetRestoreCapabilities()`'s
schema-hash lookup path (the contract-hash lookup and everything else in this
API surface work normally). `get_restore_capabilities()`/
`test_get_restore_capabilities_hashes` are left in this codebase as a failing
canary rather than worked around from the Python side (e.g. by mutating
`Environment.CurrentDirectory` before the call) - a global-process-state
workaround was judged too risky to introduce unilaterally without Emulator
担当's own review, since it could have unaudited side effects on other
relative-path-resolving code elsewhere in the Emulator. **Escalation to
Emulator担当 for a proper fix (e.g. having `FindFileUpwards`/`CandidateRoots`
tolerate a null/empty `AppContext.BaseDirectory` and fall through to the next
candidate instead of throwing) is required before `get_restore_capabilities()`
can be considered functional from Python.**

