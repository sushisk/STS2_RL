"""Branch Worker Pool - Combat execution infrastructure Phase 5.

Implements the worker/lease layer described by
docs/architecture/combat/mermaid_combat_branch_scheduler_detail.mermaid.

Design notes:
  * IPC uses explicit ``multiprocessing.Process`` instances with one input ``Queue`` per
    worker and one shared result ``Queue``. ``ProcessPoolExecutor`` is deliberately not
    used here because the Lease contract requires worker affinity: a valid
    State-Holding Worker Lease must route the next continuation step to the exact OS
    process that already holds that live ``LiveCombatSession``.
  * ``context_id`` is a coordinator-visible identity for "the same Decision Context",
    not an Emulator action signature. By default it is a SHA-256 digest over the stable
    root snapshot's canonical JSON plus the replay-prefix signatures and target
    parameters. Callers may also assign one explicitly; the default is stable across
    processes for identical root+prefix content and includes ordered snapshot truth only
    as an internal identity hash, never as evaluator input.
  * ``decision_result_digest`` is a cheap SHA-256 digest over ``DecisionSignature``'s
    dataclass fields. Lease validation uses this digest together with
    ``combat_session_id``/``step_index``/``state_epoch`` to verify worker/process trust.
    It intentionally does not replace Phase 2's semantic replay comparison.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing
import queue
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from search.candidate_pipeline import (
    CandidatePipelineResult,
    PipelineCandidateRef,
    build_candidate_pipeline_result,
)
from search.decision_context import (
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    BOUNDARY_TERMINAL,
    DecisionContext,
    DecisionSignature,
    ReplayMismatch,
    ReplayPrefixEntry,
    ReplaySuccess,
    append_replay_prefix_entry,
    boundary_of_battle_state,
    replay_decision_context,
    search_root,
)

if False:  # pragma: no cover - type-checking-only without importing at runtime.
    from combat_state_snapshot import CombatStateSnapshot


WORK_KIND_CONTINUATION = "continuation"
WORK_KIND_SUB_BRANCH = "sub_branch"
WORK_KIND_VALUES = frozenset({WORK_KIND_CONTINUATION, WORK_KIND_SUB_BRANCH})

EXECUTION_MODE_BOOTSTRAP_STEP = "bootstrap_step"
EXECUTION_MODE_HOLDER_STEP = "holder_step"
EXECUTION_MODE_VALUES = frozenset({EXECUTION_MODE_BOOTSTRAP_STEP, EXECUTION_MODE_HOLDER_STEP})

BRANCH_STATUS_SUCCESS = "success"
BRANCH_STATUS_FAULT = "fault"


def _json_digest(payload: Any, *, length: int = 16) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def decision_result_digest(signature: DecisionSignature) -> str:
    """Cheap Lease fingerprint for the current Decision Result.

    The digest is internal lease metadata only. It hashes ``DecisionSignature``'s
    dataclass representation, including Pending candidate semantic keys, and never
    exposes ordered DrawPile state.
    """
    return _json_digest(dataclasses.asdict(signature))


def derive_context_id(context: DecisionContext) -> str:
    """Derive a stable identity for a Decision Context from root snapshot + prefix.

    This is intentionally separate from ``DecisionSignature``: two independently restored
    processes may see equivalent choices while carrying different live process/session
    trust state. The root snapshot is serialized canonically with volatile metadata
    stripped by ``combat_state_snapshot.canonical_json``; replay-prefix entries are
    reduced to semantic actions, target parameters, and expected signatures.
    """
    root_payload = _snapshot_identity_json(context.root_snapshot)
    prefix_payload = [
        {
            "semantic_action": dataclasses.asdict(entry.semantic_action),
            "expected_signature": dataclasses.asdict(entry.expected_signature),
            "target_index": entry.target_index,
            "target_enemy_index": entry.target_enemy_index,
        }
        for entry in context.replay_prefix
    ]
    return _json_digest(
        {
            "root_snapshot_sha256": hashlib.sha256(root_payload.encode("utf-8")).hexdigest(),
            "replay_prefix": prefix_payload,
            "search_hypothesis_id": context.search_hypothesis_id,
        }
    )


def _snapshot_identity_json(snapshot: Any) -> str:
    return search_root(snapshot).identity_payload()


def _snapshot_ipc_json(snapshot: Any) -> Any:
    """Return a Queue-picklable snapshot payload for spawned worker processes."""
    return search_root(snapshot).ipc_payload()


def _work_item_for_ipc(work_item: WorkItem) -> WorkItem:
    context = work_item.decision_context
    ipc_root = _snapshot_ipc_json(context.root_snapshot)
    if ipc_root is context.root_snapshot:
        return work_item
    return dataclasses.replace(
        work_item,
        decision_context=dataclasses.replace(context, root_snapshot=ipc_root),
    )


@dataclass(frozen=True)
class WorkItem:
    """One branch candidate to execute.

    ``candidate`` is Phase 4's ``PipelineCandidateRef``; it carries only an unexecuted
    semantic action plus the current context signature. ``decision_context`` is retained
    so Bootstrap+Step workers can Restore+Replay before stepping the candidate.
    """

    work_id: str
    decision_context: DecisionContext
    candidate: PipelineCandidateRef
    context_id: str
    work_kind: str

    def __post_init__(self) -> None:
        if self.work_kind not in WORK_KIND_VALUES:
            raise ValueError(f"unknown work_kind {self.work_kind!r} (known: {sorted(WORK_KIND_VALUES)})")
        if not self.candidate.current_context_signature.matches_for_replay(
            self.decision_context.current_context_signature
        ):
            raise ValueError("candidate current_context_signature does not match its DecisionContext")

    @property
    def search_hypothesis_id(self) -> Optional[str]:
        return self.decision_context.search_hypothesis_id

    @classmethod
    def from_candidate_ref(
        cls,
        decision_context: DecisionContext,
        candidate: PipelineCandidateRef,
        *,
        work_kind: str,
        context_id: Optional[str] = None,
        work_id: Optional[str] = None,
    ) -> "WorkItem":
        resolved_context_id = context_id or derive_context_id(decision_context)
        resolved_work_id = work_id or _json_digest(
            {
                "context_id": resolved_context_id,
                "work_kind": work_kind,
                "semantic_action": dataclasses.asdict(candidate.semantic_action),
                "target_index": candidate.target_index,
                "target_enemy_index": candidate.target_enemy_index,
                "score": candidate.score,
            }
        )
        return cls(
            work_id=resolved_work_id,
            decision_context=decision_context,
            candidate=candidate,
            context_id=resolved_context_id,
            work_kind=work_kind,
        )


@dataclass(frozen=True)
class Lease:
    """State-Holding Worker Lease scoped to ``(context_id, search_hypothesis_id)``."""

    worker_id: int
    worker_generation: int
    context_id: str
    search_hypothesis_id: Optional[str]
    state_epoch: int
    combat_session_id: Optional[str]
    step_index: int
    decision_result_digest: str

    @property
    def key(self) -> tuple[str, Optional[str]]:
        return (self.context_id, self.search_hypothesis_id)

    def is_valid_for(
        self,
        work_item: WorkItem,
        *,
        worker_generation: Optional[int] = None,
    ) -> bool:
        if work_item.work_kind != WORK_KIND_CONTINUATION:
            return False
        expected_signature = work_item.decision_context.current_context_signature
        if self.context_id != work_item.context_id:
            return False
        if self.search_hypothesis_id != work_item.search_hypothesis_id:
            return False
        if worker_generation is not None and self.worker_generation != worker_generation:
            return False
        if self.combat_session_id != expected_signature.combat_session_id:
            return False
        if self.step_index != expected_signature.step_index:
            return False
        return self.decision_result_digest == decision_result_digest(expected_signature)


@dataclass
class LeaseRegistry:
    """Small in-memory lease table owned by the Search Coordinator."""

    _leases: dict[tuple[str, Optional[str]], Lease] = field(default_factory=dict)

    def get(self, context_id: str, search_hypothesis_id: Optional[str]) -> Optional[Lease]:
        return self._leases.get((context_id, search_hypothesis_id))

    def set(self, lease: Lease) -> None:
        self._leases[lease.key] = lease

    def invalidate(self, context_id: str, search_hypothesis_id: Optional[str]) -> None:
        self._leases.pop((context_id, search_hypothesis_id), None)

    def invalidate_lease(self, lease: Lease) -> None:
        current = self._leases.get(lease.key)
        if current == lease:
            self._leases.pop(lease.key, None)

    def invalidate_worker(self, worker_id: int) -> None:
        for key, lease in list(self._leases.items()):
            if lease.worker_id == worker_id:
                self._leases.pop(key, None)

    def worker_ids_holding_leases(self) -> set[int]:
        return {lease.worker_id for lease in self._leases.values()}


@dataclass(frozen=True)
class BranchTerminalResult:
    is_terminal: bool
    outcome: str


@dataclass(frozen=True)
class BranchResult:
    """Normalized result shape returned from every worker path."""

    status: str
    work_item: WorkItem
    execution_mode: str
    worker_id: Optional[int]
    worker_generation: Optional[int]
    result_signature: Optional[DecisionSignature] = None
    child_snapshot: Optional["CombatStateSnapshot"] = None
    terminal_result: Optional[BranchTerminalResult] = None
    pending_decision_context: Optional[DecisionContext] = None
    pending_pipeline_result: Optional[CandidatePipelineResult] = None
    established_lease: Optional[Lease] = None
    next_decision_result: Optional[Any] = None
    """Populated only for a Stable-boundary success with the exact post-step
    `BattleState` produced by the Branch Worker. This lets API consumers expose and
    extend the branch's actual state instead of reconstructing it from its parent."""
    next_legal_actions: Optional[list] = None
    """Populated only for a Stable-boundary success (`child_snapshot is not None`) -
    the resolved state's own cached legal actions, so a caller can present/branch
    further from this decision point without a separate restore-only dispatch. Not
    populated for Pending (candidates already live in `pending_pipeline_result`) or
    Terminal (no further decision) results."""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {BRANCH_STATUS_SUCCESS, BRANCH_STATUS_FAULT}:
            raise ValueError(f"unknown BranchResult status {self.status!r}")
        if self.execution_mode not in EXECUTION_MODE_VALUES:
            raise ValueError(f"unknown execution_mode {self.execution_mode!r}")
        has_child = self.child_snapshot is not None
        has_terminal = self.terminal_result is not None
        if has_child and has_terminal:
            raise ValueError("BranchResult cannot contain both child_snapshot and terminal_result")
        if self.status == BRANCH_STATUS_SUCCESS:
            if self.result_signature is None:
                raise ValueError("successful BranchResult requires result_signature")
            boundary = self.result_signature.boundary
            if boundary == BOUNDARY_STABLE and not has_child:
                raise ValueError("Stable success requires exactly child_snapshot")
            if boundary == BOUNDARY_TERMINAL and not has_terminal:
                raise ValueError("Terminal success requires exactly terminal_result")
            if boundary == BOUNDARY_PENDING:
                if has_child or has_terminal:
                    raise ValueError("Pending success must not carry child_snapshot or terminal_result")
                if self.pending_decision_context is None or self.pending_pipeline_result is None:
                    raise ValueError("Pending success requires pending_decision_context and pending_pipeline_result")
                if self.established_lease is None:
                    raise ValueError("Pending success requires established_lease")


