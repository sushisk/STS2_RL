"""Paired-Emulator regression for observable-state replay draw pinning."""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance


def _config() -> dict:
    return {
        "instance_type": "combat", "character_id": "SILENT", "player_hp": 70, "player_max_hp": 70,
        "hand_cards": [{"card_id": "ACROBATICS", "is_upgraded": False}, {"card_id": "NEUTRALIZE", "is_upgraded": False}],
        "draw_pile_cards": [
            {"card_id": "DEFEND_SILENT", "is_upgraded": True}, {"card_id": "DEFEND_SILENT", "is_upgraded": False},
            {"card_id": "STRIKE_SILENT", "is_upgraded": False}, {"card_id": "SURVIVOR", "is_upgraded": False},
        ],
        "discard_pile": [], "exhaust_pile": [], "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999, "max_hp": 999}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _find_card(response: dict, action_type: str, card_id: str) -> dict:
    return next(a for a in _legal_actions(response) if a["action_type"] == action_type and (a.get("parameters") or {}).get("cardId") == card_id)


def test_acrobatics_replay_prefix_pins_observable_state_without_instance_identity() -> None:
    inst = CombatInstance("acrobatics-observable-replay", _config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        acrobatics = _find_card(start, "card", "ACROBATICS")
        pending = inst.commit_action(start["decision_point_id"], acrobatics["action_id"])
        assert pending["status"] == "completed", pending
        candidates = [a for a in _legal_actions(pending) if a["action_type"] == "choice_card"]
        assert len(candidates) == 4, candidates

        constraints = inst._phase._replay_prefix[0].visible_draw_constraints
        assert [offset for offset, _key in constraints] == [0, 1, 2]
        defend_keys = [key for _offset, key in constraints if key[0] == "DEFEND_SILENT"]
        assert len(defend_keys) == 2
        assert {bool(key[5]) for key in defend_keys} == {False, True}
        assert inst._phase._replay_prefix[0].visible_draw_tracking_blocked is False

        for rng_id in range(1, 9):
            candidate = candidates[(rng_id - 1) % len(candidates)]
            result = inst.emulate_action(
                parent_branch_id="root", branch_id=f"acrobatics-hyp-{rng_id}", rng_id=rng_id,
                decision_point_id=pending["decision_point_id"], action_id=candidate["action_id"], simulation_options=None,
            )
            assert result["status"] == "completed", (rng_id, candidate, result)
            assert result.get("fault_kind") != "replay_mismatch", (rng_id, result)
    finally:
        inst.close()


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failed = []
    for test in tests:
        try:
            test(); print(f"PASS {test.__name__}")
        except Exception:
            failed.append(test.__name__); print(f"FAIL {test.__name__}"); traceback.print_exc()
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
