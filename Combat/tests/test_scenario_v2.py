"""Live-emulator tests for the 2026-07-20 15:49 Emulator build's new CombatScenario
surface (card upgrade state, potions, nullable HP, stable enemy index) and this
project's CombatEnv/scenario_from_runs.py adaptation to it.

No pytest dependency (not installed in this environment) - a plain assertion-based
runner. Requires a live GameInstance (CoreCLR bootstrap, ~1-2s) - run standalone:
`python test_scenario_v2.py`. All tests share one BattleEmulator/CombatEnv instance and
run in one process (bootstrap cost paid once), consistent with the one-GameInstance-
per-process constraint documented in battle_emulator.py's module docstring.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "env"))

from battle_emulator import BattleEmulator, battle_state_key  # noqa: E402
from battle_emulator import coerce_terminal_observation, state_has_living_enemies  # noqa: E402
from battle_emulator import is_action_continuation_pending_choice  # noqa: E402
from combat_env import CombatEnv  # noqa: E402
from legacy.heuristic_agent import HeuristicAgent, is_non_terminal_self_loop  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402
from run_trajectory_batch import load_fixed_50  # noqa: E402
from legacy.state_evaluator import DEFAULT_WEIGHTS, StateEvaluator  # noqa: E402
from slot_name_inference import ensure_inferred_slot_names  # noqa: E402
from mad_science_restore import mad_science_state_from_event_choices, mad_science_state_from_props  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402


def test_battle_state_key_distinguishes_card_cost_and_potion_slots():
    base_state = {
        "seed": 1,
        "hp": 80,
        "maxHp": 80,
        "block": 20,
        "energy": 0,
        "stars": 0,
        "potions": [None, {"id": "TOUCH_OF_INSANITY", "rarity": "Uncommon", "targetType": "AnyPlayer"}, None],
        "hand": [{"id": "GLOW", "type": "Skill", "rarity": "Common", "cost": 1, "targetType": "Self", "upgraded": False, "upgradeLevel": 0, "tinkerTimeType": None, "tinkerTimeRider": None}],
        "drawPile": [],
        "discardPile": [],
        "exhaustPile": [],
        "playPile": [],
        "playerPowers": [{"id": "DEXTERITY_POWER", "amount": 1, "type": "Buff"}],
        "orbSlots": 0,
        "orbs": [],
        "pendingChoice": None,
        "enemies": [{"id": "DEVOTED_SCULPTOR", "hp": 157, "maxHp": 172, "block": 0, "isAlive": True, "intent": {"stateId": "FORBIDDEN_INCANTATION_MOVE"}, "powers": [], "slotName": None, "stateLog": []}],
        "relics": [{"id": "ANCHOR"}],
    }
    key_before = battle_state_key(CombatEnv().battle_state if False else type("StubState", (), {"engine_state": base_state, "turn": 1, "shuffle_rng_seed": None})())

    after_state = {
        **base_state,
        "potions": [None, None, None],
        "hand": [{**base_state["hand"][0], "cost": 0}],
    }
    key_after = battle_state_key(type("StubState", (), {"engine_state": after_state, "turn": 1, "shuffle_rng_seed": None})())
    assert key_before != key_after, (key_before, key_after)


def test_battle_state_key_distinguishes_enchantment():
    """Two otherwise-identical states differing only by a card's enchantment (e.g.
    Sharp's extra damage) must never hash the same - a search tree collapsing them
    into one node would silently discard a genuinely different future."""
    base_state = {
        "seed": 1,
        "hp": 80,
        "maxHp": 80,
        "block": 20,
        "energy": 0,
        "stars": 0,
        "potions": [None, None, None],
        "hand": [{"id": "STRIKE_IRONCLAD", "type": "Attack", "rarity": "Basic", "cost": 1, "targetType": "AnyPlayer", "upgraded": False, "upgradeLevel": 0, "tinkerTimeType": None, "tinkerTimeRider": None, "enchantment": None}],
        "drawPile": [],
        "discardPile": [],
        "exhaustPile": [],
        "playPile": [],
        "playerPowers": [],
        "orbSlots": 0,
        "orbs": [],
        "pendingChoice": None,
        "enemies": [{"id": "DEVOTED_SCULPTOR", "hp": 157, "maxHp": 172, "block": 0, "isAlive": True, "intent": {"stateId": "FORBIDDEN_INCANTATION_MOVE"}, "powers": [], "slotName": None, "stateLog": []}],
        "relics": [{"id": "ANCHOR"}],
    }
    key_unenchanted = battle_state_key(type("StubState", (), {"engine_state": base_state, "turn": 1, "shuffle_rng_seed": None})())

    enchanted_state = {
        **base_state,
        "hand": [{**base_state["hand"][0], "enchantment": {"id": "SHARP", "amount": 3, "status": "Active"}}],
    }
    key_enchanted = battle_state_key(type("StubState", (), {"engine_state": enchanted_state, "turn": 1, "shuffle_rng_seed": None})())
    assert key_unenchanted != key_enchanted, (key_unenchanted, key_enchanted)

    different_amount_state = {
        **base_state,
        "hand": [{**base_state["hand"][0], "enchantment": {"id": "SHARP", "amount": 5, "status": "Active"}}],
    }
    key_different_amount = battle_state_key(type("StubState", (), {"engine_state": different_amount_state, "turn": 1, "shuffle_rng_seed": None})())
    assert key_enchanted != key_different_amount, (key_enchanted, key_different_amount)


def test_state_has_living_enemies_and_terminal_coercion():
    state = {
        "hp": 18,
        "enemies": [
            {"id": "TEST_SUBJECT", "hp": 0, "maxHp": 111, "isAlive": False},
        ],
    }
    assert not state_has_living_enemies(state), state
    is_terminal, outcome = coerce_terminal_observation(state, False, "in_progress")
    assert is_terminal is True, (is_terminal, outcome)
    assert outcome == "victory", outcome


def test_is_non_terminal_self_loop_detects_unchanged_state():
    state = {
        "seed": 1,
        "hp": 80,
        "maxHp": 80,
        "block": 0,
        "energy": 1,
        "stars": 0,
        "potions": [None, None, None],
        "hand": [],
        "drawPile": [],
        "discardPile": [],
        "exhaustPile": [],
        "playPile": [],
        "playerPowers": [],
        "orbSlots": 0,
        "orbs": [],
        "pendingChoice": None,
        "enemies": [{"id": "CALCIFIED_CULTIST", "hp": 48, "maxHp": 48, "block": 0, "isAlive": True, "intent": {"stateId": "INCANTATION_MOVE"}, "powers": [], "slotName": None, "stateLog": []}],
        "relics": [],
    }
    before = type("StubState", (), {"engine_state": state, "turn": 1, "shuffle_rng_seed": None, "is_terminal": False})()
    after = type("StubState", (), {"engine_state": dict(state), "turn": 1, "shuffle_rng_seed": None, "is_terminal": False})()
    assert is_non_terminal_self_loop(before, after) is True


def test_choose_action_with_detail_penalizes_return_to_seen_state():
    before_state = {
        "seed": 1,
        "hp": 80,
        "maxHp": 80,
        "block": 0,
        "energy": 1,
        "stars": 0,
        "potions": [None, None, None],
        "hand": [],
        "drawPile": [],
        "discardPile": [],
        "exhaustPile": [],
        "playPile": [],
        "playerPowers": [],
        "orbSlots": 0,
        "orbs": [],
        "pendingChoice": None,
        "enemies": [{"id": "CALCIFIED_CULTIST", "hp": 48, "maxHp": 48, "block": 0, "isAlive": True, "intent": {"stateId": "INCANTATION_MOVE"}, "powers": [], "slotName": None, "stateLog": []}],
        "relics": [],
    }
    loop_state = dict(before_state)
    progress_state = {**before_state, "block": 8}
    battle_state = type(
        "StubState",
        (),
        {
            "engine_state": before_state,
            "turn": 1,
            "shuffle_rng_seed": None,
            "is_terminal": False,
        },
    )()
    loop_result = type("StubState", (), {"engine_state": loop_state, "turn": 1, "shuffle_rng_seed": None, "is_terminal": False, "outcome": "in_progress"})()
    progress_result = type("StubState", (), {"engine_state": progress_state, "turn": 1, "shuffle_rng_seed": None, "is_terminal": False, "outcome": "in_progress"})()

    class StubEmulator:
        def enumerate_legal_actions(self, _battle_state):
            return [
                {"action_id": 0, "action_type": "system", "label": "Loop", "parameters": {}},
                {"action_id": 1, "action_type": "system", "label": "Progress", "parameters": {}},
            ]

        def target_candidates(self, _battle_state, _action):
            return [None]

        def apply_action(self, _battle_state, action, target_index=None, continuation_resolver=None, continuation_deadline=None):
            _ = target_index, continuation_resolver, continuation_deadline
            return loop_result if action["label"] == "Loop" else progress_result

    agent = HeuristicAgent(StubEmulator(), StateEvaluator(), dict(DEFAULT_WEIGHTS))
    chosen, details = agent.choose_action_with_detail(
        battle_state,
        historical_state_keys={battle_state_key(loop_result)},
    )
    loop_detail = next(d for d in details if d["label"] == "Loop")
    progress_detail = next(d for d in details if d["label"] == "Progress")
    assert loop_detail.get("historical_state_loop_penalty") is True, loop_detail
    assert progress_detail.get("historical_state_loop_penalty") is None, progress_detail
    assert chosen.label == "Progress", (chosen, details)


def test_enumerate_legal_actions_refreshes_non_continuation_cache():
    emu = BattleEmulator()
    sentinel = [{"action_id": 999, "action_type": "system", "label": "STALE", "parameters": {}}]
    fresh = [{"action_id": 0, "action_type": "system", "label": "End Turn", "parameters": {}}]
    restore_calls = {"count": 0}

    battle_state = type(
        "StubState",
        (),
        {
            "engine_state": {"pendingChoice": None},
            "is_terminal": False,
            "_cached_legal_actions": sentinel,
        },
    )()

    original_restore = emu._restore  # noqa: SLF001
    try:
        emu._restore = lambda _state: type("StubGame", (), {"GetLegalActions": lambda self: []})()  # noqa: SLF001
        from battle_emulator import legal_actions_to_list as original_to_list
        import battle_emulator as battle_emulator_module

        def fake_restore(_state):
            restore_calls["count"] += 1
            return type("StubGame", (), {"GetLegalActions": lambda self: object()})()

        def fake_to_list(_legal_actions):
            return fresh

        emu._restore = fake_restore  # noqa: SLF001
        battle_emulator_module.legal_actions_to_list = fake_to_list
        got = emu.enumerate_legal_actions(battle_state)
        assert got == fresh, got
        assert restore_calls["count"] == 1, restore_calls
    finally:
        emu._restore = original_restore  # noqa: SLF001
        battle_emulator_module.legal_actions_to_list = original_to_list


def test_upgrade_and_potions_survive_apply_action_restore():
    """Regression test for a real bug found via generate_heuristic_trajectories.py:
    build_scenario_from_state() (used by every BattleEmulator.apply_action() restore -
    i.e. every single simulated step in beam search/lookahead/HeuristicAgent's own
    candidate scoring, not just real committed steps) used to silently drop card
    upgrade state and ALL potions on every restore, even though
    build_scenario_from_spec() (used only once, at initialize()) already carried both
    correctly. Surfaced as an engine-side 'Illegal action' InvalidOperationException
    when a potion listed in legal_actions (captured before restore) no longer existed
    after restore. Fixed in battle_emulator.py's build_scenario_from_state; this test
    pins the fix by round-tripping through apply_action() (not just initialize())."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [{"card_id": "STRIKE_IRONCLAD", "is_upgraded": True}],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [],
        "potions": [{"slot": 0, "potion_id": "FIRE_POTION"}],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    legal = emu.enumerate_legal_actions(state)
    end_turn = next(a for a in legal if a["action_type"] == "system")

    # apply_action() always restores via build_scenario_from_state() internally, even
    # for an action as simple as End Turn - this alone is enough to trigger the bug.
    state2 = emu.apply_action(state, end_turn)
    assert state2.engine_state["hand"] == [] or all(
        c["upgraded"] for c in state2.engine_state["hand"] if c["id"] == "STRIKE_IRONCLAD"
    ), state2.engine_state["hand"]
    potions_after = state2.engine_state["potions"]
    assert potions_after[0] is not None and potions_after[0]["id"] == "FIRE_POTION", potions_after

    # The potion action must still be legal post-restore (this is what actually broke -
    # a stale legal_actions list referencing a potion the restored state had silently lost).
    legal2 = emu.enumerate_legal_actions(state2)
    assert any(a["action_type"] == "potion" for a in legal2), legal2

    # And actually playing it must not raise "Illegal action" from the engine.
    potion_action = next(a for a in legal2 if a["action_type"] == "potion")
    emu.apply_action(state2, potion_action)


