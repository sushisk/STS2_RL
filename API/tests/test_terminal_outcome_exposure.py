"""Regression coverage for exposing the win/loss `outcome` signal at terminal
decisions - Combat combat-end (victory/defeat) and Whole Run `RUN_TERMINAL`.

Before this change, `masked_emulator_dto` at a normal (non-faulted) terminal
decision carried no win/loss signal Training could read:

- Combat: `legal_actions` became `[]`, but `BattleState.is_terminal`/`.outcome` are
  Python-side attributes never copied into `engine_state` (see
  `instance_combat.py::_decision_response_fields`).
- Whole Run: the `RUN_TERMINAL` payload was hardcoded to `{"run_terminal": True}`,
  discarding the `outcome` `Run/run_emulator_bridge.py::observation_to_dict()`
  already puts on the raw Observation (see `instance_whole_run.py`'s
  `_decision_response_fields`/`emulate_action`/`get_decision`).

Combat cases are driven for real (cheap - a single card play settles it, matching
`Combat/tests/test_battle_emulator_transition_outcome.py`'s proven scenarios). Whole
Run cases are NOT driven to a real RUN_TERMINAL (WholeRunInstance always enables
God Mode, and a full run naturally reaching floor-17 victory or defeat is not a
practical regression test) - instead the two response-building code paths
(`_decision_response_fields` for root, and the Branch bookkeeping capture/re-serve
pair in `emulate_action`/`get_decision`) are exercised directly against a
hand-crafted terminal `_View`/`_BranchBookkeeping`, which is how
`API/tests/test_whole_run_view.py` already tests this instance's response-shaping
logic without a full playthrough.

Native assertion runner, no pytest dependency (matches this package's convention).
Run: `python test_terminal_outcome_exposure.py`.
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
    # Exact scenario proven in Combat/tests/test_battle_emulator_transition_outcome.py's
    # test_defeat_step_reports_terminal_defeat - player_hp=1 vs a large-HP enemy that
    # kills in a couple of unblocked turns.
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
    """Exercises the exact `_decision_response_fields` code path root's real
    get_decision/commit_action use, against a hand-crafted RUN_TERMINAL `_View` -
    see module docstring for why this isn't driven via a real full playthrough."""
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
        response = inst._decision_response_fields(  # noqa: SLF001 - see module docstring
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


def test_whole_run_branch_get_decision_reserves_captured_outcome():
    """Exercises get_decision's `book.terminal` re-serve branch directly against a
    hand-crafted already-terminal bookkeeping entry - see module docstring."""
    inst = WholeRunInstance("wr-branch-terminal", _whole_run_config(), branch_worker_count=1)
    try:
        book = _BranchBookkeeping("root", [], HistoryBuilder(), rng_id=1)
        book.terminal = True
        book.outcome = "defeat"
        inst._bookkeeping["b1"] = book  # noqa: SLF001 - see module docstring
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