@dataclass(frozen=True)
class WorkerExecutionRequest:
    work_item: WorkItem
    execution_mode: str
    expected_lease: Optional[Lease] = None


def _resolve_and_step(session, state, work_item: WorkItem):
    legal_actions = state._cached_legal_actions or session.get_legal_actions()  # noqa: SLF001
    resolved_action = work_item.candidate.semantic_action.resolve(legal_actions)
    next_state = session.step(
        state,
        resolved_action,
        target_index=work_item.candidate.target_index,
        target_enemy_index=work_item.candidate.target_enemy_index,
        stop_at_pending=True,
    )
    signature = DecisionSignature.from_battle_state(
        next_state,
        semantic_action=work_item.candidate.semantic_action,
        resolved_action=resolved_action,
        target_index=work_item.candidate.target_index,
        target_enemy_index=work_item.candidate.target_enemy_index,
    )
    return next_state, signature


def _fault_result(
    work_item: WorkItem,
    execution_mode: str,
    worker_id: Optional[int],
    worker_generation: Optional[int],
    exc: BaseException,
    *,
    fault_kind: str = "worker_exception",
) -> BranchResult:
    return BranchResult(
        status=BRANCH_STATUS_FAULT,
        work_item=work_item,
        execution_mode=execution_mode,
        worker_id=worker_id,
        worker_generation=worker_generation,
        result_signature=None,
        diagnostics={
            "fault_kind": fault_kind,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
    )


def _is_process_tainting_fault(diagnostics: dict[str, Any]) -> bool:
    """Return true for faults that indicate this worker's CLR process may be tainted."""
    if not diagnostics:
        return False
    text = "\n".join(
        str(diagnostics.get(key, ""))
        for key in ("exception_type", "message", "traceback")
    )
    return (
        "NullReferenceException" in text
        and ("EndCombatInternal" in text or "CheckWinCondition" in text)
    )


def _build_success_result(
    session,
    work_item: WorkItem,
    execution_mode: str,
    worker_id: int,
    worker_generation: int,
    state_epoch: int,
    next_state,
    signature: DecisionSignature,
) -> BranchResult:
    boundary = signature.boundary
    entry = ReplayPrefixEntry(
        semantic_action=work_item.candidate.semantic_action,
        expected_signature=signature,
        target_index=work_item.candidate.target_index,
        target_enemy_index=work_item.candidate.target_enemy_index,
    )
    if boundary == BOUNDARY_STABLE:
        return BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=work_item,
            execution_mode=execution_mode,
            worker_id=worker_id,
            worker_generation=worker_generation,
            result_signature=signature,
            child_snapshot=session.capture_snapshot(),
            next_decision_result=next_state,
            next_legal_actions=list(next_state._cached_legal_actions or []),  # noqa: SLF001
        )
    if boundary == BOUNDARY_TERMINAL:
        return BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=work_item,
            execution_mode=execution_mode,
            worker_id=worker_id,
            worker_generation=worker_generation,
            result_signature=signature,
            terminal_result=BranchTerminalResult(is_terminal=next_state.is_terminal, outcome=next_state.outcome),
        )
    if boundary != BOUNDARY_PENDING:
        raise RuntimeError(f"unexpected successful worker boundary {boundary!r}")

    new_context = DecisionContext(
        root_snapshot=work_item.decision_context.root_snapshot,
        replay_prefix=append_replay_prefix_entry(work_item.decision_context.replay_prefix, entry),
        plan_path=append_replay_prefix_entry(work_item.decision_context.plan_path, entry),
        current_decision_result=next_state,
        current_context_signature=signature,
        search_hypothesis_id=work_item.search_hypothesis_id,
    )
    new_context_id = derive_context_id(new_context)
    lease = Lease(
        worker_id=worker_id,
        worker_generation=worker_generation,
        context_id=new_context_id,
        search_hypothesis_id=new_context.search_hypothesis_id,
        state_epoch=state_epoch,
        combat_session_id=signature.combat_session_id,
        step_index=signature.step_index,
        decision_result_digest=decision_result_digest(signature),
    )
    pipeline_result = build_candidate_pipeline_result(new_context)
    return BranchResult(
        status=BRANCH_STATUS_SUCCESS,
        work_item=work_item,
        execution_mode=execution_mode,
        worker_id=worker_id,
        worker_generation=worker_generation,
        result_signature=signature,
        pending_decision_context=new_context,
        pending_pipeline_result=pipeline_result,
        established_lease=lease,
    )


