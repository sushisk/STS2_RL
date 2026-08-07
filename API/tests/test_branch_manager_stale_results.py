"""Regression coverage for stale results on BranchManager's shared result queue."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import search.branch_manager as branch_manager_module  # noqa: E402
from search.branch_manager import BranchManager  # noqa: E402


class _FakeWorkItem:
    def __init__(self) -> None:
        self.context_id = "context-1"
        self.search_hypothesis_id = "hypothesis-1"


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


class _ResultQueue:
    def __init__(self, result) -> None:
        self._events = [
            (999, SimpleNamespace(status="fault", diagnostics={"fault_kind": "stale"})),
            (1, result),
        ]

    def get(self, timeout: float):
        assert timeout > 0
        return self._events.pop(0)


class _FakePool:
    def __init__(self, result) -> None:
        self.worker_ids = [0]
        self.worker_generations = {0: 1}
        self._result_queue = _ResultQueue(result)
        self.submissions = 0

    def _submit(self, worker_id: int, request) -> int:
        assert worker_id == 0
        self.submissions += 1
        return self.submissions

    def respawn_worker(self, worker_id: int, *, lease_registry) -> None:
        raise AssertionError("stale result must not respawn the current worker")


def test_stale_request_id_is_discarded_before_current_result() -> None:
    current_result = SimpleNamespace(
        status="fault",
        established_lease=None,
        worker_id=0,
        diagnostics={"fault_kind": "synthetic"},
    )
    pool = _FakePool(current_result)
    manager = BranchManager(pool, _FakeLeaseRegistry(), max_branches=2)  # type: ignore[arg-type]
    [branch_id] = manager.submit_many([(_FakeWorkItem(), None)])  # type: ignore[list-item]

    original_route = branch_manager_module._route_work_item
    original_ipc = branch_manager_module._work_item_for_ipc

    def _route(work_item, lease_registry, *, worker_ids, worker_generations, next_bootstrap_index):
        return _FakeRequest(work_item, "bootstrap_step"), worker_ids[0], next_bootstrap_index + 1, None

    branch_manager_module._route_work_item = _route
    branch_manager_module._work_item_for_ipc = lambda item: item
    try:
        results = manager.poll(timeout=1.0, branch_ids=[branch_id])
    finally:
        branch_manager_module._route_work_item = original_route
        branch_manager_module._work_item_for_ipc = original_ipc

    assert results == {branch_id: current_result}
    assert manager.get_branch_status([branch_id]) == {branch_id: "faulted"}
    assert pool.submissions == 1
