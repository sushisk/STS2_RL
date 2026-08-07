"""Coverage for `CombatInstance.emulate_actions` (DTO v0.7 batch operation).

Exercises the two-phase contract:

* Phase A rejects the whole batch before mutation if any item is invalid.
* Every non-root parent must already exist when the batch starts.
* Phase B queues all WorkItems before one blocking `poll()` call.
* Per-Branch execution outcomes are terminal and can mix completed/faulted results.
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
from API.validation import RequestRejected  # noqa: E402
from search.branch_worker_pool import BranchResult  # noqa: E402


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


def _bash_action_id(legal_actions: list) -> str:
    return next(
        action["action_id"]
        for action in legal_actions
        if action.get("parameters", {}).get("cardId") == "BASH"
    )


def _defend_action_id(legal_actions: list) -> str:
    return next(
        action["action_id"]
        for action in legal_actions
        if action.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD"
    )


def _prepare_two_parents(inst: CombatInstance) -> dict:
    start = inst.start_instance_response()
    legal = start["masked_emulator_dto"]["legal_actions"]
    return inst.emulate_actions(
        items=[
            {
                "parent_branch_id": "root",
                "branch_id": "b1",
                "rng_id": 1,
                "decision_point_id": start["decision_point_id"],
                "action_id": _bash_action_id(legal),
            },
            {
                "parent_branch_id": "root",
                "branch_id": "b2",
                "rng_id": 2,
                "decision_point_id": start["decision_point_id"],
                "action_id": _defend_action_id(legal),
            },
        ],
        simulation_options=None,
    )


def test_multi_parent_batch_executes_all_children() -> None:
    """Prepare b1/b2 first, then extend both parents in one later batch."""
    inst = CombatInstance("batch-multi-parent", _combat_config(), worker_count=2)
    try:
        prepared = _prepare_two_parents(inst)
        assert prepared["branch_results"]["b1"]["status"] == "completed", prepared
        assert prepared["branch_results"]["b2"]["status"] == "completed", prepared

        b1_view = prepared["branch_results"]["b1"]
        b2_view = prepared["branch_results"]["b2"]
        response = inst.emulate_actions(
            items=[
                {
                    "parent_branch_id": "b1",
                    "branch_id": "c1",
                    "rng_id": 1,
                    "decision_point_id": b1_view["decision_point_id"],
                    "action_id": b1_view["masked_emulator_dto"]["legal_actions"][0]["action_id"],
                },
                {
                    "parent_branch_id": "b2",
                    "branch_id": "c2",
                    "rng_id": 2,
                    "decision_point_id": b2_view["decision_point_id"],
                    "action_id": b2_view["masked_emulator_dto"]["legal_actions"][0]["action_id"],
                },
            ],
            simulation_options=None,
        )
        assert response["status"] == "completed", response
        assert response["branch_results"]["c1"]["status"] == "completed", response
        assert response["branch_results"]["c2"]["status"] == "completed", response
        assert response["branch_results"]["c1"]["parent_branch_id"] == "b1"
        assert response["branch_results"]["c2"]["parent_branch_id"] == "b2"
    finally:
        inst.close()


def test_same_batch_new_parent_is_rejected_atomically() -> None:
    """root->b1 and b1->b1a cannot appear in the same v0.7 batch."""
    inst = CombatInstance("batch-same-request-parent", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        raised = False
        try:
            inst.emulate_actions(
                items=[
                    {
                        "parent_branch_id": "root",
                        "branch_id": "b1",
                        "rng_id": 1,
                        "decision_point_id": start["decision_point_id"],
                        "action_id": _bash_action_id(legal),
                    },
                    {
                        "parent_branch_id": "b1",
                        "branch_id": "b1a",
                        "rng_id": 1,
                        "decision_point_id": "not-issued-yet",
                        "action_id": "0",
                    },
                ],
                simulation_options=None,
            )
        except RequestRejected:
            raised = True
        assert raised, "same-batch newly-created parent must be rejected"
        assert not inst._branch_ids.is_known("b1")  # noqa: SLF001
        assert not inst._branch_ids.is_known("b1a")  # noqa: SLF001
        assert inst._bookkeeping == {}  # noqa: SLF001
    finally:
        inst.close()


def test_atomic_rejection_leaves_no_partial_branches() -> None:
    inst = CombatInstance("batch-atomic-rejection", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        bash_id = _bash_action_id(legal)
        dp_root = start["decision_point_id"]

        raised = False
        try:
            inst.emulate_actions(
                items=[
                    {
                        "parent_branch_id": "root",
                        "branch_id": "b1",
                        "rng_id": 1,
                        "decision_point_id": dp_root,
                        "action_id": bash_id,
                    },
                    {
                        "parent_branch_id": "root",
                        "branch_id": "b2",
                        "rng_id": 2,
                        "decision_point_id": dp_root,
                        "action_id": "not-a-real-action-id",
                    },
                ],
                simulation_options=None,
            )
        except RequestRejected:
            raised = True
        assert raised, "batch with one invalid item must raise RequestRejected"
        assert not inst._branch_ids.is_known("b1")  # noqa: SLF001
        assert not inst._branch_ids.is_known("b2")  # noqa: SLF001
        assert inst._bookkeeping == {}  # noqa: SLF001
        assert inst._branch_manager.active_branch_count() == 0  # noqa: SLF001
        root_again = inst.get_decision("root")
        assert root_again["decision_point_id"] == dp_root
    finally:
        inst.close()


def test_duplicate_branch_id_within_batch_is_rejected() -> None:
    inst = CombatInstance("batch-dup-branch-id", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        bash_id = _bash_action_id(legal)
        dp_root = start["decision_point_id"]

        raised = False
        try:
            inst.emulate_actions(
                items=[
                    {
                        "parent_branch_id": "root",
                        "branch_id": "dup",
                        "rng_id": 1,
                        "decision_point_id": dp_root,
                        "action_id": bash_id,
                    },
                    {
                        "parent_branch_id": "root",
                        "branch_id": "dup",
                        "rng_id": 2,
                        "decision_point_id": dp_root,
                        "action_id": bash_id,
                    },
                ],
                simulation_options=None,
            )
        except RequestRejected:
            raised = True
        assert raised
        assert not inst._branch_ids.is_known("dup")  # noqa: SLF001
    finally:
        inst.close()


def test_mixed_execution_outcome_flows_through_emulate_actions() -> None:
    """Inject one fault through the manager poll result and exercise the full batch path."""
    inst = CombatInstance("batch-mixed-outcome", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        original_poll = inst._branch_manager.poll  # noqa: SLF001

        def _mixed_poll(*args, **kwargs):
            results = original_poll(*args, **kwargs)
            fault_internal_id = inst._bookkeeping["faulty"].internal_id  # noqa: SLF001
            original = results[fault_internal_id]
            results[fault_internal_id] = BranchResult(
                status="fault",
                work_item=original.work_item,
                execution_mode=original.execution_mode,
                worker_id=original.worker_id,
                worker_generation=original.worker_generation,
                diagnostics={
                    "message": "synthetic worker crash",
                    "fault_kind": "worker_process_crash",
                },
            )
            return results

        inst._branch_manager.poll = _mixed_poll  # noqa: SLF001
        try:
            response = inst.emulate_actions(
                items=[
                    {
                        "parent_branch_id": "root",
                        "branch_id": "healthy",
                        "rng_id": 1,
                        "decision_point_id": start["decision_point_id"],
                        "action_id": _bash_action_id(legal),
                    },
                    {
                        "parent_branch_id": "root",
                        "branch_id": "faulty",
                        "rng_id": 2,
                        "decision_point_id": start["decision_point_id"],
                        "action_id": _defend_action_id(legal),
                    },
                ],
                simulation_options=None,
            )
        finally:
            inst._branch_manager.poll = original_poll  # noqa: SLF001

        assert response["status"] == "completed", response
        assert response["branch_results"]["healthy"]["status"] == "completed", response
        assert response["branch_results"]["faulty"]["status"] == "faulted", response
        assert response["branch_results"]["faulty"]["fault_kind"] == "worker_process_crash"
    finally:
        inst.close()


def test_missing_poll_result_quarantines_entire_batch() -> None:
    inst = CombatInstance("batch-missing-result", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        original_poll = inst._branch_manager.poll  # noqa: SLF001

        # Simulate the true invariant breach: poll returns before resolving anything,
        # leaving both manager records queued. emulate_actions must not translate that
        # into a normal completed response with a synthetic per-Branch fault.
        def _missing_poll(*args, **kwargs):
            return {}

        inst._branch_manager.poll = _missing_poll  # noqa: SLF001
        try:
            try:
                inst.emulate_actions(
                    items=[
                        {
                            "parent_branch_id": "root",
                            "branch_id": "ok",
                            "rng_id": 1,
                            "decision_point_id": start["decision_point_id"],
                            "action_id": _bash_action_id(legal),
                        },
                        {
                            "parent_branch_id": "root",
                            "branch_id": "missing",
                            "rng_id": 2,
                            "decision_point_id": start["decision_point_id"],
                            "action_id": _defend_action_id(legal),
                        },
                    ],
                    simulation_options=None,
                )
            except RuntimeError as exc:
                assert "no terminal result" in str(exc)
            else:
                raise AssertionError("missing poll result must fail the whole batch")
        finally:
            inst._branch_manager.poll = original_poll  # noqa: SLF001

        assert inst._branch_manager.active_branch_count() == 0  # noqa: SLF001
        assert {record.state for record in inst._branch_manager._records.values()} == {"released"}  # noqa: SLF001
        assert inst._bookkeeping == {}  # noqa: SLF001
        assert inst._rng_table._index_by_key == {}  # noqa: SLF001
        assert inst._rng_table._next_index_by_parent_decision == {}  # noqa: SLF001

        # IDs were committed before the invariant failure, so they stay burned but no
        # longer expose a contradictory public running/completed state.
        assert inst._branch_ids.is_known("ok")  # noqa: SLF001
        assert inst._branch_ids.is_known("missing")  # noqa: SLF001
        for branch_id in ("ok", "missing"):
            try:
                inst.get_decision(branch_id)
            except RequestRejected:
                pass
            else:
                raise AssertionError("quarantined public branch must not be usable")
        assert original_poll(timeout=0.01) == {}
    finally:
        inst.close()


def test_parallel_dispatch_queues_all_items_before_single_poll() -> None:
    inst = CombatInstance("batch-parallel-dispatch", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        legal = start["masked_emulator_dto"]["legal_actions"]
        observed_queued_snapshot = {}
        observed_worker_ids: set[int] = set()
        original_poll = inst._branch_manager.poll  # noqa: SLF001

        def _spying_poll(*args, **kwargs):
            for branch_id, record in inst._branch_manager._records.items():  # noqa: SLF001
                observed_queued_snapshot[branch_id] = record.state
            results = original_poll(*args, **kwargs)
            observed_worker_ids.update(
                result.worker_id for result in results.values() if result.worker_id is not None
            )
            return results

        inst._branch_manager.poll = _spying_poll  # noqa: SLF001
        try:
            response = inst.emulate_actions(
                items=[
                    {
                        "parent_branch_id": "root",
                        "branch_id": "p1",
                        "rng_id": 1,
                        "decision_point_id": start["decision_point_id"],
                        "action_id": _bash_action_id(legal),
                    },
                    {
                        "parent_branch_id": "root",
                        "branch_id": "p2",
                        "rng_id": 2,
                        "decision_point_id": start["decision_point_id"],
                        "action_id": _defend_action_id(legal),
                    },
                ],
                simulation_options=None,
            )
        finally:
            inst._branch_manager.poll = original_poll  # noqa: SLF001

        assert response["status"] == "completed", response
        assert len(observed_queued_snapshot) == 2
        assert set(observed_queued_snapshot.values()) == {"queued"}
        assert len(observed_worker_ids) == 2, observed_worker_ids
        assert response["branch_results"]["p1"]["status"] == "completed"
        assert response["branch_results"]["p2"]["status"] == "completed"
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
