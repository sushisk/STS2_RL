# Canonical CombatStateSnapshot Contract v0.7

**Status: Phase 3C.2 Python Restore Pet integration, independently verified
(2026-07-30).**

This document supersedes `combat_state_contract.v0.6.md` for RL-side Python
Restore API integration. v0.5 (C# contract) and v0.6 (Phase 3C.1 Python
integration) remain unchanged as historical documents. The underlying Emulator
Restore capability still reports C# contract version `0.5`; v0.7 records this
round's addition of Pet support to the same Python wrapper contract v0.6
established, plus the two v0.6 §9 known limitations' current status.

## 1. Python Restore API Surface

Unchanged from v0.6. `Combat/live_combat_session.py::LiveCombatSession`
exposes:

```text
validate_restore_snapshot(snapshot) -> RestoreValidationResult
restore_snapshot(snapshot) -> BattleState
restore_snapshot_json(json_text: str) -> BattleState
get_restore_capabilities() -> RestoreCapabilities
```

No new Python Restore API surface was added for Pet support. `Player.Pets`
already existed in the Python `CombatStateSnapshot` DTO schema (added in Phase
2B) and already flowed through the existing canonical-JSON / CLR-DTO
conversion path (`emulator_bridge.snapshot_to_clr`) unchanged - this round
required zero changes to `Combat/emulator_bridge.py` or
`Combat/live_combat_session.py`, confirmed by independent review of both
files' diffs (empty).

## 2. Validation And Rejection

Unchanged from v0.6. `validate_restore_snapshot()` is a side-effect-free dry
run; a rejected `restore_snapshot()`/`restore_snapshot_json()` call never
touches the current live session and does not set `_session_faulted = True`.

## 3. Success Semantics

Unchanged from v0.6. A successful Restore establishes a new live combat
session identity, and Restore is not a step (no transition, no trajectory
entry recorded).

## 4. Post-Teardown Failure

Unchanged from v0.6.

## 5. Fault Recovery

Unchanged from v0.6.

## 6. Capabilities

`get_restore_capabilities()` returns the live CLR `RestoreCapabilities`
contract as a Python dataclass. As of Phase 3C.2:

```text
restore_api_version = phase3c.2
milestone = phase3c.2
contract_version = 0.5
snapshot_schema_version = phase2b.2
supports_pets = true          # changed from false in Phase 3C.1
transaction_model = validate_before_destroy
rollback_after_teardown = false
issues_new_combat_session = true
preserves_stable_ids = true
```

All other fields (`contract_sha256`, `snapshot_schema_sha256`,
`supported_completeness`, the other `supports_*` flags,
`supported_power_scope`, `rejection_codes`) are unchanged in shape from v0.6;
`supported_power_scope` gained one additional descriptive entry,
`pet_power_same_constraints_as_player_enemy`, and `rejection_codes` gained
Pet-specific entries (`unsupported_pet_kind`, `pet_missing_owner_instance_id`,
`pet_owner_mismatch`, `pet_missing_monster_id`) alongside the existing ones -
`pet_count` was removed, since a Pet-containing Snapshot is no longer rejected
for the mere presence of a Pet.

## 7. Phase 3C.2 Scope

Supported (added this round, in **bold**):

```text
clean, complete, normal_player_decision snapshots
no CombatHistory
**Pets (Osty-class, single pet, hook-free reconstruction)**
no Pending Choice/Target
no Action continuation
no unsupported broader Power internalData (including Pet-owned Powers)
single enemy
valid stable references (including Pet InstanceId/CombatId/OwnerInstanceId)
```

Still unsupported and rejected in Phase 3C.2:

```text
CombatHistory (including a Pet-containing Snapshot with non-empty history -
    Pet support does not relax this gate, see section 8-F)
Pending Choice
Pending Target
Action continuation
Orbs
Relic SavedProperties
Power AssociatedCard (including on a Pet-owned Power)
Power amount 0 (including on a Pet-owned Power)
broader Power internalData (including on a Pet-owned Power)
duplicate or dangling stable IDs (including Pet-related references)
unknown schema versions
non-Osty Pet species (Byrdpip/PaelsLegion etc. - verified only via direct
    source inspection of their fixed `MoveStateMachine`, never round-tripped
    through an actual Capture -> Restore -> Capture cycle this round; treated
    as a known limitation, not a supported case, until independently verified)
```

The corresponding capability flags for the still-unsupported items remain
`False`. These items remain deferred to Phase 3C.3 and later.

## 8. Pet Restore Support

### 8-A. Object model - no new Python types

Pets are represented with the same `CreatureSnapshot`/`PowerSnapshot` Python
dataclasses already used for Enemies (added in Phase 2B for Pet Capture, see
`SnapshotBuilder.CapturePet`). `CreatureSnapshot.Kind == "pet"` is the only
discriminator; no separate Pet dataclass, no second Snapshot parser, and no
second canonicalizer were introduced on the Python side. This mirrors the
Emulator-side design decision (`restore_snapshot_phase3c2_pet_design_20260728.md`
§1-B) to reuse the same DTO for Pet and Enemy Creatures.

