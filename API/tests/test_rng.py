"""RNG `rng_id` coverage (RL担当指示：RL–Training API実装 §8 "RNG").

Verifies: same parent Decision + same `rng_id` maps to the same Hypothesis; different
`rng_id` values map to different Hypotheses; `emulate_action` with `rng_id=0` is
rejected; a non-root parent Branch only accepts its own lineage `rng_id` (contract §3/
§4.5); and no RNG internal state (seed, shuffle state, hypothesis id) ever appears in a
Response.

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

from API.identifiers import RngHypothesisTable  # noqa: E402
from API.instance_combat import CombatInstance  # noqa: E402
from API.instance_whole_run import WholeRunInstance  # noqa: E402
from API.validation import RequestRejected, validate_request  # noqa: E402


def _combat_config():
    return {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _safe_action_id(legal):
    # Avoid index 0 ("system"/End Turn) - a real, reproducible pre-existing Emulator
    # TimeoutException against CALCIFIED_CULTIST documented earlier in this project
    # (Phase M report). Any no-target card action sidesteps it.
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def test_rng_hypothesis_table_same_rng_id_same_index():
    table = RngHypothesisTable()
    a = table.hypothesis_index_for("root", "d-1", 1)
    b = table.hypothesis_index_for("root", "d-1", 1)
    assert a == b


def test_rng_hypothesis_table_different_rng_id_different_index():
    table = RngHypothesisTable()
    a = table.hypothesis_index_for("root", "d-1", 1)
    b = table.hypothesis_index_for("root", "d-1", 2)
    assert a != b


def test_rng_hypothesis_table_scoped_per_parent_and_decision():
    table = RngHypothesisTable()
    a = table.hypothesis_index_for("root", "d-1", 1)
    b = table.hypothesis_index_for("root", "d-2", 1)
    c = table.hypothesis_index_for("branch-1", "d-1", 1)
    # Different (parent, decision) keys start their own independent index sequences -
    # all legitimately 0 (first-seen), not required to differ from each other.
    assert a == 0 and b == 0 and c == 0


def test_emulate_action_rng_id_zero_rejected_at_validation():
    payload = {
        "schema_version": "0.5", "request_id": "r1", "operation": "emulate_action", "instance_id": "i1",
        "parent_branch_id": "root", "branch_id": "b1", "rng_id": 0, "decision_point_id": "d1", "action_id": "0",
    }
    raised = False
    try:
        validate_request(payload)
    except RequestRejected:
        raised = True
    assert raised, "emulate_action.rng_id=0 must be rejected"


def test_combat_same_rng_id_from_root_gives_same_hypothesis_result():
    inst = CombatInstance("r1", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="ba", rng_id=5, decision_point_id=dp0, action_id=defend_id, simulation_options=None)
        b2 = inst.emulate_action(parent_branch_id="root", branch_id="bb", rng_id=5, decision_point_id=dp0, action_id=defend_id, simulation_options=None)
        assert b1["status"] == b2["status"] == "completed"
        # Same rng_id + same action from the same parent Decision -> identical resulting
        # public state (deterministic replay of the same Hypothesis).
        assert b1["masked_emulator_dto"] == b2["masked_emulator_dto"]
    finally:
        inst.close()


def test_combat_non_root_parent_requires_its_own_lineage_rng_id():
    inst = CombatInstance("r2", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="ba", rng_id=1, decision_point_id=dp0, action_id=defend_id, simulation_options=None)
        assert b1["status"] == "completed"
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]

        raised = False
        try:
            inst.emulate_action(
                parent_branch_id="ba", branch_id="bb", rng_id=2, decision_point_id=b1["decision_point_id"],
                action_id=_safe_action_id(b1_legal), simulation_options=None,
            )
        except RequestRejected:
            raised = True
        assert raised, "a non-root parent must reject an emulate_action using a different rng_id than its own"

        # Using the SAME lineage rng_id (1) must succeed.
        deep = inst.emulate_action(
            parent_branch_id="ba", branch_id="bc", rng_id=1, decision_point_id=b1["decision_point_id"],
            action_id=_safe_action_id(b1_legal), simulation_options=None,
        )
        assert deep["status"] == "completed", deep
    finally:
        inst.close()


def test_no_rng_internal_state_leaks_into_combat_response():
    inst = CombatInstance("r3", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")
        em = inst.emulate_action(parent_branch_id="root", branch_id="ba", rng_id=1, decision_point_id=dp0, action_id=defend_id, simulation_options=None)

        def walk(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    lowered = str(k).lower()
                    for forbidden in ("seed", "shuffle", "hypothesis", "rngstate", "rng_state", "runrng", "run_rng"):
                        assert forbidden not in lowered, f"forbidden RNG-related key {k!r} found at {path}.{k}"
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")

        walk(em)
        walk(start)
    finally:
        inst.close()


def _whole_run_config(seed=18):
    return {"instance_type": "whole_run", "seed": seed, "character_id": "IRONCLAD", "ascension": 0}


def _pick_progression_action_id(legal):
    # Mirrors test_e2e.py's `_pick_action_id` - avoids getting stuck picking "system"/end
    # turn repeatedly in Combat, which would never progress a fight to its conclusion.
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def _advance_to_event_choice(inst):
    # Mirrors test_e2e.py's two-phase advance: an early event_choice (e.g. Neow's Room)
    # can be reached BEFORE the first map_select boundary ever appears - emulate_action
    # requires root to have captured a Map Snapshot first (see instance_whole_run.py's
    # `parent_view.map_snapshot is None` check), so map_select must be reached first.
    decision = inst.start_instance_response()
    for _ in range(150):
        if decision["masked_emulator_dto"].get("boundary") == "map_select":
            break
        legal = decision["masked_emulator_dto"]["legal_actions"]
        decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=_pick_progression_action_id(legal))
    else:
        raise AssertionError("never reached map_select")
    for _ in range(150):
        if decision["masked_emulator_dto"].get("boundary") == "event_choice":
            return decision
        legal = decision["masked_emulator_dto"]["legal_actions"]
        decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=_pick_progression_action_id(legal))
    raise AssertionError("never reached event_choice")


def _advance_to_map(inst):
    decision = inst.start_instance_response()
    for _ in range(50):
        if decision["masked_emulator_dto"].get("boundary") == "map_select":
            return decision
        legal = decision["masked_emulator_dto"]["legal_actions"]
        decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=legal[0]["action_id"])
    raise AssertionError("never reached map_select")


def test_whole_run_positive_rng_id_rejected_at_map_boundary():
    """Superseded design note: Whole Run's `rng_id` is now a REAL Active Event RNG
    Hypothesis (RL担当指示：Active Event RNG Hypothesis実装), not bookkeeping-only - a
    positive `rng_id` is only accepted at an `event_choice` boundary and `rejected`
    (fault_kind `rng_hypothesis_unsupported_at_boundary`) everywhere else, including
    `map_select`. Full reproducibility/fairness/separation/lineage coverage for the
    Active Event case lives in `test_event_rng_hypothesis_integration.py` (which needs a
    real Event Decision, not just a Map one, to exercise).
    """
    inst = WholeRunInstance("wr1", _whole_run_config(), branch_worker_count=2)
    try:
        decision = _advance_to_map(inst)
        dp0 = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        raised = False
        try:
            inst.emulate_action(parent_branch_id="root", branch_id="ba", rng_id=1, decision_point_id=dp0, action_id=legal[0]["action_id"], simulation_options=None)
        except RequestRejected as exc:
            raised = True
            assert exc.fault_kind == "rng_hypothesis_unsupported_at_boundary"
        assert raised, "map_select must reject a positive rng_id emulate_action"
    finally:
        inst.close()


def test_whole_run_grandchild_inherits_parent_event_rng_plan_unchanged():
    inst = WholeRunInstance("wr2", _whole_run_config(seed=1), branch_worker_count=2)
    try:
        decision = _advance_to_event_choice(inst)
        dp0 = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        assert legal, "event_choice must offer at least one legal action"

        b1 = inst.emulate_action(parent_branch_id="root", branch_id="ba", rng_id=1, decision_point_id=dp0, action_id=legal[0]["action_id"], simulation_options=None)
        assert b1["status"] == "completed", b1
        parent_book = inst._bookkeeping["ba"]  # noqa: SLF001
        assert parent_book.event_rng_plan is not None

        if b1["masked_emulator_dto"].get("boundary") != "event_choice" or not b1["masked_emulator_dto"].get("legal_actions"):
            return  # event concluded on this seed's first Action - nothing deeper to branch from
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]
        deep = inst.emulate_action(
            parent_branch_id="ba", branch_id="bc", rng_id=1, decision_point_id=b1["decision_point_id"],
            action_id=b1_legal[0]["action_id"], simulation_options=None,
        )
        assert deep["status"] == "completed", deep
        child_book = inst._bookkeeping["bc"]  # noqa: SLF001
        # A non-root parent INHERITS its own established plan unchanged - never
        # re-derives/recomputes it (RL担当指示：Active Event RNG Hypothesis実装 §4).
        assert child_book.event_rng_plan is parent_book.event_rng_plan
    finally:
        inst.close()


def test_whole_run_event_rng_hypothesis_survives_parent_release_while_child_live():
    inst = WholeRunInstance("wr3", _whole_run_config(seed=1), branch_worker_count=2)
    try:
        decision = _advance_to_event_choice(inst)
        dp0 = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]

        b1 = inst.emulate_action(parent_branch_id="root", branch_id="pa", rng_id=1, decision_point_id=dp0, action_id=legal[0]["action_id"], simulation_options=None)
        assert b1["status"] == "completed", b1
        if b1["masked_emulator_dto"].get("boundary") != "event_choice" or not b1["masked_emulator_dto"].get("legal_actions"):
            return
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]
        deep = inst.emulate_action(
            parent_branch_id="pa", branch_id="ch", rng_id=1, decision_point_id=b1["decision_point_id"],
            action_id=b1_legal[0]["action_id"], simulation_options=None,
        )
        assert deep["status"] == "completed", deep
        key = inst._bookkeeping["ch"].event_rng_plan.hypothesis_key  # noqa: SLF001
        assert inst._event_rng_registry.is_live(key)  # noqa: SLF001

        # Releasing the PARENT alone must not drop the shared registry entry while the
        # child Branch still references the same Hypothesis Key.
        inst.release_branches(["pa"])
        assert inst._event_rng_registry.is_live(key)  # noqa: SLF001

        inst.release_branches(["ch"])
        assert not inst._event_rng_registry.is_live(key)  # noqa: SLF001
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
