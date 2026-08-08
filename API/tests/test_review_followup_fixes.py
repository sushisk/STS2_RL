"""Regression tests for adversarial-review follow-up fixes."""

from __future__ import annotations

import gc
import sys
import weakref
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import search.branch_manager as branch_manager_module  # noqa: E402
from search.branch_manager import BranchManager  # noqa: E402
from search.branch_worker_pool import LeaseRegistry  # noqa: E402


class _FakeWorkItem:
    work_kind = "sub_branch"
    context_id = "new-context"
    search_hypothesis_id = "new-hypothesis"


class _ContinuationFakeWorkItem:
    work_kind = "continuation"
    context_id = "pending-context"
    search_hypothesis_id = "new-hypothesis"


class _HeavyFakeWorkItem(_FakeWorkItem):
    def __init__(self) -> None:
        self.payload = bytearray(1024)


class _FakeRequest:
    def __init__(self, work_item, execution_mode, expected_lease=None) -> None:
        self.work_item = work_item
        self.execution_mode = execution_mode
        self.expected_lease = expected_lease


class _SubmitFailPool:
    worker_ids = [0]
    worker_generations = {0: 1}

    def _submit(self, worker_id, request):
        raise RuntimeError("synthetic submit failure")


class _SentinelLease:
    pass


class _EstablishedLease:
    key = ("pending-context", "new-hypothesis")
    worker_id = 0


class _PendingResult:
    status = "success"
    worker_id = 0

    def __init__(self, lease) -> None:
        self.established_lease = lease


def test_branch_manager_rejects_nonpositive_capacity() -> None:
    for invalid in (0, -1, True):
        try:
            BranchManager(object(), LeaseRegistry(), max_branches=invalid)  # type: ignore[arg-type]
        except ValueError as exc:
            assert "positive integer" in str(exc)
        else:
            raise AssertionError(f"max_branches={invalid!r} must be rejected")


def test_submit_failure_restores_route_side_effects() -> None:
    leases = LeaseRegistry()
    sentinel = _SentinelLease()
    leases._leases[("existing-context", "existing-hypothesis")] = sentinel  # type: ignore[assignment]  # noqa: SLF001
    manager = BranchManager(_SubmitFailPool(), leases, max_branches=4)  # type: ignore[arg-type]
    (branch_id,) = manager.submit([_FakeWorkItem()])  # type: ignore[list-item]

    original_route = branch_manager_module._route_work_item
    original_ipc = branch_manager_module._work_item_for_ipc

    def _mutating_route(work_item, lease_registry, *, worker_ids, worker_generations, next_bootstrap_index):
        # Model the current bootstrap route behavior: a routing decision may evict an
        # existing worker lease before the IPC enqueue itself succeeds.
        lease_registry._leases.clear()  # noqa: SLF001
        return _FakeRequest(work_item, "bootstrap_step"), 0, next_bootstrap_index + 1, None

    branch_manager_module._route_work_item = _mutating_route
    branch_manager_module._work_item_for_ipc = lambda item: item
    try:
        try:
            manager.poll(timeout=1.0, branch_ids=[branch_id])
        except RuntimeError as exc:
            assert "synthetic submit failure" in str(exc)
        else:
            raise AssertionError("submit failure must propagate")
    finally:
        branch_manager_module._route_work_item = original_route
        branch_manager_module._work_item_for_ipc = original_ipc

    assert leases._leases == {("existing-context", "existing-hypothesis"): sentinel}  # noqa: SLF001
    assert manager._next_bootstrap_index == 0  # noqa: SLF001
    assert manager.get_branch_status([branch_id])[branch_id] == "released"


def test_release_drops_heavy_work_items_across_many_tombstones() -> None:
    manager = BranchManager(object(), LeaseRegistry(), max_branches=1)  # type: ignore[arg-type]
    refs: list[weakref.ReferenceType[_HeavyFakeWorkItem]] = []

    for _ in range(5000):
        item = _HeavyFakeWorkItem()
        refs.append(weakref.ref(item))
        (branch_id,) = manager.submit([item])  # type: ignore[list-item]
        manager.release_branches([branch_id])

    del item
    gc.collect()

    assert manager.active_branch_count() == 0
    assert len(manager._records) == 5000  # noqa: SLF001 - lightweight status tombstones remain intentional.
    assert all(ref() is None for ref in refs)
    assert all(
        record.work_item is None
        and record.result is None
        and record.worker_id is None
        and record.worker_generation is None
        and record.request_id is None
        and record.execution_mode is None
        and not record.child_branch_ids
        for record in manager._records.values()  # noqa: SLF001
    )


def test_released_parent_keeps_only_links_needed_for_cancel_cascade() -> None:
    manager = BranchManager(object(), LeaseRegistry(), max_branches=2)  # type: ignore[arg-type]
    (parent_id,) = manager.submit([_FakeWorkItem()])  # type: ignore[list-item]
    (child_id,) = manager.submit([_FakeWorkItem()], parent_branch_id=parent_id)  # type: ignore[list-item]

    manager.release_branches([parent_id])
    parent = manager._records[parent_id]  # noqa: SLF001
    assert parent.work_item is None
    assert parent.child_branch_ids == [child_id]

    statuses = manager.cancel_branches([parent_id])
    assert statuses[parent_id] == "released"
    assert statuses[child_id] == "cancelled"

    manager.release_branches([child_id])
    assert manager._records[parent_id].child_branch_ids == []  # noqa: SLF001


def test_release_terminal_pending_branch_invalidates_lease_and_child_can_bootstrap() -> None:
    leases = LeaseRegistry()
    manager = BranchManager(object(), leases, max_branches=2)  # type: ignore[arg-type]
    (parent_id,) = manager.submit([_FakeWorkItem()])  # type: ignore[list-item]
    child_work_item = _ContinuationFakeWorkItem()
    (child_id,) = manager.submit([child_work_item], parent_branch_id=parent_id)  # type: ignore[list-item]

    lease = _EstablishedLease()
    manager._finish(manager._records[parent_id], _PendingResult(lease))  # type: ignore[arg-type]  # noqa: SLF001
    assert leases.get(*lease.key) is lease

    manager.release_branches([parent_id])

    assert leases.get(*lease.key) is None
    assert manager.get_branch_status([parent_id])[parent_id] == "released"
    assert manager.get_branch_status([child_id])[child_id] == "queued"
    assert manager._records[parent_id].child_branch_ids == [child_id]  # noqa: SLF001

    request, worker_id, next_index, _ = branch_manager_module._route_work_item(
        child_work_item,  # type: ignore[arg-type]
        leases,
        worker_ids=[0],
        worker_generations={0: 1},
        next_bootstrap_index=0,
    )
    assert request.execution_mode == "bootstrap_step"
    assert request.expected_lease is None
    assert worker_id == 0
    assert next_index == 1