class _WorkerRuntime:
    def __init__(self, worker_id: int, worker_generation: int, repo_root: Optional[str], session_factory: Optional[Callable[[], Any]] = None) -> None:
        from live_combat_session import LiveCombatSession

        self.worker_id = worker_id
        self.worker_generation = worker_generation
        self.session = (
            session_factory()
            if session_factory is not None
            else LiveCombatSession(repo_root=Path(repo_root) if repo_root is not None else None)
        )
        self.current_state = None
        self.current_context_id: Optional[str] = None
        self.current_hypothesis_id: Optional[str] = None
        self.state_epoch = 0

    def execute(self, request: WorkerExecutionRequest) -> BranchResult:
        work_item = request.work_item
        try:
            if request.execution_mode == EXECUTION_MODE_BOOTSTRAP_STEP:
                replay_outcome = replay_decision_context(self.session, work_item.decision_context)
                if isinstance(replay_outcome, ReplayMismatch):
                    self.current_state = None
                    self.current_context_id = None
                    self.current_hypothesis_id = None
                    return BranchResult(
                        status=BRANCH_STATUS_FAULT,
                        work_item=work_item,
                        execution_mode=request.execution_mode,
                        worker_id=self.worker_id,
                        worker_generation=self.worker_generation,
                        diagnostics={
                            "fault_kind": "replay_mismatch",
                            "stage": replay_outcome.stage,
                            "step_index": replay_outcome.step_index,
                            "detail": replay_outcome.detail,
                            "diverged_fields": replay_outcome.diverged_fields,
                            # API.instance_combat._finalize_branch_result reads "message" for the
                            # RPC-visible "error" field; without this key it always falls back to
                            # the generic "branch execution faulted" string and the stage/detail/
                            # diverged_fields above never reach the client.
                            "message": (
                                f"replay_mismatch at stage={replay_outcome.stage} "
                                f"step_index={replay_outcome.step_index}: {replay_outcome.detail} "
                                f"diverged_fields={replay_outcome.diverged_fields}"
                            ),
                        },
                    )
                assert isinstance(replay_outcome, ReplaySuccess)
                self.state_epoch += len(work_item.decision_context.replay_prefix)
                state = replay_outcome.final_state
            elif request.execution_mode == EXECUTION_MODE_HOLDER_STEP:
                if request.expected_lease is None:
                    raise RuntimeError("Holder Step request requires expected_lease")
                if self.current_state is None:
                    raise RuntimeError("Holder Step requested but worker has no retained state")
                if self.current_context_id != work_item.context_id:
                    raise RuntimeError(
                        f"Holder Step context mismatch: worker={self.current_context_id!r} request={work_item.context_id!r}"
                    )
                if self.current_hypothesis_id != work_item.search_hypothesis_id:
                    raise RuntimeError("Holder Step search_hypothesis_id mismatch")
                if self.state_epoch != request.expected_lease.state_epoch:
                    raise RuntimeError(
                        f"Holder Step state_epoch mismatch: worker={self.state_epoch} lease={request.expected_lease.state_epoch}"
                    )
                state = self.current_state
            else:
                raise RuntimeError(f"unknown execution mode {request.execution_mode!r}")

            next_state, signature = _resolve_and_step(self.session, state, work_item)
            self.state_epoch += 1
            result = _build_success_result(
                self.session,
                work_item,
                request.execution_mode,
                self.worker_id,
                self.worker_generation,
                self.state_epoch,
                next_state,
                signature,
            )
            if result.status == BRANCH_STATUS_SUCCESS and signature.boundary == BOUNDARY_PENDING:
                self.current_state = next_state
                self.current_context_id = result.established_lease.context_id if result.established_lease else None
                self.current_hypothesis_id = work_item.search_hypothesis_id
            else:
                self.current_state = None
                self.current_context_id = None
                self.current_hypothesis_id = None
            return result
        except BaseException as exc:  # noqa: BLE001 - worker must normalize faults.
            self.current_state = None
            self.current_context_id = None
            self.current_hypothesis_id = None
            return _fault_result(
                work_item,
                request.execution_mode,
                self.worker_id,
                self.worker_generation,
                exc,
                # Exceptions that already carry their own diagnostic classification (e.g.
                # `SnapshotRestoreMissingMoveError.fault_kind`) keep it, instead of
                # collapsing into the generic "worker_exception" default - see
                # `fault_taxonomy.py`'s own `carried_fault_kind` convention.
                fault_kind=getattr(exc, "fault_kind", None) or "worker_exception",
            )


