"""Regression coverage for the deck-composition-only CombatScenario start mode
(CombatScenario.Deck/DeckCards, sushisk/STS2_Emulator#15) as wired through
CombatInstance/battle_emulator.build_scenario_from_spec().

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402

_DECK = (
    ["STRIKE_IRONCLAD"] * 5
    + ["DEFEND_IRONCLAD"] * 4
    + ["BASH"]
)


def _deck_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "deck": list(_DECK),
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def test_deck_composition_computes_combat_start_deck_multiset() -> None:
    inst = CombatInstance("deck-multiset", _deck_config(), worker_count=1)
    try:
        assert inst._combat_start_deck_multiset == dict(Counter(_DECK)), inst._combat_start_deck_multiset
    finally:
        inst.close()


def test_deck_composition_deals_real_hand_and_draw_pile() -> None:
    inst = CombatInstance("deck-deal", _deck_config(), worker_count=1)
    try:
        dto = inst.start_instance_response()["masked_emulator_dto"]
        hand = dto["hand"]
        draw_pile = dto["drawPile"]

        assert len(hand) == 5, hand

        hand_counts = Counter(c["id"] for c in hand)
        draw_counts = Counter()
        for entry in draw_pile:
            draw_counts[entry["id"]] += entry["count"]

        total = Counter()
        total.update(hand_counts)
        total.update(draw_counts)
        assert dict(total) == dict(Counter(_DECK)), (hand_counts, draw_counts)
    finally:
        inst.close()


def test_deck_composition_structured_form_upgrade_survives() -> None:
    config = {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "deck_cards": [{"card_id": card_id, "is_upgraded": card_id == "STRIKE_IRONCLAD"} for card_id in _DECK],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    inst = CombatInstance("deck-structured", config, worker_count=1)
    try:
        assert inst._combat_start_deck_multiset == dict(Counter(_DECK)), inst._combat_start_deck_multiset
        dto = inst.start_instance_response()["masked_emulator_dto"]

        # Every STRIKE_IRONCLAD was declared upgraded; wherever they landed (hand or draw
        # pile), they must show as upgraded - not silently created unupgraded.
        strikes_in_hand = [c for c in dto["hand"] if c["id"] == "STRIKE_IRONCLAD"]
        strike_draw_entries = [e for e in dto["drawPile"] if e["id"] == "STRIKE_IRONCLAD"]
        assert all(c["upgraded"] for c in strikes_in_hand), dto["hand"]
        assert all(e["upgraded"] for e in strike_draw_entries), dto["drawPile"]
        assert len(strikes_in_hand) + sum(e["count"] for e in strike_draw_entries) == 5, dto
    finally:
        inst.close()


_TESTS = [
    test_deck_composition_computes_combat_start_deck_multiset,
    test_deck_composition_deals_real_hand_and_draw_pile,
    test_deck_composition_structured_form_upgrade_survives,
]


def main() -> int:
    failures = 0
    for test in _TESTS:
        try:
            test()
            print(f"{test.__name__}: PASS")
        except Exception:
            failures += 1
            print(f"{test.__name__}: FAIL")
            traceback.print_exc()
    if failures:
        print(f"{failures} test(s) failed")
        return 1
    print("test_deck_composition_scenario: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
