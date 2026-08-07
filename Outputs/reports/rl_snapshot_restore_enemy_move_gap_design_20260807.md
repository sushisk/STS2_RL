# Snapshot Restore does not preserve enemy Move state - correctness gap + fix design

Date: 2026-08-07
Authoring side: RL repo (`C:\STS2_RL`)
Scope: investigation + fix design proposal for `C:\STS2_Emulator` only. **No `C:\STS2_Emulator` source
was edited to produce this report** - per the existing RL-side policy recorded in
`Outputs/reports/unauthorized_emulator_change_runic_capacitor.md` §10 ("RL side must not continue
with: direct `C:\STS2_Emulator` source changes... Emulator design or API changes"), this is
investigation + handoff material only. Implementation is Emulator担当's call.

## 1. Why this is a correctness bug, not a diagnostics nicety

`Common/contracts/combat_state_contract.v0.6.md` §9-A (unchanged through v0.7/v0.8, still current)
already documented that `SnapshotRestorer.cs` never restores `EnemySnapshot.Intent`, and recorded it
as an "accepted, understood scope gap." That framing undersells the actual impact once you consider
what the whole Branch Worker / search system depends on:

- An enemy's Intent/Move is **not** decided at End Turn time - it is rolled once, right after the
  enemy's *previous* move resolves, and held fixed until performed (that's the entire reason Intent
  is shown to the player ahead of time for planning). A captured Stable Snapshot's `Intent.stateId`
  is therefore real, already-determined combat state, not a display-only artifact.
- Every Branch Worker evaluation restores a Snapshot and re-executes candidate actions on it,
  ultimately to answer "what would happen if root's own timeline continued from here." If Restore
  drops the already-decided Move, the only way to give the monster ANY move again is to roll a fresh
  one - which draws NEW randomness the real root timeline never drew at this point, and can produce a
  genuinely different move than the one root would actually use.
- Net effect: a Branch that reaches End Turn is not evaluating "what root would actually do" for the
  enemy's turn - it is evaluating "what some other unrelated roll of the same move table would do."
  That is a live-vs-branch state divergence in exactly the property (`(instance_id, branch_id) states
  must be faithful continuations of the same underlying combat`) the whole search/RL API design
  depends on for its results to be trustworthy.