def _worker_main(worker_id: int, worker_generation: int, repo_root: Optional[str], in_queue, out_queue) -> None:
    runtime = _WorkerRuntime(worker_id, worker_generation, repo_root)
    while True:
        message = in_queue.get()
        if message is None:
            return
        request_id, request = message
        try:
            result = runtime.execute(request)
        except BaseException as exc:  # noqa: BLE001 - defensive; execute normally catches.
            result = _fault_result(
                request.work_item,
                request.execution_mode,
                worker_id,
                worker_generation,
                exc,
                fault_kind=getattr(exc, "fault_kind", None) or "worker_exception",
            )
        out_queue.put((request_id, result))


@dataclass
class _WorkerHandle:
    worker_id: int
    worker_generation: int
    process: Any
    in_queue: Any
    pid: Optional[int] = None


class WorkerDiedError(RuntimeError):
    """A worker's OS process is no longer alive - used internally by
    ``BranchWorkerPool.is_worker_alive``/respawn bookkeeping. Not raised across a
    ``dispatch_work_items``/``execute`` call boundary - a dead/hung worker surfaces to
    callers as a normal ``BRANCH_STATUS_FAULT`` result (``fault_kind="task_timeout"``),
    matching the existing ``search.fault_taxonomy`` contract, never as an uncaught
    exception."""


