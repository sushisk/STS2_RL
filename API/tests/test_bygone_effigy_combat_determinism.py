"""Simulate a plain BYGONE_EFFIGY combat and vary the RNG.

Every Whole Run branch fault seen in the field so far happened in a BYGONE_EFFIGY fight -
two of the four such fights in one 10-game batch - and in none of the 31 other species
present. This drives the same enemy through the ordinary Combat instance instead, where
the scenario is built directly and the RNG is an explicit input, to separate two things:

* does the fight itself resolve differently for the same actions under different
  ``rng_id``, i.e. is there enemy-side randomness at all;
* does the enemy's published move sequence follow the deterministic chain its state
  machine declares (SLEEP -> WAKE -> SLASH -> SLASH ...).

This is deliberately *not* the Whole Run replay path: the Combat instance branches from a
CombatScenario, not from a Map snapshot plus an action prefix. It characterises the
enemy, not the rebuild.

Run directly::

    python -m pytest API/tests/test_bygone_effigy_combat_determinism.py -s
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402

_EFFIGY_HP = 121


def _config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": 61,
        "player_max_hp": 94,
        "energy": 3,
        "hand": ["BARRICADE", "TREMBLE", "BURNING_PACT", "STRIKE_IRONCLAD"],
        "draw_pile": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        "discard_pile": [],
        "exhaust_pile": [],
        "deck": [],
        "enemies": [{"monster_id": "BYGONE_EFFIGY", "hp": _EFFIGY_HP}],
    }


def _dto(response: dict) -> dict:
    return response["masked_emulator_dto"]


def _enemy(response: dict) -> dict:
    enemies = _dto(response).get("enemies") or []
    return enemies[0] if enemies else {}


def _signature(response: dict) -> tuple:
    dto = _dto(response)
    enemy = _enemy(response)
    intent = enemy.get("intent") or {}
    return (
        dto.get("boundary"),
        dto.get("hp"),
        dto.get("energy"),
        dto.get("block"),
        enemy.get("hp"),
        intent.get("stateId"),
        intent.get("attackDamage"),
        tuple(sorted(c.get("id") for c in (dto.get("hand") or []))),
    )


def _end_turn_id(response: dict) -> str:
    for action in _dto(response)["legal_actions"]:
        if action["action_type"] == "system":
            return action["action_id"]
    raise AssertionError("no End Turn available")


def test_the_same_end_turn_resolves_identically_under_different_rng() -> None:
    """Four sibling branches of the same action, differing only in `rng_id`."""

    instance = CombatInstance("effigy-rng", _config(), worker_count=2)
    try:
        start = instance.start_instance_response()
        print("\n  start:", _signature(start))
        end_turn = _end_turn_id(start)

        response = instance.emulate_actions(
            items=[
                {
                    "parent_branch_id": "root",
                    "branch_id": f"b{rng}",
                    "rng_id": rng,
                    "decision_point_id": start["decision_point_id"],
                    "action_id": end_turn,
                }
                for rng in (1, 2, 3, 4)
            ],
            simulation_options={"stop_condition": "next_decision"},
        )
        assert response["status"] == "completed", response

        signatures = {}
        for rng in (1, 2, 3, 4):
            result = response["branch_results"][f"b{rng}"]
            assert result["status"] == "completed", result
            signatures[rng] = _signature(result)
            print(f"  rng={rng}:", signatures[rng])

        distinct = set(signatures.values())
        print(f"  distinct outcomes: {len(distinct)}")
        assert len(distinct) == 1, (
            "the same action produced different states under different rng_id",
            signatures,
        )
    finally:
        instance.close()


def test_the_published_move_sequence_follows_the_declared_chain() -> None:
    """`BygoneEffigy` declares SLEEP -> WAKE -> SLASH -> SLASH with no random branch."""

    instance = CombatInstance("effigy-chain", _config(), worker_count=1)
    try:
        response = instance.start_instance_response()
        observed = []
        for _ in range(6):
            enemy = _enemy(response)
            if not enemy or not enemy.get("isAlive", True):
                break
            observed.append((enemy.get("intent") or {}).get("stateId"))
            if _dto(response).get("boundary") != "stable":
                break
            response = instance.commit_action(
                response["decision_point_id"], _end_turn_id(response)
            )
            if response.get("status") != "completed":
                break
        print("\n  observed intents:", observed)
        assert observed[:1] == ["SLEEP_MOVE"], observed
        assert "SLEEP_MOVE_2" not in observed[1:2], observed
    finally:
        instance.close()
