# Canonical CombatStateSnapshot Contract v0.3

**Status: Phase 1 confirmed and accepted (2026-07-26).** This is the canonical,
jointly-confirmed contract document for the RL↔Emulator combat-state boundary.
It supersedes the informal v0.1/v0.2 proposal-and-response documents that led
to it (see §14 for provenance); those remain as historical design-process
records under `C:\STS2_Emulator\docs\reports\` and
`C:\STS2_RL\Outputs\reports\` but are no longer the source of truth.

Authored jointly from the RL担当 and Emulator担当 Phase 1 completion reports:

* `C:\STS2_Emulator\docs\reports\combat_state_contract_phase1_emulator_report_20260726.md`
* `C:\STS2_RL\Outputs\reports\rl_combat_state_contract_phase1_report_20260726.md`

---

## 1. Purpose and scope

A single canonical state representation - **Canonical CombatStateSnapshot** -
governs the RL↔Emulator combat boundary. `CombatScenario` remains a separate,
lighter-weight "new combat start" input format; it is not the canonical
snapshot and is not used for mid-combat resume (§3, §6).

```text
C# live combat state
  ↕ Capture / Restore
C# CombatStateSnapshot DTO      (Phase 2/3 - not yet implemented)
  ↕ Serialize / Deserialize
Python CombatStateSnapshot      (Phase 2/3 - not yet implemented)
```

Phase 1 (confirmed complete, this document) covers only the **live execution
path** - eliminating per-decision restore and defining a verifiable Quiescent
Decision Boundary. Full Snapshot capture/restore (RNG, CombatHistory,
Relic/Power internal state, `RestoreSnapshot` without re-running
`StartCombat`'s hooks) is Phase 2/3 and remains unimplemented.

## 2. API roles

```text
StartCombat(CombatScenario)
```

* Starts a new combat. Runs `EnterRoom`, relic `AfterObtained()`, and every
  turn-1 hook (`Hook.AfterEnergyReset`/`Hook.BeforeHandDraw`/
  `Hook.AfterPlayerTurnStart`) for real, exactly like a genuinely fresh
  combat. **Implemented today as `GameInstance.ResetFromScenario`** - its doc
  comment (as of Emulator commit `ce7ecc2`) states explicitly that it is a
  new-combat-start entry point only, not a mid-combat resume primitive.
* Live decision loops call this **exactly once per episode**.

```text
CaptureSnapshot()
RestoreSnapshot(CombatStateSnapshot)
```

* Phase 2/3, **not implemented**. Will save/resume combat at a decision
  boundary without re-running `EnterRoom`/`AfterObtained`/`StartTurn`/initial
  draw. See §9 for the field list already scoped for this.

`ResetFromScenario` must not be used for mid-combat resume - confirmed as an
architectural fact (not merely a style preference): every call runs the real
`StartTurn` sequence, and `RelicCmd.Obtain` genuinely re-fires each relic's
`AfterObtained()` every time, regardless of what HP/board state the scenario
specifies. This was the root cause of the Scenario `6546-21`
`no_legal_actions_while_non_terminal` failure under the pre-Phase-1
architecture (fresh restore every decision).

## 3. Live execution (Phase 1 - implemented)

```text
StartCombat (once)
  → Observation + LegalActions
  → Step
  → Observation + LegalActions
  → Step
  → ...
```

Per-decision `ResetFromScenario` and a separate re-`GetLegalActions()` call
are eliminated on this path. Only `SnapshotBranchEvaluator`-shaped work
(exploration/beam search/lookahead) branches from a full Snapshot - and since
Snapshot capture/restore is Phase 2/3, that branching still uses the legacy
fresh-restore mechanism today (§6, §13).

**Confirmed interaction not anticipated in v0.1/v0.2**: Heuristic candidate
evaluation and Choice Policy fallback continue to restore the *same shared*
`GameInstance` the live session is using (§6). RL's Phase 1 implementation
handles this by checking `(combatSessionId, stepIndex)` before every commit
and resynchronizing (a single additional `ResetFromScenario`) only if
interference is detected - see
`rl_combat_state_contract_phase1_report_20260726.md` §2-C. This mechanism is
part of the confirmed contract going forward, not a temporary workaround.

## 4. Quiescent Decision Boundary

Control may return to Python only when one of the following holds:

* A normal Player decision (card/potion/system action).
* A published Choice/Target decision.
* Terminal.

**Common condition**: C# combat state must not be able to progress any
further on its own without new Python input.

**`CurrentlyRunningAction` rule** (confirmed, resolves the v0.2 self-contradiction):

```text
CurrentlyRunningAction is allowed to be:
  null
  OR the action whose resolution is the reason a PendingChoice/
     PendingTargetSelection is currently published
