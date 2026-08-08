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
  ``branch_worker_pool.py``), and ``poll()`` never submits a second request to a busy
  worker. Therefore at most one Branch is ever ``running`` on a given worker at once.
  Branches waiting for that worker remain coordinator-side ``queued`` Branches.
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
    work_item: Optional[WorkItem]
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
        if not isinstance(max_branches, int) or isinstance(max_branches, bool) or max_branches <= 0:
            raise ValueError("max_branches must be a positive integer")
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

    def poll(
        self,
        timeout: float = 120.0,
        *,
        branch_ids: Optional[list[str]] = None,
    ) -> dict[str, BranchResult]:
        """Resolve selected queued Branches, or every queued Branch when unspecified.

        ``branch_ids`` lets a coordinator bind one poll to the Branches owned by the
        current request. This prevents a pre-existing queued Branch from another caller
        or transaction from being dispatched, timed out, or quarantined as collateral.
        Every explicitly selected Branch must exist and still be ``queued`` when polling
        starts.

        ``timeout`` is a per-Branch execution timeout. ``poll()`` keeps at most one
        outstanding request on each worker, so Branches beyond the worker count stay
        coordinator-side ``queued`` until a compatible worker slot becomes available.
        The deadline starts only when that Branch is actually submitted to a worker;
        completion on another worker never extends it. If a worker times out, only its
        executing Branch receives ``task_timeout``. The worker is respawned, and queued
        tails are routed to the replacement (or another compatible worker) with a fresh
        deadline.

        If coordinator-side dispatch/result handling raises unexpectedly, every Branch
        admitted by this poll call is cancelled and released before the exception
        propagates, preventing queued/running leftovers from being executed by a later
        unrelated ``poll()``.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        if branch_ids is None:
            poll_branch_ids = [
                branch_id
                for branch_id, record in list(self._records.items())
                if record.state == BRANCH_STATE_QUEUED
            ]
        else:
            poll_branch_ids = []
            seen: set[str] = set()
            for branch_id in branch_ids:
                if branch_id in seen:
                    continue
                seen.add(branch_id)
                record = self._records.get(branch_id)
                if record is None:
                    raise UnknownBranchError(branch_id)
                if record.state != BRANCH_STATE_QUEUED:
                    raise RuntimeError(
                        f"Branch {branch_id} must be queued when explicitly polled, "
                        f"got state={record.state!r}"
                    )
                poll_branch_ids.append(branch_id)

        waiting = list(poll_branch_ids)
        running_by_request_id: dict[int, str] = {}
        request_worker_ids: dict[int, int] = {}
        worker_request_ids: dict[int, int] = {}
        worker_deadlines: dict[int, float] = {}
        results: dict[str, BranchResult] = {}

        def _dispatch_available() -> None:
            """Fill every currently free worker without queueing behind a live request."""
            while waiting:
                free_worker_ids = [
                    worker_id
                    for worker_id in self._pool.worker_ids
                    if worker_id not in worker_request_ids
                ]
                if not free_worker_ids:
                    return

                dispatched = False
                for branch_id in list(waiting):
                    record = self._records[branch_id]
                    if record.state != BRANCH_STATE_QUEUED:
                        waiting.remove(branch_id)
                        continue
                    work_item = record.work_item
                    if work_item is None:
                        raise RuntimeError(f"queued Branch {branch_id} has no WorkItem")

                    # A valid continuation Lease must retain worker affinity. If its
                    # holder is busy, leave this Branch queued and try another item.
                    lease = None
                    if getattr(work_item, "work_kind", None) == "continuation":
                        lease = self._lease_registry.get(
                            work_item.context_id,
                            work_item.search_hypothesis_id,
                        )
                    if lease is not None:
                        generation = self._pool.worker_generations.get(lease.worker_id)
                        if (
                            generation is not None
                            and lease.is_valid_for(work_item, worker_generation=generation)
                            and lease.worker_id not in free_worker_ids
                        ):
                            continue

                    previous_bootstrap_index = self._next_bootstrap_index
                    lease_table = getattr(self._lease_registry, "_leases", None)
                    lease_snapshot = dict(lease_table) if isinstance(lease_table, dict) else None
                    try:
                        request, worker_id, next_bootstrap_index, _ = _route_work_item(
                            work_item,
                            self._lease_registry,
                            worker_ids=free_worker_ids,
                            worker_generations=self._pool.worker_generations,
                            next_bootstrap_index=previous_bootstrap_index,
                        )
                        if worker_id not in free_worker_ids:
                            # Defensive invariant: the only legitimate non-free route is a
                            # valid holder Lease, and that case is deferred above.
                            raise RuntimeError(
                                f"routing selected busy worker {worker_id} for Branch {branch_id}"
                            )

                        ipc_work_item = _work_item_for_ipc(work_item)
                        ipc_request = request.__class__(
                            ipc_work_item,
                            request.execution_mode,
                            request.expected_lease,
                        )
                        request_id = self._pool._submit(worker_id, ipc_request)  # noqa: SLF001
                    except Exception:
                        self._next_bootstrap_index = previous_bootstrap_index
                        if lease_snapshot is not None:
                            lease_table.clear()
                            lease_table.update(lease_snapshot)
                        raise
                    self._next_bootstrap_index = next_bootstrap_index
                    record.state = BRANCH_STATE_RUNNING
                    record.worker_id = worker_id
                    record.worker_generation = self._pool.worker_generations[worker_id]
                    record.request_id = request_id
                    record.execution_mode = request.execution_mode
                    self._request_id_to_branch_id[request_id] = branch_id
                    running_by_request_id[request_id] = branch_id
                    request_worker_ids[request_id] = worker_id
                    worker_request_ids[worker_id] = request_id
                    worker_deadlines[worker_id] = time.monotonic() + timeout
                    waiting.remove(branch_id)
                    dispatched = True
                    break

                if not dispatched:
                    return

        try:
            _dispatch_available()
            while waiting or running_by_request_id:
                if not running_by_request_id:
                    # With no live request, every worker is free. If routing still could
                    # not dispatch a queued Branch, the state machine is inconsistent.
                    _dispatch_available()
                    if not running_by_request_id:
                        raise RuntimeError("queued Branches could not be routed to any worker")

                next_deadline = min(worker_deadlines.values())
                wait_s = max(0.0, next_deadline - time.monotonic())
                try:
                    received_id, result = self._pool._result_queue.get(timeout=wait_s)  # noqa: SLF001
                except queue.Empty:
                    now = time.monotonic()
                    expired_worker_ids = [
                        worker_id
                        for worker_id, deadline in worker_deadlines.items()
                        if deadline <= now
                    ]
                    for hung_worker_id in expired_worker_ids:
                        request_id = worker_request_ids.pop(hung_worker_id)
                        branch_id = running_by_request_id.pop(request_id)
                        request_worker_ids.pop(request_id, None)
                        worker_deadlines.pop(hung_worker_id, None)
                        self._request_id_to_branch_id.pop(request_id, None)
                        record = self._records[branch_id]

                        # A concurrent Cancel may already have respawned this worker.
                        # Preserve that terminal state instead of overwriting it with a
                        # synthetic timeout when the abandoned request never responds.
                        if record.state in (BRANCH_STATE_CANCELLED, BRANCH_STATE_RELEASED):
                            continue

                        work_item = record.work_item
                        if work_item is None:
                            raise RuntimeError(f"running Branch {branch_id} has no WorkItem")
                        stale_generation = self._pool.worker_generations.get(hung_worker_id)
                        self._pool.respawn_worker(
                            hung_worker_id,
                            lease_registry=self._lease_registry,
                        )
                        fault = _fault_result(
                            work_item,
                            record.execution_mode,
                            hung_worker_id,
                            stale_generation,
                            TimeoutError(f"timed out waiting for Branch {record.branch_id}"),
                            fault_kind="task_timeout",
                        )
                        self._finish(record, fault)
                        results[record.branch_id] = fault

                    _dispatch_available()
                    continue

                if received_id not in running_by_request_id:
                    # Stray/late result from a request already cancelled/timed out.
                    continue

                branch_id = running_by_request_id.pop(received_id)
                worker_id = request_worker_ids.pop(received_id)
                worker_request_ids.pop(worker_id, None)
                worker_deadlines.pop(worker_id, None)
                self._request_id_to_branch_id.pop(received_id, None)
                record = self._records[branch_id]

                if record.state not in (BRANCH_STATE_CANCELLED, BRANCH_STATE_RELEASED):
                    self._finish(record, result)
                    results[branch_id] = result
                _dispatch_available()

            return results
        except Exception:
            self._quarantine_poll_branches(poll_branch_ids)
            raise

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
        work_item = record.work_item
        if work_item is None:
            raise RuntimeError(f"active Branch {record.branch_id} has no WorkItem")
        record.result = result
        record.state = BRANCH_STATE_COMPLETED if result.status == "success" else BRANCH_STATE_FAULTED
        self._lease_registry.invalidate(work_item.context_id, work_item.search_hypothesis_id)
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
        """Release each Branch while retaining only a lightweight status tombstone.

        Non-terminal Branches are cancelled first so ``release`` remains safe to call
        unconditionally. Terminal Pending successes relinquish their established Lease
        before compaction; descendants can still continue by replaying their retained
        DecisionContext through the normal bootstrap route. The public/internal Branch
        id remains known and reports ``released``, but execution-heavy state is dropped
        immediately rather than being retained for the lifetime of the manager.
        """
        statuses: dict[str, str] = {}
        for branch_id in branch_ids:
            record = self._records.get(branch_id)
            if record is None:
                raise UnknownBranchError(branch_id)
            if record.state != BRANCH_STATE_RELEASED:
                if record.state in _ACTIVE_STATES or record.state not in _TERMINAL_STATES:
                    self._cancel_one(record)
                elif record.result is not None and record.result.established_lease is not None:
                    self._lease_registry.invalidate_lease(record.result.established_lease)
                record.state = BRANCH_STATE_RELEASED
            self._compact_released_record(record)
            statuses[branch_id] = record.state
        return statuses

    def _compact_released_record(self, record: BranchRecord) -> None:
        """Discard heavy execution state while preserving status/cascade correctness."""
        record.work_item = None
        record.result = None
        record.worker_id = None
        record.worker_generation = None
        record.request_id = None
        record.execution_mode = None

        # A released descendant that is itself a leaf no longer needs to stay in the
        # cascade graph. Keep links that still bridge to a non-released descendant so a
        # later parent Cancel retains the existing cascade semantics.
        retained_children: list[str] = []
        for child_id in record.child_branch_ids:
            child = self._records.get(child_id)
            if child is None:
                continue
            if child.state != BRANCH_STATE_RELEASED or child.child_branch_ids:
                retained_children.append(child_id)
        record.child_branch_ids[:] = retained_children
        self._prune_released_leaf_from_parent(record)

    def _prune_released_leaf_from_parent(self, record: BranchRecord) -> None:
        current = record
        while (
            current.state == BRANCH_STATE_RELEASED
            and not current.child_branch_ids
            and current.parent_branch_id is not None
        ):
            parent = self._records.get(current.parent_branch_id)
            if parent is None:
                current.parent_branch_id = None
                return
            try:
                parent.child_branch_ids.remove(current.branch_id)
            except ValueError:
                pass
            current.parent_branch_id = None
            current = parent

    def close_all(self) -> None:
        """Episode-close / Training-disconnect cleanup: cancel and release every Branch
        this Manager currently knows about."""
        all_ids = list(self._records)
        if not all_ids:
            return
        self.cancel_branches(all_ids)
        self.release_branches(all_ids)
