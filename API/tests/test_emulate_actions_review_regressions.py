"""Regression coverage for the DTO v0.7 review findings."""

from __future__ import annotations

import queue
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import search.branch_manager as branch_manager_module  # noqa: E402
from API.instance_combat import CombatInstance  # noqa: E402
from API.validation import RequestRejected  # noqa: E402
from search.branch_manager import BranchManager  # noqa: E402


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


def _root_items(start: dict, count: int, *, prefix: str = "b") -> list[dict]:
    action_id = start["masked_emulator_dto"]["legal_actions"][0]["action_id"]
    return [
        {
            "parent_branch_id": "root",
            "branch_id": f"{prefix}{index}",
            "rng_id": index + 1,
            "decision_point_id": start["decision_point_id"],
            "action_id": action_id,
        }
        for index in range(count)
    ]


def test_phase_b_submit_failure_restores_rng_and_leaves_no_partial_state() -> None:
    inst = CombatInstance("review-submit-fault", _combat_config(), worker_count=2)
    original_submit_many = inst._branch_manager.submit_many  # noqa: SLF001

    def _failing_submit_many(entries):
        assert len(entries) == 2
        raise RuntimeError("synthetic submit failure")

    try:
        start = inst.start_instance_response()
        inst._branch_manager.submit_many = _failing_submit_many  # type: ignore[method-assign]  # noqa: SLF001
        try:
            inst.emulate_actions(items=_root_items(start, 2), simulation_options=None)
        except RuntimeError as exc:
            assert "synthetic submit failure" in str(exc)
        else:
            raise AssertionError("synthetic submit failure must propagate")

        assert inst._bookkeeping == {}  # noqa: SLF001
        assert inst._branch_manager.active_branch_count() == 0  # noqa: SLF001
        assert inst._rng_table._index_by_key == {}  # noqa: SLF001
        assert inst._rng_table._next_index_by_parent_decision == {}  # noqa: SLF001
        assert not inst._branch_ids.is_known("b0")  # noqa: SLF001
        assert not inst._branch_ids.is_known("b1")  # noqa: SLF001
    finally:
        inst._branch_manager.submit_many = original_submit_many  # type: ignore[method-assign]  # noqa: SLF001
        inst.close()


def test_phase_b_poll_failure_quarantines_internal_branches() -> None:
    inst = CombatInstance("review-poll-fault", _combat_config(), worker_count=2)
    original_poll = inst._branch_manager.poll  # noqa: SLF001

    def _failing_poll(*args, **kwargs):
        raise RuntimeError("synthetic coordinator failure")

    try:
        start = inst.start_instance_response()
        inst._branch_manager.poll = _failing_poll  # type: ignore[method-assign]  # noqa: SLF001
        try:
            inst.emulate_actions(items=_root_items(start, 2, prefix="q"), simulation_options=None)
        except RuntimeError as exc:
            assert "synthetic coordinator failure" in str(exc)
        else:
            raise AssertionError("synthetic coordinator failure must propagate")
        finally:
            inst._branch_manager.poll = original_poll  # type: ignore[method-assign]  # noqa: SLF001

        assert inst._bookkeeping == {}  # noqa: SLF001
        assert inst._rng_table._index_by_key == {}  # noqa: SLF001
        assert inst._rng_table._next_index_by_parent_decision == {}  # noqa: SLF001
        assert inst._branch_manager.active_branch_count() == 0  # noqa: SLF001
        assert len(inst._branch_manager._records) == 2  # noqa: SLF001
        assert {record.state for record in inst._branch_manager._records.values()} == {"released"}  # noqa: SLF001

        # Public IDs may remain permanently burned, but they are quarantined rather than
        # usable. A later poll must have no queued leftovers to execute.
        assert inst._branch_ids.is_known("q0")  # noqa: SLF001
        try:
            inst.get_decision("q0")
        except RequestRejected:
            pass
        else:
            raise AssertionError("quarantined public branch must not be usable")
        assert original_poll(timeout=0.01) == {}
    finally:
        inst.close()


def test_batch_capacity_boundary_is_explicitly_64_then_65_rejects() -> None:
    inst = CombatInstance(
        "review-batch-capacity", _combat_config(), worker_count=2, max_branches=64
    )
    original_submit_many = inst._branch_manager.submit_many  # noqa: SLF001
    observed_sizes: list[int] = []

    class _ReachedSubmit(RuntimeError):
        pass

    def _stop_at_submit(entries):
        observed_sizes.append(len(entries))
        raise _ReachedSubmit

    try:
        start = inst.start_instance_response()
        inst._branch_manager.submit_many = _stop_at_submit  # type: ignore[method-assign]  # noqa: SLF001

        try:
            inst.emulate_actions(items=_root_items(start, 64, prefix="ok"), simulation_options=None)
        except _ReachedSubmit:
            pass
        else:
            raise AssertionError("64 items should reach the manager submission boundary")
        assert observed_sizes == [64]

        try:
            inst.emulate_actions(items=_root_items(start, 65, prefix="too-many"), simulation_options=None)
        except RequestRejected as exc:
            assert "max batch size 64" in str(exc)
        else:
            raise AssertionError("65 items must be rejected deterministically")
        assert observed_sizes == [64]
    finally:
        inst._branch_manager.submit_many = original_submit_many  # type: ignore[method-assign]  # noqa: SLF001
        inst.close()


class _FakeWorkItem:
    def __init__(self, index: int) -> None:
        self.context_id = f"context-{index}"
        self.search_hypothesis_id = f"hypothesis-{index}"


