"""Internal (not yet Training-facing) Branch Cancel/Release management.

RL担当指示：公開DTO監査・Combat Worker Respawn・Branch Cancel, Part C.

``BranchManager`` wraps a ``BranchWorkerPool`` + ``LeaseRegistry`` with an explicit
per-Branch state machine so a caller (eventually Training, via a not-yet-built public
API) can submit WorkItems asynchronously, poll their status, and Cancel/Release them
without ever mutating Main Run state or any *other* Branch.

Design notes
------------
* Submission is per-``WorkItem`` and asynchronous: ``submit()`` enqueues a Branch as
  ``queued`` without blocking; ``poll(timeout)`` is what actually routes queued Branches
  onto a worker (``running``) and drains completed results off the shared result queue.
  This decoupling - rather than reusing the blocking, whole-batch
  ``branch_worker_pool.dispatch_work_items()`` - is what makes a genuinely in-flight
  ("running") Branch cancellable: we always know which OS worker/generation/request_id a
  running Branch is currently occupying, so Cancel can act on exactly that worker.
* Worker affinity guarantee this relies on: each worker process consumes its input
  Queue strictly one request at a time (see ``_worker_main`` in
  ``branch_worker_pool.py``), so at most one Branch is ever "running" on a given worker
  at once. Killing that worker to cancel Branch B therefore can never interrupt some
  *other* Branch's in-flight request - there isn't one.
* Cancelling a Branch never touches Main Run state (Main never holds a Branch Worker
  slot - see ``BranchWorkerPool.respawn_worker`` docstring) and never invalidates a
  *different* Branch's Lease: worker-kill-based Cancel only invalidates Leases owned by
  the one worker being killed, and by the affinity guarantee above that worker's only
  live occupant is the Branch being cancelled.
* Parent/child cascade: a Branch produced by continuing another Branch's Pending result
  may be registered with ``parent_branch_id`` set. Cancelling a parent cascades to all
  registered descendants (a parent's Pending Decision Context is what any child's
  WorkItem was built from; once the parent is discarded, continuing a child against it
  is meaningless).
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Optional

from search.branch_worker_pool import (
    BranchResult,
    BranchWorkerPool,
    LeaseRegistry,
    WorkItem,
    _fault_result,
    _route_work_item,
    _work_item_for_ipc,
)

BRANCH_STATE_QUEUED = "queued"
BRANCH_STATE_RUNNING = "running"
BRANCH_STATE_COMPLETED = "completed"
BRANCH_STATE_PARTIAL = "partial"
BRANCH_STATE_CANCELLED = "cancelled"
BRANCH_STATE_FAULTED = "faulted"
BRANCH_STATE_RELEASED = "released"

_TERMINAL_STATES = frozenset(
    {BRANCH_STATE_COMPLETED, BRANCH_STATE_PARTIAL, BRANCH_STATE_CANCELLED, BRANCH_STATE_FAULTED, BRANCH_STATE_RELEASED}
)
_ACTIVE_STATES = frozenset({BRANCH_STATE_QUEUED, BRANCH_STATE_RUNNING})


class BranchLimitExceededError(RuntimeError):
    """Raised by ``submit()`` when accepting the new Branches would exceed the
    Manager's ``max_branches`` safety cap on simultaneously active (queued+running)
    Branches."""


class UnknownBranchError(KeyError):
    """Raised when a ``branch_id`` is not (or no longer) known to this Manager."""


class BranchReleasedError(RuntimeError):
    """Raised by ``get_branch_result()`` when the Branch's result was already released -
    a released Branch's result must never be read again, per the Cancel contract."""


@dataclass
class BranchRecord:
    branch_id: str
    work_item: WorkItem
    state: str
    parent_branch_id: Optional[str] = None
    child_branch_ids: list = field(default_factory=list)
    worker_id: Optional[int] = None
    worker_generation: Optional[int] = None
    request_id: Optional[int] = None
    execution_mode: Optional[str] = None
    result: Optional[BranchResult] = None