Today the practical symptom is worse than silent divergence: the underlying C# monster-move state
machine (`MonsterMoveStateMachine`/`MoveState`, `MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine`)
has no valid current Move at all after Restore, so any attempt to actually resolve the enemy's turn
hangs `GameInstance.WaitUntilChoiceOrSettled()` for ~15s and then throws an opaque `TimeoutException`
- the real underlying `InvalidOperationException("No move has been set for the monster")`
(`MoveState.cs`'s `UnsetMove()`) never reaches Python at all. Confirmed empirically against a live
Restore + End Turn before writing this report (restore a mid-combat Snapshot with a living enemy,
call `LiveCombatSession.step()` with the "system"/End Turn action - hangs ~15s, then raises a raw
`System.TimeoutException`, not any of `live_combat_session.py`'s own structured exception types).

An RL-side interim mitigation already ships in this repo (see §5) that fails fast and clearly instead
of hanging - but it can only ever *detect* the gap, not restore correct behavior. Only Emulator-side
work can fix the actual divergence.

## 2. Root cause (confirmed by direct source inspection)

`C:\STS2_Emulator\Sts2Emulator\Api\Internal\RealEngine\SnapshotRestorer.cs`:

- `ApplySnapshotState()` (the top-level per-enemy/player state application entry point) calls
  `ApplyPrimitiveState`, `ApplyPets`, `ApplyOrbs`, `RebuildPiles`, `ApplyRelics`, `ApplyPotions`,
  `ApplyCombatHistory`, `ApplyPowers`, `ApplyTurnRoundAndSide`, `ValidateFinalState` - zero mentions
  of `Intent`, `StateLog`, `RollMove`, `MoveState`, or `MonsterMoveStateMachine` anywhere in this
  file (grep-confirmed).
- `ApplyPowers()` (line ~999) already has the exact per-enemy iteration shape needed:
  `foreach (var enemy in _snapshot.Enemies) { ... }` - a natural sibling location for a new
  `ApplyEnemyMoves()` step, called from `ApplySnapshotState()` right after `ApplyPowers(player,
  combatState)`.

## 3. The fix is a straightforward reuse of an EXISTING, already-battle-tested mechanism

This is the useful part of this investigation: the Emulator does not need new plumbing. The exact
machinery already exists, is already public, and is already used by the OTHER restore path
(`GameInstance.ResetFromScenario`'s `EnemyScenario.ForcedMove`/`StateLog` fields) for precisely this
purpose - `SnapshotRestorer.cs` just never got wired up to call it.

### 3.1 The data is already captured

`C:\STS2_Emulator\Sts2Emulator\Dto\Snapshot\EnemySnapshot.cs` already has both fields Restore would
need:

```csharp
/// <summary>Simplified compared to GameInstance.BuildIntentDict: stateId + intentTypes + best-effort
/// attack damage/repeats. Null if the monster has no current move.</summary>
public Dictionary<string, object?>? Intent { get; set; }

public string[] StateLog { get; set; } = Array.Empty<string>();
```

No schema change is needed - `Intent.stateId` and `StateLog` are already round-tripped through
Capture/serialize/deserialize today; `SnapshotRestorer` simply never reads them back.

### 3.2 A deterministic, RNG-free "force this exact move" API already exists

`MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine`:

```csharp
public void ForceCurrentState(MonsterState state)
{
    SetCurrentState(state);
}
```

...and `MegaCrit.Sts2.Core.Models.MonsterModel`:

```csharp
public void SetMoveImmediate(MoveState state, bool forceTransition = false)
{
    if (NextMove.CanTransitionAway || forceTransition)
    {
        NextMove = state;
        MoveStateMachine.ForceCurrentState(state);
        NCreature creatureNode = Creature.GetCreatureNode();
        if (creatureNode != null && CombatState.IsLiveCombat())
        {
            TaskHelper.RunSafely(creatureNode.RefreshIntents());
        }
    }
}
```

Neither call draws RNG (contrast with `RollMove(targets, owner, rng)`, which does) - `SetMoveImmediate`
directly sets both the state machine's `_currentState` and the cached `NextMove` property to a
specific, already-known `MoveState` object. This is exactly "reproduce the exact previously-decided
Move," not "roll a new one."

### 3.3 The OTHER restore path already does this end to end - `SnapshotRestorer` can reuse it directly

`C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs` (~line 1819-1927), `ApplyScenarioMoveHistory()` +
its helpers (`ResolveMoveState`, `IsGenericStunnedMoveId`, `CreateGenericStunnedMoveState`,
`ShouldAppendForcedMoveToStateLog`) already implement, for the `ResetFromScenario`/`EnemyScenario.
ForcedMove`+`StateLog` path:

- Resolve a move-id string (e.g. `"INCANTATION_MOVE"`) to the matching registered `MoveState` via
  `machine.States.Values.OfType<MoveState>().FirstOrDefault(...)`.
- A documented special case for the generic `STUNNED` move id (which is not itself a registered
  per-monster state and needs synthesis via `CreateGenericStunnedMoveState`, including Tunneler's own
  bite-move follow-up special case).
- A documented Queen-specific special case (`ENRAGE_MOVE` after Amalgam death must not be re-appended
  to `StateLog`).
- `StateLog` replace-vs-append semantics matching whether the caller already supplied one.

This is already a correct, tested, production implementation of "restore a monster's Move state from
a move-id string + optional history," modulo the fact that it currently only accepts its input from
`EnemyScenario` fields, not `EnemySnapshot` fields. `EnemyScenario.ForcedMove`/`StateLog` and
`EnemySnapshot.Intent["stateId"]`/`StateLog` are shaped compatibly enough (a move-id string plus a
`string[]` history) that the cleanest fix is very likely:

1. Extract `ApplyScenarioMoveHistory()`'s body into a shared helper parameterized on
   `(Creature enemyCreature, string? forcedMoveId, IReadOnlyList<string> stateLog)` instead of
   `EnemyScenario` directly (or add a small adapter that reads those two values off either DTO).
2. Call it from `ResetFromScenario`'s existing call site (unchanged behavior) AND from a new
   `SnapshotRestorer.ApplyEnemyMoves(CombatState combatState)` step, invoked from
   `ApplySnapshotState()` right after `ApplyPowers(player, combatState)` (i.e. after HP/Block/Powers
   are already applied, matching the existing method's own documented timing rule: "nobody has called
   `RollMove()` yet, so nothing has genuinely decided a move yet and there is nothing to race").
3. Source `forcedMoveId` from `enemySnapshot.Intent?["stateId"] as string` (null/absent -> skip, same
   as `ApplyScenarioMoveHistory`'s existing `string.IsNullOrWhiteSpace` short-circuit) and `stateLog`
   from `enemySnapshot.StateLog`.

### 3.4 Open questions for Emulator担当 to resolve during implementation

- Whether `EnemySnapshot.Intent` can ever legitimately be `null`/missing `stateId` for a **living**
  enemy at a genuine Stable capture boundary (this report assumes "no, only just-spawned-mid-Transition
  enemies could transiently lack one, never a Stable-boundary capture" - worth confirming against
  the real capture code, `GameInstance.BuildIntentDict`, rather than assuming).
- Whether `Intent`'s `stateId` is guaranteed to already be `NormalizeId`-comparable against
  `MonsterState.StateId`/`ResolveMoveState`'s existing normalization, or needs adjustment.
- Whether restoring `StateLog` this way needs any additional guard for the Queen/Amalgam and
  generic-`STUNNED` special cases specifically in the Restore (not Scenario) context - these were
  clearly hard-won fixes for real observed bugs in the Scenario path; a shared helper should carry
  them over rather than re-deriving them.
- Whether this should land as a `SnapshotRestorer` behavior change under the existing
  `phase3c.4`/`0.5` contract version, or needs a version bump given it changes what a Restore-then-
  End-Turn actually produces (recommend treating it as a bug fix to existing documented behavior,
  not a new capability, since §9-A already described the CORRECT target state - Restore should not
  discard already-decided derived state - it just never got there).
- `SnapshotRestorer.ApplyPrimitiveState()` currently reads `combatState.Enemies.Single()` /
  `_snapshot.Enemies.Single()` (singular) while `ApplyPowers()` correctly iterates
  `foreach (var enemy in _snapshot.Enemies)` - worth Emulator担当 double-checking whether that
  `.Single()` is intentionally single-enemy-only scoped for that one primitive-stat step or a
  pre-existing latent multi-enemy bug; out of scope for this report, noted only because a new
  `ApplyEnemyMoves()` step should use the `foreach` shape, not the `.Single()` shape.

## 4. What this report is NOT proposing

- No `C:\STS2_Emulator` source edit was made or is included here (no diff/patch attached, unlike the
  `unauthorized_emulator_change_runic_capacitor.patch` precedent - that incident is exactly why this
  report stops at investigation + design).
- No DLL rebuild was performed or requested from the RL side.
- No claim that this fix is risk-free - `SetMoveImmediate`/`ForceCurrentState`/`ResolveMoveState` are
  all real, exercised production code paths, but wiring them into a NEW call site (`SnapshotRestorer`)
  still needs Emulator担当's own review/tests (round-trip determinism, the Queen/STUNNED special
  cases, multi-enemy scenarios) before shipping - not something to rubber-stamp from outside that
  team's ownership.

## 5. RL-side interim mitigation already in place (unaffected by whether/when this lands)

`Combat/live_combat_session.py`'s `LiveCombatSession.step()` now refuses to execute End Turn
(`action_type == "system"`) against any state where a living enemy's `Intent.stateId` is still
`"UNSET_MOVE"`, raising `SnapshotRestoreMissingMoveError` (`fault_kind =
"snapshot_restore_missing_monster_move"`) immediately instead of letting the underlying engine hang
~15s and surface an opaque `TimeoutException`. This is wired through `Combat/search/fault_taxonomy.py`
(`FAULT_SNAPSHOT_MOVE_MISSING`) and `Combat/search/branch_worker_pool.py` so Branch Worker/Search
Coordinator callers see a specific, actionable `fault_kind` today.

This mitigation is a **detection-only** safety net, not a fix for the divergence described in §1 - it
prevents wasted `request_timeout_s` and opaque errors, but a Branch that never actually reaches End
Turn after a Restore still cannot evaluate enemy-turn outcomes at all. Once the Emulator-side fix in
§3 lands, `Combat/tests/test_end_turn_after_restore.py`'s two "fails fast" tests are written to be
flipped to assert successful End Turn resolution instead (see that file's own module docstring) - they
stay in the suite as the concrete acceptance check for this fix, not deleted.