```

Any other currently-executing action, an autonomously-runnable ready action
in the queue, or an unpublished Continuation means the boundary has not been
reached.

**Why `CurrentlyRunningAction` can be non-null and still be quiescent**: every
`CardSelectCmd`-driven choice (hand/deck/discard/upgrade/transform/enchant/
bundle selection - the sole funnel for every mid-effect card-selection
prompt) calls `context.SignalPlayerChoiceBegun` first, which pauses a
*separate*, freshly-generated `GenericHookGameAction` via
`ActionQueueSet.PauseActionForPlayerChoice`, and only afterward awaits
`Selector.GetSelectedCards` (where `InteractiveCardSelector` suspends and
publishes the `PendingChoice`). The action genuinely still executing
throughout - whatever originally called into `CardSelectCmd` - is left
untouched by `PauseActionForPlayerChoice`, which never references
`ActionExecutor.CurrentlyRunningAction` (confirmed by reading
`ActionQueueSet.cs`/`HookPlayerChoiceContext.cs`). So a non-null
`CurrentlyRunningAction` is expected and correct exactly when
`_pendingChoice`/`_pendingTarget` is currently published.

**Implementation** (Emulator commit `ce7ecc2`,
`GameInstance.AssertQuiescentDecisionBoundary`): throws
`QuiescentBoundaryViolationException` rather than silently returning a
result built from state that could still be mid-cascade. Invoked at the tail
of `Step()` and `ResetFromScenario`, scoped to combat only
(`_combatState != null`) - not yet applied to `Reset()`/`ChooseRoom()`
(out of Phase 1's investigated scope).

## 5. DecisionFrame

```text
DecisionFrame =
  combatSessionId       (Emulator: GameObservation.CombatSessionId,
                          reissued every new combat)
  + stepIndex            (Emulator: GameObservation.StepIndex,
                          incremented every Step())
  + continuationStepIndex (RL-side only: identifies a sub-step within
                          one outer decision's Choice continuation loop -
                          not tracked by the Emulator)
