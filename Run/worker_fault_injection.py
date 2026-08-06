"""Fault injection + Worker respawn + Lease invalidation helpers for `worker_pool.py`.

Two distinct fault classes are exercised, matching the instruction's "Workerが終了・再生成
・Faultした場合は、旧generationに属するLeaseを全て無効化してください":

- In-worker fault: the worker process stays alive, but the specific request fails (e.g. an
  illegal room_id) - `_WorkerRuntime.execute()` already catches this and returns
  `BRANCH_STATUS_FAULT` normally. No process-level action is needed; the caller may still
  choose to respawn the slot anyway (e.g. because its live retained state is now unknown/
  possibly inconsistent after a fault mid-Choice).
- OS-level worker death: the process itself is terminated (deliberately here, to simulate a
  genuine crash - e.g. a fatal CLR-level exception that Python's own exception handling
  cannot catch, unlike the in-worker case above). `WholeRunWorkerPool.is_worker_alive()`
  detects this (polled in short slices inside `execute()`/`dispatch_choice_work_items()`)
  without hanging for the full `request_timeout_s`, and now auto-respawns + returns a
  synthesized `BRANCH_STATUS_FAULT` result rather than raising `WorkerDiedError` out to the
  caller.

In both cases, `WholeRunWorkerPool.respawn_worker(slot, lease_registry)` terminates the old
process (if still alive), spawns a fresh one with `worker_generation + 1`, and invalidates
every Lease that belonged to the OLD (slot, generation) pair - so a stale Lease can never
route a continuation to a worker that no longer holds the state it claims to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker_pool import (
    BRANCH_STATUS_FAULT,
    ChoiceWorkItem,
    LeaseRegistry,
    WholeRunWorkerPool,
    WORK_KIND_SUB_BRANCH,
    derive_context_id,
)


@dataclass
class FaultInjectionResult:
    slot: Any
    old_pid: "int | None"
    old_generation: int
    new_pid: "int | None"
    new_generation: int
    leases_before: int
    leases_after: int
    old_generation_leases_survived: int


def inject_in_worker_fault(pool: WholeRunWorkerPool, registry: LeaseRegistry, slot: Any, map_snapshot: str) -> dict:
    """Submits a `ChoiceWorkItem` with a deliberately-illegal `room_id` to `slot` - this
    raises inside `WholeRunSession.choose_room()`, caught by `_WorkerRuntime.execute()`,
    which returns `BRANCH_STATUS_FAULT` and clears its own retained context - the worker
    process itself stays alive (no respawn needed to recover from this class alone, but
    the caller may still call `respawn_worker` for defense-in-depth; see module docstring).
    """
    context_id = derive_context_id(
        map_snapshot=map_snapshot, room_id=999999, action_prefix=[], choice_type="event", relic_injection=None
    )
    bad_item = ChoiceWorkItem(
        work_id=f"{context_id}:fault-probe",
        context_id=context_id,
        choice_type="event",
        map_snapshot=map_snapshot,
        room_id=999999,  # not a real MapRoomOption.RoomId - ChooseRoom raises "Illegal room: ..."
        action_prefix=[],
        relic_injection=None,
        target_boundary="event_choice",
        work_kind=WORK_KIND_SUB_BRANCH,
    )
    (result,) = pool.dispatch_choice_work_items([bad_item], registry)
    return {
        "status": result.status,
        "worker_slot": result.worker_slot,
        "pid": result.pid,
        "diagnostics": result.diagnostics,
    }


def kill_worker_process(pool: WholeRunWorkerPool, slot: Any) -> int:
    """Terminates `slot`'s OS process directly (simulating a genuine crash) and returns
    the PID that was killed. Does NOT respawn - call `respawn_and_verify` next.
    """
    handle = pool._workers[slot]  # noqa: SLF001 - test/fault-injection code, intentional white-box access
    old_pid = handle.pid
    if handle.process.is_alive():
        handle.process.terminate()
        handle.process.join(timeout=5)
    return old_pid


def respawn_and_verify(pool: WholeRunWorkerPool, registry: LeaseRegistry, slot: Any) -> FaultInjectionResult:
    """Respawns `slot` and verifies the generation incremented, the PID changed, and every
    Lease belonging to the OLD generation is gone from `registry` afterward.
    """
    old_handle = pool._workers[slot]  # noqa: SLF001
    old_pid = old_handle.pid
    old_generation = old_handle.worker_generation
    leases_before = len(registry._leases)  # noqa: SLF001
    old_generation_leases = [
        lease for lease in registry._leases.values() if lease.worker_slot == slot and lease.worker_generation == old_generation  # noqa: SLF001
    ]

    pool.respawn_worker(slot, registry)

    new_handle = pool._workers[slot]  # noqa: SLF001
    survived = [
        lease for lease in registry._leases.values() if lease.worker_slot == slot and lease.worker_generation == old_generation  # noqa: SLF001
    ]
    return FaultInjectionResult(
        slot=slot,
        old_pid=old_pid,
        old_generation=old_generation,
        new_pid=new_handle.pid,
        new_generation=new_handle.worker_generation,
        leases_before=leases_before,
        leases_after=len(registry._leases),  # noqa: SLF001
        old_generation_leases_survived=len(survived),
    )