def test_relic_internal_counter_is_lost_on_fresh_scenario_reconstruction():
    """Known gap (part of the same "hard mid-combat state reconstruction" theme as
    sushisk/STS2_Emulator#8/STS2_RL#43/#44): unlike PlayerPowers, which supports
    PowerStack.DynamicVars to restore a power's own internal counter (e.g.
    WitheringPresencePower's CardsLeft), CombatScenario.Relics is just a plain relic-id
    list granted fresh via RelicCmd.Obtain - there is no structured equivalent for a
    relic's own internal state.

    Nunchaku is a concrete instance: its public stackCount is always 1, but it separately
    tracks a private [SavedProperty] AttacksPlayed counter (attacks played since its last
    energy-grant trigger, surfaced read-only via displayAmount). A scenario authored from
    a real mid-combat state (e.g. by scenario_harvest.py) where 3 of the next 10 attacks
    have already been played has no field to carry that "3" across - reconstructing via
    CombatScenario always starts the relic fresh at 0, unlike the card-instance case
    (upgrade_level/enchantment) already fixed earlier this session.

    The ground-truth "3" is produced via LiveCombatSession.step() directly (real gameplay,
    no restore in the loop - see the companion test below for what happens when the
    restore-based BattleEmulator.apply_action() path is used instead, a related but
    distinct and more severe bug). Then reconstruction goes through
    BattleEmulator.initialize() with only a plain relics list, exactly what
    scenario_harvest.py can express, and the counter is confirmed lost - not yet fixed,
    unlike upgrade_level/enchantment."""
    session = LiveCombatSession()
    source_spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"] * 3, "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["NUNCHAKU"], "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999}],
    }
    source_state = session.start_combat(source_spec)
    for _ in range(3):
        strike = next(
            a for a in source_state._cached_legal_actions  # noqa: SLF001
            if a["action_type"] == "card" and a["parameters"].get("cardId") == "STRIKE_IRONCLAD"
        )
        source_state = session.step(source_state, strike)
    source_nunchaku = next(r for r in source_state.engine_state["relics"] if r["id"] == "NUNCHAKU")
    assert source_nunchaku["displayAmount"] == 3, source_nunchaku

    # Training's only available carrier for "this combat already has NUNCHAKU" is the
    # plain relic-id list - reconstruct exactly that way, same as scenario_harvest.py would.
    emu = BattleEmulator()
    reconstructed_spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["NUNCHAKU"], "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999}],
    }
    reconstructed_state = emu.initialize(reconstructed_spec)
    reconstructed_nunchaku = next(
        r for r in reconstructed_state.engine_state["relics"] if r["id"] == "NUNCHAKU"
    )

    # BUG (known, not yet fixed): the internal counter did not survive reconstruction.
    assert reconstructed_nunchaku["displayAmount"] == 0, reconstructed_nunchaku
    assert reconstructed_nunchaku["displayAmount"] != source_nunchaku["displayAmount"]


