# Canonical CombatStateSnapshot Contract v0.4

**Status: Phase 2B confirmed (2026-07-26).** Snapshot **schema**, **completeness
semantics**, **Power `_internalData` classification**, and **stable-ID Restore
remapping design** are now fixed. `RestoreSnapshot` itself remains
**unimplemented** - Phase 3 scope, gated on the open items in §9-D and §13.

This document supersedes `combat_state_contract.v0.3.md` (Phase 1: live
execution + Quiescent Decision Boundary + DecisionFrame) as the canonical
record. v0.3 remains under `C:\STS2_RL\Common\contracts\` as a historical
record of Phase 1's confirmed results (§8 of that file) - those results are
carried forward unchanged here (§7) and are not re-litigated.

Authored jointly from the RL担当 and Emulator担当 Phase 2A/2B completion
reports:

* `C:\STS2_Emulator\docs\reports\combat_state_snapshot_phase2a_emulator_report_20260726.md`
* `C:\STS2_Emulator\docs\reports\combat_state_snapshot_phase2b_emulator_report_20260726.md`
* `C:\STS2_RL\Outputs\reports\rl_combat_state_snapshot_phase2a_report_20260726.md`
* `C:\STS2_RL\Outputs\reports\rl_combat_state_snapshot_phase2b_report_20260726.md`

---

## 1. Purpose and scope

Unchanged from v0.3 §1: a single canonical state representation - **Canonical
CombatStateSnapshot** - governs the RL↔Emulator combat boundary, distinct from
the lighter-weight `CombatScenario` new-combat-start input format.

```text
C# live combat state
  ↕ Capture (implemented, Phase 2A/2B)  /  Restore (Phase 3 - not yet implemented)
C# CombatStateSnapshot DTO              (implemented: 16 DTOs, formal JSON Schema)
  ↕ Serialize / Deserialize             (implemented, lossless round-trip)