### 8-B. No Pet-specific RNG type

Confirmed (both by the Emulator-side design/implementation/audit rounds, and
independently by this round's own reading of `CombatState.CreateCreature`
and `Osty.GenerateMoveStateMachine()`) that Pet generation consumes no RNG
stream: `CombatState.CreateCreature`'s `Niche` consumption is gated on
`side == CombatSide.Enemy`, and restored Pets are created with
`CombatSide.Player`. `Osty`'s move state machine is fixed `NOTHING_MOVE` and
never calls into `RandomBranchState`/`Monster.Rng`. `SnapshotBuilder.CaptureRng()`
still captures only Run RNG, Player RNG, and enemy Monster RNG - no Pet RNG
field exists in the schema, and none was added. Python does not introduce a
Pet-specific RNG type or capture field; `test_full_rng_stream_equality_across_round_trip`'s
existing three-stream comparison remains the complete RNG contract, unchanged
by Pet support.

### 8-C. No Pet-specific Power type

`DieForYouPower` (the only Pet-owned Power exercised this round, granted to
Osty) is an ordinary `PowerModel` subclass with `Creature`-typed `Owner`
semantics and no custom `internalData`. `PowerModel.ApplyInternal` does not
distinguish Player/Enemy/Pet owners. Pet Powers are restored through the
exact same `ApplyPowers()`/`ApplyPower()`/`PowerModel.ApplyInternal` path
already used for Player and Enemy Powers - no Pet-specific Power class was
added on either side of the RL/Emulator boundary. `AllPowers()`'s eligibility
scan (used by `validate_restore_snapshot()`) was extended on the Emulator
side to include `Player.Pets[].Powers`, so `unsupported_internal_data`/
`unsupported_power_associated_card`/`unsupported_zero_amount_power` apply
identically to a Pet-owned Power as to a Player- or Enemy-owned one -
independently confirmed this round (`test_rejection_categories_via_public_python_api`
family; also directly audited in the Emulator-side round, §4-E of
`restore_snapshot_phase3c2_pet_audit_20260730.md`).

### 8-D. Hook-free Pet generation

Restore never calls `OstyCmd.Summon`, `PlayerCmd.AddPet<T>`, or
`CreatureCmd.Add` - the full, gameplay-facing summon commands that write
CombatHistory (`CombatManager.Instance.History.Summoned(...)`) and invoke
fresh-start hooks (`Hook.AfterSummon`, `Creature.AfterAddedToRoom()`).
Instead, Restore uses the same class of low-level, save/load-equivalent
primitives Phase 3B established for Enemies:

```text
CombatState.CreateCreature(ResolveMonster(pet.MonsterId).ToMutable(), CombatSide.Player, pet.SlotName)
combatState.AddCreature(petCreature)
CombatManager.Instance.AddCreature(petCreature)
player.PlayerCombatState.AddPetInternal(petCreature)
Register(pet.InstanceId, petCreature)
```

`AddPetInternal` is a `public` "Internal"-suffix method (the same naming
convention Phase 3B's `AddRelicInternal`/`AddPotionInternal` already
established as an explicitly sanctioned save/load path), requiring no
reflection. None of these four calls write to CombatHistory or invoke a
fresh-start hook - confirmed both by direct Emulator-side source inspection
and by this round's own independent test
(`test_pet_object_restore_round_trip`/`test_pet_json_restore_round_trip_matches_object_restore`
capture a recaptured Snapshot and rely on the existing `ValidateFinalState`-style
clean-CombatHistory invariant already checked by `_make_eligible`/the shared
eligibility gate).

### 8-E. Pet stable ID preservation

A Pet's `InstanceId` is preserved exactly across a Restore
(Capture -> Restore -> Capture), confirmed by
`test_pet_object_restore_round_trip`/`test_pet_json_restore_round_trip_matches_object_restore`.
`CombatId` (an incrementing per-combat-session integer counter, not a stable
cross-session identity) is reproduced only when the Player -> Enemy -> Pet
creation order at Restore time matches the original live session's order -
the Emulator-side audit independently confirmed this holds for a real
`BOUND_PHYLACTERY`-summoned Osty (`restore_snapshot_phase3c2_pet_audit_20260730.md`
§4-H, its `pet_roundtrip_from_real_summon` case), and this round's own
`_pet_restore_snapshot()` fixture (built the same way, via a live
`BOUND_PHYLACTERY` summon through `LiveCombatSession.start_combat()`)
independently reproduces the same result via `_assert_restored_pet_matches`'s
`CombatId` comparison.

### 8-F. History-containing Pet Snapshot is still rejected

