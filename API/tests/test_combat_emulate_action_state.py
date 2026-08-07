"""Regression coverage for CombatInstance.emulate_action Stable Branch state.

A Stable Branch result must expose the exact post-action BattleState returned by the
Branch Worker. It must not reuse the parent's BattleState with only legal actions
replaced, because that returns the pre-branch board in masked_emulator_dto and corrupts
deeper Branch decisions.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402


def _combat_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def test_emulate_action_returns_post_branch_state() -> None:
    inst = CombatInstance("branch-state", _combat_config(), worker_count=1)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        bash_id = next(
            action["action_id"]
            for action in legal
            if action.get("parameters", {}).get("cardId") == "BASH"
        )
        start_hp = start["masked_emulator_dto"]["enemies"][0]["hp"]

        branch = inst.emulate_action(
            parent_branch_id="root",
            branch_id="b1",
            rng_id=1,
            decision_point_id=start["decision_point_id"],
            action_id=bash_id,
            simulation_options=None,
        )
        assert branch["status"] == "completed", branch
        branch_hp = branch["masked_emulator_dto"]["enemies"][0]["hp"]
        assert branch_hp < start_hp, (
            "emulate_action must expose the post-action Branch state "
            f"(start_hp={start_hp}, branch_hp={branch_hp})"
        )

        cached = inst.get_decision("b1")
        assert cached["masked_emulator_dto"]["enemies"][0]["hp"] == branch_hp

        root = inst.get_decision("root")
        assert root["masked_emulator_dto"]["enemies"][0]["hp"] == start_hp
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
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(_run_all())