```

`action_id` is valid only within the `DecisionFrame` it was issued against.
RL's `LiveCombatSession` rejects an action submitted against a stale frame
via `DecisionFrameMismatchError` (RL commit referenced in
`rl_combat_state_contract_phase1_report_20260726.md`); the Emulator does not
independently enforce this beyond its existing "actionId must be in the
freshly-rebuilt current legal action set" check inside `Step()` - frame-level
rejection is RL's responsibility per the Phase 1 task split.

## 6. Phase 1 scope

**In scope** (confirmed implemented and tested):

* Normal Policy.
* Choice Policy.
* The live progression of whichever action Heuristic actually selected.

**Out of scope for Phase 1** (confirmed, continues on the legacy path):

* Heuristic candidate evaluation.
* Beam search.
* Lookahead.
* Shadow branching.
* Any full restore from a mid-combat snapshot.

These continue to use `ResetFromScenario`-based restore and are classified
`legacy_approximate_restore` (§13) - not a complete counterfactual evaluation
- until Phase 3 (`RestoreSnapshot`) exists.

## 7. Difference evaluation

**Old fresh-restore path is not the correctness baseline.**

Accepted differences (Phase 1 vs. pre-Phase-1):

* Turn-start/start-of-combat hooks no longer re-fire on every decision.
* Scenario `6546-21` completes normally instead of failing.
* HP/enemy-state/LegalActions that were corrupted by the old path are now
  correct.

Not accepted (must be zero):

* Illegal actions.
* Action-mapping mismatches.
* Choice stalls.
* Missed terminal detection.
* Quiescent Decision Boundary violations.
* Non-determinism within one live-execution path for a fixed seed.
* A mismatch between the Policy's input and the `DecisionFrame` it is
  actually being executed against.

## 8. Phase 1 confirmed results

| Item | Emulator | RL |
|---|---|---|
| Commit | `ce7ecc2cc66332c4c2a2abf2f2cd24040dd3baea` (code) / `a4c3c028b54835b18536fe6ee8c78a6ffccf5301` (report) | see `rl_combat_state_contract_phase1_report_20260726.md` §1 |
| Referenced counterpart commit | n/a | Emulator `a4c3c028b54835b18536fe6ee8c78a6ffccf5301` |
| DLL SHA256 (full, both `Sts2Emulator/bin/Debug/net8.0/` and `Sts2Emulator.Cli/bin/Debug/net8.0/`) | `041a44cc3e250f13fb4dc5eed5edb2ee310fa42108249a0bb16928f62dfc5b00` | referenced the same value |
| Scenario `6546-21` | 40-decision synthetic replay (single `ResetFromScenario` + `Step` loop), 0 boundary violations | 49-decision full replay via `LiveCombatSession`, **victory**, 0 boundary violations, 0 `no_legal_actions` |
| Existing regression | `smoke_choice_context.py` 21/21 | `test_scenario_v2.py` + `test_choice_semantics.py` 52/52 |
| `ResetFromScenario` call count per combat | N/A (Emulator does not itself constrain caller behavior) | Measured across 10 scenarios × 2 arms (20 episodes, 641 live `Step()` calls, shadow evaluation enabled): 1 `resume_from()` call per episode (unconditional, by construction) + `resynchronize()` only when Heuristic/shadow evaluation touched the shared `GameInstance` in between - 8 resynchronizations total (0.4/episode average). Down from ~2 calls per *decision* on the old path - see §8 caveat below |
| Choice Policy fixed-30-scenario completion rate | n/a | 86.7% → 96.7% |
| Per-combat wall time (fixed-30) | n/a | 7.37s → 0.557s (~13.2x) |
| teacher2000 smoke20 old-vs-new diff | n/a | 0 differences across 73 choice decisions |
| Old `DecisionFrame`'s action rejected | n/a (Emulator relies on existing legal-action-set check, not frame-level rejection - see §5) | **Confirmed PASS**: captured a `BattleState`/frame at `stepIndex=0`, advanced the live session one real decision (to `stepIndex=6`), then submitted an action against the stale `stepIndex=0` frame directly to `LiveCombatSession.step()` - `DecisionFrameMismatchError` was raised as expected, quoting both the stale and current frame |

**Caveat on "ResetFromScenario once per combat"**: not strictly met.
`preflight_validate()`'s existing architecture calls `initialize()` once for
spec validation, and `LiveCombatSession.resume_from()` calls it again to
establish the live session - two calls per episode rather than one. This is
accepted as satisfying the contract's actual intent (eliminating
**per-decision** restore, which is what caused the Scenario `6546-21`
failure) rather than the literal "exactly one call" wording. Reducing this
further (skip `resume_from` when the live state already matches
`preflight_validate`'s result) is left as a future optimization, not a Phase
1 blocker.

## 9. Snapshot required fields (Phase 2/3 - scoped, not implemented)

Recorded here so Phase 2 design starts from an agreed list rather than
re-deriving it:

* HP/Block/Energy/Stars.
* All card piles, order, per-card instance state (upgrade/tinker).
* PlayPile.
* TurnNumber/RoundNumber/CurrentSide/Phase.
* Enemy state, intent, internal counters.
* Relic ID, base state, individual `SavedProperty` fields.
* Power ID, Amount, turn-start value, duration control, individual state
  (including `_internalData` where a per-class serializer exists).
* Potions/Orbs.
* Full RNG internal state and cursor (`RunRngSet` x12 purposes,
  `PlayerRngSet` x3 purposes per player, per-`MonsterModel.Rng`) - see
  `combat_state_snapshot_contract_proposal_20260726.md` §1-E for the
  Emulator-side inventory of where these live and how they are already
  gettable via `Rng.ToSerializable()`.
* CombatHistory (capture confirmed straightforward via the already-public
  `CombatManager.History.Entries`; restore requires a new identifier-based
  re-linking layer since entries hold live object references - see
  `combat_state_snapshot_contract_v0.1_emulator_response_20260726.md` §2).
* Pending Choice.
* Terminal/victory/loss progression state.

`action_id` remains valid only within one `DecisionFrame`, not across an
entire Snapshot.

## 10. Unsupported state

`null` is used only when a value genuinely does not exist. Unimplemented,
failed-to-capture, or unrestorable state must never be represented as `null`.

Snapshot capture must reject (via a `SnapshotUnsupportedStateException`-class
exception, not yet implemented) rather than silently produce a partial
Snapshot when required information cannot be captured. A partial Snapshot
must never be restored as if it were complete.

Relic/Power serializer coverage is tracked per class:

```text
supported | unsupported | not_applicable
```

`PowerModel._internalData`-using subclasses must be inventoried individually
(Phase 2/3); a class without a registered serializer means full-Snapshot
capture of that combat must be rejected, not silently approximated.

## 11. Schema management

Every Snapshot must carry:

```text
schemaVersion
emulatorCommit
snapshotId
captureBoundary
```

RL and Emulator both record the same schema hash. `pendingChoice`-related
fields not yet in the published schema are to be formalized as part of Phase
2's DTO work (Phase 1 already exposes `combatSessionId`/`stepIndex` via
`GameObservation`/`Metadata`/`StepResult.Info` - see §8).

The canonical-contract cross-reference files (`compatibleRlCommit`/
`compatibleEmulatorCommit`/`emulatorDllSha256`) are defined in §12.

## 12. Canonical/reference file registry

* **Canonical contract** (this file):
  `C:\STS2_RL\Common\contracts\combat_state_contract.v0.3.md`
* **Emulator-side reference**:
  `C:\STS2_Emulator\docs\contracts\combat_state_contract.reference.json`
  (fields: `contractVersion`, `canonicalPath`, `sha256` of this file,
  `compatibleRlCommit`, `compatibleEmulatorCommit`, `emulatorDllSha256`)

Whenever this canonical document is revised, its own `sha256` must be
recomputed and the Emulator-side reference JSON updated to match - a stale
`sha256` in the reference file is a contract-drift signal, not a cosmetic
detail.

## 13. Known residual constraints

* **`legacy_approximate_restore`**: Heuristic candidate evaluation, beam
  search, and lookahead continue to use `ResetFromScenario`-based restore
  through Phase 1 and Phase 2. Their evaluation results must be labeled
  `legacy_approximate_restore` in logs/reports and must not be described as a
  complete counterfactual evaluation until Phase 3 (`RestoreSnapshot`)
  exists and is validated.
* **`FurCoat.BeforeCombatStart()` `NullReferenceException`**: observed by RL
  during Phase 1's fixed-30-scenario run (console-logged via
  `TaskHelper.LogTaskExceptions`/`RunSafely`, never propagated to Python -
  `illegal_action_count`/`exception_count` stayed 0). Confirmed unrelated to
  Phase 1's changes (Quiescent Decision Boundary/`combatSessionId`) - the
  same `ResetFromScenario` → `StartCombatInternal` → `Hook.BeforeCombatStart`
  path existed identically before Phase 1 and under the old per-decision
  restore path, so this is a pre-existing engine-side bug that Phase 1
  merely made newly *observable* (first time this console path was
  inspected), not newly introduced. Recorded as a known issue; does not
  block Phase 1 acceptance. Not fixed as part of Phase 1 or this contract
  fixation - left for a future, separately-authorized investigation.

## 14. Provenance / change history

* v0.1: initial joint proposal (Canonical CombatStateSnapshot concept,
  `StartCombat`/`CaptureSnapshot`/`RestoreSnapshot` roles, Decision boundary
  draft, Snapshot required-field draft). RL and Emulator each responded with
  approve/revise/infeasible per item.
* v0.2: Decision boundary refined to "Quiescent Decision Boundary" language;
  Emulator response identified and specified the fix for the
  `CurrentlyRunningAction`/`CardSelectCmd` self-contradiction (§4).
* v0.3: Phase 1 scope frozen (LiveCombatSession/Quiescent Decision Boundary/
  DecisionFrame/`combatSessionId` only; Heuristic/beam-search/lookahead
  explicitly deferred); Phase 1 implemented and tested by both sides;
  results confirmed and this document fixed as the canonical record (this
  file, 2026-07-26).

Phase 2 (Snapshot Capture: RNG, CombatHistory, Relic/Power serializers,
stable instance IDs) and Phase 3 (`RestoreSnapshot` without the
`StartCombat`/`StartTurn` hook path) are scoped in §9 and §2 respectively but
not started. Any resumption of Phase 2/3 work requires a new joint
instruction and supervisor confirmation before implementation begins.