Pet support removes only the `pet_count` rejection gate. A Snapshot
containing both a Pet and non-empty `CombatHistory.Entries` is still rejected
under `combat_history_non_empty`, unchanged - Pet support does not implicitly
relax any other eligibility gate. Confirmed both by the Emulator-side audit's
`reject_history_with_pet` case and by this round's
`test_rejection_categories_via_public_python_api`, which additionally asserts
`pet_count` is absent from a rejected Pet-containing Snapshot's
`rejection_codes` (a positive assertion that the OLD reason is gone, not just
that rejection still occurs). Real Scenario `6546-21` (which contains a
live-summoned Osty Pet) remains correctly rejected via the public API for its
pre-existing `reference_integrity` reason (dangling SOUL card references,
unrelated to Pet support) - confirmed by `test_real_6546_21_rejected_via_public_api`,
unchanged from Phase 3C.1.

### 8-G. `supports_pets` capability flag

`get_restore_capabilities().supports_pets` is `True` as of this round (was
`False` in Phase 3C.1) - see section 6.

## 9. Restore To Step Determinism

Unchanged from v0.6, extended to cover Pets: for a supported Pet-containing
snapshot, two independent restores followed by the same logical action,
reselected from each fresh LegalActions list, must produce equivalent
resulting state INCLUDING the Pet's own signature (InstanceId, Hp, MaxHp,
Block, CombatId, Powers) - confirmed by
`test_pet_restore_step_determinism_reselects_fresh_action`.

## 10. Known Limitations

### 10-A. `EnemySnapshot.Intent` is not preserved across Restore

Unchanged from v0.6 §9-A - still an accepted, understood scope gap. Not
Pet-specific; applies equally to any Enemy in a Pet-containing Snapshot.

### 10-B. `get_restore_capabilities()`'s pythonnet `AppContext.BaseDirectory` bug - RESOLVED

v0.6 §9-B documented that `GetRestoreCapabilities()` threw
`System.ArgumentException: The path is empty` under pythonnet/CoreCLR hosting
because `AppContext.BaseDirectory` is empty in that hosting context and
`FindFileUpwards`/`CandidateRoots` did not tolerate that. **The Emulator side
has fixed this in the Phase 3C.2 DLL**: `CandidateRoots()` now also yields
`typeof(GameInstance).Assembly.Location`'s directory, and `FindFileUpwards()`
skips null/empty/invalid root candidates instead of throwing. Independently
re-verified this round: `session.get_restore_capabilities()` now succeeds and
returns a `SnapshotSchemaSha256` matching the real schema file's hash
(`test_get_restore_capabilities_hashes` now passes end-to-end, including its
previously-unreachable hash-comparison assertions - see section 11 for one
additional, unrelated fixture issue this uncovered).

### 10-C. Non-Osty Pet species are unverified

`Byrdpip` and `PaelsLegion` (other `PlayerCmd.AddPet<T>`-summonable Pet types
identified during the Emulator-side implementation round) were confirmed via
direct source inspection to also use a fixed `MoveStateMachine`, but neither
was exercised through an actual Capture -> Restore -> Capture cycle by either
side this round. Treat only Osty as verified; other Pet species are a known,
recorded gap for a future round if/when they become relevant to RL training
scenarios.

## 11. Artifact Hash Resolution Rules

This round's independent audit surfaced a subtlety worth recording as a
standing rule for any test or tooling that recomputes an artifact hash
locally to compare against `RestoreCapabilities`' reported hash fields
(`contract_sha256`, `snapshot_schema_sha256`):

- The Emulator's reported hashes reflect the **canonical, LF-line-ending git
  blob content** of the corresponding file (`combat_state_contract.v0.5.md`,
  `combat_state_snapshot.schema.json`), regardless of which machine or
  checkout produced the DLL.
- A local RL-side git checkout may have this repository's `.gitattributes`
  (`* text=auto`) combined with a local `core.autocrlf = true` setting,
  which checks text files out with CRLF line endings on disk even though the
  underlying git blob (and therefore the canonical hash) is LF. This affects
  files checked out into any `git worktree` created under this policy,
  independent of Phase 3C.2 itself - confirmed present in both the Phase
  3C.1 and Phase 3C.2 worktrees' copies of `combat_state_contract.v0.5.md`.
- Any Python code that hashes a local copy of one of these files for
  comparison against a `RestoreCapabilities` hash field **must normalize
  `\r\n` to `\n` before hashing** (or otherwise read in a line-ending-agnostic
  way) to get a checkout-independent, canonical-content comparison. This
  round's fix to `test_get_restore_capabilities_hashes` (see the audit
  report) applies this rule; it was not previously necessary because the
  test never reached this assertion before the Phase 3C.2 `AppContext.BaseDirectory`
  fix (section 10-B) let it proceed further than any prior round.
- `combat_state_snapshot.schema_sha256()` does not need this treatment: it
  reads directly from a fixed absolute path under `C:\STS2_Emulator`
  (`_SCHEMA_PATH`), which is not subject to RL-side git worktree checkout
  settings at all.
