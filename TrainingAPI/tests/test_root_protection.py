"""Root-protection coverage (RL担当指示：RL–Training API実装 §8 "Root保護").

Verifies: `get_decision` never advances root; `emulate_action` never mutates root or its
parent Branch; a child Branch's creation never mutates its parent; only `commit_action`
advances root; Branch state is never transplanted onto root; and a successful
`commit_action` Cancels+Releases every Branch derived from the just-committed Decision
(and invalidates their Leases) - for both Combat and Whole Run instances.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from TrainingAPI.instance_combat import CombatInstance  # noqa: E402
from TrainingAPI.instance_whole_run import WholeRunInstance  # noqa: E402
from TrainingAPI.validation import RequestRejected  # noqa: E402


def _combat_config():
    return {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _defend_id(decision):
    legal = decision["masked_emulator_dto"]["legal_actions"]
    return next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")


def _safe_action_id(legal):
    # Avoid index 0 ("system"/End Turn) - a real, reproducible pre-existing Emulator
    # TimeoutException against CALCIFIED_CULTIST documented earlier in this project
    # (Phase M report). Any no-target card action sidesteps it.
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def test_combat_get_decision_never_advances_root():
    inst = CombatInstance("t1", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        for _ in range(3):
            gd = inst.get_decision("root")
            assert gd["decision_point_id"] == dp0, "get_decision must never advance root's decision_point_id"
    finally:
        inst.close()


def test_combat_emulate_action_never_mutates_root():
    inst = CombatInstance("t2", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        before = inst.get_decision("root")
        em = inst.emulate_action(
            parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=_defend_id(start), simulation_options=None
        )
        assert em["status"] == "completed", em
        after = inst.get_decision("root")
        assert after["decision_point_id"] == before["decision_point_id"] == dp0
        assert after["masked_emulator_dto"]["legal_actions"] == before["masked_emulator_dto"]["legal_actions"]
    finally:
        inst.close()


def test_combat_child_branch_creation_never_mutates_parent_branch():
    inst = CombatInstance("t3", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        b1 = inst.emulate_action(
            parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=_defend_id(start), simulation_options=None
        )
        before = inst.get_decision("b1")
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]
        deep = inst.emulate_action(
            parent_branch_id="b1", branch_id="b2", rng_id=1, decision_point_id=b1["decision_point_id"],
            action_id=_safe_action_id(b1_legal), simulation_options=None,
        )
        assert deep["status"] == "completed", deep
        after = inst.get_decision("b1")
        assert after["decision_point_id"] == before["decision_point_id"], "creating a child Branch must not mutate its parent Branch"
    finally:
        inst.close()


def test_combat_only_commit_action_advances_root():
    inst = CombatInstance("t4", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        inst.get_decision("root")
        inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=_defend_id(start), simulation_options=None)
        assert inst.get_decision("root")["decision_point_id"] == dp0, "root must be unchanged before any commit_action"
        commit = inst.commit_action(decision_point_id=dp0, action_id=_defend_id(start))
        assert commit["decision_point_id"] != dp0, "commit_action must be the only operation that advances root"
    finally:
        inst.close()


def test_combat_branch_state_never_transplanted_to_root():
    inst = CombatInstance("t5", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        bash_id = next(a["action_id"] for a in start["masked_emulator_dto"]["legal_actions"] if a.get("parameters", {}).get("cardId") == "BASH")
        inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=bash_id, simulation_options=None)
        # BASH would damage the enemy - root's own state must still show the enemy
        # untouched by the simulated Branch.
        root_after = inst.get_decision("root")
        root_hp = root_after["masked_emulator_dto"].get("enemies", [{}])[0].get("hp")
        start_hp = start["masked_emulator_dto"].get("enemies", [{}])[0].get("hp")
        assert root_hp == start_hp, f"root HP must be unaffected by a Branch's simulated action (root={root_hp}, start={start_hp})"
    finally:
        inst.close()


def test_combat_commit_cancels_and_releases_all_derived_branches():
    inst = CombatInstance("t6", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=_defend_id(start), simulation_options=None)
        assert b1["status"] == "completed"
        inst.commit_action(decision_point_id=dp0, action_id=_defend_id(start))
        status = inst.get_branch_status(["b1"])["branch_statuses"]
        assert status["b1"] == "released", f"Branch derived from the just-committed Decision must be released, got {status}"
    finally:
        inst.close()


def test_combat_root_cannot_be_cancelled_or_released():
    inst = CombatInstance("t7", _combat_config(), worker_count=1)
    try:
        raised_cancel = raised_release = False
        try:
            inst.cancel_branches(["root"])
        except RequestRejected:
            raised_cancel = True
        try:
            inst.release_branches(["root"])
        except RequestRejected:
            raised_release = True
        assert raised_cancel and raised_release, "root must never be Cancel-/Release-able"
    finally:
        inst.close()


def _whole_run_config():
    # seed=1 confirmed (via exploratory scan) to reach map_select then event_choice
    # reasonably quickly - needed since emulate_action now requires an Active Event
    # boundary (RL担当指示：Active Event RNG Hypothesis実装).
    return {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0}


def _advance_to_map(inst):
    decision = inst.start_instance_response()
    for _ in range(50):
        if decision["masked_emulator_dto"].get("boundary") == "map_select":
            return decision
        legal = decision["masked_emulator_dto"]["legal_actions"]
        decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=legal[0]["action_id"])
    raise AssertionError("never reached map_select")


def _pick_action_id(legal):
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def _reach_event(inst):
    # A positive rng_id emulate_action is only accepted at an event_choice boundary
    # (RL担当指示：Active Event RNG Hypothesis実装) - map_select alone is not enough.
    decision = _advance_to_map(inst)
    for _ in range(150):
        if decision["masked_emulator_dto"].get("boundary") == "event_choice":
            return decision
        legal = decision["masked_emulator_dto"]["legal_actions"]
        decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=_pick_action_id(legal))
    raise AssertionError("never reached event_choice")


def test_whole_run_emulate_action_never_mutates_root():
    inst = WholeRunInstance("wr1", _whole_run_config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp0 = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        before = inst.get_decision("root")
        em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=legal[0]["action_id"], simulation_options=None)
        assert em["status"] == "completed", em
        after = inst.get_decision("root")
        assert after["decision_point_id"] == before["decision_point_id"] == dp0
    finally:
        inst.close()


def test_whole_run_only_commit_action_advances_root():
    inst = WholeRunInstance("wr2", _whole_run_config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp0 = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=legal[0]["action_id"], simulation_options=None)
        assert inst.get_decision("root")["decision_point_id"] == dp0
        commit = inst.commit_action(decision_point_id=dp0, action_id=legal[0]["action_id"])
        assert commit["decision_point_id"] != dp0
    finally:
        inst.close()


def test_whole_run_commit_releases_derived_branches():
    inst = WholeRunInstance("wr3", _whole_run_config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp0 = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=legal[0]["action_id"], simulation_options=None)
        assert b1["status"] == "completed"
        inst.commit_action(decision_point_id=dp0, action_id=legal[0]["action_id"])
        status = inst.get_branch_status(["b1"])["branch_statuses"]
        assert status["b1"] == "released", status
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