class BranchWorkerPool:
    """Persistent Branch Worker Pool with worker-affinity Holder dispatch."""

    # Workers are separate OS processes reached via multiprocessing.Queue, which
    # requires WorkItem.decision_context.root_snapshot to be pickle/queue-safe -
    # a CLR-wrapped CombatStateSnapshot generally is not, hence the IPC JSON
    # serialization in _work_item_for_ipc(). See AlcBranchWorkerPool for the
    # same-process counterpart, where this conversion is unnecessary overhead.
    requires_ipc_serialization = True

    def __init__(
        self,
        *,
        worker_count: int = 2,
        repo_root: Optional[Path | str] = None,
        request_timeout_s: float = 120.0,
    ) -> None:
        if worker_count <= 0:
            raise ValueError("worker_count must be positive")
        self.worker_count = worker_count
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else None
        self.request_timeout_s = request_timeout_s
        self._ctx = multiprocessing.get_context("spawn")
        self._result_queue = self._ctx.Queue()
        self._workers: dict[int, _WorkerHandle] = {}
        self._next_bootstrap_index = 0
        self._next_request_id = 0
        self._closed = False
        for worker_id in range(worker_count):
            self._spawn_worker(worker_id, generation=1)

    def _spawn_worker(self, worker_id: int, *, generation: int) -> None:
        in_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_main,
            args=(worker_id, generation, str(self.repo_root) if self.repo_root is not None else None, in_queue, self._result_queue),
            daemon=True,
        )
        process.start()
        self._workers[worker_id] = _WorkerHandle(worker_id, generation, process, in_queue, pid=process.pid)

    @property
    def worker_generations(self) -> dict[int, int]:
        return {worker_id: handle.worker_generation for worker_id, handle in self._workers.items()}

    @property
    def worker_ids(self) -> list[int]:
        return list(self._workers)

    @property
    def worker_pids(self) -> dict[int, Optional[int]]:
        return {worker_id: handle.pid for worker_id, handle in self._workers.items()}

    def is_worker_alive(self, worker_id: int) -> bool:
        handle = self._workers.get(worker_id)
        return handle is not None and handle.process.is_alive()

    def respawn_worker(self, worker_id: int, lease_registry: Optional[LeaseRegistry] = None) -> None:
        """Kills (if still alive) and replaces the OS process at `worker_id` with a fresh
        one carrying an incremented `worker_generation`. Never touches any other worker -
        a single worker's hang/crash never tears down the rest of the Pool. Every Lease
        the OLD generation held is invalidated (`lease_registry.invalidate_worker`) so a
        stale Holder Step can never be routed to the dead process's replacement, which
        holds no live state at all. Does not, and must never, touch Main's own session -
        Main Worker and Branch Workers are always separate processes/objects (Combat's
        Main loop uses its own ``LiveCombatSession``, never one of this Pool's workers).
        """
        if self._closed:
            raise RuntimeError("BranchWorkerPool is closed")
        old_handle = self._workers[worker_id]
        old_generation = old_handle.worker_generation
        if old_handle.process.is_alive():
            old_handle.process.terminate()
            old_handle.process.join(timeout=5)
        # The old in_queue is now permanently abandoned (its reader process is dead) -
        # close it and cancel its background feeder thread's join so interpreter exit
        # never blocks trying to flush a queue nobody will ever read again.
        old_handle.in_queue.close()
        old_handle.in_queue.cancel_join_thread()
        self._spawn_worker(worker_id, generation=old_generation + 1)
        if lease_registry is not None:
            lease_registry.invalidate_worker(worker_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in self._workers.values():
            handle.in_queue.put(None)
        for handle in self._workers.values():
            handle.process.join(timeout=10)
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=5)
            handle.in_queue.close()
            handle.in_queue.cancel_join_thread()
        self._result_queue.close()
        self._result_queue.cancel_join_thread()

    def __enter__(self) -> "BranchWorkerPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _submit(self, worker_id: int, request: WorkerExecutionRequest) -> int:
        if self._closed:
            raise RuntimeError("BranchWorkerPool is closed")
        handle = self._workers[worker_id]
        self._next_request_id += 1
        request_id = self._next_request_id
        handle.in_queue.put((request_id, request))
        return request_id

    def execute(
        self,
        worker_id: int,
        request: WorkerExecutionRequest,
        *,
        lease_registry: Optional[LeaseRegistry] = None,
    ) -> BranchResult:
        stale_generation = self._workers[worker_id].worker_generation
        request_id = self._submit(worker_id, request)
        while True:
            try:
                received_id, result = self._result_queue.get(timeout=self.request_timeout_s)
            except queue.Empty:
                self.respawn_worker(worker_id, lease_registry=lease_registry)
                return _fault_result(
                    request.work_item,
                    request.execution_mode,
                    worker_id,
                    stale_generation,
                    TimeoutError(f"timed out waiting for worker request {request_id}"),
                    fault_kind="task_timeout",
                )
            if received_id == request_id:
                if (
                    result.status == BRANCH_STATUS_FAULT
                    and result.worker_id is not None
                    and _is_process_tainting_fault(result.diagnostics)
                ):
                    self.respawn_worker(result.worker_id, lease_registry=lease_registry)
                return result
            # Stray/late result from an old (respawned-away) generation's in-flight
            # request - discard rather than raise, matching the instruction's
            # "旧Workerから遅れて結果が届いても...破棄してください" contract.
            continue

    def dispatch_work_items(self, work_items: list[WorkItem], lease_registry: LeaseRegistry) -> list[BranchResult]:
        return dispatch_work_items(work_items, lease_registry, worker_pool=self)


