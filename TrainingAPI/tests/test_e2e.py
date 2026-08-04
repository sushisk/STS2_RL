"""End-to-end coverage (RL担当指示：RL–Training API実装 §8 "E2E").

Drives the FULL wire transport (`api_runtime.RLApiServerProcess` - a real separate OS
process, real `multiprocessing` Queues, real plain-dict JSON-safe payloads) via
`MockTrainingClient`, for both Whole Run and independent Combat, through the required
8-step sequence: start_instance -> root Decision -> multiple emulate_action -> a deeper
Branch off one of them -> Cancel/Release the unneeded ones -> commit_action -> next
Decision -> close_instance.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from TrainingAPI.mock_training_client import MockTrainingClient  # noqa: E402


def _combat_config():
    return {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _safe_action_id(legal):
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def test_e2e_combat_full_sequence():
    with MockTrainingClient() as client:
        # 1. start_instance
        start = client.start_instance(_combat_config())
        assert start["status"] == "completed", start

        # 2. root Decision (already have it from start_instance; confirm get_decision agrees)
        gd = client.get_decision("root")
        assert gd["status"] == "completed"
        assert gd["decision_point_id"] == start["decision_point_id"]

        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")
        bash_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "BASH")

        # 3. multiple emulate_action from root
        b1 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=start["decision_point_id"], action_id=defend_id)
        b2 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=start["decision_point_id"], action_id=bash_id)
        assert b1["status"] == "completed" and b2["status"] == "completed", (b1, b2)

        # 4. a deeper Branch off b1
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]
        deep = client.emulate_action(parent_branch_id=b1["branch_id"], rng_id=1, decision_point_id=b1["decision_point_id"], action_id=_safe_action_id(b1_legal))
        assert deep["status"] == "completed", deep

        # 5. Cancel/Release the unneeded ones (b2 and the deep branch; keep b1 pending)
        cancel = client.cancel_branches([b2["branch_id"], deep["branch_id"]])
        assert cancel["status"] == "completed", cancel
        release = client.release_branches([b2["branch_id"], deep["branch_id"]])
        assert release["status"] == "completed", release
        statuses = client.get_branch_status([b2["branch_id"], deep["branch_id"]])["branch_statuses"]
        assert statuses[b2["branch_id"]] == "released" and statuses[deep["branch_id"]] == "released", statuses

        # 6. commit_action on root
        commit = client.commit_action(start["decision_point_id"], defend_id)
        assert commit["status"] == "completed", commit
        assert commit["decision_point_id"] != start["decision_point_id"]

        # b1 must now be released too (derived from the just-committed Decision).
        b1_status = client.get_branch_status([b1["branch_id"]])["branch_statuses"][b1["branch_id"]]
        assert b1_status == "released", b1_status

        # 7. next Decision
        next_decision = client.get_decision("root")
        assert next_decision["status"] == "completed"
        assert next_decision["decision_point_id"] == commit["decision_point_id"]

        # 8. instance終了
        closed = client.close_instance()
        assert closed["status"] == "completed", closed


def _whole_run_config():
    # seed=1 confirmed (via exploratory scan) to reach map_select then event_choice
    # reasonably quickly - needed since emulate_action now requires an Active Event
    # boundary (RL担当指示：Active Event RNG Hypothesis実装).
    return {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0}


def _pick_action_id(legal):
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def test_e2e_whole_run_full_sequence():
    with MockTrainingClient() as client:
        # 1. start_instance
        decision = client.start_instance(_whole_run_config())
        assert decision["status"] == "completed", decision

        # Advance root (ordinary commit_action, no branching yet) until map_select, then
        # onward to an event_choice boundary - emulate_action now requires an Active
        # Event boundary (RL担当指示：Active Event RNG Hypothesis実装), not just a Map
        # Snapshot.
        for _ in range(150):
            if decision["masked_emulator_dto"].get("boundary") == "map_select":
                break
            legal = decision["masked_emulator_dto"]["legal_actions"]
            decision = client.commit_action(decision["decision_point_id"], _pick_action_id(legal))
        assert decision["masked_emulator_dto"].get("boundary") == "map_select", "never reached map_select"
        for _ in range(150):
            if decision["masked_emulator_dto"].get("boundary") == "event_choice":
                break
            legal = decision["masked_emulator_dto"]["legal_actions"]
            decision = client.commit_action(decision["decision_point_id"], _pick_action_id(legal))
        assert decision["masked_emulator_dto"].get("boundary") == "event_choice", "never reached event_choice"

        # 2. root Decision
        gd = client.get_decision("root")
        assert gd["status"] == "completed"
        assert gd["decision_point_id"] == decision["decision_point_id"]

        legal = decision["masked_emulator_dto"]["legal_actions"]
        assert len(legal) >= 1

        # 3. multiple emulate_action from root (same rng_id different Action if
        # possible, plus a different rng_id)
        second_action = legal[1]["action_id"] if len(legal) > 1 else legal[0]["action_id"]
        b1 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=decision["decision_point_id"], action_id=legal[0]["action_id"])
        assert b1["status"] == "completed", b1
        b2 = client.emulate_action(parent_branch_id="root", rng_id=2, decision_point_id=decision["decision_point_id"], action_id=second_action)
        assert b2["status"] == "completed", b2

        # 4. a deeper Branch off b1 (if b1 didn't immediately hit run_terminal/a new map)
        deep = None
        if (
            b1["masked_emulator_dto"].get("boundary") == "event_choice"
            and not b1.get("masked_emulator_dto", {}).get("run_terminal")
            and b1["masked_emulator_dto"].get("legal_actions")
        ):
            b1_legal = b1["masked_emulator_dto"]["legal_actions"]
            deep = client.emulate_action(parent_branch_id=b1["branch_id"], rng_id=1, decision_point_id=b1["decision_point_id"], action_id=b1_legal[0]["action_id"])
            assert deep["status"] == "completed", deep

        # 5. Cancel/Release unneeded Branches
        to_release = [b2["branch_id"]] + ([deep["branch_id"]] if deep else [])
        client.cancel_branches(to_release)
        client.release_branches(to_release)
        statuses = client.get_branch_status(to_release)["branch_statuses"]
        assert all(s == "released" for s in statuses.values()), statuses

        # 6. commit_action on root
        commit = client.commit_action(decision["decision_point_id"], legal[0]["action_id"])
        assert commit["status"] == "completed", commit

        # 7. next Decision
        next_decision = client.get_decision("root")
        assert next_decision["status"] == "completed"

        # 8. instance終了
        closed = client.close_instance()
        assert closed["status"] == "completed", closed


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
