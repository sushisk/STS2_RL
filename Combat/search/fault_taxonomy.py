"""Fault taxonomy and Commit aggregation helpers for Combat search Phase 7.

This module implements the Search Coordinator-side fault vocabulary and one-round Commit
aggregation described by:

* docs/architecture/combat/mermaid_combat_fault_worker_detail.mermaid
* docs/architecture/combat/mermaid_combat_commit_detail.mermaid

It deliberately wraps Phase 5's ``BranchResult``/``WorkItem`` instead of changing those
types. Only final WorkItem outcomes are accepted by aggregation; in-flight retry states
are kept out of scoring so a later successful retry cannot be double-counted.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from live_combat_session import (
    ActionExecutionError,
    FaultedCombatSessionError,
    QuiescentBoundaryViolation,
    SnapshotRestoreFailedError,
    SnapshotRestoreMissingMoveError,
    SnapshotRestoreRejectedError,
)
from search.branch_worker_pool import BRANCH_STATUS_FAULT, BRANCH_STATUS_SUCCESS, BranchResult, WorkItem
from search.candidate_pipeline import NoViableCandidates
from search.decision_context import ReplayPrefixEntry, SemanticAction
from search.main_loop import PlannedStep, SearchEvaluationFailure, SearchSuccess


# Branch-worker fault taxonomy. These are the only values accepted by worker_reuse_policy().
FAULT_VALIDATION_REJECTION = "validation_rejection"
FAULT_REPLAY_MISMATCH = "replay_mismatch"
FAULT_POST_TEARDOWN_RESTORE_FAILURE = "post_teardown_restore_failure"
FAULT_ACTION_FAULT = "action_fault"
FAULT_TASK_TIMEOUT = "task_timeout"
FAULT_WORKER_PROCESS_CRASH = "worker_process_crash"
FAULT_DEPTH_CAP_EXCEEDED = "depth_cap_exceeded"
FAULT_ZERO_CANDIDATES = "zero_candidates"
# `SnapshotRestoreMissingMoveError.fault_kind` - a living enemy still has no current Move
# (`Intent.stateId == "UNSET_MOVE"`) when `step()` is asked to execute End Turn. Caught at
# the actual trigger point (before the CLR call that would otherwise hang ~15s and
# collapse into an opaque `FAULT_TASK_TIMEOUT`) - see that exception's own docstring.
FAULT_SNAPSHOT_MOVE_MISSING = SnapshotRestoreMissingMoveError.fault_kind

BRANCH_FAULT_KINDS = frozenset(
    {
        FAULT_VALIDATION_REJECTION,
        FAULT_REPLAY_MISMATCH,
        FAULT_POST_TEARDOWN_RESTORE_FAILURE,
        FAULT_ACTION_FAULT,
        FAULT_TASK_TIMEOUT,
        FAULT_WORKER_PROCESS_CRASH,
        FAULT_DEPTH_CAP_EXCEEDED,
        FAULT_ZERO_CANDIDATES,
        FAULT_SNAPSHOT_MOVE_MISSING,
    }
)


# Main-process fault sources are intentionally separate from branch-worker faults.
SRC_MAIN_STEP = "main_step"
SRC_MAIN_INVARIANT = "main_invariant"
MAIN_FAULT_SOURCES = frozenset({SRC_MAIN_STEP, SRC_MAIN_INVARIANT})


REUSE_SAFE = "reuse_safe"
FORCE_RESTART = "force_restart"
WorkerReusePolicy = str


WORK_ITEM_RUNNING = "Running"
WORK_ITEM_RETRYING = "Retrying"
WORK_ITEM_FINAL_SUCCESS = "FinalSuccess"
WORK_ITEM_FINAL_FAULT = "FinalFault"
WORK_ITEM_FINAL_STATES = frozenset({WORK_ITEM_FINAL_SUCCESS, WORK_ITEM_FINAL_FAULT})
WORK_ITEM_STATES = frozenset(
    {WORK_ITEM_RUNNING, WORK_ITEM_RETRYING, WORK_ITEM_FINAL_SUCCESS, WORK_ITEM_FINAL_FAULT}
)


DECISION_LOG_STATUS_SUCCESS = "success"
DECISION_LOG_STATUS_VALIDATION_REJECTED = "validation_rejected"
DECISION_LOG_STATUS_REPLAY_MISMATCH = "replay_mismatch"
DECISION_LOG_STATUS_ACTION_FAULT = "action_fault"
DECISION_LOG_STATUS_TIMEOUT = "timeout"
DECISION_LOG_STATUS_COVERAGE_FAILED = "coverage_failed"
DECISION_LOG_STATUS_DIAGNOSTIC_ONLY = "diagnostic_only"


_FAULT_BY_EXCEPTION_TYPE = {
    SnapshotRestoreRejectedError.__name__: FAULT_VALIDATION_REJECTION,
    SnapshotRestoreFailedError.__name__: FAULT_POST_TEARDOWN_RESTORE_FAILURE,
    SnapshotRestoreMissingMoveError.__name__: FAULT_SNAPSHOT_MOVE_MISSING,
    ActionExecutionError.__name__: FAULT_ACTION_FAULT,
    FaultedCombatSessionError.__name__: FAULT_ACTION_FAULT,
    QuiescentBoundaryViolation.__name__: FAULT_ACTION_FAULT,
    TimeoutError.__name__: FAULT_TASK_TIMEOUT,
    NoViableCandidates.__name__: FAULT_ZERO_CANDIDATES,
}

_FAULT_BY_DIAGNOSTIC_KIND = {
    "validation_rejection": FAULT_VALIDATION_REJECTION,
    "snapshot_restore_rejected": FAULT_VALIDATION_REJECTION,
    "SnapshotRestoreRejectedError": FAULT_VALIDATION_REJECTION,
    "replay_mismatch": FAULT_REPLAY_MISMATCH,
    "post_teardown_restore_failure": FAULT_POST_TEARDOWN_RESTORE_FAILURE,
    "snapshot_restore_failed": FAULT_POST_TEARDOWN_RESTORE_FAILURE,
    "SnapshotRestoreFailedError": FAULT_POST_TEARDOWN_RESTORE_FAILURE,
    FAULT_SNAPSHOT_MOVE_MISSING: FAULT_SNAPSHOT_MOVE_MISSING,
    "SnapshotRestoreMissingMoveError": FAULT_SNAPSHOT_MOVE_MISSING,
    "action_fault": FAULT_ACTION_FAULT,
    "worker_exception": FAULT_ACTION_FAULT,
    "task_timeout": FAULT_TASK_TIMEOUT,
    "timeout": FAULT_TASK_TIMEOUT,
    "worker_process_crash": FAULT_WORKER_PROCESS_CRASH,
    "process_crash": FAULT_WORKER_PROCESS_CRASH,
    "depth_cap_exceeded": FAULT_DEPTH_CAP_EXCEEDED,
    "zero_candidates": FAULT_ZERO_CANDIDATES,
    "no_viable_candidates": FAULT_ZERO_CANDIDATES,
}

_DECISION_LOG_STATUS_BY_FAULT = {
    FAULT_VALIDATION_REJECTION: DECISION_LOG_STATUS_VALIDATION_REJECTED,
    FAULT_REPLAY_MISMATCH: DECISION_LOG_STATUS_REPLAY_MISMATCH,
    FAULT_ACTION_FAULT: DECISION_LOG_STATUS_ACTION_FAULT,
    FAULT_TASK_TIMEOUT: DECISION_LOG_STATUS_TIMEOUT,
    FAULT_POST_TEARDOWN_RESTORE_FAILURE: DECISION_LOG_STATUS_DIAGNOSTIC_ONLY,
    FAULT_WORKER_PROCESS_CRASH: DECISION_LOG_STATUS_DIAGNOSTIC_ONLY,
    FAULT_DEPTH_CAP_EXCEEDED: DECISION_LOG_STATUS_DIAGNOSTIC_ONLY,
    FAULT_ZERO_CANDIDATES: DECISION_LOG_STATUS_COVERAGE_FAILED,
    FAULT_SNAPSHOT_MOVE_MISSING: DECISION_LOG_STATUS_VALIDATION_REJECTED,
}


def classify_fault(exc_or_diagnostics: Any) -> str:
    """Map a Phase 5 diagnostic dict or real exception into the Phase 7 taxonomy."""
    if isinstance(exc_or_diagnostics, dict):
        fault_kind = exc_or_diagnostics.get("fault_kind")
        exception_type = exc_or_diagnostics.get("exception_type")
        if exception_type in _FAULT_BY_EXCEPTION_TYPE:
            return _FAULT_BY_EXCEPTION_TYPE[str(exception_type)]
        if fault_kind in _FAULT_BY_DIAGNOSTIC_KIND:
            mapped = _FAULT_BY_DIAGNOSTIC_KIND[str(fault_kind)]
            if mapped == FAULT_ACTION_FAULT and exception_type in {
                SnapshotRestoreRejectedError.__name__,
                SnapshotRestoreFailedError.__name__,
            }:
                return _FAULT_BY_EXCEPTION_TYPE[str(exception_type)]
            return mapped
        if exception_type:
            return _FAULT_BY_EXCEPTION_TYPE.get(str(exception_type), FAULT_ACTION_FAULT)
        raise ValueError(f"cannot classify diagnostics without fault_kind/exception_type: {exc_or_diagnostics!r}")

    for exc_type, fault_kind in (
        (SnapshotRestoreRejectedError, FAULT_VALIDATION_REJECTION),
        (SnapshotRestoreFailedError, FAULT_POST_TEARDOWN_RESTORE_FAILURE),
        (SnapshotRestoreMissingMoveError, FAULT_SNAPSHOT_MOVE_MISSING),
        (ActionExecutionError, FAULT_ACTION_FAULT),
        (FaultedCombatSessionError, FAULT_ACTION_FAULT),
        (QuiescentBoundaryViolation, FAULT_ACTION_FAULT),
        (TimeoutError, FAULT_TASK_TIMEOUT),
    ):
        if isinstance(exc_or_diagnostics, exc_type):
            return fault_kind
    if isinstance(exc_or_diagnostics, NoViableCandidates):
        return FAULT_ZERO_CANDIDATES
    carried_fault_kind = getattr(exc_or_diagnostics, "fault_kind", None)
    if carried_fault_kind in BRANCH_FAULT_KINDS:
        return str(carried_fault_kind)
    raise ValueError(f"cannot classify fault object {exc_or_diagnostics!r}")


def worker_reuse_policy(fault_kind: str) -> WorkerReusePolicy:
    """Return the worker reuse policy for a branch-worker fault kind."""
    if fault_kind == FAULT_VALIDATION_REJECTION:
        return REUSE_SAFE
    if fault_kind in BRANCH_FAULT_KINDS:
        return FORCE_RESTART
    if fault_kind in MAIN_FAULT_SOURCES:
        raise ValueError(f"{fault_kind!r} is a Main fault source, not a Branch Worker fault")
    raise ValueError(f"unknown branch fault kind {fault_kind!r}")


@dataclass(frozen=True)
class WorkItemAttempt:
    work_id: str
    attempt_count: int = 0
    state: str = WORK_ITEM_RUNNING
    worker_generation: int = 1

    def __post_init__(self) -> None:
        if self.state not in WORK_ITEM_STATES:
            raise ValueError(f"unknown WorkItem state {self.state!r}")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative")
        if self.worker_generation <= 0:
            raise ValueError("worker_generation must be positive")


@dataclass(frozen=True)
class RetryDecision:
    next_state: str
    policy: WorkerReusePolicy
    should_retry: bool
    worker_generation: int
    attempt_count: int


def decide_retry(
    attempt: WorkItemAttempt,
    fault_kind: str,
    *,
    max_retries: int = 1,
) -> RetryDecision:
    """Advance a WorkItem after a fault.

    ``max_retries`` defaults to 1: one fresh attempt is allowed after the initial failed
    execution. The timeout path always bumps ``worker_generation`` with FORCE_RESTART.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if attempt.state not in {WORK_ITEM_RUNNING, WORK_ITEM_RETRYING}:
        raise ValueError(f"cannot retry from final WorkItem state {attempt.state!r}")

    policy = worker_reuse_policy(fault_kind)
    next_attempt_count = attempt.attempt_count + 1
    should_retry = next_attempt_count <= max_retries
    next_generation = attempt.worker_generation + 1 if policy == FORCE_RESTART else attempt.worker_generation
    return RetryDecision(
        next_state=WORK_ITEM_RETRYING if should_retry else WORK_ITEM_FINAL_FAULT,
        policy=policy,
        should_retry=should_retry,
        worker_generation=next_generation,
        attempt_count=next_attempt_count,
    )