class BranchManager:
    """Owns the logical Branch state machine layered over one ``BranchWorkerPool``."""

    def __init__(
        self,
        pool: BranchWorkerPool,
        lease_registry: LeaseRegistry,
        *,
        max_branches: int = 256,
    ) -> None:
        self._pool = pool
        self._lease_registry = lease_registry
        self.max_branches = max_branches
        self._records: dict[str, BranchRecord] = {}
        self._request_id_to_branch_id: dict[int, str] = {}
        self._next_serial = 0
        self._next_bootstrap_index = 0

    # -- submission ---------------------------------------------------------------

    def _new_branch_id(self) -> str:
        self._next_serial += 1
        return f"branch-{self._next_serial}"

    def active_branch_count(self) -> int:
        return sum(1 for record in self._records.values() if record.state in _ACTIVE_STATES)

    def submit(self, work_items: list[WorkItem], *, parent_branch_id: Optional[str] = None) -> list[str]:
        """Register ``work_items`` as new ``queued`` Branches; does not dispatch them
        onto a worker yet (that happens lazily, in ``poll()``)."""
        return self.submit_many([(work_item, parent_branch_id) for work_item in work_items])

    def submit_many(self, entries: list[tuple[WorkItem, Optional[str]]]) -> list[str]:
        """Atomically register a heterogeneous-parent batch as ``queued`` Branches.

        All parent/capacity checks and all new ``BranchRecord`` construction happen
        before mutating manager state. This gives coordinator batch callers one submit
        boundary instead of a sequence of partially-committed ``submit()`` calls.
        """
        for _, parent_branch_id in entries:
            if parent_branch_id is not None and parent_branch_id not in self._records:
                raise UnknownBranchError(parent_branch_id)
        if self.active_branch_count() + len(entries) > self.max_branches:
            raise BranchLimitExceededError(
                f"submitting {len(entries)} Branch(es) would exceed max_branches={self.max_branches} "
                f"(currently {self.active_branch_count()} active)"
            )

        new_records: list[BranchRecord] = []
        for work_item, parent_branch_id in entries:
            branch_id = self._new_branch_id()
            new_records.append(
                BranchRecord(
                    branch_id=branch_id,
                    work_item=work_item,
                    state=BRANCH_STATE_QUEUED,
                    parent_branch_id=parent_branch_id,
                )
            )

        for record in new_records:
            self._records[record.branch_id] = record
        for record in new_records:
            if record.parent_branch_id is not None:
                self._records[record.parent_branch_id].child_branch_ids.append(record.branch_id)
        return [record.branch_id for record in new_records]

    # -- dispatch / polling ---------------------------------------------------------

    def poll(self, timeout: float = 120.0) -> dict[str, BranchResult]:
        """Route every still-``queued`` Branch onto a worker, then drain the shared
        result Queue until every Branch this call put into ``running`` has resolved.

        ``timeout`` is a per-worker-head Branch execution timeout, not a batch-wide idle
        timeout. Each worker gets an absolute deadline for the Branch currently at the
        head of its FIFO; completion on one worker never extends another worker's hung
        Branch. When a worker completes one Branch, the next queued Branch assigned to
        that worker receives a fresh deadline. A timed-out worker is respawned and all
        still-outstanding requests already submitted to that worker are faulted because
        its process queue is discarded by the respawn.

        If coordinator-side dispatch raises unexpectedly, every Branch admitted by this
        poll call is cancelled and released before the exception propagates, preventing
        queued/running leftovers from being executed by a later unrelated ``poll()``.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        poll_branch_ids = [
            branch_id
            for branch_id, record in list(self._records.items())
            if record.state == BRANCH_STATE_QUEUED
        ]
        newly_running: dict[int, str] = {}
        request_worker_ids: dict[int, int] = {}
        worker_deadlines: dict[int, float] = {}
        try:
            for branch_id in poll_branch_ids:
                record = self._records[branch_id]
                if record.state != BRANCH_STATE_QUEUED:
                    continue
                request, worker_id, self._next_bootstrap_index, _ = _route_work_item(
                    record.work_item,
                    self._lease_registry,
                    worker_ids=self._pool.worker_ids,
                    worker_generations=self._pool.worker_generations,
                    next_bootstrap_index=self._next_bootstrap_index,
                )
                ipc_work_item = _work_item_for_ipc(record.work_item)
                ipc_request = request.__class__(ipc_work_item, request.execution_mode, request.expected_lease)
                request_id = self._pool._submit(worker_id, ipc_request)  # noqa: SLF001
                record.state = BRANCH_STATE_RUNNING
                record.worker_id = worker_id
                record.worker_generation = self._pool.worker_generations[worker_id]
                record.request_id = request_id
                record.execution_mode = request.execution_mode
                self._request_id_to_branch_id[request_id] = branch_id
                newly_running[request_id] = branch_id
                request_worker_ids[request_id] = worker_id
                worker_deadlines.setdefault(worker_id, time.monotonic() + timeout)
        except Exception:
            self._quarantine_poll_branches(poll_branch_ids)
            raise

        results: dict[str, BranchResult] = {}
        remaining = set(newly_running)
        while remaining:
            active_worker_ids = {
                request_worker_ids[rid]
                for rid in remaining
                if request_worker_ids[rid] in worker_deadlines
            }
            if not active_worker_ids:
                break
            now = time.monotonic()
            next_deadline = min(worker_deadlines[worker_id] for worker_id in active_worker_ids)
            wait_s = max(0.0, next_deadline - now)
            try:
                received_id, result = self._pool._result_queue.get(timeout=wait_s)  # noqa: SLF001
            except queue.Empty:
                now = time.monotonic()
                expired_worker_ids = {
                    worker_id
                    for worker_id in active_worker_ids
                    if worker_deadlines[worker_id] <= now
                }
                for hung_worker_id in expired_worker_ids:
                    stale_generation = self._pool.worker_generations.get(hung_worker_id)
                    self._pool.respawn_worker(hung_worker_id, lease_registry=self._lease_registry)
                    for rid in list(remaining):
                        if request_worker_ids[rid] != hung_worker_id:
                            continue
                        record = self._records[newly_running[rid]]
                        fault = _fault_result(
                            record.work_item,
                            record.execution_mode,
                            hung_worker_id,
                            stale_generation,
                            TimeoutError(f"timed out waiting for Branch {record.branch_id}"),
                            fault_kind="task_timeout",
                        )
                        self._finish(record, fault)
                        results[record.branch_id] = fault
                        remaining.discard(rid)
                        self._request_id_to_branch_id.pop(rid, None)
                    worker_deadlines.pop(hung_worker_id, None)
                continue
            if received_id not in remaining:
                continue
            branch_id = newly_running[received_id]
            record = self._records[branch_id]
            worker_id = request_worker_ids[received_id]
            if record.state == BRANCH_STATE_CANCELLED:
                # Cancelled after submission but before its result arrived - discard.
                remaining.discard(received_id)
                self._request_id_to_branch_id.pop(received_id, None)
            else:
                self._finish(record, result)
                results[branch_id] = result
                remaining.discard(received_id)
                self._request_id_to_branch_id.pop(received_id, None)

            if any(request_worker_ids[rid] == worker_id for rid in remaining):
                worker_deadlines[worker_id] = time.monotonic() + timeout
            else:
                worker_deadlines.pop(worker_id, None)
        return results

    def _quarantine_poll_branches(self, branch_ids: list[str]) -> None:
        """Best-effort cleanup for coordinator exceptions during dispatch."""
        known = [branch_id for branch_id in branch_ids if branch_id in self._records]
        if not known:
            return
        try:
            self.cancel_branches(known)
        finally:
            self.release_branches(known)

    def _finish(self, record: BranchRecord, result: BranchResult) -> None:
        record.result = result
        record.state = BRANCH_STATE_COMPLETED if result.status == "success" else BRANCH_STATE_FAULTED
        self._lease_registry.invalidate(record.work_item.context_id, record.work_item.search_hypothesis_id)
        if result.status == "success" and result.established_lease is not None:
            self._lease_registry.set(result.established_lease)
        if result.status == "fault" and result.worker_id is not None:
            self._lease_registry.invalidate_worker(result.worker_id)

    # -- status ---------------------------------------------------------------------

    def get_branch_status(self, branch_ids: list[str]) -> dict[str, str]:
        statuses = {}
        for branch_id in branch_ids:
            record = self._records.get(branch_id)
            if record is None:
                raise UnknownBranchError(branch_id)
            statuses[branch_id] = record.state
        return statuses

    def get_branch_result(self, branch_id: str) -> BranchResult:
        record = self._records.get(branch_id)
        if record is None:
            raise UnknownBranchError(branch_id)
        if record.state == BRANCH_STATE_RELEASED:
            raise BranchReleasedError(f"Branch {branch_id}'s result was released and must not be reused")
        if record.result is None:
            raise RuntimeError(f"Branch {branch_id} has no result yet (state={record.state!r})")
        return record.result

    # -- cancel / release -------------------------------------------------------------

    def cancel_branches(self, branch_ids: list[str]) -> dict[str, str]:
        """Cancel each Branch (and cascade to its registered descendants). Idempotent:
        an already-``cancelled``/``released`` Branch is left untouched. Never mutates
        Main Run state or any Branch not in ``branch_ids``/its descendants. Returns the
        resulting state of every Branch actually visited (including cascaded children)."""
        to_visit = list(branch_ids)
        visited: dict[str, str] = {}
        while to_visit:
            branch_id = to_visit.pop()
            if branch_id in visited:
                continue
            record = self._records.get(branch_id)
            if record is None:
                raise UnknownBranchError(branch_id)
            to_visit.extend(record.child_branch_ids)
            visited[branch_id] = self._cancel_one(record)
        return visited

    def _cancel_one(self, record: BranchRecord) -> str:
        if record.state in (BRANCH_STATE_CANCELLED, BRANCH_STATE_RELEASED):
            return record.state  # idempotent double-cancel.
        if record.state == BRANCH_STATE_QUEUED:
            record.state = BRANCH_STATE_CANCELLED
            return record.state
        if record.state == BRANCH_STATE_RUNNING:
            # Stuck inside a synchronous Emulator call with no cooperative-stop
            # mechanism available - kill+respawn the exact worker holding this
            # Branch's outstanding request. By worker affinity (see module docstring)
            # no other Branch can be occupying that worker right now.
            assert record.worker_id is not None
            self._pool.respawn_worker(record.worker_id, lease_registry=self._lease_registry)
            if record.request_id in self._request_id_to_branch_id:
                del self._request_id_to_branch_id[record.request_id]
            record.state = BRANCH_STATE_CANCELLED
            return record.state
        # completed/partial/faulted: discard the result, mark cancelled (still
        # release-able afterwards).
        if record.result is not None and record.result.established_lease is not None:
            self._lease_registry.invalidate_lease(record.result.established_lease)
        record.result = None
        record.state = BRANCH_STATE_CANCELLED
        return record.state

    def release_branches(self, branch_ids: list[str]) -> dict[str, str]:
        """Release each Branch. Non-terminal Branches are cancelled first (their result,
        if any, is discarded) so ``release`` is always safe to call unconditionally.
        Idempotent on an already-``released`` Branch."""
        statuses: dict[str, str] = {}
        for branch_id in branch_ids:
            record = self._records.get(branch_id)
            if record is None:
                raise UnknownBranchError(branch_id)
            if record.state == BRANCH_STATE_RELEASED:
                statuses[branch_id] = record.state
                continue
            if record.state in _ACTIVE_STATES or record.state not in _TERMINAL_STATES:
                self._cancel_one(record)
            record.result = None
            record.state = BRANCH_STATE_RELEASED
            statuses[branch_id] = record.state
        return statuses

    def close_all(self) -> None:
        """Episode-close / Training-disconnect cleanup: cancel and release every Branch
        this Manager currently knows about."""
        all_ids = list(self._records)
        if not all_ids:
            return
        self.cancel_branches(all_ids)
        self.release_branches(all_ids)
