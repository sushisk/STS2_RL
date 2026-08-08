"""Regression coverage for terminal win/loss exposure in Combat and Whole Run.

Combat tests drive real terminal transitions. Whole Run response shaping uses
hand-crafted terminal views/bookkeeping because a full God Mode run is impractical
for a focused regression test.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402
from API.instance_whole_run import WholeRunInstance, _BranchBookkeeping, _View  # noqa: E402
from API.history_builder import HistoryBuilder  # noqa: E402


def _victory_combat_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["WHIRLWIND"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
    }


def _defeat_combat_config() -> dict:
    # A large-HP enemy defeats the 1-HP player after a few unblocked turns.
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": 1,
        "player_max_hp": 80,
        "hand": ["DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999}],
    }


def test_combat_victory_exposes_terminal_and_outcome():
    inst = CombatInstance("c-victory", _victory_combat_config(), worker_count=1)
    try:
        decision = inst.start_instance_response()
        legal = decision["masked_emulator_dto"]["legal_actions"]
        whirlwind = next(a for a in legal if a.get("parameters", {}).get("cardId") == "WHIRLWIND")
        result = inst.commit_action(decision["decision_point_id"], whirlwind["action_id"])
        dto = result["masked_emulator_dto"]
        assert dto["legal_actions"] == []
        assert dto.get("terminal") is True
        assert dto.get("outcome") == "victory"
    finally:
        inst.close()


def test_combat_defeat_exposes_terminal_and_outcome():
    inst = CombatInstance("c-defeat", _defeat_combat_config(), worker_count=1)
    try:
        decision = inst.start_instance_response()
        for _ in range(5):
            dto = decision["masked_emulator_dto"]
            if dto.get("legal_actions") == []:
                break
            legal = dto["legal_actions"]
            end_turn = next(a for a in legal if a["action_type"] == "system")
            decision = inst.commit_action(decision["decision_point_id"], end_turn["action_id"])
        dto = decision["masked_emulator_dto"]
        assert dto["legal_actions"] == []
        assert dto.get("terminal") is True
        assert dto.get("outcome") == "defeat"
    finally:
        inst.close()


def test_combat_non_terminal_decision_has_no_terminal_key():
    inst = CombatInstance("c-nonterm", _victory_combat_config(), worker_count=1)
    try:
        decision = inst.start_instance_response()
        dto = decision["masked_emulator_dto"]
        assert dto["legal_actions"] != []
        assert "terminal" not in dto
        assert "outcome" not in dto
    finally:
        inst.close()


def _whole_run_config() -> dict:
    return {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0}


def test_whole_run_root_run_terminal_view_exposes_outcome():
    inst = WholeRunInstance("wr-root-terminal", _whole_run_config(), branch_worker_count=1)
    try:
        terminal_view = _View(
            legal_actions_raw=[],
            boundary="run_terminal",
            observation={"boundary": "run_terminal", "outcome": "victory", "state": {}},
            room_context={},
            map_snapshot=None,
            room_id=None,
            action_prefix=(),
            choice_type="run_terminal",
            chain_blocked=False,
            event_rng_state=None,
        )
        response = inst._decision_response_fields(  # noqa: SLF001
            "root", terminal_view, branch_log=[], history=HistoryBuilder()
        )
        dto = response["masked_emulator_dto"]
        assert dto.get("run_terminal") is True
        assert dto.get("outcome") == "victory"
    finally:
        inst.close()


def test_whole_run_non_terminal_view_has_no_run_terminal_key():
    inst = WholeRunInstance("wr-root-nonterm", _whole_run_config(), branch_worker_count=1)
    try:
        decision = inst.start_instance_response()
        dto = decision["masked_emulator_dto"]
        assert dto.get("boundary") != "run_terminal"
        assert "run_terminal" not in dto
        assert "outcome" not in dto
    finally:
        inst.close()


def test_whole_run_branch_get_decision_preserves_captured_outcome():
    inst = WholeRunInstance("wr-branch-terminal", _whole_run_config(), branch_worker_count=1)
    try:
        book = _BranchBookkeeping("root", [], HistoryBuilder(), rng_id=1)
        book.terminal = True
        book.outcome = "defeat"
        inst._bookkeeping["b1"] = book  # noqa: SLF001
        inst._branch_ids.register("b1")  # noqa: SLF001
        inst._decision_points.issue("b1")  # noqa: SLF001

        response = inst.get_decision("b1")
        dto = response["masked_emulator_dto"]
        assert dto.get("run_terminal") is True
        assert dto.get("outcome") == "defeat"
    finally:
        inst.close()


def _run_all() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    failures = 0
    for test in sorted(tests, key=lambda fn: fn.__name__):
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