@dataclass(frozen=True)
class DepthCapExceeded(RuntimeError):
    current_depth: int
    max_depth: int
    fault_kind: str = FAULT_DEPTH_CAP_EXCEEDED

    def __str__(self) -> str:
        return f"continuation depth {self.current_depth} exceeded cap {self.max_depth}"


def depth_cap_fault(current_depth: int, max_depth: int) -> Optional[DepthCapExceeded]:
    """Return a typed depth-cap fault when ``current_depth >= max_depth``."""
    if max_depth <= 0:
        raise ValueError("max_depth must be positive")
    if current_depth < 0:
        raise ValueError("current_depth must be non-negative")
    if current_depth >= max_depth:
        return DepthCapExceeded(current_depth=current_depth, max_depth=max_depth)
    return None


@dataclass(frozen=True)
class MainCombatFaultOutcome:
    fault_source: str
    detail: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fault_source not in MAIN_FAULT_SOURCES:
            raise ValueError(f"unknown Main fault source {self.fault_source!r}")


@dataclass(frozen=True)
class BranchDecisionLogEntry:
    status: str
    root_action_key: str
    hypothesis_id: Optional[str]
    plan_path: tuple[ReplayPrefixEntry, ...]
    replay_prefix_digest: str
    work_id: str
    work_item_state: str
    score: Optional[float] = None
    semantic_action: Optional[SemanticAction] = None
    target_index: Optional[int] = None
    target_enemy_index: Optional[int] = None
    expected_signature: Any = None
    result_signature: Any = None
    fault_kind: Optional[str] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.work_item_state not in WORK_ITEM_STATES:
            raise ValueError(f"unknown WorkItem state {self.work_item_state!r}")
        if self.work_item_state == WORK_ITEM_FINAL_SUCCESS and self.score is None:
            raise ValueError("FinalSuccess log entries require score")


