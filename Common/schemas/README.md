# Common/schemas

JSON Schema definitions for the data STS2_Emulator exchanges with STS2_RL, extracted
directly from the emulator's actual C# DTOs and `GameInstance.BuildFullStateDict()` /
`BuildLegalActions()` - not invented ahead of the implementation. Re-derive these from
source (see each file's own `description`) whenever `STS2_Emulator/Sts2Emulator/Dto/*.cs`
or `GameInstance.cs`'s `Build*` methods change.

- `combat_scenario_input_schema.json` (v2, added 2026-07-20) - the Python scenario spec
  dict `STS2_RL/Combat/battle_emulator.py:build_scenario_from_spec()` converts into a
  C# `Sts2Emulator.Dto.CombatScenario` for `ResetFromScenario`. This is the one
  *input*-side schema in this directory; the other three are all observation/action
  *output*.
- `combat_state_schema.json` - the `state` dict inside `GameObservation.State`
  (`Sts2Emulator.Dto.GameObservation`), built by `GameInstance.BuildFullStateDict()`.
  This is the authoritative state surface (per that method's own doc comment) - prefer
  it over `GameObservation`'s older typed `Player`/`Enemies`/`Hand`/etc. fields, which
  are kept only for backward-compat CLI rendering.
- `legal_action_schema.json` - one entry of `GetLegalActions()` /
  `ResetResult.LegalActions` / `StepResult.LegalActions`
  (`Sts2Emulator.Dto.LegalAction`), as converted to a plain dict by
  `STS2_RL/Combat/emulator_bridge.py:legal_action_to_dict()`.
- `transition_schema.json` - one `Step()` result (`Sts2Emulator.Dto.StepResult`) and,
  for the initial state, `ResetFromScenario`'s `ResetResult` (same
  `Observation`/`LegalActions` shape, no `ActionId`/`Reward`/`Done`/`Info`).
- `choice_semantics_schema.json` (v1, added 2026-07-24) - **RL-side**, not
  Emulator-derived like the four files above. Shape of one entry in RL's own versioned
  lookup table normalizing what a `choice_card`/`choice_skip`/`choice_confirm` choice
  actually means (destination zone, real operation) beyond what Emulator's
  `choiceOperation`/`sourceZone`/`destinationZone`/`originEntityType`/`originEntityId`
  fields can derive mechanically (see `STS2_Emulator/docs/API_REFERENCE.md` §3.3.1's
  "Known gaps" - Emulator deliberately does not hardcode this). Re-derive/extend by
  reading `STS2_Emulator/Sts2Emulator/Imported/Source` card/relic/power/potion/monster
  effect source, not from `Build*` methods.
- `choice_semantics_lookup.v1.json` - the populated table (57 entries) for
  `choice_semantics_schema.json`, covering every `CardSelectCmd.From*` call site found by
  census as of 2026-07-24 (122 call sites / 113 files) - either as a specific entity row
  (`hardcoded_entity_rule`) or one of the shared `__GENERIC_*__` rows for operations
  Emulator itself already fully resolves. See
  `Outputs/reports/choice_semantics_analysis_report.md` for the full methodology,
  entity census, and open items.

## 2026-07-20 15:49 build: what changed (Emulator "優先度2")

All four gaps listed below as of the previous version of this file are now fixed,
verified against actual source (`Sts2Emulator/Dto/CombatScenario.cs`,
`CardInstanceScenario.cs`, `PotionScenario.cs`, `Api/GameInstance.cs`) and a rebuilt DLL
(`Sts2Emulator.Cli/bin/Debug/net8.0/Sts2Emulator.dll`, 2026-07-20 15:49:09), not merely
taken on the reporting party's word:

1. **Card upgrade state** - `CombatScenario.HandCards`/`DrawPileCards`/
   `DiscardPileCards`/`ExhaustPileCards` (structured `CardInstanceScenario{CardId,
   IsUpgraded}` lists) alongside the original plain-string `Hand`/`DrawPile`/etc. -
   see `combat_scenario_input_schema.json`.
2. **Potions** - `CombatScenario.Potions` (`PotionScenario{Slot, PotionId}` list, exact/
   non-additive belt state).
3. **Player HP** - `PlayerHp`/`PlayerMaxHp` are independently nullable now (four
   combinations - see that schema's description); previously both had to be supplied
   together or a caller risked double-applying a max-HP-changing relic's effect.
4. **Stable enemy identification** - `state["enemies"][i]["index"]` (position within
   `CombatState.Enemies`, stable across deaths, disambiguates duplicate monster types)
   and the matching `Parameters["enemyIndex"]` on a `choice_target` `LegalAction`.

Also fixed in the same build: (a) the `LEAD_PAPERWEIGHT`/`CLAWS`-class hang this
project's own investigation found and reported
(`Combat/evaluation/reports/emulator_hang/`) - any relic whose `AfterObtained()` awaits
an interactive card-choice prompt (24 total, see
`Sts2Emulator.Api.Internal.RealEngine.AutoSkipCardSelector`'s doc comment) now has that
prompt auto-declined instead of hanging; (b) `EnterRoomWithTimeout` now uses
`Task.WaitAny` instead of `Task.Wait`, so a genuine synchronous validation exception
(e.g. an invalid `PlayerHp`) surfaces as its real, unwrapped type again instead of a
wrapped `AggregateException`.

## 2026-07-21 build: Stars support added

`Sts2Emulator/Dto/CombatScenario.cs` now has `public int? Stars { get; set; }` (the
Regent combat resource), and `GameInstance.cs` now populates `state["stars"]` in
`BuildFullStateDict()`. Reflected in `combat_scenario_input_schema.json`,
`combat_state_schema.json`, and both `battle_emulator.py` scenario builders
(`build_scenario_from_spec()` and `build_scenario_from_state()`). This fixes the earlier
Regent restore gap where a Stars-spending card could be legal in the pre-restore state
but fail after `apply_action()` rebuilt the scenario without carrying current Stars.

## 2026-07-21: SlotName restoration provenance

`combat_scenario_input_schema.json` now documents RL-side `slot_name_manifest` metadata.
This is not a `CombatScenario` field; it records how `EnemyScenario.SlotName` was
restored before conversion. Current sources are `source_history`,
`encounter_definition`, `inferred_from_order`, and `unavailable`. This was added after
fixed50 showed that WRIGGLER and EXOSKELETON can produce `UNSET_MOVE`/empty legal
actions when SlotName is dropped.

## `CombatEnv` exists now - superseded gap

An earlier version of this file said the mid-term plan's Phase 1 `CombatEnv` "has not
been built yet". That's no longer true: `STS2_RL/Combat/env/combat_env.py` implements
`reset()`/`get_legal_actions()`/`step()` per `transition_schema.json`'s shape (including
`reward`/`done`/`legal_actions`/`info`, unlike `emulator_bridge.py:observation_to_dict()`'s
older reduced form). See `STS2_RL/docs/RL_HANDOFF.md` section 7 for the current
architecture (real committed trajectories go through `CombatEnv`; Heuristic search's own
internal candidate exploration still uses `BattleEmulator` directly, by design).

## Remaining known gap in `CombatScenario` input

`Ascension` cannot be set via `CombatScenario`/`ResetFromScenario` at all (only the
simpler `Reset(seedText, characterId, enemyId, ascension)` takes an ascension
parameter) - a scenario reconstructed from real run data can record the run's actual
`ascension` as metadata, but cannot apply it. Not yet raised with the Emulator side as
of this writing.

## Enemy `index` stability - corrected understanding

An earlier note here (and in `combat_state_schema.json`) claimed `state["enemies"][i]["index"]`
is "stable across deaths". That was **wrong** - verified against `CombatState.cs`
source: the engine physically `Remove`s a dead creature from the underlying list (not
just an `isAlive=false` flag), so surviving enemies' `index` values shift immediately,
even within a single `Step()` call. `combat_state_schema.json`'s own field description
has already been corrected to reflect this (see its `index` property) - this note exists
so anyone skimming only this README doesn't repeat the earlier, incorrect claim. See
`STS2_RL/docs/RL_HANDOFF.md` section 5.5 for the full, current explanation.