Python CombatStateSnapshot              (implemented: 1:1 dataclass mirror)
```

## 2. API roles

`StartCombat`/`ResetFromScenario`/live execution/Quiescent Decision
Boundary/`DecisionFrame`: unchanged from v0.3 §2-§5, carried forward verbatim
(§7 below).

```text
CaptureSnapshot() / CaptureSnapshotJson()
```

* **Implemented** (Emulator commits `5766528` Phase 2A, `6aa903e` Phase 2B).
  Read-only with respect to live state - reuses
  `AssertQuiescentDecisionBoundary`, so an attempt to capture mid-cascade
  throws `QuiescentBoundaryViolationException` rather than returning an
  inconsistent snapshot. Combat-only.
* RL wraps this as `LiveCombatSession.capture_snapshot()` - callable only at a
  decision boundary, never invoked automatically by the normal decision loop,
  never passed to `BattleEmulator.apply_action()`.

```text
ValidateSnapshotReferences(CombatStateSnapshot) -> string[]
```

* **Implemented** (Emulator commit `6aa903e`). Diagnostic referential-integrity
  check (§10). Does not imply Restore-readiness by itself.

```text
RestoreSnapshot(CombatStateSnapshot)
```

* **Phase 3, not implemented.** §9-C/§9-D record the concrete remapping design
  and open dependencies a Phase 3 implementation must resolve.

## 3-6. Live execution / Quiescent Decision Boundary / DecisionFrame / Phase scope

Unchanged from v0.3 §3-§6. Carried forward verbatim - see v0.3 for full text.
Phase 2A/2B did not touch the live execution path, `LiveCombatSession`, or any
Policy/Choice Policy/Heuristic code (confirmed by both sides' Phase
2A/2B reports' 遵守事項 sections).

## 7. Phase 1 confirmed results (carried forward, unchanged)

See v0.3 §8 for the full table (commits, DLL hashes, Scenario `6546-21`
49-decision live replay, regression counts, `ResetFromScenario`-once
measurement, `DecisionFrame` rejection test). Not re-verified in Phase 2A/2B
(out of scope - Phase 2A/2B touched only Capture-side code, confirmed via both
sides' unchanged-regression-count results in §8 below).

## 8. Phase 2A/2B confirmed results

| Item | Emulator | RL |
|---|---|---|
| Phase 2A commit | `5766528a3311c7fd3e65918662d38bd8888f7707` (code) / `eeef0a3` (report) | referenced the same |
| Phase 2B commit | `6aa903ec8f656055e8c698e933412bff38115eba` (code) / `326919e` (report) | referenced the same |
| Phase 2B DLL SHA256 (`Sts2Emulator.Cli/bin/Debug/net8.0/`) | `e40e7e3d812e73ac032f49cbd9095846a62dc82fc74431c37aa042f702e0bb53` | referenced the same value |
| Formal JSON Schema | `docs/schemas/combat_state_snapshot.schema.json`, SHA256 `ab3f1c721fe70ca9a334cd114fb8d3224ed67df532a9a9e64c0e6b4f0bf0f158` | Python↔Schema field-set match: **16/16 DTOs, 0 drift** (see `verify_snapshot_phase2b.py` §A) |
| DTO↔Schema reflection test (Emulator-side) | 80/80 | n/a (independent Python-side check instead, see above) |
| Existing regression | `smoke_choice_context.py` 21/21 (unchanged) | `test_scenario_v2.py` + `test_choice_semantics.py` 52/52 (unchanged) |
| Phase 2A/2B supplementary tests (Emulator-side) | 68/68 + 20/20 | n/a |
| Scenario `6546-21` w/ per-decision Capture | 40 captures, 0 boundary violations | 49-decision replay w/ per-decision Capture, victory, 0 boundary violations (unchanged from Phase 2A) |
| Schema-conformance rejection tests (RL-side) | n/a | unknown field / missing field / type-mismatch / malformed `StableInstanceId`: **all correctly rejected by strict schema validator**; lenient loader's intentionally-different record-not-reject behavior separately confirmed (see §9-B) |
| Reference-integrity cross-check | `GameInstance.ValidateSnapshotReferences` and RL's independent `validate_snapshot_references` agree on issue-presence for Scenario `302-13` (5 dangling references, both sides) | same |
| Canonical serialization stability | n/a | two back-to-back captures of the same quiescent state produce byte-identical `canonical_json()` (SnapshotId/CapturedAtUtc excluded by design) |

## 8-A. Async combat-setup settlement guarantee (fixed, Emulator commit `ae56293`)

**Guarantee (new, added this revision)**: `Reset()`/`ResetFromScenario()` now
only return once the real turn-1 combat setup (`CombatManager.
StartCombatInternal()` - action-queue drain, monster `RollMove`,
`Hook.BeforeCombatStart`, `StartTurn`'s hand draw/hooks) has genuinely
settled - either a `TurnStarted`/`CombatEnded` event has fired, or a
`PendingChoice`/`PendingTargetSelection` has been published (whichever comes
first), with a 15-second timeout raising `TimeoutException` if neither
occurs. Implemented via `GameInstance.EnterRoomAndWaitForCombatSetUpToSettle`
(replaces the two callers' previous direct `EnterRoomWithTimeout` calls;
`EnterRoomWithTimeout` itself is unchanged).

**Why this was needed**: `CombatManager.AfterCombatRoomLoaded()` dispatches
`StartCombatInternal()` as a genuine fire-and-forget background `Task`
(`TaskHelper.RunSafely`, never awaited by any caller in the
`RunManager.EnterRoom` chain) - a structural property of the decompiled
`Imported` game logic that predates this project. Adding the Quiescent
Decision Boundary check in Phase 1 (`ce7ecc2`) made this pre-existing race
newly *observable* (not newly introduced) as an intermittent
`QuiescentBoundaryViolationException` when a later `Step()`/`CaptureSnapshot()`
call raced against the still-running background setup task. Confirmed via a
naturally-occurring exception stack trace rooted in
`ThreadPoolWorkQueue.Dispatch` and controlled diagnostic-delay experiments
(temporary, reverted) that without this fix `ResetFromScenario()` could
return a false `"victory"` or an empty, undrawn hand mid-setup. See the
Emulator investigation report (`quiescent_boundary_nondeterminism_
investigation_20260726.md`, commit `8b91e58`) for the full A/B evidence.

**Confirmed scope of the fix (both sides)**: eliminates the race for
`Reset()`/`ResetFromScenario()`'s own synchronous return; does not touch
`Step()`'s existing continuation-resolution logic (already correct) or any
Policy/Choice Policy/Heuristic code.

**RL-side independent verification (new, this revision)**: when tests are
invoked via the native `main()`-style harness both files already use
(declaration-order `list(globals().items())` collection, matching the
methodology the Emulator's own investigation used), RL's own same-process
repro **confirms 0 `QuiescentBoundaryViolationException` across 100
forward-order + 100 reverse-order iterations (10,400 total test executions,
~1,608s/~1,608s wall time)** - full agreement with the Emulator's own
native-harness result. The only failures in either direction were the
expected WRIGGLER quarantine-reason change (100/100 iterations each
direction, exactly once per iteration - see below), zero other unexpected
failures.

**Separately, RL found that the SAME combined test files, invoked via
`pytest` instead, still intermittently raise `QuiescentBoundaryViolationException`
after this fix** (non-trivial run-level failure rate across a dozen
single-shot `pytest` invocations of the combined suite, both with default and
`--assert=plain` settings - ruling out pytest's assertion-rewriting
specifically as the differentiator). **Root cause of this pytest-specific
discrepancy is not yet identified** - candidates include `pytest`'s
test-collection/import machinery, output-capture plugins, or other per-test
overhead subtly shifting ThreadPool scheduling enough to reopen a narrower
residual race window that the native harness does not expose at a practical
sample size. This does not contradict the Emulator's own native-harness
result but means **the race is not fully closed for every invocation
context** - flagged as a new, unresolved open item (§13) rather than treated
as a clean, complete fix. See
`rl_combat_state_snapshot_phase2b_final_integration_20260727.md` for full
data.

## 9. Snapshot schema (formalized, Phase 2B)

### 9-A. Canonical schema and DTO↔Schema mapping

* **Canonical schema file**: `C:\STS2_Emulator\docs\schemas\combat_state_snapshot.schema.json`
  (JSON Schema Draft 2020-12), SHA256 `ab3f1c721fe70ca9a334cd114fb8d3224ed67df532a9a9e64c0e6b4f0bf0f158`.
* **Naming convention**: PascalCase throughout (matches C# property names
  verbatim - `GameInstance.JsonOptions` sets no naming policy). **One
  deliberate exception**: `PendingChoice` is a verbatim passthrough of the
  pre-existing (Round 1) `GameInstance.BuildPendingChoiceDict()` /
  `Observation.State.pendingChoice` projection and keeps its original
  **camelCase** keys (`choiceType`, `originEntityType`, etc.) - this is a
  formalized, intentional exception, not an inconsistency (see the Phase 2B
  Emulator report §2 for the full reasoning, including the RL-side
  verification-script bug this clarifies was never a real Snapshot defect).
* **16 `$defs` types**, each `additionalProperties: false` with `required`
  matching the full property set: `CombatStateSnapshot`, `SnapshotMetadata`,
  `UnsupportedSnapshotField`, `PlayerSnapshot`, `EnemySnapshot`,
  `CardInstanceSnapshot`, `RelicSnapshot`, `PowerSnapshot`, `PotionSnapshot`,
  `OrbSnapshot`, `SerializableRngSnapshot`, `PlayerRngSnapshot`,
  `MonsterRngSnapshot`, `RngSnapshotSet`, `CombatHistoryEntrySnapshot`,
  `CombatHistorySnapshot`, plus the `PendingChoice`/`StableInstanceId` shared
  defs.
* **RL-side Python mirror**: `C:\STS2_RL\Combat\combat_state_snapshot.py` - one
  dataclass per `$defs` type (including newly-added standalone
  `PlayerRngSnapshot`/`MonsterRngSnapshot` dataclasses, replacing the Phase 2A
  ad-hoc dict representation to reach full 1:1 parity). Verified field-for-field
  identical to the schema (`verify_snapshot_phase2b.py` §A, 16/16 pass, 0 drift).
* **SchemaVersion**: `"phase2a.1"` → `"phase2b.1"` (additive: `PowerSnapshot`
  gained `InternalData`/`InternalDataSerializerVersion`; no field removed, no
  existing field's meaning changed). Both versions remain individually
  recognized (`KNOWN_SCHEMA_VERSIONS` on the RL side); a phase2a.1-captured
  Snapshot does not need re-capture to remain valid, it simply predates the
  Power-internal-data fields.

### 9-B. Lenient parsing vs. strict schema conformance (RL-side, formalized)

Two deliberately different validation modes coexist, both intentional
(confirmed via `verify_snapshot_phase2b.py` §B, all cases pass):

| | `CombatStateSnapshot.from_dict()` (lenient) | `validate_against_formal_schema()` (strict) |
|---|---|---|
| Unknown field | recorded in `unknown_fields`, not rejected | rejected (`additionalProperties: false`) |
| Missing required field | `SnapshotValidationError` | rejected |
| Unrecognized `SchemaVersion` | `SnapshotValidationError` | n/a (schema enum) |
| Type mismatch, coercible (e.g. `"7"` for an int field) | silently coerced via `int()` | rejected (schema requires `type: integer`, not integer-like string) |
| Type mismatch, non-coercible | raises (`ValueError` from `int()`) | rejected |
| Malformed `StableInstanceId` (fails `^[a-z]+-[0-9]{6,}$`) | accepted as opaque string (loader does not format-check) | rejected (schema `pattern`) |

The lenient loader remains the one `LiveCombatSession.capture_snapshot()`
actually uses (unchanged Phase 2A design - never rejects a merely-unknown
field, since a forward-compatible Emulator addition should not break RL
capture). The strict validator is a separate, additive conformance-checking
utility (diagnostics/tests only) that never runs on the normal capture path.

### 9-C. `complete` semantics (reconfirmed, extended)

`Metadata.Completeness` remains the Emulator's own, never-upgraded verdict
(`complete` | `partial_known_gaps` | `unsupported_state` | `capture_failed`) -
RL's `completeness_is_complete()` reads it verbatim, unchanged from Phase 2A.

**New in Phase 2B**: a Snapshot's *Restore-input eligibility* is a
**separate, additional** gate, not a mutation of `Completeness` itself:
`restore_input_eligibility()` (RL, `combat_state_snapshot.py`) requires BOTH
`Completeness == "complete"` AND a clean
`validate_snapshot_references()` result (no dangling reference, no duplicate
stable ID). A Snapshot the Emulator marks `complete` can still be **Restore-
ineligible** if RL's own reference-integrity check finds a dangling reference
- this satisfies this task's requirement ("dangling referenceが1件でも存在
する場合completeness != completeとし、Phase 3 Restore入力として使用可能とは
判定しない") without ever touching the Emulator's own authoritative field.

### 9-D. Power `_internalData` classification (Emulator, 35 classes - confirmed by direct hook inspection, no guessing)

| Category | Count | Meaning |
|---|---|---|
| `serialize_required` | 17 | Actually left different future behavior; no other recovery mechanism. Generic-reflection serializer (`"generic-reflection-v1"`), recorded in `PowerSnapshot.InternalData`/`InternalDataSerializerVersion`. |
| `safe_to_recompute` | 16 | Intentionally uncaptured (not a gap) - value is provably re-derivable post-Restore, **conditional on CombatHistory being restored first** (§9-E). |
| `gameplay_irrelevant` | 0 (class-level) | No class was *entirely* irrelevant; one field (`HellraiserPower.showedCapReachedMessage`) is display-only but its class is `serialize_required` for another field. |
| `unsupported_unknown` | **2** (`GigantificationPower`, `VigorPower`) | `commandToModify: AttackCommand?` - a live, in-progress command-builder reference, not serializable data. Flagged honestly (`HasUnsupportedInternalData=True`, `InternalData=null`, `UnsupportedSnapshotField` entry) rather than guessed as safe. |

Full per-class evidence table: Phase 2B Emulator report §4 (not duplicated
here to avoid drift between two copies of the same evidence - that report is
the source of truth for individual class justifications).

**`unsupported_unknown` handling (RL, confirmed)**: a Snapshot containing a
`GigantificationPower`/`VigorPower` instance with
`HasUnsupportedInternalData=True` is never treated as `complete` by RL (the
Emulator's own `Completeness` verdict already reflects this - RL does not
independently re-check per-Power status, per the never-upgrade principle of
§9-C; RL's `restore_input_eligibility()` gate additionally guards against
using such a Snapshot as Phase 3 Restore input).

### 9-E. CombatHistory ↔ Power restore-order dependency (critical, Phase 3 gate)

The `safe_to_recompute` classification (§9-D) is only correct **if
CombatHistory is restored before Powers are restored** - several
`safe_to_recompute` classes (`FeralPower`/`JugglingPower` explicitly,
`PaleBlueDotPower` derivately) recompute their internal counters from
`CombatHistory` entries at `AfterApplied`. Phase 3's Restore order (§9-G) must
respect this dependency; reversing it would make `safe_to_recompute` silently
wrong (an empty/partial CombatHistory yields a zero or incorrect recount, not
an exception - a **silent correctness bug**, not a crash). This is the single
most consequential ordering constraint carried into Phase 3 design.

### 9-F. Dangling CombatHistory references (root cause corrected and confirmed; one half fixed)

**§9-F as it stood before this revision attributed the Scenario `6546-21`
dangling references to `TOOLBOX`/`FESTIVE_POPPER`. This attribution was
never confirmed against source and has been directly disproven by the
Emulator's own investigation** (`combat_state_snapshot_6546_21_dangling_
reference_investigation_20260726.md`): `FestivePopper.AfterPlayerTurnStart`
only deals unpowered damage (`FestivePopper.cs:19-31`, no Creature/Power
creation whatsoever), and `Toolbox.BeforeHandDraw`'s only card-adding call
(`Toolbox.cs:22-34`) had not even resolved yet at the point of capture in
this reproduction (its choice was still pending). Neither relic could have
produced any of the 5 references. **The correct root causes, confirmed via
direct reflection into `CombatManager.Instance.History.Entries` plus a
temporary (reverted) diagnostic trace, are two entirely different relics
also present in `6546-21`'s relic list**:

* **`creature-000063`/`power-000064`** (`PowerReceivedEntry`, `DIE_FOR_YOU_
  POWER`) - **`BOUND_PHYLACTERY.BeforeCombatStart()`**
  (`BoundPhylactery.cs:22-24`) calls `OstyCmd.Summon`, which creates a real
  companion creature ("Osty") in `Player.PlayerCombatState.Pets`
  (`OstyCmd.cs:35-91`) and applies `DieForYouPower` to it. Directly
  confirmed **still alive** in `Player.PlayerCombatState.Pets` at the moment
  of Capture (`Player.IsOstyAlive == True`, `Pets.Count == 1`) - i.e. this
  was never a "discarded by scenario overwrite" case at all.
  **Classification: `capture_bug`.** `SnapshotBuilder` simply never
  traversed the public `PlayerCombatState.Pets` collection - **fixed this
  session** (Emulator commit `f2343b1`, `CreatureSnapshot`/
  `PlayerSnapshot.Pets`); confirmed via regression that both ids are no
  longer dangling in Scenario `6546-21`.
* **`card-000065`/`card-000066`/`card-000067`** (`CardGeneratedEntry`,
  `SOUL`) - **`FuneraryMask.BeforeHandDraw()`** (`FuneraryMask.cs:23-34`)
  loops 3 times generating a `Soul` card into the Draw pile via
  `CardPileCmd.AddGeneratedCardToCombat`. A temporary diagnostic trace
  confirmed these 3 cards are present in the Draw pile immediately before
  `ResetFromScenario`'s scenario-pile overwrite and are gone from every
  pile (and `Player.Deck`) immediately after.
  **Classification: `source_live_state_inconsistency`** - the identical
  "real hook fires once during `ResetFromScenario`'s natural setup, then
  the scenario's authoritative pile overwrite discards the created
  instance" mechanism as the original `CardDrawnEntry` pattern, just via a
  different relic and a card-pile target rather than the natural turn-1
  draw. **Not fixed** - this requires either a `ResetFromScenario`
  redesign or Restore-side filtering (Phase 3A, see §13/§14).

**Originally documented pattern** (unchanged, still accurate):
`CardDrawnEntry` - the natural turn-1 draw, discarded by the scenario's
specified hand. Classification: `source_live_state_inconsistency`. `Reset()`
(no scenario) continues to have zero such references.

**Generalized finding**: dangling references are not one phenomenon with one
classification. Two independent causes exist and must both be checked for:
(a) `SnapshotBuilder` failing to traverse a real, currently-live collection
(`capture_bug` - a Capture-side defect, fixable without touching
`ResetFromScenario`), and (b) `ResetFromScenario`'s natural-hook-then-
overwrite architecture discarding a real instance before Capture ever runs
(`source_live_state_inconsistency` - not fixable on the Capture side alone).
A relic-list correlation alone (as the pre-revision text relied on) is not
sufficient evidence for either classification - the actual hook body and the
live object graph at capture time must both be checked.

### 9-G. Stable-ID Restore remapping design (Emulator, design only - not implemented)

1. Restore builds a fresh `RestoreIdMap: Dictionary<string, object>` (logical
   id → newly-constructed live instance) during its own execution - the
   opaque-string-id principle (never a memory address/reference hash) is what
   makes this meaningful across separate Capture/Restore processes.
2. Construction order (dependency-driven): Player+Creature → Enemies+Creature
   → cards (all piles) → Relics → Powers (`Owner`/`Applier`/`Target`
   resolved via `RestoreIdMap`; `InternalData` reverse-transform via a
   not-yet-implemented `RestoreInternalDataGeneric`) → Potions/Orbs → RNG
   (`LoadFromSerializable`, existing API) → **CombatHistory last** (§9-E
   dependency - `Powers` must NOT be restored after CombatHistory, this
   order must not be reversed).
3. Open Phase 3 design decisions not made in this document (Emulator report
   §7-D): how to treat known-dangling CombatHistory entries during Restore
   (drop them vs. redesign `ResetFromScenario` itself), whether Relic
   Restore re-triggers the known `AfterObtained()` re-fire problem (Phase 1
   finding), and `RestoreInternalDataGeneric`'s actual implementation.

## 10. Reference-integrity validation (both sides, cross-confirmed)

* **Emulator**: `GameInstance.ValidateSnapshotReferences(CombatStateSnapshot) -> string[]`
  (`SnapshotReferenceValidator.cs`) - walks every `*InstanceId`/`*InstanceIds`
  value (including inside `CombatHistory.Entries[].Fields`' free-form dicts),
  flags dangling references and duplicate id assignment.
* **RL**: `combat_state_snapshot.validate_snapshot_references()` - an
  independent Python-side implementation of the same traversal, usable on
  archived/diagnostic JSON with no live `GameInstance` required. Cross-checked
  against the Emulator's own validator on the same captured state (Scenario
  `302-13`): both report exactly 5 dangling references, 0 duplicates - full
  agreement.
* Neither validator implies Restore-readiness by itself; `restore_input_eligibility()`
  (§9-C) is the combined gate RL uses for that judgment.

## 11. Canonical serialization (fixed, Phase 2B)

`combat_state_snapshot.canonical_json()`: recursively key-sorted,
whitespace-free JSON of a Snapshot's plain-dict form, with
`Metadata.SnapshotId`/`Metadata.CapturedAtUtc` excluded by default (the two
fields expected to legitimately differ between two captures of the *same*
underlying state). Confirmed byte-identical across two back-to-back captures
of one quiescent state (`verify_snapshot_phase2b.py` §C).

## 12. Canonical/reference file registry

* **Canonical contract** (this file):
  `C:\STS2_RL\Common\contracts\combat_state_contract.v0.4.md`
* **Prior canonical contract** (Phase 1, superseded but retained as historical
  record): `C:\STS2_RL\Common\contracts\combat_state_contract.v0.3.md`
* **Formal JSON Schema**:
  `C:\STS2_Emulator\docs\schemas\combat_state_snapshot.schema.json`
* **Emulator-side reference**:
  `C:\STS2_Emulator\docs\contracts\combat_state_contract.reference.json`
* **RL source manifest**:
  `C:\STS2_RL\Common\contracts\rl_phase2b_source_manifest_20260726.json`

Whenever this canonical document is revised, its own `sha256` must be
recomputed and the Emulator-side reference JSON updated to match.

## 13. Known residual constraints

Carried forward from v0.3 §13, plus new Phase 2B items:

* **`legacy_approximate_restore`** (unchanged): Heuristic/beam-search/lookahead
  continue restore-based evaluation; not a complete counterfactual evaluation
  until Phase 3 exists and is validated.
* **`FurCoat.BeforeCombatStart()` `NullReferenceException`** (unchanged,
  pre-existing, non-blocking, unrelated to Phase 1/2A/2B - see v0.3 §13 for
  full detail).
* **`unsupported_unknown` Power classes (new)**: `GigantificationPower`,
  `VigorPower` - `commandToModify: AttackCommand?` cannot be captured; any
  Snapshot containing one is never `complete`. Phase 3 must either confirm the
  "attack resolution never crosses a published Choice/Target boundary"
  premise directly (not yet confirmed against a concrete card, per the
  Emulator report §4-D) or design a dedicated serializer.
* **Dangling CombatHistory references - corrected, split into two confirmed
  causes, one fixed (updated)**: the TOOLBOX/FESTIVE_POPPER attribution in
  the pre-revision text was disproven by direct source inspection (§9-F).
  Confirmed instead: `BOUND_PHYLACTERY` (Osty summon,
  `creature-000063`/`power-000064`) was a **`capture_bug`** -
  `SnapshotBuilder` never traversed `Player.PlayerCombatState.Pets` - and is
  **fixed** (Emulator commit `f2343b1`, `PlayerSnapshot.Pets`/
  `CreatureSnapshot`, SchemaVersion `phase2b.2`). `FUNERARY_MASK` (Soul
  generation, `card-000065/66/67`) is confirmed
  **`source_live_state_inconsistency`** - the same `ResetFromScenario`
  natural-hook-then-overwrite mechanism as the original `CardDrawnEntry`
  case - and remains **unfixed**, assigned to Phase 3A (a `ResetFromScenario`
  redesign or Restore-side filtering for hook-generated instances discarded
  by the scenario pile overwrite - see §14).
* **CombatHistory/Power restore-order dependency (new, design-only so far)**:
  §9-E - a Phase 3 implementation mistake here would be a silent correctness
  bug, not a crash. Flagged prominently for this reason.
* **`RestoreInternalDataGeneric` / Relic `AfterObtained()` re-fire under
  Restore (new, unresolved)**: both noted as Phase 3 open design questions
  (§9-G item 3), no decision made in this document.
* **Async combat-setup settlement race - fixed for the native harness,
  pytest-specific discrepancy open (new, this revision)**: see §8-A for the
  full guarantee and evidence. The underlying `StartCombatInternal()`
  fire-and-forget race is closed for `Reset()`/`ResetFromScenario()`'s own
  synchronous contract. Two residual items remain open: (1) RL found the same
  race still intermittently reproducible via `pytest` invocation even
  post-fix, root cause not identified (§8-A) - resolve or explain before
  treating any `pytest`-based regression count as authoritative for this
  boundary; (2) a pre-existing, unrelated `MonsterMoveStateMachine.RollMove`
  bug for WRIGGLER-only-no-encounter scenarios (§3-C experiment 3 of the
  Emulator investigation report) now surfaces as `quarantined` with reason
  `init_exception:TimeoutException` instead of the previous
  `no_legal_actions` - the scenario is still correctly rejected either way,
  only the reason string and detection latency changed; a single downstream
  test (`test_wriggler_missing_slot_without_encounter_is_detected`) asserts
  the old reason string and needs updating, tracked as a known, accepted
  failure, not a regression.
* **Emulator-side reference file out of sync (new, this revision)**:
  `C:\STS2_Emulator\docs\contracts\combat_state_contract.reference.json`
  still records `contractVersion: "0.3"` even though the canonical contract
  has been at v0.4 since the pet-capture-fix revision - this gap was already
  present before this revision (not newly introduced) and remains
  unaddressed; recorded here explicitly so it is not lost as a "someone else
  will surely update it" assumption.

## 14. Phase 3 implementation order and risk (Emulator, carried forward)

**Phase 3A (gate, before general Phase 3 restore work)**: resolve the
remaining `source_live_state_inconsistency` half of §9-F -
`FuneraryMask.BeforeHandDraw()` (and, by the same generalized mechanism,
any other relic/effect with a real `BeforeCombatStart`/`BeforeHandDraw`/
similar hook) generating real instances that `ResetFromScenario`'s
scenario-pile overwrite then discards, leaving `CombatHistory` referencing
them permanently. The `capture_bug` half (pets) is resolved (§9-F, §13).
Candidate approaches (neither selected yet): redesign `ResetFromScenario`
to suppress hook-generated-but-discarded instances' `CombatHistory` entries,
or have Restore filter/tolerate known-dangling entries of this shape. This
gates §9-E/CombatHistory restore below - a `CombatHistory` that still
contains unfiltered dangling entries cannot be faithfully restored.

| Order | Item | Primary risk |
|---|---|---|
| 1 | Player/Creature/Enemy/**Pet** restore | Reuse feasibility of existing `ResetFromScenario` construction logic; Pets have no `CombatScenario` representation yet (new, since §9-F's fix) |
| 2 | Card/Relic/Potion/Orb restore | Relic `AfterObtained()` re-fire (Phase 1-known issue) |
| 3 | **CombatHistory restore** | Phase 3A above must be resolved first; **must precede Power restore** (§9-E) - highest-priority technical risk |
| 4 | Power restore (incl. `InternalData` reverse transform) | Silently wrong if CombatHistory isn't already restored (§9-E) |
| 5 | RNG restore | Low risk - existing `LoadFromSerializable` API |
| 6 | Quiescent Decision Boundary re-verification post-Restore | Must confirm `AssertQuiescentDecisionBoundary` passes immediately after Restore |
| 7 | Round-trip / same-action-result equality test | Final Phase 3 acceptance gate (contract §7 differences discipline) |

## 15. Provenance / change history

* v0.1/v0.2/v0.3: see `combat_state_contract.v0.3.md` §14 (unchanged,
  carried forward by reference, not duplicated here).
* v0.4 (this document, 2026-07-26): Snapshot schema formalized (16 DTOs,
  strict JSON Schema, PascalCase + one deliberate `PendingChoice`
  camelCase exception); Power `_internalData` 35-class classification fixed
  (17/16/0/2); stable-ID Restore remapping designed (not implemented);
  `complete`/Restore-input-eligibility semantics clarified as two distinct
  gates; dangling-CombatHistory-reference known issue confirmed and (via RL
  cross-check on Scenario `6546-21`) found to be broader than originally
  scoped - flagged as the top open item, not resolved in this document.
* v0.4 revision (Emulator, same day, commits `b9109bf`/`f2343b1`): the
  TOOLBOX/FESTIVE_POPPER causal attribution in the original v0.4 §9-F was
  investigated and disproven by direct source inspection (neither relic
  creates any Creature/Power/Card). Root causes corrected to
  `BOUND_PHYLACTERY` (Osty pet summon - `capture_bug`, **fixed** this
  revision via `PlayerSnapshot.Pets`/`CreatureSnapshot`, SchemaVersion
  `phase2b.2`) and `FUNERARY_MASK` (Soul card generation -
  `source_live_state_inconsistency`, **unfixed**, assigned to a new Phase 3A
  gate in §14). §9-F/§13/§14 updated accordingly.

Any resumption of Phase 3 (`RestoreSnapshot` implementation) requires a new
joint instruction and supervisor confirmation, and should explicitly address
§13's open items (dangling-reference scope, restore-order dependency,
`unsupported_unknown` premise, `RestoreInternalDataGeneric`) before or as part
of that work - not silently during it.