@dataclass(frozen=True)
class RootActionAggregation:
    root_action_key: str
    aggregate_score: float
    representative_entry: BranchDecisionLogEntry
    valid_sample_count: int
    total_hypotheses: int
    missing_sample_count: int
    filled_score: Optional[float] = None


@dataclass(frozen=True)
class AggregationResult:
    viable_actions: tuple[RootActionAggregation, ...]
    best_action: Optional[RootActionAggregation]
    diagnostics: dict[str, Any]

    @property
    def has_viable_action(self) -> bool:
        return self.best_action is not None


def _semantic_action_key(action: SemanticAction, target_index: Optional[int], target_enemy_index: Optional[int]) -> str:
    return _digest(
        {
            "action_type": action.action_type,
            "card_id": action.card_id,
            "target_type": action.target_type,
            "target_index": target_index,
            "target_enemy_index": target_enemy_index,
        },
        length=20,
    )


def _digest(payload: Any, *, length: int = 16) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _replay_prefix_digest(entries: list[ReplayPrefixEntry]) -> str:
    return _digest([dataclasses.asdict(entry) for entry in entries])


def to_decision_log_entry(
    work_item: WorkItem,
    branch_result: BranchResult,
    *,
    work_item_state: Optional[str] = None,
) -> BranchDecisionLogEntry:
    """Build the Phase 7 shared decision-log wrapper from a Phase 5 result pair."""
    if branch_result.work_item is not work_item and branch_result.work_item.work_id != work_item.work_id:
        raise ValueError("BranchResult does not correspond to the supplied WorkItem")

    if work_item_state is None:
        work_item_state = WORK_ITEM_FINAL_SUCCESS if branch_result.status == BRANCH_STATUS_SUCCESS else WORK_ITEM_FINAL_FAULT
    if work_item_state not in WORK_ITEM_STATES:
        raise ValueError(f"unknown WorkItem state {work_item_state!r}")

    fault_kind = None
    if branch_result.status == BRANCH_STATUS_SUCCESS:
        status = DECISION_LOG_STATUS_SUCCESS
        score = float(work_item.candidate.score)
    elif branch_result.status == BRANCH_STATUS_FAULT:
        fault_kind = classify_fault(branch_result.diagnostics)
        status = _DECISION_LOG_STATUS_BY_FAULT[fault_kind]
        score = None
    else:
        raise ValueError(f"unknown BranchResult status {branch_result.status!r}")

    candidate = work_item.candidate
    return BranchDecisionLogEntry(
        status=status,
        root_action_key=_semantic_action_key(candidate.semantic_action, candidate.target_index, candidate.target_enemy_index),
        hypothesis_id=work_item.search_hypothesis_id,
        plan_path=tuple(work_item.decision_context.plan_path),
        replay_prefix_digest=_replay_prefix_digest(work_item.decision_context.replay_prefix),
        work_id=work_item.work_id,
        work_item_state=work_item_state,
        score=score,
        semantic_action=candidate.semantic_action,
        target_index=candidate.target_index,
        target_enemy_index=candidate.target_enemy_index,
        expected_signature=None,
        result_signature=branch_result.result_signature,
        fault_kind=fault_kind,
        diagnostics=dict(branch_result.diagnostics),
    )


