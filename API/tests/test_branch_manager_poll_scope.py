"""Regression coverage for request-scoped BranchManager.poll ownership."""

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
    def __init__(self, index: int) -> None:
        self.context_id = f"context-{index}"
        self.search_hypothesis_id = f"hypothesis-{index}"


class _FakeRequest:
    def __init__(self, work_item, execution_mode, expected_lease=None) -> None:
        self.work_item = work_item
        self.execution_mode = execution_mode
        self.expected_lease = expected_lease


class _FakeLeaseRegistry:
    def get(self, *args):
        return None

    def invalidate(self, *args) -> None:
        pass

    def set(self, *args) -> None:
        pass

    def invalidate_worker(self, *args) -> None:
        pass


class _ResultQueue:
    def __init__(self, result) -> None:
        self._result = result
        self._used = False

    def get(self, timeout: float):
        assert timeout > 0
        assert not self._used
        self._used = True
        return 1, self._result


class _FakePool:
    def __init__(self, result) -> None:
        self.worker_ids = [0]
        self.worker_generations = {0: 1}
        self._result_queue = _ResultQueue(result)
        self.submissions: list[int] = []

    def _submit(self, worker_id: int, request) -> int:
        self.submissions.append(worker_id)
        return len(self.submissions)

    def respawn_worker(self, worker_id: int, *, lease_registry) -> None:
        raise AssertionError("worker should not be respawned in poll ownership test")


def test_poll_branch_ids_leaves_unowned_queued_branch_untouched() -> None:
    result = SimpleNamespace(
        status="fault",
        established_lease=None,
        worker_id=0,
        diagnostics={"fault_kind": "synthetic"},
    )
    pool = _FakePool(result)
    manager = BranchManager(pool, _FakeLeaseRegistry(), max_branches=4)  # type: ignore[arg-type]
    first, second = manager.submit_many(
        [(_FakeWorkItem(0), None), (_FakeWorkItem(1), None)]  # type: ignore[list-item]
    )

    original_route = branch_manager_module._route_work_item
    original_ipc = branch_manager_module._work_item_for_ipc

    def _route(work_item, lease_registry, *, worker_ids, worker_generations, next_bootstrap_index):
        return _FakeRequest(work_item, "bootstrap_step"), worker_ids[0], next_bootstrap_index + 1, None

    branch_manager_module._route_work_item = _route
    branch_manager_module._work_item_for_ipc = lambda item: item
    try:
        results = manager.poll(timeout=1.0, branch_ids=[second])
    finally:
        branch_manager_module._route_work_item = original_route
        branch_manager_module._work_item_for_ipc = original_ipc

    assert results == {second: result}
    assert pool.submissions == [0]
    assert manager.get_branch_status([first, second]) == {
        first: "queued",
        second: "faulted",
    }