def _choose_bootstrap_worker(worker_ids: list[int], lease_registry: LeaseRegistry, next_index: int) -> tuple[int, int]:
    leased_worker_ids = lease_registry.worker_ids_holding_leases()
    unleased = [worker_id for worker_id in worker_ids if worker_id not in leased_worker_ids]
    candidates = unleased or worker_ids
    chosen = candidates[next_index % len(candidates)]
    return chosen, next_index + 1


def _route_work_item(
    work_item: WorkItem,
    lease_registry: LeaseRegistry,
    *,
    worker_ids: list[int],
    worker_generations: dict[int, int],
    next_bootstrap_index: int,
) -> tuple[WorkerExecutionRequest, int, int, Optional[Lease]]:
    """Pure routing decision; separated so tests can exercise it with fake workers."""
    if work_item.work_kind == WORK_KIND_CONTINUATION:
        lease = lease_registry.get(work_item.context_id, work_item.search_hypothesis_id)
        if lease is not None:
            generation = worker_generations.get(lease.worker_id)
            if generation is not None and lease.is_valid_for(work_item, worker_generation=generation):
                return (
                    WorkerExecutionRequest(work_item, EXECUTION_MODE_HOLDER_STEP, expected_lease=lease),
                    lease.worker_id,
                    next_bootstrap_index,
                    None,
                )
            lease_registry.invalidate_lease(lease)

    worker_id, next_bootstrap_index = _choose_bootstrap_worker(worker_ids, lease_registry, next_bootstrap_index)
    evicted = None
    if worker_id in lease_registry.worker_ids_holding_leases():
        lease_registry.invalidate_worker(worker_id)
    return (
        WorkerExecutionRequest(work_item, EXECUTION_MODE_BOOTSTRAP_STEP),
        worker_id,
        next_bootstrap_index,
        evicted,
    )