def test_relic_internal_counter_is_lost_on_every_apply_action_restore_step():
    """More severe sibling of the test above, found while writing it: this isn't only a
    "Training authors a fresh scenario" problem. BattleEmulator.apply_action() restores via
    build_scenario_from_state() on every single call (see
    test_upgrade_and_potions_survive_apply_action_restore's docstring - this is the path
    every simulated step in beam search/lookahead/HeuristicAgent scoring uses, not just
    real committed steps), and build_scenario_from_state() has the exact same
    relics-are-a-plain-id-list limitation (battle_emulator.py: `scenario.Relics =
    str_list([r["id"] for r in engine_state.get("relics")])` - id only).

    So a relic like Nunchaku never correctly accumulates its internal counter across
    simulated steps at all: each apply_action() silently resets it to 0 before executing
    that one step, and only that step's own contribution survives into the next
    engine_state. After 3 real Strikes played one apply_action() at a time, the counter
    ends at 1 (only the last restore-then-play's own increment), not 3 - proven by
    contrast with the companion test above, where the same 3 Strikes via
    LiveCombatSession.step() (no restore in the loop) correctly reach 3."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [{"card_id": "STRIKE_IRONCLAD"}] * 3,
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["NUNCHAKU"], "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999}],
    }
    state = emu.initialize(spec)
    for _ in range(3):
        legal = emu.enumerate_legal_actions(state)
        strike = next(
            a for a in legal
            if a["action_type"] == "card" and a["parameters"].get("cardId") == "STRIKE_IRONCLAD"
        )
        state = emu.apply_action(state, strike)
    nunchaku = next(r for r in state.engine_state["relics"] if r["id"] == "NUNCHAKU")

    # BUG (known, not yet fixed): 3 real attacks played, but the restore-every-step path
    # only ever sees "1 attack played since the last restore" at a time.
    assert nunchaku["displayAmount"] == 1, nunchaku


def test_stars_survive_apply_action_restore():
    """Regent Stars must round-trip through initialize() and apply_action() restore."""
    emu = BattleEmulator()
    spec = {
        "character_id": "REGENT", "player_hp": None, "player_max_hp": None,
        "stars": 5,
        "hand": ["DEFEND_REGENT"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    assert state.engine_state["stars"] == 5, state.engine_state
    legal = emu.enumerate_legal_actions(state)
    defend = next(a for a in legal if a["action_type"] == "card" and a["label"] == "DEFEND_REGENT")

    state2 = emu.apply_action(state, defend)
    assert state2.engine_state["stars"] == 5, state2.engine_state


def test_orbs_survive_restore_with_order_and_mutable_values():
    """OrbSlots/Orbs must survive restore with order preserved and mutable values intact."""
    emu = BattleEmulator()
    spec = {
        "character_id": "DEFECT", "player_hp": 75, "player_max_hp": 75,
        "hand": ["DEFEND_DEFECT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "orb_slots": 4,
        "orbs": [
            {"orb_id": "DARK_ORB", "base_evoke_value": 26},
            {"orb_id": "GLASS_ORB", "base_passive_value": 7},
            {"orb_id": "LIGHTNING_ORB"},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    assert state.engine_state["orbSlots"] == 4, state.engine_state
    assert [(o["id"], o["basePassiveValue"], o["baseEvokeValue"]) for o in state.engine_state["orbs"]] == [
        ("DARK_ORB", None, state.engine_state["orbs"][0]["baseEvokeValue"]),
        ("GLASS_ORB", state.engine_state["orbs"][1]["basePassiveValue"], None),
        ("LIGHTNING_ORB", None, None),
    ], state.engine_state["orbs"]
    restored = emu._restore(state)  # noqa: SLF001 - white-box restore round-trip
    from emulator_bridge import to_plain
    restored_state = to_plain(restored.GetObservation().State)
    assert restored_state["orbSlots"] == state.engine_state["orbSlots"], restored_state
    assert restored_state["orbs"] == state.engine_state["orbs"], (restored_state["orbs"], state.engine_state["orbs"])


def test_mad_science_dynamic_state_survives_restore():
    """MAD_SCIENCE's new per-instance fields must round-trip through restore-based Step()."""
    emu = BattleEmulator()
    spec = {
        "character_id": "DEFECT", "player_hp": 63, "player_max_hp": 63,
        "hand_cards": [{"card_id": "MAD_SCIENCE", "is_upgraded": False, "tinker_time_type": "Power", "tinker_time_rider": "Wisdom"}],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    hand = state.engine_state["hand"]
    assert hand[0]["id"] == "MAD_SCIENCE", hand
    assert hand[0].get("tinkerTimeType") == "Power", hand[0]
    assert hand[0].get("tinkerTimeRider") == "Wisdom", hand[0]
    legal = emu.enumerate_legal_actions(state)
    mad_science = next(a for a in legal if a["label"] == "MAD_SCIENCE")
    next_state = emu.apply_action(state, mad_science)
    assert next_state.engine_state["stepIndex"] >= state.engine_state["stepIndex"] + 1, next_state.engine_state


def test_mad_science_tinker_time_mapping_all_branches():
    cases = [
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 1}, {"name": "TinkerTimeRider", "value": 1}]}}, ("Attack", "Sapping")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 1}, {"name": "TinkerTimeRider", "value": 2}]}}, ("Attack", "Violence")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 1}, {"name": "TinkerTimeRider", "value": 3}]}}, ("Attack", "Choking")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 2}, {"name": "TinkerTimeRider", "value": 4}]}}, ("Skill", "Energized")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 2}, {"name": "TinkerTimeRider", "value": 5}]}}, ("Skill", "Wisdom")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 2}, {"name": "TinkerTimeRider", "value": 6}]}}, ("Skill", "Chaos")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 3}, {"name": "TinkerTimeRider", "value": 7}]}}, ("Power", "Expertise")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 3}, {"name": "TinkerTimeRider", "value": 8}]}}, ("Power", "Curious")),
        ({"props": {"ints": [{"name": "TinkerTimeType", "value": 3}, {"name": "TinkerTimeRider", "value": 9}]}}, ("Power", "Improvement")),
    ]
    for payload, expected in cases:
        state = mad_science_state_from_props(payload)
        assert (state["tinker_time_type"], state["tinker_time_rider"]) == expected, (payload, state)

    event_choices = [
        {"title": {"key": "TINKER_TIME.pages.INITIAL.options.CHOOSE_CARD_TYPE.title"}},
        {"title": {"key": "TINKER_TIME.pages.CHOOSE_CARD_TYPE.options.POWER.title"}},
        {"title": {"key": "TINKER_TIME.pages.CHOOSE_RIDER.options.EXPERTISE.title"}},
    ]
    event_state = mad_science_state_from_event_choices(event_choices)
    assert event_state == {"tinker_time_type": "Power", "tinker_time_rider": "Expertise"}, event_state


