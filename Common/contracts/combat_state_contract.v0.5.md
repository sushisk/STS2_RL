# Canonical CombatStateSnapshot Contract v0.5

**Status: Phase 3A.4 confirmed (2026-07-27).** Action outcome semantics -
normal completion, Pending Choice/Target, **Action fault** (Phase 3A.3), and
now **Console I/O isolation** (Phase 3A.4, §13) - are formally fixed on both
sides. `RestoreSnapshot`/Phase 3B remain **unimplemented**.

This document supersedes `combat_state_contract.v0.4.md` (Phase 2B: Snapshot
schema, Power classification, pet-capture fix, async combat-setup settlement)
as the canonical record. v0.4 remains under `C:\STS2_RL\Common\contracts\`
as a historical record - its confirmed results (§7/§8 of that file) are
carried forward unchanged here and are not re-litigated.

Authored jointly from:

* `C:\STS2_Emulator\docs\reports\step_action_exception_propagation_fix_20260727.md`
  (commits `1e637c8`, `5d222ef`, `5b5c98c`)
* `C:\STS2_Emulator\docs\reports\console_io_isolation_phase3a4_20260727.md`
  (commits `e9fb60b`, `50cf52a`, `39e0f00`)
* `C:\STS2_RL\Outputs\reports\rl_action_fault_contract_phase3a3_integration_20260727.md`
* `C:\STS2_RL\Outputs\reports\rl_console_io_isolation_phase3a4_integration_20260727.md`

---

## 1. Purpose and scope

Unchanged from v0.4 §1: Canonical CombatStateSnapshot governs Capture
(implemented) and Restore (Phase 3B, not implemented). This revision adds a
third pillar alongside Capture/Restore: **Action outcome classification** -
every `Step()`-dispatched action now resolves into exactly one of three
mutually exclusive outcomes (§4).

## 2-7. Carried forward unchanged

API roles (§2), live execution/Quiescent Decision Boundary/DecisionFrame/
Phase 1 scope (§3-6), Phase 1 confirmed results (§7): see v0.4 for full text.
Not re-verified this round except where §8 below explicitly says so.

## 4. Action outcome classification (new, Phase 3A.3)

Every action a `Step()` call dispatches resolves into exactly one of:

```text
normal completion       - StepResult built from CombatManager's own settled state
pending choice / target - a legitimate mid-action pause, StepResult built and returned
                           (PendingChoice/PendingTargetSelection published)
action fault             - the action's own execution genuinely threw or was
                           canceled; NO StepResult is ever built
