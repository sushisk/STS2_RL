"""Active Event RNG Hypothesis integration coverage (RL担当指示：Active Event RNG
Hypothesis実装 §9), exercised against the REAL Emulator/WholeRunWorkerPool - complements
the pure-Python unit tests in `test_event_rng_hypothesis.py` (derivation function +
registry in isolation, no CLR).

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from TrainingAPI.instance_whole_run import WholeRunInstance  # noqa: E402
from TrainingAPI.mock_training_client import MockTrainingClient  # noqa: E402
from TrainingAPI.validation import RequestRejected  # noqa: E402

_SEED = 1  # confirmed (via exploratory scan) to reach map_select then event_choice quickly.


def _config():
    return {"instance_type": "whole_run", "seed": _SEED, "character_id": "IRONCLAD", "ascension": 0}


def _pick_action_id(legal):
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def _advance_until(inst, predicate, max_steps=150):
    decision = inst.get_decision("root")
    for _ in range(max_steps):
        boundary = decision["masked_emulator_dto"].get("boundary")
        if predicate(boundary):
            return decision
        legal = decision["masked_emulator_dto"]["legal_actions"]
        if not legal:
            raise AssertionError(f"stuck at boundary={boundary} with no legal actions")
        decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=_pick_action_id(legal))
    raise AssertionError(f"predicate never satisfied, last boundary={decision['masked_emulator_dto'].get('boundary')}")


def _reach_event(inst):
    _advance_until(inst, lambda b: b == "map_select")
    return _advance_until(inst, lambda b: b == "event_choice")


# -- reproducibility -----------------------------------------------------------------


def test_same_action_same_rng_id_gives_identical_result():
    inst = WholeRunInstance("t1", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        b2 = inst.emulate_action(parent_branch_id="root", branch_id="b2", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        assert b1["status"] == b2["status"] == "completed"
        assert b1["masked_emulator_dto"] == b2["masked_emulator_dto"]
    finally:
        inst.close()


def test_separate_sessions_same_input_reproduce_identically():
    results = []
    for _ in range(2):
        inst = WholeRunInstance("t2", _config(), branch_worker_count=2)
        try:
            decision = _reach_event(inst)
            dp = decision["decision_point_id"]
            action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
            em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=5, decision_point_id=dp, action_id=action_id, simulation_options=None)
            assert em["status"] == "completed", em
            results.append(em["masked_emulator_dto"])
        finally:
            inst.close()
    assert results[0] == results[1], "two independent sessions with identical input must reproduce identically"


def test_reproducible_after_worker_respawn():
    inst = WholeRunInstance("t3", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        before = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=3, decision_point_id=dp, action_id=action_id, simulation_options=None)
        assert before["status"] == "completed"

        for slot in inst._pool.worker_slots:  # noqa: SLF001
            inst._pool.respawn_worker(slot, inst._lease_registry)  # noqa: SLF001

        after = inst.emulate_action(parent_branch_id="root", branch_id="b2", rng_id=3, decision_point_id=dp, action_id=action_id, simulation_options=None)
        assert after["status"] == "completed", after
        assert before["masked_emulator_dto"] == after["masked_emulator_dto"], "result must be unchanged after Worker Respawn"
    finally:
        inst.close()


# -- fairness / separation -------------------------------------------------------------


def test_fairness_same_rng_id_reuses_one_hypothesis_regardless_of_action():
    inst = WholeRunInstance("t4", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        assert len(legal) >= 1
        key = ("root", dp, 1)
        inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=legal[0]["action_id"], simulation_options=None)
        gen_after_first = inst._event_rng_registry.generation_of(key)  # noqa: SLF001
        second_action = legal[1]["action_id"] if len(legal) > 1 else legal[0]["action_id"]
        inst.emulate_action(parent_branch_id="root", branch_id="b2", rng_id=1, decision_point_id=dp, action_id=second_action, simulation_options=None)
        gen_after_second = inst._event_rng_registry.generation_of(key)  # noqa: SLF001
        assert gen_after_first == gen_after_second == 1, "the SAME rng_id from the same Decision must reuse one Hypothesis (generation must not increment)"
    finally:
        inst.close()


def test_separation_different_rng_id_different_internal_hypothesis():
    inst = WholeRunInstance("t5", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        inst.emulate_action(parent_branch_id="root", branch_id="b2", rng_id=2, decision_point_id=dp, action_id=action_id, simulation_options=None)
        state1 = inst._event_rng_registry._entries[("root", dp, 1)].state  # noqa: SLF001
        state2 = inst._event_rng_registry._entries[("root", dp, 2)].state  # noqa: SLF001
        assert state1 != state2, "different rng_id must derive a different internal Event RNG Hypothesis state"
    finally:
        inst.close()


def test_deep_branch_inherits_parent_hypothesis_without_regenerating():
    inst = WholeRunInstance("t6", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=legal[0]["action_id"], simulation_options=None)
        assert b1["status"] == "completed", b1
        if b1["masked_emulator_dto"].get("boundary") != "event_choice":
            return  # The first branch left the event immediately, so there is no deeper event chain to verify here.
        parent_key = inst._bookkeeping["b1"].event_rng_key  # noqa: SLF001
        if parent_key is None:
            return  # the Event concluded in one step (no further Decision within it) - nothing to chain from, not a failure.
        gen_before = inst._event_rng_registry.generation_of(parent_key)  # noqa: SLF001
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]
        deep = inst.emulate_action(parent_branch_id="b1", branch_id="b2", rng_id=1, decision_point_id=b1["decision_point_id"], action_id=b1_legal[0]["action_id"], simulation_options=None)
        assert deep["status"] == "completed", deep
        assert inst._bookkeeping["b2"].event_rng_key == parent_key, "a deep Branch must inherit its parent's exact Hypothesis Key"
        gen_after = inst._event_rng_registry.generation_of(parent_key)  # noqa: SLF001
        assert gen_before == gen_after, "extending an existing Hypothesis chain must not create/regenerate a new Hypothesis"
    finally:
        inst.close()


def test_non_root_parent_wrong_lineage_rng_id_rejected():
    inst = WholeRunInstance("t6b", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        b1 = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=legal[0]["action_id"], simulation_options=None)
        assert b1["status"] == "completed", b1
        if inst._bookkeeping["b1"].event_rng_key is None:  # noqa: SLF001
            return  # Event concluded in one step - nothing further to chain from.
        b1_legal = b1["masked_emulator_dto"]["legal_actions"]
        raised = False
        try:
            inst.emulate_action(
                parent_branch_id="b1", branch_id="b2", rng_id=2, decision_point_id=b1["decision_point_id"],
                action_id=b1_legal[0]["action_id"], simulation_options=None,
            )
        except RequestRejected:
            raised = True
        assert raised, "a non-root parent must reject an emulate_action using a different rng_id than its own lineage"
    finally:
        inst.close()


# -- root protection --------------------------------------------------------------------


def test_root_snapshot_unchanged_by_hypothesis_generation_and_simulation():
    inst = WholeRunInstance("t7", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        before = inst.get_decision("root")
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        inst.emulate_action(parent_branch_id="root", branch_id="b2", rng_id=2, decision_point_id=dp, action_id=action_id, simulation_options=None)
        after = inst.get_decision("root")
        assert after["decision_point_id"] == before["decision_point_id"]
        assert after["masked_emulator_dto"] == before["masked_emulator_dto"], "root must be byte-for-byte unchanged by Hypothesis generation/Branch Simulation"

        commit = inst.commit_action(decision_point_id=dp, action_id=action_id)
        assert commit["status"] == "completed", "commit_action must still succeed using root's own real RNG regardless of prior Simulation"
    finally:
        inst.close()


# -- boundary rejection -----------------------------------------------------------------


def test_positive_rng_id_rejected_outside_active_event():
    inst = WholeRunInstance("t8", _config(), branch_worker_count=1)
    try:
        decision = _advance_until(inst, lambda b: b == "map_select")
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        raised = False
        try:
            inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        except RequestRejected as exc:
            raised = True
            assert exc.fault_kind == "rng_hypothesis_unsupported_at_boundary", exc.fault_kind
            assert exc.error == "Active Event RNG hypothesis is not available at this boundary."
        assert raised, "map_select must reject a positive rng_id emulate_action"
    finally:
        inst.close()


def test_positive_rng_id_rejected_at_every_non_event_boundary_encountered():
    """Drives forward broadly and asserts rejection at every DISTINCT non-event_choice
    boundary actually reached (covers whichever of Map/Combat-stable/Reward/Shop/Rest
    this seed's early game surfaces, without depending on a specific room sequence)."""
    inst = WholeRunInstance("t9", _config(), branch_worker_count=1)
    try:
        seen_boundaries = set()
        decision = inst.get_decision("root")
        for _ in range(120):
            boundary = decision["masked_emulator_dto"].get("boundary")
            if boundary in ("run_terminal", "event_choice"):
                pass
            elif boundary not in seen_boundaries:
                seen_boundaries.add(boundary)
                legal = decision["masked_emulator_dto"]["legal_actions"]
                if legal:
                    raised = False
                    try:
                        inst.emulate_action(
                            parent_branch_id="root", branch_id=f"probe-{boundary}-{len(seen_boundaries)}", rng_id=1,
                            decision_point_id=decision["decision_point_id"], action_id=legal[0]["action_id"], simulation_options=None,
                        )
                    except RequestRejected as exc:
                        raised = True
                        assert exc.fault_kind == "rng_hypothesis_unsupported_at_boundary"
                    assert raised, f"boundary {boundary!r} must reject a positive rng_id emulate_action"
            if boundary == "run_terminal":
                break
            legal = decision["masked_emulator_dto"]["legal_actions"]
            if not legal:
                break
            decision = inst.commit_action(decision_point_id=decision["decision_point_id"], action_id=_pick_action_id(legal))
        assert len(seen_boundaries) >= 2, f"expected to observe several distinct non-event boundaries, saw {seen_boundaries}"
    finally:
        inst.close()


# -- lifecycle ----------------------------------------------------------------------------


def test_cancel_releases_hypothesis_reference():
    inst = WholeRunInstance("t10", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        assert em["status"] == "completed"
        key = inst._bookkeeping["b1"].event_rng_key  # noqa: SLF001
        if key is None:
            return
        assert inst._event_rng_registry.is_live(key)  # noqa: SLF001
        inst.cancel_branches(["b1"])
        assert not inst._event_rng_registry.is_live(key), "Cancel must release the Hypothesis reference"  # noqa: SLF001
    finally:
        inst.close()


def test_release_releases_hypothesis_reference():
    inst = WholeRunInstance("t11", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        key = inst._bookkeeping["b1"].event_rng_key  # noqa: SLF001
        if key is None:
            return
        inst.release_branches(["b1"])
        assert not inst._event_rng_registry.is_live(key)  # noqa: SLF001
    finally:
        inst.close()


def test_root_commit_releases_all_hypothesis_references():
    inst = WholeRunInstance("t12", _config(), branch_worker_count=2)
    try:
        decision = _reach_event(inst)
        dp = decision["decision_point_id"]
        action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
        em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
        key = inst._bookkeeping["b1"].event_rng_key  # noqa: SLF001
        inst.commit_action(decision_point_id=dp, action_id=action_id)
        assert not inst._event_rng_registry._entries  # noqa: SLF001
        if key is not None:
            assert not inst._event_rng_registry.is_live(key)  # noqa: SLF001
    finally:
        inst.close()


def test_instance_close_releases_all_hypothesis_references():
    inst = WholeRunInstance("t13", _config(), branch_worker_count=2)
    decision = _reach_event(inst)
    dp = decision["decision_point_id"]
    action_id = decision["masked_emulator_dto"]["legal_actions"][0]["action_id"]
    inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp, action_id=action_id, simulation_options=None)
    registry = inst._event_rng_registry  # noqa: SLF001
    inst.close()
    assert not registry._entries  # noqa: SLF001


# -- E2E (Mock Training Client, real OS-process transport) ------------------------------


def test_e2e_mock_training_client_active_event_hypothesis_flow():
    with MockTrainingClient() as client:
        start = client.start_instance(_config())
        assert start["status"] == "completed"

        # 1. reach an Event Decision.
        decision = start
        for _ in range(150):
            boundary = decision["masked_emulator_dto"].get("boundary")
            if boundary == "event_choice" and decision is not start:
                break
            legal = decision["masked_emulator_dto"]["legal_actions"]
            decision = client.commit_action(decision["decision_point_id"], _pick_action_id(legal))
            if decision["masked_emulator_dto"].get("boundary") == "event_choice":
                break
        assert decision["masked_emulator_dto"].get("boundary") == "event_choice", "never reached a second Event Decision"

        dp = decision["decision_point_id"]
        legal = decision["masked_emulator_dto"]["legal_actions"]
        action_id = legal[0]["action_id"]
        second_action_id = legal[1]["action_id"] if len(legal) > 1 else action_id

        # 2. same rng_id, multiple Actions.
        r1 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=dp, action_id=action_id)
        r2 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=dp, action_id=second_action_id)
        assert r1["status"] == "completed" and r2["status"] == "completed"

        # 3. same Action, multiple rng_id.
        r3 = client.emulate_action(parent_branch_id="root", rng_id=2, decision_point_id=dp, action_id=action_id)
        assert r3["status"] == "completed"

        # 4. compare Branch results.
        statuses = client.get_branch_status([r1["branch_id"], r2["branch_id"], r3["branch_id"]])["branch_statuses"]
        assert all(s == "completed" for s in statuses.values()), statuses

        # 5. Cancel/Release unneeded Branches.
        client.cancel_branches([r2["branch_id"], r3["branch_id"]])
        client.release_branches([r2["branch_id"], r3["branch_id"]])

        # 6. commit_action on root.
        commit = client.commit_action(dp, action_id)
        assert commit["status"] == "completed", commit

        # 7. old Hypotheses (and r1, derived from the now-stale Decision) invalidated.
        old_status = client.get_branch_status([r1["branch_id"]])["branch_statuses"][r1["branch_id"]]
        assert old_status == "released", old_status

        client.close_instance()


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
