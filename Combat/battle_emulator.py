"""BattleEmulator: a functional (state-in, state-out) wrapper over the STS2 GameInstance.

Architecture note - why this is stateless rather than holding "the live battle":
Only one GameInstance can be live per OS process (verified experimentally: creating a
second instance and calling Reset/ResetFromScenario on it corrupts the first - its
GetObservation() starts raising "array index out of range"). Rather than juggle
multiple processes, every method here takes an explicit BattleState snapshot, restores
it into the single shared GameInstance via ResetFromScenario, performs one operation,
and returns a brand new BattleState built from the result. Real progression and
what-if lookahead are therefore the exact same operation - lookahead just discards the
resulting state instead of feeding it back into the loop. This matches the design
doc's BattleEmulator signatures (which all take `state` explicitly) and the API
reference's own framing of ResetFromScenario as being "for search algorithms that need
many independent simulations from a given position."

CombatScenario.Energy (added upstream) means a restored snapshot reproduces the exact
energy the player had at that point - no separate Python-side energy bookkeeping needed.
CombatScenario.Stars works the same way for Regent's combat resource and must be carried
through every restore-based branch just like energy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from emulator_bridge import (
    ensure_loaded,
    legal_actions_to_list,
    shared_game_instance,
    str_list,
    to_plain,
)
from battle_result import BattleResult
from choice_card_state import pending_choice_state_key
from slot_name_inference import ensure_inferred_slot_names


@dataclass(frozen=True)
class DecisionFrame:
    """Canonical CombatStateSnapshot contract v0.3 §2: DecisionFrame = combatSessionId +
    stepIndex + continuationStepIndex. `combat_session_id`/`step_index` are read directly
    off GameObservation.CombatSessionId/StepIndex (Emulator commit ce7ecc2, contract
    Phase 1) - reissued by the Emulator every time a genuinely new combat begins,
    incremented every Step() call. `continuation_step_index` is this side's OWN
    bookkeeping (Emulator does not track it, per the contract) - None for a top-level
    (non-continuation) frame. Two frames are equal only if all three fields match -
    used by LiveCombatSession.step() to reject a stale action_id (see
    live_combat_session.py::DecisionFrameMismatchError)."""

    combat_session_id: "str | None"
    step_index: int
    continuation_step_index: "int | None" = None


@dataclass
class BattleState:
    """Our own snapshot of a board position - the thing passed between BattleEmulator calls."""

    engine_state: dict = field(repr=False)
    is_terminal: bool
    outcome: str
    turn: int
    enemy_max_hps: dict = field(default_factory=dict, repr=False)
    # Which reshuffle-RNG hypothesis this state's whole trajectory is pinned to (None = let
    # Seed drive reshuffles as usual). Set via BattleEmulator.with_shuffle_seed() and carried
    # forward unchanged by every subsequent apply_action() along this branch - see lookahead.py.
    shuffle_rng_seed: "int | None" = None
    # LegalAction[] for this exact state, already converted to plain dicts - captured for
    # free from the ResetResult/StepResult that produced this state (both carry LegalActions
    # for the state they just produced, identical in meaning to a fresh GetLegalActions()
    # call - see BattleEmulator.enumerate_legal_actions). None only for a BattleState built
    # some other way; enumerate_legal_actions() falls back to a real restore+fetch then.
    _cached_legal_actions: "list[dict] | None" = field(default=None, repr=False)
    # Canonical CombatStateSnapshot contract v0.3 §2's DecisionFrame - populated by _wrap()
    # below directly from GameObservation.CombatSessionId/StepIndex (Emulator commit
    # ce7ecc2), so every BattleState this file produces (initialize()/_restore()/
    # step_live_action(), i.e. both the live-session path and HeuristicAgent's existing
    # restore-based search) carries an accurate frame - purely additive, no existing
    # behavior changes since nothing previously read this field.
    decision_frame: "DecisionFrame | None" = field(default=None, repr=False)


def state_has_living_enemies(engine_state: dict) -> bool:
    return any(
        (enemy.get("isAlive", True) and (enemy.get("hp", 0) or 0) > 0)
        for enemy in (engine_state.get("enemies") or [])
    )


def coerce_terminal_observation(engine_state: dict, is_terminal: bool, outcome: str | None) -> tuple[bool, str]:
    """Normalize obviously-terminal observations that still come back as non-terminal.

    We have live saved cases where Step() returns an observation with no living enemies
    but `IsTerminal == false` / `Outcome == in_progress`. RL-side restore/search code
    cannot legally branch from such a state (`build_scenario_from_state()` correctly
    rejects zero-living-enemy restores), so treat it as terminal here instead of letting
    the next decision crash in candidate evaluation.
    """
    normalized_outcome = str(outcome or "in_progress")
    if is_terminal:
        return True, normalized_outcome
    if state_has_living_enemies(engine_state):
        return False, normalized_outcome
    hp = engine_state.get("hp", 0) or 0
    if hp > 0:
        return True, "victory"
    return True, "defeat"


def battle_state_key(battle_state: BattleState) -> tuple:
    """A hashable, exact equality key for `battle_state` - two states with the same key
    are indistinguishable for every purpose this package cares about (search/eval), so a
    search tree may safely collapse them into one and keep only the higher-scoring one.

    Piles (hand/drawPile/discardPile/exhaustPile) are order-sensitive: draw order is
    gameplay-meaningful, and even discard/exhaust order could in principle affect a later
    reshuffle's result, so being order-sensitive there too errs on the side of never
    merging two states that could actually diverge. Powers (player and per-enemy) are
    order-INsensitive (sorted by id) since their list order is incidental engine/dict
    iteration order, not a gameplay-meaningful sequence like a pile's draw order is.
    """

    def card_signature(card):
        if not card:
            return None
        return (
            card.get("id"),
            card.get("cost"),
            card.get("type"),
            card.get("targetType"),
            card.get("upgraded"),
            card.get("upgradeLevel"),
            card.get("tinkerTimeType"),
            card.get("tinkerTimeRider"),
        )

    def card_tuple(cards):
        return tuple(
            card_signature(c)
            for c in (cards or [])
        )

    def power_tuple(powers):
        return tuple(
            sorted(
                (
                    p.get("id"),
                    p.get("amount"),
                    card_signature(p.get("associatedCard")),
                )
                for p in (powers or [])
            )
        )

    def potion_tuple(potions):
        return tuple(
            (
                p.get("id"),
                p.get("rarity"),
                p.get("targetType"),
            ) if p else None
            for p in (potions or [])
        )

    def enemy_tuple(enemies):
        return tuple(
            (
                e.get("id"),
                e.get("hp"),
                e.get("maxHp"),
                e.get("block"),
                e.get("isAlive"),
                (e.get("intent") or {}).get("stateId"),
                power_tuple(e.get("powers")),
                e.get("slotName"),
                tuple(e.get("stateLog") or []),
            )
            for e in (enemies or [])
        )

    def orb_tuple(orbs):
        return tuple(
            (
                orb.get("id"),
                _stable_orb_value(orb.get("basePassiveValue")),
                _stable_orb_value(orb.get("baseEvokeValue")),
            )
            for orb in (orbs or [])
        )

    def pending_choice_tuple(pending_choice):
        return pending_choice_state_key(pending_choice)

    state = battle_state.engine_state
    return (
        battle_state.turn,
        state.get("hp"),
        state.get("maxHp"),
        state.get("block"),
        state.get("energy"),
        state.get("stars"),
        potion_tuple(state.get("potions")),
        card_tuple(state.get("hand")),
        card_tuple(state.get("drawPile")),
        card_tuple(state.get("discardPile")),
        card_tuple(state.get("exhaustPile")),
        card_tuple(state.get("playPile")),
        power_tuple(state.get("playerPowers")),
        state.get("orbSlots"),
        orb_tuple(state.get("orbs")),
        pending_choice_tuple(state.get("pendingChoice")),
        enemy_tuple(state.get("enemies")),
        tuple(r.get("id") for r in (state.get("relics") or [])),
        state.get("seed"),
        battle_state.shuffle_rng_seed,
    )


def dedup_by_state(entries: list, state_of=lambda entry: entry) -> list:
    """Given `entries` already sorted best-score-first, keeps only the first (i.e.
    highest-scoring) entry per unique battle_state_key(). `state_of` extracts the
    BattleState from an entry (identity if entries already are BattleStates, or e.g.
    `lambda e: e[0]` for (state, sequence, score) tuples).
    """
    seen = set()
    deduped = []
    for entry in entries:
        key = battle_state_key(state_of(entry))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _power_stacks(types, powers) -> Any:
    PowerStack = types["PowerStack"]
    stacks = types["List"][PowerStack]()
    for p in powers or []:
        stack = PowerStack()
        stack.PowerId = p.get("id", p.get("power_id"))
        stack.Amount = int(p["amount"])
        associated_card = p.get("associatedCard", p.get("associated_card"))
        if associated_card:
            stack.AssociatedCard = _card_instance_from_payload(types, associated_card)
        elif str(stack.PowerId).upper() == "NIGHTMARE_POWER":
            raise ValueError("missing_associated_card")
        stacks.Add(stack)
    return stacks


def _card_instance_from_payload(types, card: dict) -> Any:
    CardInstanceScenario = types["CardInstanceScenario"]
    instance = CardInstanceScenario()
    instance.CardId = card["card_id"] if "card_id" in card else card["id"]
    instance.IsUpgraded = bool(card.get("is_upgraded", card.get("upgraded", False)))
    tinker_time_type = card.get("tinker_time_type", card.get("tinkerTimeType"))
    tinker_time_rider = card.get("tinker_time_rider", card.get("tinkerTimeRider"))
    if tinker_time_type not in (None, ""):
        instance.TinkerTimeType = tinker_time_type
    if tinker_time_rider not in (None, ""):
        instance.TinkerTimeRider = tinker_time_rider
    return instance


def _card_instances(types, cards) -> Any:
    """Builds a List[CardInstanceScenario] for CombatScenario.{Hand,DrawPile,...}Cards -
    the structured, per-instance-upgrade-aware alternative to a plain id-string pile.
    `cards` is a list of {"card_id": str, "is_upgraded": bool} dicts (see
    Common/schemas/combat_scenario_input_schema.json's `card_instance` definition).
    Some cards also carry extra per-instance scenario state (e.g. MAD_SCIENCE's
    TinkerTime fields) that must survive restore-based lookahead exactly."""
    CardInstanceScenario = types["CardInstanceScenario"]
    instances = types["List"][CardInstanceScenario]()
    for c in cards or []:
        instances.Add(_card_instance_from_payload(types, c))
    return instances


def _potion_scenarios(types, potions) -> Any:
    """Builds a List[PotionScenario] for CombatScenario.Potions from a list of
    {"slot": int, "potion_id": str} dicts."""
    PotionScenario = types["PotionScenario"]
    slots = types["List"][PotionScenario]()
    for p in potions or []:
        slot = PotionScenario()
        slot.Slot = int(p["slot"])
        slot.PotionId = p["potion_id"]
        slots.Add(slot)
    return slots


def _decimal_value(types, value):
    if value is None:
        return None
    if value.__class__.__module__ == "System" and value.__class__.__name__ == "Decimal":
        return value
    return types["Decimal"].Parse(str(value))


def _orb_scenarios(types, orb_slots, orbs) -> tuple[Any, int | None]:
    """Build CombatScenario.Orbs/OrbSlots from spec or observation data."""
    OrbScenario = types["OrbScenario"]
    instances = types["List"][OrbScenario]()
    for orb in orbs or []:
        instance = OrbScenario()
        instance.OrbId = orb["id"] if "id" in orb else orb["orb_id"]
        base_passive = orb.get("basePassiveValue", orb.get("base_passive_value"))
        base_evoke = orb.get("baseEvokeValue", orb.get("base_evoke_value"))
        if base_passive is not None:
            instance.BasePassiveValue = _decimal_value(types, base_passive)
        if base_evoke is not None:
            instance.BaseEvokeValue = _decimal_value(types, base_evoke)
        instances.Add(instance)
    return instances, (None if orb_slots is None else int(orb_slots))


def _pending_choice_scenario(types, pending_choice) -> Any:
    """Build CombatScenario.PendingChoice from observation state or spec metadata."""
    if not pending_choice:
        return None
    scenario_restorable = pending_choice.get("scenarioRestorable", pending_choice.get("scenario_restorable"))
    scope = pending_choice.get("scope")
    if scenario_restorable is False or scope == "ActionContinuation":
        return None
    choice_type = pending_choice.get("choiceType") or pending_choice.get("choice_type")
    if not choice_type:
        return None
    PendingChoiceScenario = types["PendingChoiceScenario"]
    scenario = PendingChoiceScenario()
    scenario.ChoiceType = str(choice_type)
    if pending_choice.get("minSelect", pending_choice.get("min_select")) is not None:
        scenario.MinSelect = int(pending_choice.get("minSelect", pending_choice.get("min_select")))
    if pending_choice.get("maxSelect", pending_choice.get("max_select")) is not None:
        scenario.MaxSelect = int(pending_choice.get("maxSelect", pending_choice.get("max_select")))
    scenario.Options = _card_instances(
        types,
        [
            {
                "card_id": c["id"] if "id" in c else c["card_id"],
                "is_upgraded": c.get("upgraded", c.get("is_upgraded", False)),
                "tinkerTimeType": c.get("tinkerTimeType", c.get("tinker_time_type")),
                "tinkerTimeRider": c.get("tinkerTimeRider", c.get("tinker_time_rider")),
            }
            for c in (pending_choice.get("options") or [])
        ],
    )
    return scenario


def _stable_orb_value(value):
    return None if value is None else str(value)


def pending_choice_metadata(state: dict) -> dict | None:
    pending = state.get("pendingChoice") or {}
    if not pending:
        return None
    return {
        "choiceType": pending.get("choiceType"),
        "scope": pending.get("scope"),
        "scenarioRestorable": pending.get("scenarioRestorable"),
        "minSelect": pending.get("minSelect"),
        "maxSelect": pending.get("maxSelect"),
        "selectedCount": pending.get("selectedCount"),
    }


def is_action_continuation_pending_choice(state: dict) -> bool:
    pending = pending_choice_metadata(state)
    if not pending:
        return False
    if pending.get("scope") == "ActionContinuation":
        return True
    return pending.get("scenarioRestorable") is False


def is_scenario_restorable_pending_choice(state: dict) -> bool:
    pending = pending_choice_metadata(state)
    if not pending:
        return False
    if pending.get("scenarioRestorable") is True:
        return True
    return pending.get("scope") == "StartOfCombat"


def _card_instances_from_engine_cards(types, cards) -> Any:
    """Like _card_instances(), but source cards are engine-observation card dicts
    ({"id","type","rarity","cost","targetType","upgraded","upgradeLevel", ...} - see
    BuildCardListDict) rather than a scenario spec's {"card_id","is_upgraded"} dicts."""
    return _card_instances(
        types,
        [
            {
                "card_id": c["id"],
                "is_upgraded": c.get("upgraded", False),
                "tinkerTimeType": c.get("tinkerTimeType"),
                "tinkerTimeRider": c.get("tinkerTimeRider"),
            }
            for c in (cards or [])
        ],
    )


def build_scenario_from_state(engine_state: dict, shuffle_rng_seed: "int | None" = None):
    """Rebuilds a CombatScenario from a previously captured GameObservation.State dict -
    what every apply_action()/_restore() call uses to replay one more step from a
    BattleState snapshot, including every hypothetical branch Heuristic search (beam/
    lookahead) or generate_heuristic_trajectories.py's per-candidate scoring simulates.

    Uses the structured *Cards piles (preserving per-card upgraded state) and now also
    carries Potions forward - BUG FIX: this function used to drop both silently on every
    single restore (plain Hand/DrawPile/etc. string-id lists, which discard `upgraded`,
    and no Potions field being set at all), even though build_scenario_from_spec()
    (used only once, at BattleEmulator.initialize()) already carried both correctly. Not
    caught earlier because the original test scenarios (scenario_set.py's Ironclad vs
    CalcifiedCultist) never had upgraded cards or potions to lose - only surfaced once a
    real reconstructed scenario with potions hit an "Illegal action" GameInstance.Step
    exception when HeuristicAgent's own internal candidate-scoring loop restored a
    snapshot whose potion was silently gone, yet legal_actions (captured before restore)
    still listed the now-nonexistent potion action. Every restore-based simulation this
    package does was affected until this fix (beam search, lookahead, and any
    apply_action() caller) - not unique to the new trajectory-generation pipeline.
    """
    types = ensure_loaded()
    CombatScenario = types["CombatScenario"]
    EnemyScenario = types["EnemyScenario"]

    scenario = CombatScenario()
    scenario.CharacterId = engine_state["characterId"]
    scenario.PlayerHp = max(1, int(engine_state["hp"]))
    scenario.PlayerMaxHp = int(engine_state["maxHp"])
    scenario.HandCards = _card_instances_from_engine_cards(types, engine_state.get("hand"))
    scenario.DrawPileCards = _card_instances_from_engine_cards(types, engine_state.get("drawPile"))
    scenario.DiscardPileCards = _card_instances_from_engine_cards(types, engine_state.get("discardPile"))
    scenario.ExhaustPileCards = _card_instances_from_engine_cards(types, engine_state.get("exhaustPile"))
    scenario.Potions = _potion_scenarios(
        types,
        [{"slot": i, "potion_id": p["id"]} for i, p in enumerate(engine_state.get("potions") or []) if p],
    )
    scenario.Orbs, orb_slots = _orb_scenarios(types, engine_state.get("orbSlots"), engine_state.get("orbs"))
    if orb_slots is not None:
        scenario.OrbSlots = orb_slots
    pending_choice = _pending_choice_scenario(types, engine_state.get("pendingChoice"))
    if pending_choice is not None:
        scenario.PendingChoice = pending_choice
    scenario.PlayerPowers = _power_stacks(types, engine_state.get("playerPowers"))
    if engine_state.get("energy") is not None:
        scenario.Energy = int(engine_state["energy"])
    if engine_state.get("stars") is not None:
        scenario.Stars = int(engine_state["stars"])
    if engine_state.get("stepIndex") is not None:
        scenario.StepIndex = int(engine_state["stepIndex"])
    if engine_state.get("block") is not None:
        scenario.PlayerBlock = int(engine_state["block"])
    scenario.Relics = str_list([r["id"] for r in (engine_state.get("relics") or [])])
    if shuffle_rng_seed is not None:
        scenario.ShuffleRngSeed = shuffle_rng_seed

    enemies = types["List"][EnemyScenario]()
    for e in engine_state.get("enemies") or []:
        if not e.get("isAlive", True):
            continue
        enemy = EnemyScenario()
        enemy.MonsterId = e["id"]
        enemy.Hp = max(1, int(e["hp"]))
        if e.get("maxHp") is not None:
            enemy.MaxHp = int(e["maxHp"])
        if e.get("block") is not None:
            enemy.Block = int(e["block"])
        if e.get("slotName") is not None:
            enemy.SlotName = e["slotName"]
        enemy.Powers = _power_stacks(types, e.get("powers"))
        forced_move = (e.get("intent") or {}).get("stateId")
        if forced_move:
            enemy.ForcedMove = forced_move
        enemy.StateLog = str_list(e.get("stateLog") or [])
        # NOTE: GetObservation().State's enemies entries expose no key for either of
        # these (confirmed against API_REFERENCE.md's enemies entry shape) - they are
        # write-only scenario fields with no corresponding read-side observability. This
        # mapping is forward-compatible (reads engine_state if such a key is ever added)
        # but today is a no-op every time, since engine_state never carries them: if
        # FrogKnight/WaterfallGiant actually change these values mid-battle via real
        # gameplay, a restore-based checkpoint (this function) cannot see or preserve
        # that - only an initial value set via build_scenario_from_spec survives.
        if e.get("hasBeetleCharged") is not None:
            enemy.FrogKnightHasBeetleCharged = bool(e["hasBeetleCharged"])
        if e.get("currentPressureGunDamage") is not None:
            enemy.WaterfallGiantCurrentPressureGunDamage = int(e["currentPressureGunDamage"])
        enemies.Add(enemy)
    if enemies.Count == 0:
        raise ValueError("Cannot build a scenario with no living enemies - state should be terminal")
    scenario.Enemies = enemies

    scenario.Seed = int(engine_state.get("seed", 1))
    return scenario


def build_scenario_from_spec(spec: dict):
    """Builds a CombatScenario from a hand-authored scenario spec (see scenario_set.py
    and Common/schemas/combat_scenario_input_schema.json).

    player_hp/player_max_hp are independently optional (None/absent leaves the
    corresponding CombatScenario field null - "let nature take its course", per
    CombatScenario.PlayerHp/PlayerMaxHp's doc comments) - do not default a missing value
    to some guessed number here.

    hand/draw_pile/discard_pile/exhaust_pile (plain id-string lists, no upgrade info) and
    hand_cards/draw_pile_cards/discard_pile_cards/exhaust_pile_cards (structured
    {"card_id","is_upgraded"} lists) are both accepted per pile; supplying both non-empty
    for the same pile is intentionally NOT validated here - it is passed through as-is so
    the emulator's own ArgumentException (the authoritative validation) fires, rather than
    this wrapper silently picking a precedence the caller didn't ask for.
    """
    ensure_inferred_slot_names(spec)
    types = ensure_loaded()
    CombatScenario = types["CombatScenario"]
    EnemyScenario = types["EnemyScenario"]

    scenario = CombatScenario()
    scenario.CharacterId = spec["character_id"]
    if spec.get("player_hp") is not None:
        scenario.PlayerHp = int(spec["player_hp"])
    if spec.get("player_max_hp") is not None:
        scenario.PlayerMaxHp = int(spec["player_max_hp"])
    if spec.get("player_block") is not None:
        scenario.PlayerBlock = int(spec["player_block"])
    scenario.Hand = str_list(spec.get("hand", []))
    scenario.DrawPile = str_list(spec.get("draw_pile", []))
    scenario.DiscardPile = str_list(spec.get("discard_pile", []))
    scenario.ExhaustPile = str_list(spec.get("exhaust_pile", []))
    scenario.HandCards = _card_instances(types, spec.get("hand_cards"))
    scenario.DrawPileCards = _card_instances(types, spec.get("draw_pile_cards"))
    scenario.DiscardPileCards = _card_instances(types, spec.get("discard_pile_cards"))
    scenario.ExhaustPileCards = _card_instances(types, spec.get("exhaust_pile_cards"))
    scenario.Potions = _potion_scenarios(types, spec.get("potions"))
    scenario.Orbs, orb_slots = _orb_scenarios(types, spec.get("orb_slots"), spec.get("orbs"))
    if orb_slots is not None:
        scenario.OrbSlots = orb_slots
    pending_choice = _pending_choice_scenario(types, spec.get("pending_choice"))
    if pending_choice is not None:
        scenario.PendingChoice = pending_choice
    scenario.PlayerPowers = _power_stacks(types, spec.get("player_powers"))
    if spec.get("energy") is not None:
        scenario.Energy = int(spec["energy"])
    if spec.get("stars") is not None:
        scenario.Stars = int(spec["stars"])
    if spec.get("step_index") is not None:
        scenario.StepIndex = int(spec["step_index"])
    elif spec.get("stepIndex") is not None:
        scenario.StepIndex = int(spec["stepIndex"])
    if spec.get("shuffle_rng_seed") is not None:
        scenario.ShuffleRngSeed = spec["shuffle_rng_seed"]
    scenario.Relics = str_list(spec.get("relics", []))

    enemies = types["List"][EnemyScenario]()
    for e in spec["enemies"]:
        enemy = EnemyScenario()
        enemy.MonsterId = e["monster_id"]
        enemy.Hp = int(e["hp"])
        if e.get("max_hp") is not None:
            enemy.MaxHp = int(e["max_hp"])
        if e.get("block") is not None:
            enemy.Block = int(e["block"])
        if e.get("slot_name") is not None:
            enemy.SlotName = e["slot_name"]
        if e.get("frog_knight_has_beetle_charged") is not None:
            enemy.FrogKnightHasBeetleCharged = bool(e["frog_knight_has_beetle_charged"])
        if e.get("waterfall_giant_current_pressure_gun_damage") is not None:
            enemy.WaterfallGiantCurrentPressureGunDamage = int(e["waterfall_giant_current_pressure_gun_damage"])
        enemy.Powers = _power_stacks(types, e.get("powers"))
        enemies.Add(enemy)
    scenario.Enemies = enemies

    scenario.Seed = int(spec.get("seed", 1))
    return scenario


class BattleEmulator:
    """Wraps the shared GameInstance; see module docstring for why this is stateless."""

    def __init__(self, repo_root: Path | None = None, use_legal_action_cache: bool = True):
        ensure_loaded(repo_root)
        self._repo_root = repo_root
        # Off switch exists only for controlled A/B/C performance comparisons - always
        # leave this True in normal use, it is an exact (non-approximating) cache.
        self.use_legal_action_cache = use_legal_action_cache

    def _game(self):
        return shared_game_instance(self._repo_root)

    def _wrap(
        self,
        obs,
        turn: int,
        enemy_max_hps: dict,
        shuffle_rng_seed: "int | None" = None,
        legal_actions: "list[dict] | None" = None,
    ) -> BattleState:
        # EnemyScenario has no MaxHp field (only Hp) - every ResetFromScenario therefore
        # treats whatever Hp we pass as that enemy's fresh 100%, so the engine's own
        # maxHp "chases" current hp down at every restore apply_action does internally.
        # enemy_hp itself stays correct throughout (verified experimentally) - only the
        # ratio-to-max is affected - so the true max, captured once at initialize() from
        # the real starting hp, is tracked here in Python and used to patch maxHp back to
        # its real value before this state is handed to anything that reads it.
        # Keyed by the engine's stable per-instance "index" (added to state["enemies"][i]
        # in the 2026-07-20 build - see Common/schemas/combat_state_schema.json), NOT
        # "id" (the shared monster-type id) - confirmed live that keying by "id" corrupts
        # maxHp for every enemy of a duplicated species down to whichever one happened to
        # be last in dict-construction order (e.g. three same-species enemies at hp
        # 48/48/5 all read back maxHp=5) - fixed here, was a real bug in this file, not an
        # Emulator issue.
        # NOTE, CONFIRMED AGAINST ENGINE SOURCE (not just observed): "index" recompacts
        # whenever any enemy dies - GameInstance.cs's own doc comment on BuildEnemiesDict
        # claims otherwise ("...even once some enemies have died... this 'index' field
        # does not [shift]"), but CombatState.cs physically removes a dead creature from
        # the same list BuildEnemiesDict enumerates (`_enemies.Remove(creature)` at
        # CombatState.cs:289,513 - not merely an isAlive=false flag), so every enemy
        # after the removed one shifts down by one position immediately - confirmed this
        # happens WITHIN a single apply_action()/Step() call, not only across separate
        # restore boundaries (see Combat/tests/test_scenario_v2.py's
        # test_target_tracking_after_death for the live repro). Flagged as a discrepancy
        # for the Emulator team, not something patched around here.
        #
        # Practical consequence: "index" reliably disambiguates enemies and resolves a
        # target choice ONLY within one get_legal_actions()->apply_action() round trip.
        # It does NOT survive being cached across multiple apply_action() calls once ANY
        # enemy (not just the tracked one) has died in between - re-resolve which index
        # corresponds to which physical enemy from the most recent observation every
        # time (e.g. by HP/intent/stateLog continuity), never assume persistence.
        state = to_plain(obs.State)
        for enemy in state.get("enemies") or []:
            true_max = enemy_max_hps.get(enemy.get("index"))
            if true_max is not None:
                enemy["maxHp"] = true_max
        normalized_terminal, normalized_outcome = coerce_terminal_observation(
            state,
            bool(obs.IsTerminal),
            str(obs.Outcome),
        )
        return BattleState(
            engine_state=state,
            is_terminal=normalized_terminal,
            outcome=normalized_outcome,
            turn=turn,
            enemy_max_hps=enemy_max_hps,
            shuffle_rng_seed=shuffle_rng_seed,
            _cached_legal_actions=legal_actions,
            decision_frame=DecisionFrame(
                combat_session_id=getattr(obs, "CombatSessionId", None),
                step_index=int(getattr(obs, "StepIndex", 0) or 0),
            ),
        )

    def with_shuffle_seed(self, battle_state: BattleState, shuffle_rng_seed: "int | None") -> BattleState:
        """Forks `battle_state` onto a pinned reshuffle-RNG hypothesis (or clears it with
        None) without otherwise touching it - used by LookaheadSearcher to generate K
        independent future-draw hypotheses from the same starting position (see
        lookahead.py). The seed only affects mid-combat reshuffles, so it has no visible
        effect until whatever hand/draw pile this state carries actually runs dry.
        """
        import copy

        return BattleState(
            engine_state=copy.deepcopy(battle_state.engine_state),
            is_terminal=battle_state.is_terminal,
            outcome=battle_state.outcome,
            turn=battle_state.turn,
            enemy_max_hps=battle_state.enemy_max_hps,
            shuffle_rng_seed=shuffle_rng_seed,
            # legal actions depend only on the board itself, not which reshuffle hypothesis
            # it's pinned to (that only affects FUTURE reshuffles) - safe to carry over.
            _cached_legal_actions=battle_state._cached_legal_actions,
            decision_frame=battle_state.decision_frame,
        )

    def initialize(self, scenario_spec: dict) -> BattleState:
        # turnNumber always resets to 1 under ResetFromScenario, since CombatScenario has
        # no way to carry a persisted turn count (verified experimentally) - so cumulative
        # turn count is tracked here in Python instead of trusted from engine_state.
        scenario = build_scenario_from_spec(scenario_spec)
        reset_result = self._game().ResetFromScenario(scenario)
        obs = reset_result.Observation
        enemy_max_hps = {e["index"]: e["maxHp"] for e in to_plain(obs.State).get("enemies") or []}
        legal_actions = legal_actions_to_list(reset_result.LegalActions)
        return self._wrap(obs, turn=1, enemy_max_hps=enemy_max_hps, legal_actions=legal_actions)

    def get_state(self, battle_state: BattleState) -> dict:
        return battle_state.engine_state

    def clone_state(self, battle_state: BattleState) -> BattleState:
        import copy

        return BattleState(
            engine_state=copy.deepcopy(battle_state.engine_state),
            is_terminal=battle_state.is_terminal,
            outcome=battle_state.outcome,
            turn=battle_state.turn,
            enemy_max_hps=battle_state.enemy_max_hps,
            shuffle_rng_seed=battle_state.shuffle_rng_seed,
            _cached_legal_actions=battle_state._cached_legal_actions,
            decision_frame=battle_state.decision_frame,
        )

    def _restore(self, battle_state: BattleState):
        scenario = build_scenario_from_state(battle_state.engine_state, battle_state.shuffle_rng_seed)
        game = self._game()
        game.ResetFromScenario(scenario)
        return game

    def wrap_live_state(
        self,
        obs,
        turn: int,
        enemy_max_hps: dict,
        shuffle_rng_seed: "int | None" = None,
        legal_actions: "list[dict] | None" = None,
    ) -> BattleState:
        return self._wrap(
            obs,
            turn=turn,
            enemy_max_hps=enemy_max_hps,
            shuffle_rng_seed=shuffle_rng_seed,
            legal_actions=legal_actions,
        )

    def step_live_action(
        self,
        game,
        battle_state: BattleState,
        action: dict,
        target_index: int | None = None,
        target_enemy_index: int | None = None,
    ) -> BattleState:
        result = game.Step(action["action_id"])

        legal_after = list(result.LegalActions)
        if legal_after and legal_after[0].ActionType == "choice_target":
            if target_enemy_index is not None:
                match = next(
                    (a for a in legal_after if to_plain(a.Parameters).get("enemyIndex") == target_enemy_index),
                    None,
                )
                if match is None:
                    offered = [to_plain(a.Parameters).get("enemyIndex") for a in legal_after]
                    raise ValueError(
                        f"target_enemy_index={target_enemy_index} not among currently targetable "
                        f"enemies (offered enemyIndex values: {offered}) - it may have already died"
                    )
                result = game.Step(match.ActionId)
            else:
                idx = 0 if target_index is None else max(0, min(target_index, len(legal_after) - 1))
                result = game.Step(legal_after[idx].ActionId)

        next_turn = battle_state.turn + 1 if action["action_type"] == "system" else battle_state.turn

        # Emulator commit dd8c800 ("Separate combat completion result from current Boundary
        # in StepResult") added StepResult.Transition (TransitionOutcome), non-null exactly on
        # the Step that watched combat go from in-progress to concluded. This is now the
        # authoritative Combat-end signal (contract: Transition.Kind == "combat_completed"),
        # rather than relying only on Observation.IsTerminal. FinalObservation is the
        # snapshot captured before _combatState was cleared/post-combat processing ran - the
        # correct "final combat state" to wrap, since in full-run mode (not used by this
        # legacy no-map ResetFromScenario path today, but kept mode-agnostic for forward
        # compatibility) result.Observation would already describe the NEXT settled state
        # (reward/map), not the finished combat.
        transition = result.Transition
        if transition is not None and str(transition.Kind) == "combat_completed":
            # Legacy no-map mode's existing contract (ComputeIsRunOver: !HasMap => combat
            # concluded == run over) means Observation.IsTerminal must already be True here -
            # this is a debug consistency check, not new terminal-detection logic; a mismatch
            # would mean this translation and the Emulator's own legacy-mode contract have
            # diverged, which existing Search/Main Loop/Shadow/endurance code implicitly
            # depends on via obs.IsTerminal elsewhere in this file.
            assert bool(result.Observation.IsTerminal), (
                "StepResult.Transition.Kind == 'combat_completed' but "
                "StepResult.Observation.IsTerminal is False - legacy no-map mode's "
                "run_terminal-on-combat-conclusion contract no longer holds."
            )
            return self._wrap(
                transition.FinalObservation,
                turn=next_turn,
                enemy_max_hps=battle_state.enemy_max_hps,
                shuffle_rng_seed=battle_state.shuffle_rng_seed,
                legal_actions=legal_actions_to_list(result.LegalActions),
            )

        return self._wrap(
            result.Observation,
            turn=next_turn,
            enemy_max_hps=battle_state.enemy_max_hps,
            shuffle_rng_seed=battle_state.shuffle_rng_seed,
            legal_actions=legal_actions_to_list(result.LegalActions),
        )

    def _best_continuation_card_action(
        self,
        battle_state: BattleState,
        pending_choice: dict,
        card_actions: list[dict],
    ) -> dict:
        option_cards = list(pending_choice.get("options") or [])
        play_pile_ids = {c.get("id") for c in (battle_state.engine_state.get("playPile") or [])}
        best_action = card_actions[0]
        best_score = None
        for idx, action in enumerate(card_actions):
            option = option_cards[idx] if idx < len(option_cards) else {"id": action["label"]}
            score = 0.0
            rarity = option.get("rarity")
            card_type = option.get("type")
            cost = option.get("cost")
            card_id = option.get("id")
            if card_id in play_pile_ids:
                score -= 50.0
            if rarity == "Rare":
                score += 4.0
            elif rarity == "Uncommon":
                score += 2.0
            elif rarity == "Common":
                score += 1.0
            if card_type == "Curse":
                score -= 100.0
            elif card_type == "Status":
                score -= 50.0
            elif card_type == "Attack":
                score += 1.5
            elif card_type == "Skill":
                score += 1.0
            elif card_type == "Power":
                score += 0.5
            if isinstance(cost, int):
                score += max(0.0, 3.0 - float(cost))
            if best_score is None or score > best_score:
                best_score = score
                best_action = action
        return best_action

    def _default_choose_action_continuation_live(
        self,
        game,
        battle_state: BattleState,
        legal_actions: list[dict],
        deadline: "float | None" = None,
    ) -> dict:
        _ = game
        _ = deadline
        if not legal_actions:
            raise RuntimeError("ActionContinuation exposed no legal actions.")
        pending = battle_state.engine_state.get("pendingChoice") or {}
        selected_count = int(pending.get("selectedCount") or 0)
        min_select = int(pending.get("minSelect") or 0)
        max_select = pending.get("maxSelect")

        confirm = next((a for a in legal_actions if a["action_type"] == "choice_confirm"), None)
        skip = next((a for a in legal_actions if a["action_type"] == "choice_skip"), None)
        cards = [a for a in legal_actions if a["action_type"] == "choice_card"]

        if not cards:
            return confirm or skip or legal_actions[0]
        if selected_count < min_select:
            return self._best_continuation_card_action(battle_state, pending, cards)
        if max_select is not None and selected_count >= int(max_select):
            return confirm or skip or cards[0]
        if pending.get("choiceType") == "WishDrawToHand":
            return self._best_continuation_card_action(battle_state, pending, cards)
        if cards:
            return self._best_continuation_card_action(battle_state, pending, cards)
        return skip or confirm or legal_actions[0]

    def enumerate_legal_actions(self, battle_state: BattleState) -> list[dict]:
        if battle_state.is_terminal:
            return []
        # ActionContinuation states cannot be reconstructed independently; their cached
        # LegalActions must be consumed on the same live GameInstance that produced them.
        # For ordinary non-continuation states, prefer a fresh GetLegalActions() over the
        # cached StepResult list: we have live cases where StepResult.LegalActions after a
        # complex card play is stale, while a fresh restore+GetLegalActions() returns the
        # correct post-step action set (e.g. only End Turn remaining).
        if (
            self.use_legal_action_cache
            and battle_state._cached_legal_actions is not None
            and is_action_continuation_pending_choice(battle_state.engine_state)
        ):
            return battle_state._cached_legal_actions
        game = self._restore(battle_state)
        legal_actions = legal_actions_to_list(game.GetLegalActions())
        if self.use_legal_action_cache:
            battle_state._cached_legal_actions = legal_actions
        return legal_actions

    def apply_action(
        self,
        battle_state: BattleState,
        action: dict,
        target_index: int | None = None,
        target_enemy_index: int | None = None,
        continuation_resolver=None,
        continuation_deadline: "float | None" = None,
        live_game=None,
    ) -> BattleState:
        """Restores `battle_state`, plays `action` (a dict from enumerate_legal_actions),
        resolves an AnyEnemy target choice if one comes up, and returns the resulting
        BattleState.

        Two ways to pick the target when one is needed - prefer `target_enemy_index`:

        - `target_enemy_index`: the STABLE `state["enemies"][i]["index"]` value of the
          enemy to target (matched against each choice_target candidate's
          `Parameters["enemyIndex"]` - see legal_action_schema.json). Correct regardless
          of enemy deaths or duplicate monster types. Raises `ValueError` if no candidate
          has this enemyIndex (e.g. that enemy has since died) rather than silently
          falling back to something else.
        - `target_index`: position within the *currently offered* choice_target
          candidate list (0 = first alive candidate in engine order), default 0 if
          neither is given. Kept for existing callers (beam_search.py/lookahead.py use
          target_candidates()'s position-based range) - does not survive enemies dying
          between decision and action the way target_enemy_index does.

        `target_enemy_index` takes precedence if both are given.
        """
        if battle_state.is_terminal:
            raise RuntimeError("Cannot apply an action to a terminal BattleState.")
        if live_game is not None:
            game = live_game
        elif is_action_continuation_pending_choice(battle_state.engine_state):
            raise RuntimeError(
                "ActionContinuation must be resolved on the same live GameInstance; "
                "call apply_action() on the original action with a continuation resolver, "
                "or pass live_game from that in-progress session."
            )
        else:
            game = self._restore(battle_state)
        next_state = self.step_live_action(
            game,
            battle_state,
            action,
            target_index=target_index,
            target_enemy_index=target_enemy_index,
        )

        continuation_steps = 0
        resolver = continuation_resolver or self._default_choose_action_continuation_live
        while is_action_continuation_pending_choice(next_state.engine_state):
            continuation_steps += 1
            if continuation_steps > 50:
                raise RuntimeError("ActionContinuation did not resolve within 50 continuation steps.")
            legal = self.enumerate_legal_actions(next_state)
            continuation_action = resolver(game, next_state, legal, continuation_deadline)
            next_state = self.step_live_action(game, next_state, continuation_action)

        return next_state

    def process_enemy_turn(self, battle_state: BattleState) -> BattleState:
        """No-op: Step() already runs the engine through the full enemy turn as part of
        apply_action() when the played action is End Turn. Kept only so callers that
        follow the design doc's simulate_battle() shape have a symmetric call to make.
        """
        return battle_state

    def is_terminal(self, battle_state: BattleState) -> bool:
        return battle_state.is_terminal

    def target_candidates(self, battle_state: BattleState, action: dict) -> list[int | None]:
        """Which target_index values are worth trying for `action` from `battle_state`.

        Only "AnyEnemy"-targeted card/potion plays with 2+ enemies alive need a choice;
        everything else resolves with a single implicit target. Assumes the choice_target
        list apply_action() resolves internally is offered in the same order as
        `engine_state["enemies"]" - true for every scenario exercised so far, but not
        verified against arbitrarily large enemy counts.
        """
        if action["action_type"] not in ("card", "potion"):
            return [None]
        if action["parameters"].get("targetType") != "AnyEnemy":
            return [None]
        alive = [e for e in battle_state.engine_state.get("enemies") or [] if e.get("isAlive", True)]
        if len(alive) <= 1:
            return [None]
        return list(range(len(alive)))

    def get_result(self, battle_state: BattleState, potion_table=None) -> BattleResult:
        state = battle_state.engine_state
        enemies = state.get("enemies") or []
        max_hp_total = sum(max(1, e.get("maxHp", 1)) for e in enemies) or 1
        hp_total = sum(max(0, e.get("hp", 0)) for e in enemies)
        potions = [p for p in (state.get("potions") or []) if p]
        remaining_potion_value = (
            potion_table.get_remaining_potion_value(state) if potion_table else 0.0
        )
        return BattleResult(
            win=battle_state.outcome == "victory",
            remaining_hp=int(state.get("hp", 0)),
            remaining_potions=len(potions),
            remaining_potion_value=remaining_potion_value,
            enemy_hp_removed_ratio=1.0 - (hp_total / max_hp_total),
            turn_count=battle_state.turn,
        )