ExecuteRequest = Callable[[int, WorkerExecutionRequest], BranchResult]


def restore_result_for_caller_work_item(work_item: WorkItem, result: BranchResult) -> BranchResult:
    """Rebind ``result`` to the caller's own (pre-IPC) ``work_item``.

    A worker may have executed an IPC-serialized copy of ``work_item``
    (``_work_item_for_ipc``, needed for multiprocessing workers) rather than the
    caller's own object, in which case ``result.work_item`` is that copy and, for a
    Pending boundary, ``result.pending_decision_context.root_snapshot`` is JSON text
    rather than the caller-owned root (a parsed ``CombatStateSnapshot``, or a
    ``CombatStartReplayRoot`` at combat start - neither ever IPC-serialized as text)
    (Stable/Terminal results are unaffected: their ``child_snapshot`` always comes
    fresh from ``session.capture_snapshot()``, never from the WorkItem). Restoring
    both to the caller's own ``work_item``/its own root keeps the returned
    ``BranchResult`` identical regardless of which worker backend executed it, and
    keeps ``derive_context_id()`` on the returned Pending context consistent with
    ``result.established_lease.context_id`` (computed inside the worker from the
    IPC-serialized form, before this correction runs).
    """
    if result.work_item is work_item:
        return result
    pending_context = result.pending_decision_context
    if pending_context is not None:
        pending_context = dataclasses.replace(
            pending_context,
            root_snapshot=work_item.decision_context.root_snapshot,
        )
    return dataclasses.replace(result, work_item=work_item, pending_decision_context=pending_context)


