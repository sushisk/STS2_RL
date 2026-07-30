# Canonical CombatStateSnapshot Contract v0.8

**Status: Phase 3C.3-3C.4 (CombatHistory + Power internal-state restore) and
Phase 3C.4.1 (JSON input validation) Python integration, independently
verified (2026-07-31).**

This document supersedes `combat_state_contract.v0.7.md` for RL-side Python
Restore API integration. v0.5 (C# contract) and v0.6/v0.7 (Phase 3C.1/3C.2
Python integration) remain unchanged as historical documents. The underlying
Emulator Restore capability still reports C# contract version `0.5`; v0.8
records three Emulator-side rounds landing at once (CombatHistory full
restore, Power internal-state exact restore, `RestoreSnapshotJson` input
validation hardening) and their Python-side integration.

## 1. Python Restore API Surface

`Combat/live_combat_session.py::LiveCombatSession` exposes, as of this round:

```text
validate_restore_snapshot(snapshot) -> RestoreValidationResult
validate_restore_snapshot_json(json_text: str) -> RestoreValidationResult   # new this round
restore_snapshot(snapshot) -> BattleState
restore_snapshot_json(json_text: str) -> BattleState
get_restore_capabilities() -> RestoreCapabilities
```

`validate_restore_snapshot_json` is the only new Python method this round. It
is a thin, side-effect-free wrapper around the new C# `GameInstance.
ValidateRestoreSnapshotJson(string json)` (added Phase 3C.4.1), mirroring the
existing `validate_restore_snapshot()` exactly: it does not modify
`_session_faulted` or `_current_frame`, and repeated calls with the same input
return equal results.

## 2. JSON Input Validation Responsibility (Phase 3C.4.1)

**JSON Schema validation of a Restore JSON input is the Emulator's
responsibility, not this Python wrapper's.** `RestoreSnapshotJson()` and
`ValidateRestoreSnapshotJson()` internally run, in order: (1) JSON syntax
parse, (2) a Schema-subset structural validation against
`docs/schemas/combat_state_snapshot.schema.json` (required/type/nullability/
enum/minimum/pattern/`additionalProperties`), (3) strict `CombatStateSnapshot`
deserialization, (4) the existing object-DTO preflight
(`ValidateRestorePreflight`/`SnapshotRestorer.CollectEligibilityIssues`/
reference validation). Only after all four succeed does `RestoreSnapshotJson`
proceed to `TeardownActiveRun()`.

**This Python wrapper does not add, and must never add, an independent
production JSON Schema validator.** `validate_restore_snapshot_json()` exists
only for: integration tests, dry-run diagnostics, and inspecting rejection
detail without performing a Restore. For any real Restore call, the JSON
input's correctness is guaranteed (or rejected) by the Emulator's own
`RestoreSnapshotJson()` alone - RL-side pre-validation is optional convenience,
never a substitute, and must never silently pad or infer a missing value.

New JSON-specific rejection codes flow through the existing generic
`rejection_codes: list[str]` field on `RestoreValidationResult`/the existing
`SnapshotRestoreRejectedError.rejection_codes` unchanged - no new Python
exception type or context field was needed:

```text
invalid_json_schema_unavailable
invalid_json_required_field:{path}
invalid_json_type_mismatch:{path}:expected=...:actual=...
invalid_json_enum_mismatch:{path}:value=...
invalid_json_minimum:{path}:minimum=...:actual=...
invalid_json_pattern_mismatch:{path}:value=...
invalid_json_one_of_ambiguous:{path}:matches=...
```

Object-DTO callers (`restore_snapshot(snapshot)`/`validate_restore_snapshot(snapshot)`)
cannot observe a missing JSON key (Python constructs a full object either way)
- Schema `required`-field validation is JSON-input-only. Object DTO callers
remain responsible for passing a complete, correctly-populated
`CombatStateSnapshot`, per the existing structural/eligibility/reference
checks that already applied before this round.

## 3. Validation And Rejection (unchanged from v0.6/v0.7)

`validate_restore_snapshot()`/`validate_restore_snapshot_json()` are
side-effect-free dry runs. A rejected `restore_snapshot()`/
`restore_snapshot_json()` call never touches the current live session and
does not set `_session_faulted = True` - this holds for JSON-structural
rejections exactly as it already held for object-DTO eligibility rejections,
confirmed this round by `test_invalid_json_restore_preserves_session_and_step_still_works`
and `test_restore_snapshot_json_rejects_invalid_without_prior_validate`.

## 4. Success Semantics / Post-Teardown Failure / Fault Recovery

Unchanged from v0.6/v0.7.

## 5. Capabilities

`get_restore_capabilities()` returns the live CLR `RestoreCapabilities`
contract as a Python dataclass. As of this round:

```text
restore_api_version = phase3c.4          # was phase3c.2
milestone = phase3c.4                    # was phase3c.2
contract_version = 0.5
snapshot_schema_version = phase3c.4      # was phase2b.2
supports_combat_history = true           # was false
supports_pets = true                     # unchanged since Phase 3C.2
transaction_model = validate_before_destroy
rollback_after_teardown = false
issues_new_combat_session = true
preserves_stable_ids = true
```

`supported_power_scope` now includes one `serialize_required:{ClassName}`
entry per each of the 20 Power classes requiring exact `InternalData` restore,
and one `safe_to_recompute:{ClassName}` entry per each of the 13 Power classes
whose internal counters are intentionally not captured (see section 7).
`unsupported_power_internal_data_classes` lists exactly the 2
`unsupported_unknown` classes (`GigantificationPower`, `VigorPower`).
`rejection_codes` gained the Phase 3C.4.1 JSON-validation codes (section 2)
plus `missing_power_internal_data`, `invalid_power_internal_data_field`,
`invalid_power_internal_data_serializer`. **Note**: the public
`RestoreCapabilities.RejectionCodes` vocabulary still lists
`combat_history_non_empty` and does NOT list a bare
`unknown_combat_history_entry_type` entry, even though actual validation of a
Snapshot with an unrecognized `CombatHistoryEntrySnapshot.EntryType` returns
the suffixed reason `unknown_combat_history_entry_type:{type}` - this is a
real, confirmed discrepancy between the published vocabulary list and actual
validator behavior (found independently this round, not previously
documented). It does not affect correctness of the Restore contract itself
(the actual rejection is still returned and is still prefix-matchable per
section 8 of the DTO spec's own "prefix, not exact match" guidance), but
callers relying on `get_restore_capabilities().rejection_codes` to
enumerate every string that can ever appear in a `RejectionCodes` result
should not treat that list as exhaustive.

## 6. CombatHistory (Phase 3C.3)

CombatHistory restore is now supported: `supports_combat_history` is `True`,
and a Snapshot whose `CombatHistory.Entries` is non-empty is no longer
rejected outright (the historical Phase 3C.1/3C.2 `combat_history_non_empty`
blanket rejection is gone). All 17 `CombatHistoryEntrySnapshot.EntryType`
values are supported for restore:

```text
CardPlayStartedEntry, CardPlayFinishedEntry, CardAfflictedEntry,
CardDiscardedEntry, CardDrawnEntry, CardExhaustedEntry, CardGeneratedEntry,
CreatureAttackedEntry, DamageReceivedEntry, BlockGainedEntry,
EnergySpentEntry, MonsterPerformedMoveEntry, OrbChanneledEntry,
PotionUsedEntry, PowerReceivedEntry, StarsModifiedEntry, SummonedEntry
```

An entry with an unrecognized `EntryType` is still rejected, now under
`unknown_combat_history_entry_type:{type}` rather than the old blanket
non-empty-history rejection - confirmed by
`test_rejection_categories_via_public_python_api`.

### 6-A. `CombatHistoryEntrySnapshot.PlayerTurnNumbers` (new required Python field)

Every history entry carries a `PlayerTurnNumbers` dict (keyed by
`Player.NetId` string, `future-causal` per
`docs/restore_snapshot_design_policy.md` §6's classification, since
`HappenedThisTurn`/`HappenedLastPlayerTurn` semantics depend on it), added to
the schema in `phase3c.3`. **The Python `CombatHistoryEntrySnapshot` dataclass
did not have this field before this round** - it has been added as a
required field (no default value), and `from_dict()`/`_require()` now
enforce its presence exactly like every other required field on this class.
A Snapshot dict missing this key on any history entry fails to parse into the
Python dataclass at all (`SnapshotValidationError`), independent of and prior
to any Emulator-side Restore call.

### 6-B. `CardPlay.Resources`

`CardPlay`-derived entries (`CardPlayStartedEntry`, `CardPlayFinishedEntry`,
and the `cardPlay` sub-object inside `BlockGainedEntry.Fields`) carry a
`resources` key (`energySpent`/`energyValue`/`starsSpent`/`starValue`),
captured and restored exactly - confirmed this round by
`test_combat_history_player_turn_numbers_and_cardplay_resources_preserved`.

### 6-C. `KNOWN_SCHEMA_VERSIONS` (Python-side gap, fixed this round)

**Before this round, `Combat/combat_state_snapshot.py`'s
`KNOWN_SCHEMA_VERSIONS` was `frozenset({"phase2a.1", "phase2b.1",
"phase2b.2"})` - missing `"phase3c.3"` and `"phase3c.4"`.** Since every live
`CaptureSnapshot()` call against the Emulator DLL current as of this round
emits `SchemaVersion="phase3c.4"`, this gap meant `CombatStateSnapshot.
from_dict()` rejected every live capture with `SnapshotValidationError:
unknown SnapshotMetadata.SchemaVersion` before this round's fix. Fixed to
`frozenset({"phase2a.1", "phase2b.1", "phase2b.2", "phase3c.3",
"phase3c.4"})`, matching the Schema's own enum exactly (verified directly
against `combat_state_snapshot.schema.json`, not assumed).
`Combat/evaluation/online_eval/verify_snapshot_phase2b.py` independently
checks `KNOWN_SCHEMA_VERSIONS` against the Schema enum - this script's own
assertion was failing before the fix, and passes after it.

### 6-D. No CombatHistory restore mechanics added on the Python side

The 17-entry-type restore logic itself lives entirely in the Emulator
(`SnapshotRestorer.ApplyCombatHistory`). Python does not parse, interpret, or
reconstruct history semantics beyond the generic `CombatHistoryEntrySnapshot`
dataclass (`EntryType`/`RoundNumber`/`CurrentSide`/`ActorInstanceId`/
`PlayerTurnNumbers`/`Fields`) already established in Phase 2A - `Fields` stays
a free-form dict, matching the Schema's own `additionalProperties: true`
treatment of that sub-object. No second CombatHistory parser was introduced.

## 7. Power Internal State (Phase 3C.4)

Power classification, confirmed via `ModelDb.AllPowers` runtime scan (281
concrete `PowerModel` classes, 35 using `_internalData`):

- **`serialize_required` (20 classes, `InternalData` captured and restored
  exactly)**: `AdaptablePower`, `AutomationPower`, `ChainsOfBindingPower`,
  `DampenPower`, `DarkEmbracePower`, `FeralPower`, `HardenedShellPower`,
  `HellraiserPower`, `IllusionPower`, `InterceptPower`, `JugglingPower`,
  `MockRevivePower`, `NightmarePower`, `OrbitPower`, `PaleBlueDotPower`,
  `PanachePower`, `PossessSpeedPower`, `PossessStrengthPower`,
  `ReattachPower`, `VoidFormPower`. (`FeralPower`/`JugglingPower`/
  `PaleBlueDotPower` were reclassified into this set this round, from
  `safe_to_recompute`, because their `AfterApplied` behavior depends on
  CombatHistory in a way that cannot be correctly reconstructed without
  capturing the internal counter directly.)
- **`safe_to_recompute` (13 classes, intentionally NOT captured - a
  post-Restore default/zero internal-counter state is the CORRECT restored
  state, not a gap)**: `AfterimagePower`, `CalamityPower`, `CurlUpPower`,
  `GravityPower`, `ImitationLearningPower`, `MonologuePower`, `OblivionPower`,
  `RupturePower`, `SerpentFormPower`, `SkittishPower`, `StormPower`,
  `StranglePower`, `SubroutinePower`.
- **`unsupported_unknown` (2 classes, still rejected)**: `GigantificationPower`,
  `VigorPower` (reason: reference a live, unserializable `AttackCommand` -
  continues from Phase 2B, unchanged this round).

Python does not introduce a Pet/Power-specific internal-state type - the
existing `PowerSnapshot.InternalData`/`InternalDataSerializerVersion`/
`HasUnsupportedInternalData` Python dataclass fields (added Phase 2B) already
carry this data unchanged; only the Emulator-side restore mechanics and
capability reporting changed. Confirmed this round for all three
classifications via the public Python API
(`test_power_internal_data_classifications_round_trip_and_reject_via_json_api`),
covering Player-owned, Enemy-owned, and Pet-owned Powers.

## 8. Restore To Step Determinism

Unchanged from v0.6/v0.7, extended to cover non-empty CombatHistory: for a
supported Snapshot containing CombatHistory entries, two independent restores
followed by the same logical action, reselected from each fresh LegalActions
list, must produce equivalent resulting state - confirmed by
`test_restore_step_determinism_with_non_empty_combat_history`.

## 9. Known Limitations

### 9-A. `EnemySnapshot.Intent` is not preserved across Restore

Unchanged from v0.6 §9-A / v0.7 §10-A.

### 9-B. `GetRestoreCapabilities()` pythonnet bug - RESOLVED (Phase 3C.2, still resolved)

Unchanged from v0.7 §10-B - remains resolved in this round's DLL.

### 9-C. Non-Osty Pet species are unverified

Unchanged from v0.7 §10-C.

### 9-D. Official JSON example does not cover all 17 CombatHistory entry types

`docs/contracts/combat_state_snapshot_example.v0.8.json` (the Emulator's
official, restoreable JSON example, `SchemaVersion="phase3c.4"`) has an
**empty** `CombatHistory.Entries` array. This is a discrepancy against the
governing integration instruction's assumption that the official example
"covers all 17 history entry types" - independently confirmed this round by
direct inspection of the example file (`len(entries) == 0`). The official
example remains valid and useful for JSON-API-success-path tests (Schema
validation, deserialize, Restore success) but does NOT exercise CombatHistory
entry-type coverage. This round's all-17-entry-type test coverage
(`test_combat_history_all_17_entry_types_round_trip_via_json_fixture`, etc.)
instead uses a test-local JSON fixture built by mutating a copy of the
official example, with entry field shapes ported from the Emulator's own
accepted `smoke_restore_snapshot_phase3c3_history_20260730.cs` fixture (not
invented) - this fixture is test-only, lives in
`Combat/tests/test_restore_snapshot_phase3c1.py`, and is not a new "official"
JSON example. **Recommendation for a future round**: escalate to
Emulator担当 to consider updating the official JSON example itself to include
representative CombatHistory entries, since an example with an empty history
does not demonstrate a key piece of this round's own scope.

### 9-E. `unknown_combat_history_entry_type` absent from published `RejectionCodes`

See section 5 - a confirmed discrepancy between the published capability
vocabulary and actual runtime rejection behavior. Recorded here as a
documentation gap for a future Emulator-side round to reconcile (the
`RestoreRejectionCodes` static list in `GameInstance.cs` should likely
include this entry, or the vocabulary's own documentation should clarify that
suffix-only codes without a bare-string vocabulary entry are expected). Not a
blocker for this round: actual rejection codes are always correctly returned
regardless of whether they appear in the capability-advertised vocabulary
list.

## 10. Artifact Hash Resolution Rules

Unchanged from v0.7 §11.