def test_fixed50_mad_science_specs_carry_tinker_state():
    specs = load_fixed_50()
    for idx, expected in ((19, ("Power", "Expertise")), (25, ("Power", "Expertise"))):
        spec, source_run_id, _ = specs[idx]
        found = []
        for pile in ("hand_cards", "draw_pile_cards", "discard_pile_cards", "exhaust_pile_cards"):
            found.extend(c for c in spec.get(pile, []) if c["card_id"] == "MAD_SCIENCE")
        assert found, (idx, source_run_id)
        assert {(c.get("tinker_time_type"), c.get("tinker_time_rider")) for c in found} == {expected}, found


def test_preflight_rejects_missing_mad_science_state():
    emu = BattleEmulator()
    spec = {
        "character_id": "DEFECT", "player_hp": 63, "player_max_hp": 63,
        "hand_cards": [{"card_id": "MAD_SCIENCE", "is_upgraded": False}],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    result = preflight_validate(spec, emu)
    assert "missing_mad_science_state" in result["reasons"], result


def test_preflight_rejects_missing_nightmare_associated_card():
    emu = BattleEmulator()
    spec = {
        "character_id": "SILENT", "player_hp": 63, "player_max_hp": 63,
        "hand": ["DEFEND_SILENT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [{"id": "NIGHTMARE_POWER", "amount": 1}],
        "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    result = preflight_validate(spec, emu)
    assert "missing_associated_card" in result["reasons"], result


def test_fixed50_5483_41_runic_capacitor_candidate_eval_no_exception():
    """Regression: the Runic Capacitor fixed50 scenario should evaluate all first-turn candidates."""
    emu = BattleEmulator()
    spec, source_run_id, combat_index = load_fixed_50()[41]
    assert source_run_id == "fixed50:5483", source_run_id
    assert combat_index == 41, combat_index
    state = emu.initialize(spec)
    assert state.engine_state["orbSlots"] == 6, state.engine_state.get("orbSlots")
    assert [o["id"] for o in state.engine_state["orbs"]] == [
        "LIGHTNING_ORB", "LIGHTNING_ORB", "LIGHTNING_ORB"
    ], state.engine_state["orbs"]
    legal = emu.enumerate_legal_actions(state)
    assert len(legal) == 7, legal
    for action in legal:
        next_state = emu.apply_action(state, action)
        assert next_state.engine_state.get("orbs") is not None, action


def test_start_of_combat_pending_choice_preflight_is_restorable():
    emu = BattleEmulator()
    specs = load_fixed_50()
    for idx in (27, 31):
        spec, _, _ = specs[idx]
        result = preflight_validate(spec, emu)
        assert "unsupported_pending_choice_type:Unsupported" not in result["reasons"], (idx, result)


def test_wish_action_continuation_resolves_in_same_instance():
    emu = BattleEmulator()
    evaluator = StateEvaluator()
    agent = HeuristicAgent(emu, evaluator, DEFAULT_WEIGHTS)
    spec, source_run_id, combat_index = load_fixed_50()[22]
    assert source_run_id == "fixed50:3337", source_run_id
    assert combat_index == 22, combat_index
    state = emu.initialize(spec)
    legal = emu.enumerate_legal_actions(state)
    wish = next(a for a in legal if a["label"] == "WISH")
    next_state = emu.apply_action(
        state,
        wish,
        continuation_resolver=agent._choose_action_continuation_live,
    )
    assert not is_action_continuation_pending_choice(next_state.engine_state), next_state.engine_state.get("pendingChoice")


def test_wish_action_continuation_auto_resolves_without_leaking_pending_state():
    emu = BattleEmulator()
    spec, source_run_id, combat_index = load_fixed_50()[22]
    assert source_run_id == "fixed50:3337", source_run_id
    assert combat_index == 22, combat_index
    state = emu.initialize(spec)
    legal = emu.enumerate_legal_actions(state)
    wish = next(a for a in legal if a["label"] == "WISH")
    next_state = emu.apply_action(state, wish)
    assert not is_action_continuation_pending_choice(next_state.engine_state), next_state.engine_state.get("pendingChoice")
    assert emu.enumerate_legal_actions(next_state), next_state.engine_state


def test_neows_bones_preflight_no_special_quarantine():
    """NEOWS_BONES now restores without its old unconditional deck side effect."""
    emu = BattleEmulator()
    spec = {
        "character_id": "DEFECT", "player_hp": None, "player_max_hp": None,
        "hand": ["DEFEND_DEFECT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["NEOWS_BONES"], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    result = preflight_validate(spec, emu)
    assert result["status"] == "ok", result
    assert "known_issue:neows_bones_reward_duplication" not in result["reasons"], result


def test_upgraded_and_unupgraded_mixed():
    """強化・未強化カード混在Scenario"""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": True},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": True},
        ],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    hand = state.engine_state["hand"]
    assert [c["upgraded"] for c in hand] == [True, False, True], hand
    # unupgraded Strike costs less energy/damage difference isn't checked here - just
    # confirm the upgrade flag itself round-trips per-instance, not pile-wide.


def test_upgrade_level_field_matches_is_upgraded_shorthand():
    """UpgradeLevel=1 must produce the same result as the legacy is_upgraded=True
    shorthand (no card in this content build currently exceeds MaxUpgradeLevel=1, so
    this only exercises level 1 - the loop-based CardCmd.Upgrade application in
    GameInstance.CreateScenarioCard is otherwise unverified above level 1 by this repo)."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [
            {"card_id": "STRIKE_IRONCLAD", "upgrade_level": 1},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": True},
        ],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    hand = state.engine_state["hand"]
    assert [c["upgraded"] for c in hand] == [True, True], hand
    assert [c["upgradeLevel"] for c in hand] == [1, 1], hand


def test_enchantment_applies_and_is_observable():
    """Sharp (attack-only, +damage) round-trips through initialize() and is exposed on
    the engine-observation card dict."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [
            {"card_id": "STRIKE_IRONCLAD", "enchantment": {"id": "SHARP", "amount": 3}},
        ],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    hand = state.engine_state["hand"]
    assert len(hand) == 1, hand
    enchantment = hand[0]["enchantment"]
    assert enchantment is not None and enchantment["id"] == "SHARP" and enchantment["amount"] == 3, hand


def test_enchantment_survives_apply_action_restore():
    """Enchantment must not be silently dropped by build_scenario_from_state() restore
    (the same class of bug test_upgrade_and_potions_survive_apply_action_restore
    pins for upgrade state/potions)."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [{"card_id": "STRIKE_IRONCLAD", "enchantment": {"id": "SHARP", "amount": 3}}],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    legal = emu.enumerate_legal_actions(state)
    end_turn = next(a for a in legal if a["action_type"] == "system")
    state2 = emu.apply_action(state, end_turn)
    hand2 = state2.engine_state["hand"]
    strikes = [c for c in hand2 if c["id"] == "STRIKE_IRONCLAD"]
    assert strikes and strikes[0]["enchantment"] is not None and strikes[0]["enchantment"]["id"] == "SHARP", hand2


def test_enchantment_rejected_for_incompatible_card_type():
    """Sharp is attack-only (EnchantmentModel.CanEnchantCardType); requesting it for a
    Skill must raise, not silently no-op."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [{"card_id": "DEFEND_IRONCLAD", "enchantment": {"id": "SHARP", "amount": 1}}],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "enchant_incompatible_card_type")


def test_aeonglass_increasing_intensity_upgrades_wither_via_real_engine():
    """AEONGLASS's INCREASING_INTENSITY_MOVE must escalate Wither cards through the
    real CardModel.CurrentUpgradeLevel/OnUpgrade system (CardCmd.Upgrade), not a
    private disconnected counter - forced via the new EnemyScenario.ForcedMove
    plumbing (battle_emulator.build_scenario_from_spec's "forced_move" enemy key)."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand_cards": [{"card_id": "WITHER", "upgrade_level": 0}],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "AEONGLASS", "hp": 512, "forced_move": "INCREASING_INTENSITY_MOVE"}],
    }
    state = emu.initialize(spec)
    wither_before = next(c for c in state.engine_state["hand"] if c["id"] == "WITHER")
    assert wither_before["upgraded"] is False and wither_before["upgradeLevel"] == 0, wither_before
    enemy = state.engine_state["enemies"][0]
    assert enemy["intent"]["stateId"] == "INCREASING_INTENSITY_MOVE", enemy

    legal = emu.enumerate_legal_actions(state)
    end_turn = next(a for a in legal if a["action_type"] == "system")
    state2 = emu.apply_action(state, end_turn)

    wither_after = next(c for c in state2.engine_state["hand"] if c["id"] == "WITHER")
    assert wither_after["upgraded"] is True, wither_after
    assert wither_after["upgradeLevel"] == 1, wither_after


def test_potions_present():
    """ポーションありScenario"""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "potions": [{"slot": 0, "potion_id": "FIRE_POTION"}, {"slot": 2, "potion_id": "BLOCK_POTION"}],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    potions = state.engine_state["potions"]
    assert potions[0]["id"] == "FIRE_POTION", potions
    assert potions[1] is None, potions
    assert potions[2]["id"] == "BLOCK_POTION", potions
    legal = emu.enumerate_legal_actions(state)
    potion_actions = [a for a in legal if a["action_type"] == "potion"]
    assert {a["parameters"]["potionSlot"] for a in potion_actions} == {0, 2}, potion_actions


def test_potion_belt_expands_slots():
    """Potion Belt併用Scenario"""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["POTION_BELT"], "seed": 1,
        "potions": [{"slot": 3, "potion_id": "FIRE_POTION"}],  # slot 3 needs the belt's +2 slots (base is 3: 0-2)
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    potions = state.engine_state["potions"]
    assert len(potions) >= 4, potions
    assert potions[3]["id"] == "FIRE_POTION", potions


def test_explicit_hp_not_double_applied_with_max_hp_relic():
    """HP明示指定と最大HP変更レリック併用"""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 55, "player_max_hp": 90,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["DRAGON_FRUIT"], "seed": 1,  # DragonFruit grants +max HP on pickup
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = emu.initialize(spec)
    assert state.engine_state["maxHp"] == 90, state.engine_state["maxHp"]
    assert state.engine_state["hp"] == 55, state.engine_state["hp"]


def test_duplicate_monster_stable_index():
    """同一モンスター複数体"""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [
            {"monster_id": "CALCIFIED_CULTIST", "hp": 48},
            {"monster_id": "CALCIFIED_CULTIST", "hp": 48},
            {"monster_id": "CALCIFIED_CULTIST", "hp": 5},
        ],
    }
    state = emu.initialize(spec)
    enemies = state.engine_state["enemies"]
    assert [e["index"] for e in enemies] == [0, 1, 2], enemies
    # the enemy_max_hps id-keying bug this investigation found and fixed would corrupt
    # maxHp for enemies of a duplicated species - assert all three are independently correct.
    assert [e["maxHp"] for e in enemies] == [48, 48, 5], enemies


def test_target_tracking_after_death():
    """敵死亡後の対象追跡 - documents ACTUAL (not documented-but-assumed) behavior.

    IMPORTANT FINDING (verified against CombatState.cs source, not merely observed):
    `state["enemies"][i]["index"]` is NOT stable across a kill, contrary to
    GameInstance.cs's own doc comment on BuildEnemiesDict ("...even once some enemies
    have died... this 'index' field does not [shift]"). The real engine physically
    removes a dead creature from `CombatState._enemies`
    (`CombatState.cs:289,513: _enemies.Remove(creature)`) - not merely flags it
    isAlive=false - so every enemy after the removed one recompacts down by one
    position, and BuildEnemiesDict's `.Select((e, index) => ...)` reflects that new,
    shrunk order immediately. Confirmed this recompaction happens WITHIN a single
    apply_action()/Step() call (not only across separate restore boundaries) - killing
    the enemy at index 0 out of [index 0 (5hp), index 1 (48hp)] leaves ONE remaining
    enemy reported as index 0 (the former index 1), all within that same call's
    returned observation.

    Practical consequence for CombatEnv callers: `index`/`enemyIndex` reliably
    disambiguates enemies and resolves a target choice ONLY within one decision (one
    get_legal_actions() -> step() round trip) - see
    test_enemy_index_matches_choice_target_parameters. It does NOT let a caller cache
    "enemy index=2" from an earlier observation and expect it to still identify the
    same physical enemy after ANY death has occurred since (not just that enemy's own
    death) - re-resolve which index corresponds to which physical enemy from the most
    recent observation every time, e.g. by HP/intent/stateLog continuity, not by
    assuming index persistence. Flagged to the Emulator team as a discrepancy between
    BuildEnemiesDict's doc comment and actual behavior - not something this file works
    around by re-implementing tracking itself.
    """
    env = CombatEnv()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "STRIKE_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [
            {"monster_id": "CALCIFIED_CULTIST", "hp": 5},   # index 0, will die first
            {"monster_id": "CALCIFIED_CULTIST", "hp": 48},  # index 1 pre-kill
        ],
    }
    env.reset(spec)
    legal = env.get_legal_actions()
    strike = next(a for a in legal if a["action_type"] == "card")
    result = env.step(strike, target_enemy_index=0)
    enemies_after = result["observation"]["state"]["enemies"]
    assert len(enemies_after) == 1, enemies_after
    assert enemies_after[0]["hp"] == 48, enemies_after  # the survivor, untouched
    # Recompacted, not preserved as 1 - this is the documented-vs-actual discrepancy above.
    assert enemies_after[0]["index"] == 0, enemies_after

    # Within THIS state, enemyIndex=0 correctly resolves to the (only, surviving) enemy
    # without raising (the point of this assertion is that targeting succeeds cleanly,
    # not the exact resulting HP - not asserted here since it depends on card/monster
    # damage numbers this test isn't trying to pin down).
    legal2 = result["legal_actions"]
    strike2 = next(a for a in legal2 if a["action_type"] == "card")
    result2 = env.step(strike2, target_enemy_index=0)
    assert result2["observation"]["state"]["enemies"] == [] or result2["observation"]["state"]["enemies"][0]["hp"] < 48

    # A stale/nonexistent enemyIndex (9, never valid here) still fails clearly, whether
    # or not the last enemy happened to die too (if it's dead, there's no card action
    # left to test with - that's fine, the earlier duplicate-monster/exception-type
    # tests already cover ValueError-on-bad-index thoroughly).
    if result2["observation"]["state"]["enemies"]:
        legal3 = result2["legal_actions"]
        strike3 = next((a for a in legal3 if a["action_type"] == "card"), None)
        if strike3 is not None:
            try:
                env.step(strike3, target_enemy_index=9)
                raise AssertionError("expected ValueError for a nonexistent enemyIndex")
            except ValueError:
                pass


def test_enemy_index_matches_choice_target_parameters():
    """enemyIndexとObservationの対応"""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [
            {"monster_id": "CALCIFIED_CULTIST", "hp": 48},
            {"monster_id": "CALCIFIED_CULTIST", "hp": 30},
        ],
    }
    state = emu.initialize(spec)
    obs_indices = {e["index"] for e in state.engine_state["enemies"]}
    legal = emu.enumerate_legal_actions(state)
    strike = next(a for a in legal if a["action_type"] == "card")
    game = emu._restore(state)  # noqa: SLF001 - white-box: inspect the raw choice_target step
    step_result = game.Step(strike["action_id"])
    from emulator_bridge import legal_actions_to_list
    targets = [a for a in legal_actions_to_list(step_result.LegalActions) if a["action_type"] == "choice_target"]
    target_indices = {t["parameters"]["enemyIndex"] for t in targets}
    assert target_indices == obs_indices, (target_indices, obs_indices)


def test_wriggler_slot_name_inference_and_restore():
    """PHROG_PARASITE_ELITE restores phrog/wriggler slots from encounter order."""
    emu = BattleEmulator()
    spec = {
        "character_id": "REGENT", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_REGENT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [
            {"monster_id": "PHROG_PARASITE", "hp": 68},
            {"monster_id": "WRIGGLER", "hp": 22},
            {"monster_id": "WRIGGLER", "hp": 21},
        ],
        "source": {"encounter": "PHROG_PARASITE_ELITE"},
    }
    state = emu.initialize(spec)
    slots = [e["slotName"] for e in state.engine_state["enemies"]]
    assert slots == ["phrog", "wriggler1", "wriggler2"], slots
    assert spec["slot_name_manifest"][0]["slot_name_source"] == "encounter_definition", spec["slot_name_manifest"]
    assert spec["slot_name_manifest"][1]["slot_name_source"] == "inferred_from_order", spec["slot_name_manifest"]
    legal = emu.enumerate_legal_actions(state)
    assert legal, legal
    assert all((e.get("intent") or {}).get("stateId") != "UNSET_MOVE" for e in state.engine_state["enemies"])

    state2 = emu.apply_action(state, legal[0])
    restored = emu._restore(state2)  # noqa: SLF001 - white-box: Observation -> Scenario restore
    restored_state = restored.GetObservation().State
    from emulator_bridge import to_plain
    assert [e["slotName"] for e in to_plain(restored_state)["enemies"]] == [
        e["slotName"] for e in state2.engine_state["enemies"]
    ]


def test_wriggler_missing_slot_without_encounter_defaults_to_wriggler1():
    """Without source encounter metadata, the Emulator itself now defaults a slot-dependent
    monster's SlotName (docs/reports/wriggler_timeout_investigation_20260731.md) rather than
    hanging on RollMove's fallback-less ConditionalBranchState. RL's own slot_name_manifest
    still correctly reports "unavailable" - it has no encounter provenance for this synthetic
    spec - the Emulator's default is a search-convenience fallback, not a claim of historical
    accuracy."""
    emu = BattleEmulator()
    spec = {
        "character_id": "REGENT", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_REGENT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "WRIGGLER", "hp": 22}],
    }
    result = preflight_validate(spec, emu)
    assert result["status"] == "ok", result
    enemies = result["battle_state"].engine_state["enemies"]
    assert enemies[0]["slotName"] == "wriggler1", enemies
    assert enemies[0]["intent"]["stateId"] != "UNSET_MOVE", enemies
    assert spec["slot_name_manifest"][0]["slot_name_source"] == "unavailable", spec["slot_name_manifest"]


def test_exoskeleton_slot_name_inference_order_restore_and_hash():
    """EXOSKELETONS_WEAK maps input order to first/second/third and preserves it."""
    emu = BattleEmulator()
    spec = {
        "character_id": "DEFECT", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_DEFECT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [
            {"monster_id": "EXOSKELETON", "hp": 29},
            {"monster_id": "EXOSKELETON", "hp": 28},
            {"monster_id": "EXOSKELETON", "hp": 27},
        ],
        "source": {"encounter": "EXOSKELETONS_WEAK"},
    }
    state = emu.initialize(spec)
    slot_to_intent = {
        e["slotName"]: (e.get("intent") or {}).get("stateId")
        for e in state.engine_state["enemies"]
    }
    assert slot_to_intent == {
        "first": "SKITTER_MOVE",
        "second": "MANDIBLES_MOVE",
        "third": "ENRAGE_MOVE",
    }, slot_to_intent
    assert [row["slot_name_source"] for row in spec["slot_name_manifest"]] == [
        "inferred_from_order",
        "inferred_from_order",
        "inferred_from_order",
    ]
    legal = emu.enumerate_legal_actions(state)
    state2 = emu.apply_action(state, legal[0])
    restored = emu._restore(state2)  # noqa: SLF001 - white-box: lookahead/apply_action restore path
    from emulator_bridge import to_plain
    assert [e["slotName"] for e in to_plain(restored.GetObservation().State)["enemies"]] == [
        e["slotName"] for e in state2.engine_state["enemies"]
    ]

    swapped = {
        **spec,
        "enemies": [
            {"monster_id": "EXOSKELETON", "hp": 27},
            {"monster_id": "EXOSKELETON", "hp": 28},
            {"monster_id": "EXOSKELETON", "hp": 29},
        ],
    }
    ensure_inferred_slot_names(swapped)
    state_swapped = emu.initialize(swapped)
    assert battle_state_key(state) != battle_state_key(state_swapped)


def test_exoskeleton_duplicate_slot_name_rejected():
    """Emulator rejects duplicate explicit SlotName values in one scenario."""
    emu = BattleEmulator()
    spec = {
        "character_id": "DEFECT", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_DEFECT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [
            {"monster_id": "EXOSKELETON", "hp": 29, "slot_name": "first"},
            {"monster_id": "EXOSKELETON", "hp": 28, "slot_name": "first"},
        ],
    }
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "duplicate SlotName")


def test_legacy_plain_string_scenario_regression():
    """旧Scenarioの回帰 (plain hand/draw_pile strings, no *Cards/potions fields)"""
    emu = BattleEmulator()
    spec = {
        "character_id": "Ironclad", "player_hp": 80, "player_max_hp": 80,
        "hand": ["STRIKE_IRONCLAD"] * 4 + ["BASH"],
        "draw_pile": ["STRIKE_IRONCLAD"] + ["DEFEND_IRONCLAD"] * 4,
        "discard_pile": [], "exhaust_pile": [], "player_powers": [], "relics": [],
        "seed": 1, "enemies": [{"monster_id": "CalcifiedCultist", "hp": 48, "powers": []}],
    }
    state = emu.initialize(spec)
    assert state.engine_state["hp"] == 80
    assert len(state.engine_state["hand"]) == 5
    assert all(not c["upgraded"] for c in state.engine_state["hand"])


def test_lead_paperweight_and_claws_no_longer_hang():
    """LEAD_PAPERWEIGHT・CLAWSを含むScenario"""
    import time

    emu = BattleEmulator()
    for relic in ("LEAD_PAPERWEIGHT", "CLAWS"):
        spec = {
            "character_id": "DEFECT", "player_hp": None, "player_max_hp": None,
            "hand": ["DEFEND_DEFECT"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            "player_powers": [], "relics": [relic], "seed": 1,
            "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
        }
        t0 = time.time()
        state = emu.initialize(spec)
        elapsed = time.time() - t0
        assert elapsed < 10.0, f"{relic} took {elapsed:.1f}s - regression of the fixed hang?"
        assert not state.is_terminal


def test_invalid_input_exception_types():
    """不正入力時の例外型 (real, unwrapped types - not AggregateException)"""
    emu = BattleEmulator()
    base = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }

    # unknown card id
    spec = dict(base, hand=["NOT_A_REAL_CARD_ID"])
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "unknown card id")

    # duplicate potion slot
    spec = dict(base, potions=[{"slot": 0, "potion_id": "FIRE_POTION"}, {"slot": 0, "potion_id": "BLOCK_POTION"}])
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "duplicate potion slot")

    # out-of-range potion slot (no Potion Belt granted)
    spec = dict(base, potions=[{"slot": 9, "potion_id": "FIRE_POTION"}])
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "out-of-range potion slot")

    # upgrading a non-upgradable card (ASCENDERS_BANE is a curse, never upgradable)
    spec = dict(base, hand=[], hand_cards=[{"card_id": "ASCENDERS_BANE", "is_upgraded": True}])
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "upgrade non-upgradable card")

    # PlayerHp negative
    spec = dict(base, player_hp=-5, player_max_hp=80)
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "negative PlayerHp")

    # PlayerHp exceeds PlayerMaxHp
    spec = dict(base, player_hp=999, player_max_hp=80)
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "PlayerHp > PlayerMaxHp")

    # Stars negative
    spec = dict(base, stars=-1)
    _assert_raises_not_aggregate(lambda: emu.initialize(spec), "negative Stars")


def _assert_raises_not_aggregate(fn, label: str) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, we're checking the type name
        type_name = type(exc).__name__
        assert "Aggregate" not in type_name, f"{label}: got wrapped {type_name}, expected an unwrapped exception type"
        return
    raise AssertionError(f"{label}: expected an exception, none was raised")


def main() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