class _FakeRequest:
    def __init__(self, work_item, execution_mode, expected_lease=None) -> None:
        self.work_item = work_item
        self.execution_mode = execution_mode
        self.expected_lease = expected_lease


class _FakeLeaseRegistry:
    def invalidate(self, *args) -> None:
        pass

    def set(self, *args) -> None:
        pass

    def invalidate_worker(self, *args) -> None:
        pass


class _TimedResultQueue:
    def __init__(self, clock: list[float], events: list[tuple[float, int, object]]) -> None:
        self._clock = clock
        self._events = list(events)

    def get(self, timeout: float):
        deadline = self._clock[0] + timeout
        if self._events and self._events[0][0] <= deadline + 1e-12:
            at, request_id, result = self._events.pop(0)
            self._clock[0] = at
            return request_id, result
        self._clock[0] = deadline
        raise queue.Empty


class _FakePool:
    def __init__(self, clock: list[float], result_queue: _TimedResultQueue) -> None:
        self.worker_ids = [0, 1]
        self.worker_generations = {0: 1, 1: 1}
        self._result_queue = result_queue
        self._next_request_id = 0
        self._clock = clock
        self.respawns: list[tuple[int, float]] = []

    def _submit(self, worker_id: int, request) -> int:
        self._next_request_id += 1
        return self._next_request_id

    def respawn_worker(self, worker_id: int, *, lease_registry) -> None:
        self.respawns.append((worker_id, self._clock[0]))
        self.worker_generations[worker_id] += 1


class _ExplodingPool(_FakePool):
    def _submit(self, worker_id: int, request) -> int:
        self._next_request_id += 1
        if self._next_request_id == 2:
            raise RuntimeError("synthetic IPC submit failure")
        return self._next_request_id


def _install_fake_routing():
    original_route = branch_manager_module._route_work_item
    original_ipc = branch_manager_module._work_item_for_ipc

    def _route(work_item, lease_registry, *, worker_ids, worker_generations, next_bootstrap_index):
        worker_id = worker_ids[next_bootstrap_index % len(worker_ids)]
        return (
            _FakeRequest(work_item, "bootstrap_step"),
            worker_id,
            next_bootstrap_index + 1,
            None,
        )

    branch_manager_module._route_work_item = _route
    branch_manager_module._work_item_for_ipc = lambda item: item
    return original_route, original_ipc


def test_dispatch_exception_after_first_submit_releases_entire_poll_batch() -> None:
    clock = [0.0]
    pool = _ExplodingPool(clock, _TimedResultQueue(clock, []))
    manager = BranchManager(pool, _FakeLeaseRegistry(), max_branches=8)  # type: ignore[arg-type]
    branch_ids = manager.submit_many(
        [(_FakeWorkItem(index), None) for index in range(4)]  # type: ignore[list-item]
    )

    original_route, original_ipc = _install_fake_routing()
    try:
        try:
            manager.poll(timeout=1.0)
        except RuntimeError as exc:
            assert "synthetic IPC submit failure" in str(exc)
        else:
            raise AssertionError("synthetic IPC submit failure must propagate")
    finally:
        branch_manager_module._route_work_item = original_route
        branch_manager_module._work_item_for_ipc = original_ipc

    assert manager.active_branch_count() == 0
    assert {manager._records[branch_id].state for branch_id in branch_ids} == {"released"}  # noqa: SLF001
    assert pool.respawns == [(0, 0.0)]


def test_four_items_two_workers_hung_worker_keeps_absolute_deadline() -> None:
    """A healthy worker result must not reset another worker's timeout."""
    clock = [0.0]
    healthy_result_1 = SimpleNamespace(
        status="fault", established_lease=None, worker_id=1, diagnostics={"fault_kind": "synthetic"}
    )
    healthy_result_2 = SimpleNamespace(
        status="fault", established_lease=None, worker_id=1, diagnostics={"fault_kind": "synthetic"}
    )
    timed_queue = _TimedResultQueue(
        clock,
        [
            (0.6, 2, healthy_result_1),
            (1.2, 4, healthy_result_2),
        ],
    )
    pool = _FakePool(clock, timed_queue)
    leases = _FakeLeaseRegistry()
    manager = BranchManager(pool, leases, max_branches=8)  # type: ignore[arg-type]

    original_route, original_ipc = _install_fake_routing()
    original_fault = branch_manager_module._fault_result
    original_monotonic = branch_manager_module.time.monotonic

    def _fault(work_item, execution_mode, worker_id, worker_generation, exc, *, fault_kind):
        return SimpleNamespace(
            status="fault",
            established_lease=None,
            worker_id=worker_id,
            diagnostics={"fault_kind": fault_kind, "message": str(exc)},
        )

    branch_manager_module._fault_result = _fault
    branch_manager_module.time.monotonic = lambda: clock[0]
    try:
        branch_ids = manager.submit_many(
            [(_FakeWorkItem(index), None) for index in range(4)]  # type: ignore[list-item]
        )
        results = manager.poll(timeout=1.0)
    finally:
        branch_manager_module._route_work_item = original_route
        branch_manager_module._work_item_for_ipc = original_ipc
        branch_manager_module._fault_result = original_fault
        branch_manager_module.time.monotonic = original_monotonic

    assert len(results) == 4
    assert pool.respawns == [(0, 1.0)]
    assert results[branch_ids[0]].diagnostics["fault_kind"] == "task_timeout"
    assert results[branch_ids[2]].diagnostics["fault_kind"] == "task_timeout"
    assert results[branch_ids[1]] is healthy_result_1
    assert results[branch_ids[3]] is healthy_result_2
    assert clock[0] == 1.2