```

**Normal completion and Pending Choice/Target**: unchanged from Phase 1 -
`GameAction.Execute()`'s `finally` block sets `State = Finished` and
`_completionSource.TrySetResult()` on `RanToCompletion`; a not-yet-completed
`_executionTask` (choice/target mid-effect) leaves the existing pause path
entirely untouched (Emulator commit `1e637c8`, `GameAction.cs` diff - see
`step_action_exception_propagation_fix_20260727.md` §2-3).

**Action fault (new)**: `_executionTask.IsFaulted`/`IsCanceled` are now
distinguished from success (previously collapsed into an unconditional
`TrySetResult()`, silently reporting a faulted/canceled action as
successful - the root cause Phase 3A.1-3A.3 traced and fixed). Faulted:
`TrySetException` with the fault's own `InnerExceptions` (original exception
preserved verbatim, never rewrapped). Canceled: `TrySetCanceled()`.

## 5. Action fault propagation and faulted-session rejection (new, Phase 3A.3)

```text
Sts2Emulator.Api.ActionFaultedException      (C#, Emulator commit 5d222ef)
Sts2Emulator.Api.FaultedCombatSessionException (C#, Emulator commit 5d222ef)
      |
      v  (Combat/emulator_bridge.py registers both in _types)
Combat/live_combat_session.py::ActionExecutionError    (Python)
Combat/live_combat_session.py::FaultedCombatSessionError (Python)
```

### 5-A. C# side (Emulator, `GameInstance.cs`)

* `ThrowIfSettledTaskFaulted` - invoked in `WaitUntilChoiceOrSettled()`
  immediately before `FinishSettling()` (both the early-return and
  after-wait paths). If the settled task is faulted/canceled: sets
  `_sessionFaulted = true` first, waits (1ms-poll, 100ms cap, reusing the
  existing `ActionExecutorCleanupGraceMs` constant - no new fixed sleep) for
  `ActionExecutor.CurrentlyRunningAction` to clear, then throws
  `ActionFaultedException` (fault) with the original exception as
  `InnerException`, or a fresh `OperationCanceledException` wrapper
  (cancel - no original exception object exists for `TrySetCanceled()`).
  `FinishSettling()` - the only path that builds `StepResult`/
  `Observation`/`LegalActions` - is never reached.
* `EnsureSessionNotFaulted` - invoked at the top of `Step()`/
  `GetObservation()`/`GetLegalActions()`/`CaptureSnapshot()` (after
  `EnsureInitialized()`). Throws `FaultedCombatSessionException` while
  `_sessionFaulted` is true. `Reset()`/`ResetFromScenario()` are NOT gated by
  this - they are the only two operations permitted on a faulted session.
* `_sessionFaulted` clears to `false` **only** at the success-tail of
  `Reset()`/`ResetFromScenario()` (immediately before building the returned
  `Observation`/`LegalActions` - i.e. only once that Reset attempt is known
  to have actually succeeded, not merely attempted).

### 5-B. Python side (RL, `Combat/live_combat_session.py`)

* `ActionFaultContext` (dataclass): `combat_session_id`/`step_index`/
  `action_id`/`action_type`/`card_id`/`target_index`/`target_enemy_index`
  come from **Python's own already-tracked state** (the `DecisionFrame` the
  session held before the failed call, and the `action`/target parameters
  the caller passed to `step()`) - authoritative, not parsed from any
  string. `action_description`/`original_exception_type`/
  `original_exception_message` come from the CLR exception object
  structurally (`InnerException.GetType().FullName`/`.Message`) when
  available, falling back to a documented best-effort regex over
  `ActionFaultedException.Message` only when the object isn't available
  (neither C# exception class exposes these as typed properties - confirmed
  by reading both files in full). `raw_message` always preserves the
  verbatim C# message. Per this task's explicit requirement, no code path
  relies on the raw exception string alone.
* `ActionExecutionError(RuntimeError)` - `.context: ActionFaultContext`,
  `.__cause__` preserves the original CLR exception chain.
* `FaultedCombatSessionError(RuntimeError)` - `.combat_session_id`. Raised
  proactively by `LiveCombatSession`'s own `_session_faulted` bookkeeping
  (kept in sync with the C# flag - cleared only on a successful
  `start_combat()`/`resume_from()`, mirroring §5-A's "成功のみ解除" rule
  exactly, including for the `_resynchronize()` fallback path, which also
  calls `ResetFromScenario`) BEFORE the CLR call is even attempted, and
  defensively also catches a CLR `FaultedCombatSessionException` reaching
  Python unexpectedly.
* `LiveCombatSession.step()`/new `get_observation()`/new `get_legal_actions()`/
  `capture_snapshot()` all call `_ensure_session_not_faulted()` first.
  `step()` additionally wraps both `step_live_action()` call sites (the
  initial action and every ActionContinuation micro-step) in a
  fault-translating `except` clause.
* **Deliberately NOT touched**: `Combat/battle_emulator.py`'s
  `step_live_action()`/`apply_action()` themselves (shared by
  `HeuristicAgent`/beam-search/lookahead's restore-based candidate
  evaluation - explicitly out of this task's scope). A fault occurring on
  that legacy path still surfaces as a raw, untranslated CLR
  `ActionFaultedException` - this is intentional, not an oversight (see §8
  for a naturally-observed instance of exactly this).

### 5-C. Guarantees (both sides, verified - see integration report §2-3)

* No `StepResult`/trajectory/Observation/LegalActions is ever built or
  returned from a faulted attempt (C# `FinishSettling()` unreached; Python
  `step()` raises instead of returning a `BattleState`).
* No automatic retry of the same action.
* No automatic `ResetFromScenario` to silently paper over a fault.
* No continued `Step()` on the same session after a fault.
* Fault clears only via an explicitly-called, successfully-completed
  `start_combat()`/`resume_from()` (Python) / `Reset()`/`ResetFromScenario()`
  (C#) - never implicitly.
* Distinct from `QuiescentBoundaryViolation`: structurally different
  situations ("engine settled into an inconsistent-looking state with no
  explanation" vs. "a specific action's own execution genuinely threw") and
  structurally different Python exception hierarchies (neither is a
  subclass of the other - verified, integration report §2).

## 6. Episode handling for a faulted action (new, RL policy)

An episode in which `ActionExecutionError` occurs is classified:

```text
engine_action_fault
```

Distinct from ordinary defeat, `no_legal_actions`, timeout, and quarantine.
**Current policy: discard the entire episode - do not include it in
training data.** `Training`/reward function/Policy/Value/Heuristic code is
unaffected (none of it was changed this round); this is a data-pipeline
classification rule for whichever harness generates trajectories, to be
wired into that harness in a future round if/when trajectory generation at
scale resumes - not implemented as running code in this round (no trajectory
generation was exercised or changed).

## 8. Phase 3A.3 confirmed results (new)

| Item | Emulator | RL |
|---|---|---|
| Commits | `1e637c8` (Imported, `GameAction.cs`)/`5d222ef` (Emulator, `GameInstance.cs`+2 new exception types)/`5b5c98c` (report) | referenced the same |
| DLL SHA256 | `f61a1533e7ba8c746d1257ccf9e68f7ea239394ec2f268f1df4aece514ad1bf0` | referenced the same value |
| Exception-injection tests | 5 injection points × 8 checks = 40/40 (`Console.Out` corrupted to force a real `Log.Info` `ObjectDisposedException`) | Independently reproduced the SAME technique from Python (`Combat/tests/test_action_fault_contract.py`) - 6/6 pass, including full `ActionFaultContext` field verification |
| Scenario `6546-21` | 49 decision, victory, 0 boundary violations, 0 action faults | same, reconfirmed |
| Fault→reject→recover cycle | confirmed C#-side | confirmed Python-side (`start_combat()` after a fault clears `_session_faulted`, normal operation resumes) |
| Native harness forward/reverse | 100/100 (5,200/5,200 tests), 0 QB violations, 100 known WRIGGLER each | 30/30 (1,560/1,560 tests) reconfirmation, 0 QB violations, 30 known WRIGGLER each, 0 other |
| pytest capture-mode matrix (Phase 3A.3, pre-Console-isolation) | 100 default/50 fd/50 sys/50 -s, all reported 0 QB violations | RL's own re-test at that time found a materially different result (natural `ActionFaultedException` occurrences) - see §9/§13, now resolved by Phase 3A.4 |
| pytest capture-mode matrix (Phase 3A.4, post-Console-isolation) | 100 default/100 fd/50 sys/50 -s, 0 QB/0 IOException-family ActionFaultedException/0 other in every mode | Independently re-derived (fresh subprocess per run, not a single looped process): same 100/100/50/50, **0/300 QB, 0/300 IOException-family ActionExecutionError, 0/300 other** - full agreement |

## 9. Known residual constraints (carried forward + new)

Carried forward from v0.4 §13 unchanged: `legacy_approximate_restore`,
`FurCoat` NRE, `unsupported_unknown` Power classes, dangling-CombatHistory
two-cause split (Osty fixed, `FUNERARY_MASK` SOUL×3 open, Phase 3A gate),
CombatHistory/Power restore-order dependency, `RestoreInternalDataGeneric`,
Emulator reference-file `contractVersion` sync gap.

**RESOLVED this revision (Phase 3A.4, §13) - pytest/Console-capture race**:
RL's prior investigation (`rl_pytest_quiescent_nondeterminism_investigation_
20260727.md`) had identified pytest's fd-based output capture as *correlated*
with intermittent `QuiescentBoundaryViolationException`; Phase 3A.3's
fault-propagation fix then revealed the actual mechanism (a genuine
`Console.WriteLine` I/O exception racing against pytest's fd-capture,
re-surfacing honestly as `ActionFaultedException` instead of a misleading
boundary violation - RL's Phase 3A.3 integration report). **Phase 3A.4
(`SafeConsoleTextWriter`/`SafeConsoleOutput`, §13) now isolates this at the
source**: RL's own independently-run matrix (default×100/`--capture=fd`×100/
`--capture=sys`×50/`-s`×50, 300 total fresh `pytest` subprocess invocations)
confirms **0/300 `QuiescentBoundaryViolation`, 0/300 `ActionExecutionError`
rooted in `IOException`/`ObjectDisposedException`/`UnauthorizedAccessException`,
0/300 other unexpected failures** - full agreement with the Emulator's own
100/100/50/50 result. `pytest`
is still not recommended as the sole authoritative regression signal (the
native harness remains canonical, per longstanding project practice), but
the specific Console-I/O-race class of false positive this section
originally flagged is now closed, not merely explained.

## 10. Imported patch manifest reference

Carried forward and extended - `step_action_exception_propagation_fix_
20260727.md` §8 is now the authoritative, consolidated list of every
`EMULATOR_PATCH`-tagged change under `Sts2Emulator/Imported/Source/`
requiring re-application after any future re-decompile:

1. `MegaCrit.Sts2.Core.GameActions/ActionExecutor.cs` (commit `74a6f54`) -
   `try/finally` around the `NonInteractiveMode` execute-and-finish
   sequence, guaranteeing `CurrentlyRunningAction` clears even on fault.
2. `MegaCrit.Sts2.Core.GameActions/GameAction.cs` (commit `1e637c8`) - the
   3-way `RanToCompletion`/`Faulted`/`Canceled` `CompletionTask` split (§4).

Both are required together for the Action fault contract (§5) to function -
neither alone is sufficient (see the Emulator report §8 for the precise
dependency).

## 13. Console I/O isolation (new, Phase 3A.4)

**Guarantee**: a Console output failure must never decide whether a
`GameAction` succeeds or faults - only genuine game-logic exceptions may.

```text
Sts2Emulator.Api.Internal.SafeConsoleTextWriter   (C#, Emulator commit e9fb60b/50cf52a)
Sts2Emulator.Api.Internal.SafeConsoleOutput
```

### 13-A. Mechanism (Emulator, `GameInstance.cs` + 2 new internal files)

* `SafeConsoleTextWriter` wraps a `TextWriter` (`_inner`), delegating every
  write unconditionally, catching **only**
  `IOException`/`ObjectDisposedException`/`UnauthorizedAccessException` from
  `_inner`'s own `Write`/`WriteLine`/`Flush` calls (`when` clause, exact
  3-type match - no other exception type is caught).
* `SafeConsoleOutput.Install()` wraps `Console.Out`/`Console.Error` exactly
  once (double-checked lock), called from `GameInstance.EnsureTestMode()`
  before any Imported code runs. **No `Sts2Emulator/Imported` file was
  touched** - individual `Log.Info`/`GD.Print` call sites are unmodified;
  protection is transparent, at the `Console.Out`/`Console.Error` level.
* On a caught failure: write is silently discarded, `FailureCount`
  (`Interlocked`) increments, the first failure's `{stream}: {type}:
  {message}` is recorded (`Interlocked.CompareExchange`, exactly once) -
  exposed via `GameInstance.ConsoleOutFailureCount`/`ConsoleErrorFailureCount`/
  `FirstConsoleOutFailure`/`FirstConsoleErrorFailure`. `RecordFailure` itself
  never writes to Console (no recursion). No retry, no fixed sleep.
* Any OTHER exception type from `_inner` (e.g. `InvalidOperationException`,
  `NotSupportedException`) is NOT caught - propagates normally into the
  existing Phase 3A.3 `ActionFaultedException` contract (§5) unchanged.

### 13-B. Why `UnauthorizedAccessException` is included (empirical, not
guessed)

Initial scope was `IOException`/`ObjectDisposedException` (the
originally-observed patterns). The Emulator's own required regression
(pytest default-capture ×100) with only those 2 types still found 8/100 runs
naturally raising `ActionFaultedException`, all rooted in
`System.UnauthorizedAccessException: Access to the path is denied.` from
`System.ConsolePal.WindowsConsoleStream.Write` - the *actual* dominant
real-world form of the pytest-fd-capture race on Windows, not a secondary
one. Added in `50cf52a` after this measurement; re-run confirmed 0/100 (both
default and `--capture=fd`) after the addition. RL's own independent
re-verification (§13-C) reproduces this exact finding.

### 13-C. RL-side independent verification

* **Reflection-based tamper tests** (`Combat/tests/test_action_fault_
  contract.py`, mirroring the Emulator's own Layer B methodology): swaps
  only the already-installed `SafeConsoleTextWriter`'s private `_inner`
  field (via reflection - `Console.Out` → `SyncTextWriter._out` →
  `SafeConsoleTextWriter` → `_inner`), confirming `ObjectDisposedException`
  and a genuine native `IOException` (broken `AnonymousPipeServerStream`,
  avoiding the pythonnet `TargetInvocationException`-wrapping pitfall the
  Emulator report also flagged) do NOT fault the action, while an unrelated
  type (`NotSupportedException`, from a fixed-capacity `MemoryStream`
  overflow) still DOES fault normally - the safety net does not overreach.
  `UnauthorizedAccessException` specifically was not independently
  synthesized (no portable, native way to construct one without a
  Python-subclassed `TextWriter`, which reintroduces the same
  `TargetInvocationException`-wrapping artifact) - relied on the pytest
  capture-mode matrix (§13-D) as empirical confirmation instead, consistent
  with how the Emulator side itself originally discovered this type.
* **pytest capture-mode matrix** (`Combat/evaluation/online_eval/
  pytest_capture_mode_repro.py`, new - spawns N genuinely independent fresh
  `pytest` subprocesses per mode, not a single looped process): **default
  ×100, `--capture=fd` ×100, `--capture=sys` ×50, `-s` ×50 (300 total) - 0
  `QuiescentBoundaryViolation`, 0 `ActionExecutionError` rooted in any of
  the 3 neutralized types, 0 other unexpected failures, in every mode.**
  Full agreement with the Emulator's own matrix (§8). See RL's Phase 3A.4
  integration report for the complete per-mode breakdown.

### 13-D. Guarantees (both sides)

* Console I/O failure never faults a `GameAction` (§13-A/13-C).
* A genuine, unrelated exception from the same call site still faults
  normally - Console isolation does not become a general exception
  blanket (§13-A/13-C control case).
* Healthy Console output is unaffected (transparent pass-through).
* Failure counts/first-failure are diagnostically exposed, never logged
  back through Console (no recursion).
* Thread-safe (`Interlocked` only, plus .NET's own `Synchronized` wrapper
  around whatever `Console.SetOut`/`SetError` receives).
* No fixed sleep, no retry.
* `Sts2Emulator/Imported` untouched.

## 11. Canonical/reference file registry (updated)

* **Canonical contract** (this file):
  `C:\STS2_RL\Common\contracts\combat_state_contract.v0.5.md`
* **Prior canonical contracts** (superseded, retained as historical
  record): `combat_state_contract.v0.4.md`, `combat_state_contract.v0.3.md`
* **RL source manifest**:
  `C:\STS2_RL\Common\contracts\rl_phase3a3_source_manifest_20260727.json`
  (covers both Phase 3A.3 and this revision's Phase 3A.4 addendum - see that
  file's own `phase3a4Addendum` section)

## 12. Provenance / change history

* v0.1-v0.4: see `combat_state_contract.v0.4.md` §15 (unchanged, carried
  forward by reference).
* v0.5 revision 2 (Phase 3A.4, 2026-07-27): Console I/O isolation
  (`SafeConsoleTextWriter`/`SafeConsoleOutput`, §13) fixed and cross-verified
  (independent Python-side reflection-tamper reproduction of all 3
  neutralized exception types except `UnauthorizedAccessException`, which
  relied on the empirical pytest matrix instead - see §13-C for why). §9's
  pytest/Console-capture entry updated from "root cause identified" to
  "resolved".
* v0.5 revision 1 (Phase 3A.3, this document's original 2026-07-27 text):
  Action outcome classification formalized
  (normal/Pending Choice-Target/fault); `ActionFaultedException`/
  `FaultedCombatSessionException` (C#) and `ActionExecutionError`/
  `FaultedCombatSessionError` (Python) contract fixed and cross-verified
  (independent Python-side fault-injection reproduction, 6/6); faulted-episode
  training-data policy (`engine_action_fault`, discard) recorded; Imported
  patch manifest consolidated (§10); the pytest/Console-capture
  nondeterminism previously flagged as unresolved is now root-caused
  precisely (§9) - still open as an environment-level limitation, not a code
  defect, and `pytest` remains non-authoritative for regression purposes.

Any resumption of Phase 3B (`RestoreSnapshot` implementation) or further
Phase 3A work requires a new joint instruction and supervisor confirmation.
