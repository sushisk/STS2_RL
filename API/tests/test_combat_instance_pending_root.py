"""Regression coverage for CombatInstance root handling at mid-combat Pending boundaries."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402


def _liquid_memories_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _first_action_id(response: dict, action_type: str) -> str:
    return next(action["action_id"] for action in _legal_actions(response) if action["action_type"] == action_type)


def test_root_emulate_action_from_mid_combat_pending_replays_from_held_stable_snapshot() -> None:
    inst = CombatInstance("pending-root-regression", _liquid_memories_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        potion_id = _first_action_id(start, "potion")

        pending = inst.commit_action(start["decision_point_id"], potion_id)
        assert pending["status"] == "completed", pending
        pending_actions = [action for action in _legal_actions(pending) if action["action_type"] == "choice_card"]
        assert len(pending_actions) >= 2, pending_actions

        first = inst.emulate_action(
            parent_branch_id="root",
            branch_id="pending-choice-0",
            rng_id=1,
            decision_point_id=pending["decision_point_id"],
            action_id=pending_actions[0]["action_id"],
            simulation_options=None,
        )
        assert first["status"] == "completed", first

        second = inst.emulate_action(
            parent_branch_id="root",
            branch_id="pending-choice-1",
            rng_id=2,
            decision_point_id=pending["decision_point_id"],
            action_id=pending_actions[1]["action_id"],
            simulation_options=None,
        )
        assert second["status"] == "completed", second
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