def dispatch_work_items(
    work_items: list[WorkItem],
    lease_registry: LeaseRegistry,
    *,
    worker_pool: Optional[BranchWorkerPool] = None,
    execute_request: Optional[ExecuteRequest] = None,
    worker_ids: Optional[list[int]] = None,
    worker_generations: Optional[dict[int, int]] = None,
) -> list[BranchResult]:
    """Dispatch a batch of WorkItems and update Lease lifecycle from their results.

    For unit tests, pass ``execute_request`` plus ``worker_ids``/``worker_generations`` to
    test routing without spawning processes. For real execution, pass ``worker_pool``.
    """
    use_real_pool = execute_request is None
    if execute_request is None:
        if worker_pool is None:
            raise ValueError("dispatch_work_items() requires either worker_pool or execute_request")
        execute_request = worker_pool.execute
        worker_ids = worker_pool.worker_ids
        worker_generations = worker_pool.worker_generations
        next_bootstrap_index = worker_pool._next_bootstrap_index  # noqa: SLF001
    else:
        if worker_ids is None or worker_generations is None:
            raise ValueError("fake execute_request requires worker_ids and worker_generations")
        next_bootstrap_index = 0

    assert worker_ids is not None
    assert worker_generations is not None

    pending_real: dict[int, WorkItem] = {}
    pending_request_worker_ids: dict[int, int] = {}
    pending_request_execution_mode: dict[int, str] = {}
    fake_results_by_work_id: dict[str, BranchResult] = {}
    for work_item in work_items:
        request, worker_id, next_bootstrap_index, _ = _route_work_item(
            work_item,
            lease_registry,
            worker_ids=worker_ids,
            worker_generations=worker_generations,
            next_bootstrap_index=next_bootstrap_index,
        )
        if use_real_pool:
            assert worker_pool is not None
            if getattr(worker_pool, "requires_ipc_serialization", True):
                ipc_work_item = _work_item_for_ipc(work_item)
                ipc_request = dataclasses.replace(request, work_item=ipc_work_item)
            else:
                ipc_request = request
            request_id = worker_pool._submit(worker_id, ipc_request)  # noqa: SLF001
            pending_real[request_id] = work_item
            pending_request_worker_ids[request_id] = worker_id
            pending_request_execution_mode[request_id] = request.execution_mode
        else:
            result = execute_request(worker_id, request)
            fake_results_by_work_id[work_item.work_id] = result

    if use_real_pool:
        assert worker_pool is not None
        results_by_work_id: dict[str, BranchResult] = {}
        remaining_request_ids = set(pending_real)
        request_id_to_worker_id = dict(pending_request_worker_ids)
        while remaining_request_ids:
            try:
                request_id, result = worker_pool._result_queue.get(timeout=worker_pool.request_timeout_s)  # noqa: SLF001
            except queue.Empty:
                # Every request still outstanding at this point is hung - respawn each
                # distinct worker exactly once and synthesize a fault result for each of
                # its still-pending requests, rather than waiting indefinitely.
                hung_worker_ids = {request_id_to_worker_id[rid] for rid in remaining_request_ids}
                for hung_worker_id in hung_worker_ids:
                    stale_generation = worker_pool.worker_generations.get(hung_worker_id)
                    worker_pool.respawn_worker(hung_worker_id, lease_registry=lease_registry)
                    for rid in list(remaining_request_ids):
                        if request_id_to_worker_id[rid] != hung_worker_id:
                            continue
                        work_item = pending_real[rid]
                        results_by_work_id[work_item.work_id] = _fault_result(
                            work_item,
                            pending_request_execution_mode[rid],
                            hung_worker_id,
                            stale_generation,
                            TimeoutError(f"timed out waiting for worker request {rid}"),
                            fault_kind="task_timeout",
                        )
                        mark_request_finished = getattr(worker_pool, "mark_request_finished", None)
                        if mark_request_finished is not None:
                            mark_request_finished(rid)
                        remaining_request_ids.discard(rid)
                continue
            if request_id not in remaining_request_ids:
                # Stray/late result: either a genuine duplicate, or a response from a
                # worker generation we already gave up on and respawned away. Discard.
                log_stale_result = getattr(worker_pool, "log_stale_result", None)
                if log_stale_result is not None:
                    log_stale_result(request_id, result, expected_request_ids=set(remaining_request_ids))
                continue
            work_item = pending_real[request_id]
            results_by_work_id[work_item.work_id] = result
            mark_request_finished = getattr(worker_pool, "mark_request_finished", None)
            if mark_request_finished is not None:
                mark_request_finished(request_id)
            remaining_request_ids.discard(request_id)
            if (
                result.status == BRANCH_STATUS_FAULT
                and result.worker_id is not None
                and _is_process_tainting_fault(result.diagnostics)
            ):
                worker_pool.respawn_worker(result.worker_id, lease_registry=lease_registry)
    else:
        results_by_work_id = fake_results_by_work_id

    results: list[BranchResult] = []
    for work_item in work_items:
        result = restore_result_for_caller_work_item(work_item, results_by_work_id[work_item.work_id])
        lease_registry.invalidate(work_item.context_id, work_item.search_hypothesis_id)
        if result.status == BRANCH_STATUS_SUCCESS and result.established_lease is not None:
            lease_registry.set(result.established_lease)
        if result.status == BRANCH_STATUS_FAULT and result.worker_id is not None:
            lease_registry.invalidate_worker(result.worker_id)
        results.append(result)

    if worker_pool is not None:
        worker_pool._next_bootstrap_index = next_bootstrap_index  # noqa: SLF001
    return results