def _require_final_entries(entries: list[BranchDecisionLogEntry]) -> None:
    non_final = [entry.work_id for entry in entries if entry.work_item_state not in WORK_ITEM_FINAL_STATES]
    if non_final:
        raise ValueError(f"aggregation accepts only FinalSuccess/FinalFault entries; non-final={non_final!r}")


def _success_entries(entries: list[BranchDecisionLogEntry]) -> list[BranchDecisionLogEntry]:
    return [
        entry
        for entry in entries
        if entry.work_item_state == WORK_ITEM_FINAL_SUCCESS and entry.status == DECISION_LOG_STATUS_SUCCESS
    ]


def aggregate_hypothesis_results(
    entries: list[BranchDecisionLogEntry],
    *,
    min_coverage_fraction: float = 0.5,
) -> AggregationResult:
    """Aggregate one Root Action x Hypothesis evaluation segment.

    Missing/faulted hypothesis samples are filled with the worst score among all valid
    samples across all root actions in this aggregation round.
    """
    if not 0.0 <= min_coverage_fraction <= 1.0:
        raise ValueError("min_coverage_fraction must be between 0 and 1")
    _require_final_entries(entries)

    all_hypotheses = {entry.hypothesis_id for entry in entries if entry.hypothesis_id is not None}
    total_hypotheses = len(all_hypotheses)
    valid_entries = _success_entries(entries)
    fault_count = len(entries) - len(valid_entries)
    if total_hypotheses == 0 or not valid_entries:
        return AggregationResult(
            viable_actions=(),
            best_action=None,
            diagnostics={
                "mode": "hypothesis",
                "total_entries": len(entries),
                "faulted_or_excluded_entries": fault_count,
                "excluded_root_actions": [],
                "reason": "no valid hypothesis samples",
            },
        )

    worst_valid_score = min(float(entry.score) for entry in valid_entries if entry.score is not None)
    required = math.ceil(total_hypotheses * min_coverage_fraction)

    by_root_hypothesis: dict[str, dict[str, BranchDecisionLogEntry]] = {}
    all_roots = sorted({entry.root_action_key for entry in entries})
    for entry in valid_entries:
        if entry.hypothesis_id is None:
            continue
        per_root = by_root_hypothesis.setdefault(entry.root_action_key, {})
        current = per_root.get(entry.hypothesis_id)
        if current is None or (entry.score is not None and current.score is not None and entry.score > current.score):
            per_root[entry.hypothesis_id] = entry

    viable: list[RootActionAggregation] = []
    excluded: list[dict[str, Any]] = []
    for root_key in all_roots:
        samples = by_root_hypothesis.get(root_key, {})
        valid_count = len(samples)
        if valid_count < required:
            excluded.append({"root_action_key": root_key, "valid_sample_count": valid_count, "required": required})
            continue
        missing_count = total_hypotheses - valid_count
        score_sum = sum(float(entry.score) for entry in samples.values() if entry.score is not None)
        score_sum += missing_count * worst_valid_score
        aggregate_score = score_sum / total_hypotheses
        representative = max(samples.values(), key=lambda entry: (float(entry.score), entry.work_id))
        viable.append(
            RootActionAggregation(
                root_action_key=root_key,
                aggregate_score=aggregate_score,
                representative_entry=representative,
                valid_sample_count=valid_count,
                total_hypotheses=total_hypotheses,
                missing_sample_count=missing_count,
                filled_score=worst_valid_score if missing_count else None,
            )
        )

    viable_sorted = tuple(sorted(viable, key=lambda item: (-item.aggregate_score, item.root_action_key)))
    return AggregationResult(
        viable_actions=viable_sorted,
        best_action=viable_sorted[0] if viable_sorted else None,
        diagnostics={
            "mode": "hypothesis",
            "total_entries": len(entries),
            "total_hypotheses": total_hypotheses,
            "min_coverage_fraction": min_coverage_fraction,
            "required_valid_samples": required,
            "worst_valid_score_all_roots": worst_valid_score,
            "faulted_or_excluded_entries": fault_count,
            "excluded_root_actions": excluded,
        },
    )


