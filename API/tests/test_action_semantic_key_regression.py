"""Regression coverage for Emulator-authored LegalAction.semantic_key consumption."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _touch_and_liquid_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "BASH"],
        "draw_pile": [],
        "discard_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [
            {"slot": 0, "potion_id": "TOUCH_OF_INSANITY"},
            {"slot": 1, "potion_id": "LIQUID_MEMORIES"},
        ],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _duplicate_card_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _find_action(response: dict, action_type: str, *, potion_id: str | None = None) -> dict:
    for action in _legal_actions(response):
        if action["action_type"] != action_type:
            continue
        params = action.get("parameters") or {}
        if potion_id is not None and params.get("potionId") != potion_id:
            continue
        return action
    raise AssertionError(f"missing {action_type} action potion_id={potion_id!r}: {_legal_actions(response)!r}")


def test_liquid_memories_replay_does_not_resolve_to_touch_of_insanity() -> None:
    inst = CombatInstance("semantic-key-liquid-memories", _touch_and_liquid_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        potions = [a for a in _legal_actions(start) if a["action_type"] == "potion"]
        assert [(a["parameters"]["potionId"], a["semantic_key"]) for a in potions] == [
            ("TOUCH_OF_INSANITY", "0:TOUCH_OF_INSANITY"),
            ("LIQUID_MEMORIES", "1:LIQUID_MEMORIES"),
        ]

        liquid = _find_action(start, "potion", potion_id="LIQUID_MEMORIES")
        pending = inst.commit_action(start["decision_point_id"], liquid["action_id"])
        assert pending["status"] == "completed", pending

        pending_cards = [a for a in _legal_actions(pending) if a["action_type"] == "choice_card"]
        pending_card_ids = {(a.get("parameters") or {}).get("cardId") for a in pending_cards}
        assert pending_card_ids == {"DEFEND_IRONCLAD", "STRIKE_IRONCLAD"}, pending_cards

        for rng_id in (1, 2, 3):
            for repeat in range(2):
                choice = pending_cards[repeat % len(pending_cards)]
                result = inst.emulate_action(
                    parent_branch_id="root",
                    branch_id=f"liquid-rng-{rng_id}-{repeat}",
                    rng_id=rng_id,
                    decision_point_id=pending["decision_point_id"],
                    action_id=choice["action_id"],
                    simulation_options=None,
                )
                assert result["status"] == "completed", (rng_id, repeat, choice, result)
    finally:
        inst.close()


def test_same_action_type_candidates_have_distinct_semantic_keys() -> None:
    inst = CombatInstance("semantic-key-structural", _duplicate_card_config(), worker_count=1)
    try:
        start = inst.start_instance_response()
        legal = _legal_actions(start)
        keys = [(a["action_type"], a.get("semantic_key", "")) for a in legal]
        assert len(keys) == len(set(keys)), keys

        card_keys = [
            (a["parameters"]["cardId"], a["semantic_key"])
            for a in legal
            if a["action_type"] == "card" and a["parameters"]["cardId"] == "STRIKE_IRONCLAD"
        ]
        assert card_keys == [("STRIKE_IRONCLAD", "0:STRIKE_IRONCLAD"), ("STRIKE_IRONCLAD", "1:STRIKE_IRONCLAD")]
    finally:
        inst.close()


def _run_all() -> int:
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
    sys.exit(_run_all())
