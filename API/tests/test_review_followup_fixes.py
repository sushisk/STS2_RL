"""Regression tests for adversarial-review follow-up fixes."""

from __future__ import annotations

import sys
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