def aggregate_plain_results(entries: list[BranchDecisionLogEntry]) -> AggregationResult:
    """Aggregate RNG-independent one-round branch results: best successful score wins."""
    _require_final_entries(entries)
    valid_entries = _success_entries(entries)
    fault_count = len(entries) - len(valid_entries)
    if not valid_entries:
        return AggregationResult(
            viable_actions=(),
            best_action=None,
            diagnostics={
                "mode": "plain",
                "total_entries": len(entries),
                "faulted_or_excluded_entries": fault_count,
                "reason": "no successful branch results",
            },
        )

    viable = tuple(
        RootActionAggregation(
            root_action_key=entry.root_action_key,
            aggregate_score=float(entry.score),
            representative_entry=entry,
            valid_sample_count=1,
            total_hypotheses=1,
            missing_sample_count=0,
        )
        for entry in sorted(valid_entries, key=lambda item: (-float(item.score), item.root_action_key, item.work_id))
    )
    return AggregationResult(
        viable_actions=viable,
        best_action=viable[0],
        diagnostics={
            "mode": "plain",
            "total_entries": len(entries),
            "faulted_or_excluded_entries": fault_count,
        },
    )


def build_commit_decision(
    aggregation: AggregationResult,
    *,
    hypothesis_involved: bool,
    verify_main_invariant: Callable[[], bool],
) -> Union[SearchSuccess, SearchEvaluationFailure, MainCombatFaultOutcome]:
    """Build the Phase 3 Search result shape from an aggregation outcome."""
    if not aggregation.has_viable_action:
        return SearchEvaluationFailure(detail="search evaluation produced zero viable Root Actions")

    if not verify_main_invariant():
        return MainCombatFaultOutcome(
            fault_source=SRC_MAIN_INVARIANT,
            detail="Main state identity changed during search aggregation",
            diagnostics=dict(aggregation.diagnostics),
        )

    best = aggregation.best_action
    assert best is not None
    entry = best.representative_entry
    if entry.semantic_action is None:
        raise ValueError("best aggregation entry does not carry a semantic_action")
    step = PlannedStep(
        semantic_action=entry.semantic_action,
        target_index=entry.target_index,
        target_enemy_index=entry.target_enemy_index,
        expected_signature=None if hypothesis_involved else entry.result_signature,
    )
    return SearchSuccess(planned_sequence=[step])
