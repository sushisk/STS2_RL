"""Search Coordinator integration - Combat execution infrastructure Phase 8.

This module is intentionally thin assembly over Phases 2-7. It builds a Phase-3
``SearchStrategy`` by composing the existing Candidate Pipeline, RNG Hypothesis helpers,
Branch Worker Pool, and Commit aggregation. Faulted branch work is retried before final
Commit aggregation according to Phase 7's retry taxonomy; deeper tree search is still
outside this assembly layer.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from combat_state_snapshot import CombatStateSnapshot, canonical_json
from search.branch_worker_pool import (
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    WORK_KIND_CONTINUATION,
    WORK_KIND_SUB_BRANCH,
    BranchResult,
    BranchWorkerPool,
    LeaseRegistry,
    WorkItem,
    derive_context_id,
    dispatch_work_items,
)
from search.candidate_pipeline import CandidatePipelineSuccess, NoViableCandidates, PipelineCandidateRef
from search.candidate_pipeline import build_candidate_pipeline_result
from search.decision_context import DecisionContext
from search.fault_taxonomy import (
    MainCombatFaultOutcome,
    aggregate_hypothesis_results,
    aggregate_plain_results,
    build_commit_decision,
    classify_fault,
    decide_retry,
    to_decision_log_entry,
    WORK_ITEM_FINAL_FAULT,
    WORK_ITEM_FINAL_SUCCESS,
    WorkItemAttempt,
)
from search.belief_coverage import CoverageAssessment, compute_public_multiset_with_coverage
from search.main_loop import SearchEvaluationFailure, SearchStrategy
from search.main_loop import MainLoopState
from search.rng_hypothesis import (
    build_grid,
    consume_check,
    generate_belief_hypotheses,
    with_search_hypothesis,
)


@dataclass(frozen=True)
class SearchCoordinatorConfig:
    """Tunables consumed by the composed Phase 4-7 search pieces."""

    width: int = 8
    hypothesis_count: int = 4
    min_coverage_fraction: float = 0.5
    worker_count: int = 2
    max_retries: int = 1
    request_timeout_s: float = 120.0


@dataclass
class SearchCoordinatorMetrics:
    """Optional per-call counters for observational shadow evaluation."""

    pipeline_candidate_count: int = 0
    pruned_candidate_count: int = 0
    work_item_count: int = 0
    hypothesis_involved: bool = False
    hypothesis_count: int = 0
    dispatch_round_count: int = 0
    branch_step_count: int = 0
    bootstrap_step_count: int = 0
    holder_step_count: int = 0
    replay_count: int = 0
    fault_count: int = 0
    retry_count: int = 0
    worker_ids_used: set[int] = field(default_factory=set)
    final_success_count: int = 0
    final_fault_count: int = 0
    aggregation_mode: Optional[str] = None
    best_aggregate_score: Optional[float] = None
    aggregation_diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def worker_utilization_fraction(self) -> float:
        if self.work_item_count <= 0:
            return 0.0
        return len(self.worker_ids_used) / float(self.work_item_count)


@dataclass(frozen=True)
class MainInvariantCheckResult:
    """Detailed result for Main state identity verification at Commit time."""

    ok: bool
    mismatches: tuple[str, ...] = ()
    diagnostics: dict[str, object] = dataclasses.field(default_factory=dict)


class MainInvariantViolatedError(RuntimeError):
    """Raised when Search detects Main moved while a strategy call was in flight.

    ``SearchStrategy`` cannot return ``MainCombatFaultOutcome`` without lying about its
    fixed success/evaluation-failure contract. Future Main-loop integration should catch
    this specific exception and route ``.outcome`` into Main's own fault path.
    """

    def __init__(self, outcome: MainCombatFaultOutcome, check_result: MainInvariantCheckResult) -> None:
        self.outcome = outcome
        self.check_result = check_result
        mismatch_text = ", ".join(check_result.mismatches) or "unknown mismatch"
        super().__init__(f"Main invariant violated during Search commit: {mismatch_text}; outcome={outcome!r}")


def _snapshot_content_digest(snapshot: CombatStateSnapshot) -> str:
    """Hash only stable snapshot content, excluding volatile capture metadata.

    This intentionally mirrors Branch Worker Pool's root-snapshot side of
    ``derive_context_id()`` without folding replay-prefix or hypothesis fields into the
    answer, because the invariant checks Held Snapshot identity separately.
    """

    text = canonical_json(dataclasses.asdict(snapshot), exclude_volatile=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _live_state_identity(loop_state: MainLoopState) -> tuple[Optional[str], int]:
    frame = loop_state.current_result.decision_frame
    if frame is None:
        return None, -1
    return frame.combat_session_id, frame.step_index


def _check_main_invariant(
    original_context: DecisionContext,
    main_state_provider: Callable[[], MainLoopState],
) -> MainInvariantCheckResult:
    live_state = main_state_provider()
    expected_signature = original_context.current_context_signature
    live_session_id, live_step_index = _live_state_identity(live_state)
    expected_identity = (expected_signature.combat_session_id, expected_signature.step_index)
    live_identity = (live_session_id, live_step_index)
    mismatches: list[str] = []
    diagnostics: dict[str, object] = {
        "expected_state_identity": {
            "combat_session_id": expected_identity[0],
            "step_index": expected_identity[1],
        },
        "live_state_identity": {
            "combat_session_id": live_identity[0],
            "step_index": live_identity[1],
        },
    }

    if live_identity != expected_identity:
        mismatches.append("state_identity")

    if live_state.held_stable_snapshot is None:
        mismatches.append("held_snapshot")
        diagnostics["expected_held_snapshot_sha256"] = _snapshot_content_digest(original_context.root_snapshot)
        diagnostics["live_held_snapshot_sha256"] = None
    else:
        expected_snapshot_digest = _snapshot_content_digest(original_context.root_snapshot)
        live_snapshot_digest = _snapshot_content_digest(live_state.held_stable_snapshot)
        diagnostics["expected_held_snapshot_sha256"] = expected_snapshot_digest
        diagnostics["live_held_snapshot_sha256"] = live_snapshot_digest
        if live_snapshot_digest != expected_snapshot_digest:
            mismatches.append("held_snapshot")

    replay_prefix_matches = live_state.replay_prefix == original_context.replay_prefix
    diagnostics["expected_replay_prefix_length"] = len(original_context.replay_prefix)
    diagnostics["live_replay_prefix_length"] = len(live_state.replay_prefix)
    diagnostics["replay_prefix_matches"] = replay_prefix_matches
    if not replay_prefix_matches:
        mismatches.append("replay_prefix")

    return MainInvariantCheckResult(ok=not mismatches, mismatches=tuple(mismatches), diagnostics=diagnostics)


def _candidate_batch(pipeline: CandidatePipelineSuccess) -> list[PipelineCandidateRef]:
    return [pipeline.continuation_candidate, *pipeline.sub_branch_candidates]


def _requires_hypothesis(decision_context: DecisionContext, candidates: list[PipelineCandidateRef]) -> bool:
    """Use one integration-level mode per comparison batch.

    The Mermaid contract does not define comparing hypothesis-backed candidates against
    true-RNG passthrough candidates in the same root-action ranking. Therefore, if any
    candidate in the pruned batch needs a hypothesis, the whole batch is evaluated on
    the same Root Action x Hypothesis grid.
    """

    return any(consume_check(candidate, decision_context).requires_hypothesis for candidate in candidates)


def _plain_work_items(decision_context: DecisionContext, candidates: list[PipelineCandidateRef]) -> list[WorkItem]:
    context_id = derive_context_id(decision_context)
    return [
        WorkItem.from_candidate_ref(
            decision_context,
            candidate,
            work_kind=WORK_KIND_CONTINUATION if index == 0 else WORK_KIND_SUB_BRANCH,
            context_id=context_id,
        )
        for index, candidate in enumerate(candidates)
    ]


def _hypothesis_work_items(
    decision_context: DecisionContext,
    candidates: list[PipelineCandidateRef],
    *,
    config: SearchCoordinatorConfig,
    combat_start_deck_multiset: dict[str, int],
) -> list[WorkItem]:
    work_items, _coverage = _hypothesis_work_items_with_coverage(
        decision_context,
        candidates,
        config=config,
        combat_start_deck_multiset=combat_start_deck_multiset,
    )
    return work_items


def _hypothesis_work_items_with_coverage(
    decision_context: DecisionContext,
    candidates: list[PipelineCandidateRef],
    *,
    config: SearchCoordinatorConfig,
    combat_start_deck_multiset: dict[str, int],
) -> tuple[list[WorkItem], CoverageAssessment]:
    public_multiset, coverage = compute_public_multiset_with_coverage(
        decision_context.root_snapshot,
        combat_start_deck_multiset=combat_start_deck_multiset,
    )
    shuffle_rng = decision_context.root_snapshot.Rng.RunRng["Shuffle"]
    hypotheses = generate_belief_hypotheses(
        public_multiset,
        count=config.hypothesis_count,
        rng_seed_source=lambda _index: shuffle_rng,
    )
    cells = build_grid(candidates, hypotheses, decision_context.root_snapshot)
    root_index_by_id = {id(candidate): index for index, candidate in enumerate(candidates)}

    work_items: list[WorkItem] = []
    for cell in cells:
        root_index = root_index_by_id[id(cell.root_action)]
        context = dataclasses.replace(decision_context, root_snapshot=cell.derived_snapshot)
        context = with_search_hypothesis(context, cell.hypothesis)
        work_items.append(
            WorkItem.from_candidate_ref(
                context,
                cell.root_action,
                work_kind=WORK_KIND_CONTINUATION if root_index == 0 else WORK_KIND_SUB_BRANCH,
            )
        )
    return work_items, coverage


def _coverage_diagnostics(coverage: CoverageAssessment) -> dict[str, object]:
    return {
        "is_complete": coverage.is_complete,
        "uncertain_sources": list(coverage.uncertain_sources),
        "reason": coverage.reason,
    }


def _dispatch_work_items_until_final(
    work_items: list[WorkItem],
    lease_registry: LeaseRegistry,
    *,
    pool: BranchWorkerPool,
    config: SearchCoordinatorConfig,
    metrics: Optional[SearchCoordinatorMetrics] = None,
) -> list[tuple[WorkItem, BranchResult, str]]:
    """Dispatch and retry WorkItems until each has a final outcome.

    The outer cap is one round above the legitimate maximum: initial execution plus
    ``max_retries`` resubmissions. Hitting it means retry state accounting regressed.
    ``FORCE_RESTART`` decisions are tracked as generation bumps, but BranchWorkerPool has
    no process-level restart API yet; fault dispatch already invalidates affected leases,
    so resubmission goes through normal Bootstrap/Holder routing with stale leases gone.
    """

    attempts = {item.work_id: WorkItemAttempt(work_id=item.work_id) for item in work_items}
    by_work_id = {item.work_id: item for item in work_items}
    final_results: dict[str, BranchResult] = {}
    final_states: dict[str, str] = {}
    pending = list(work_items)
    max_rounds = config.max_retries + 2
    round_count = 0

    while pending:
        round_count += 1
        if metrics is not None:
            metrics.dispatch_round_count += 1
        if round_count > max_rounds:
            states = {work_id: dataclasses.asdict(attempt) for work_id, attempt in attempts.items()}
            raise RuntimeError(
                f"SearchCoordinator retry loop exceeded defensive round cap "
                f"{max_rounds} for max_retries={config.max_retries}; states={states!r}"
            )

        branch_results = dispatch_work_items(pending, lease_registry, worker_pool=pool)
        if len(branch_results) != len(pending):
            raise RuntimeError(
                f"dispatch_work_items returned {len(branch_results)} results for {len(pending)} WorkItems"
            )

        retry_next_round: list[WorkItem] = []
        for work_item, branch_result in zip(pending, branch_results):
            if metrics is not None:
                metrics.branch_step_count += 1
                if branch_result.execution_mode == "bootstrap_step":
                    metrics.bootstrap_step_count += 1
                    metrics.replay_count += 1
                elif branch_result.execution_mode == "holder_step":
                    metrics.holder_step_count += 1
                if branch_result.worker_id is not None:
                    metrics.worker_ids_used.add(branch_result.worker_id)
            attempt = attempts[work_item.work_id]
            if branch_result.status == BRANCH_STATUS_SUCCESS:
                attempts[work_item.work_id] = dataclasses.replace(
                    attempt,
                    state=WORK_ITEM_FINAL_SUCCESS,
                    worker_generation=branch_result.worker_generation or attempt.worker_generation,
                )
                final_results[work_item.work_id] = branch_result
                final_states[work_item.work_id] = WORK_ITEM_FINAL_SUCCESS
                continue

            if branch_result.status != BRANCH_STATUS_FAULT:
                raise RuntimeError(f"unknown BranchResult status {branch_result.status!r}")

            fault_kind = classify_fault(branch_result.diagnostics)
            if metrics is not None:
                metrics.fault_count += 1
            retry = decide_retry(attempt, fault_kind, max_retries=config.max_retries)
            attempts[work_item.work_id] = dataclasses.replace(
                attempt,
                attempt_count=retry.attempt_count,
                state=retry.next_state,
                worker_generation=retry.worker_generation,
            )
            if retry.should_retry:
                if metrics is not None:
                    metrics.retry_count += 1
                retry_next_round.append(by_work_id[work_item.work_id])
                continue

            final_results[work_item.work_id] = branch_result
            final_states[work_item.work_id] = WORK_ITEM_FINAL_FAULT

        pending = retry_next_round

    if metrics is not None:
        metrics.final_success_count += sum(1 for state in final_states.values() if state == WORK_ITEM_FINAL_SUCCESS)
        metrics.final_fault_count += sum(1 for state in final_states.values() if state == WORK_ITEM_FINAL_FAULT)
    return [(item, final_results[item.work_id], final_states[item.work_id]) for item in work_items]


def build_search_strategy(
    pool: BranchWorkerPool,
    *,
    config: SearchCoordinatorConfig,
    combat_start_deck_multiset: dict[str, int],
    lease_registry: LeaseRegistry,
    main_state_provider: Optional[Callable[[], MainLoopState]] = None,
    metrics: Optional[SearchCoordinatorMetrics] = None,
) -> SearchStrategy:
    """Build a Phase-3 ``SearchStrategy`` over the real Phase 4-7 components.

    ``lease_registry`` is supplied by the caller and intentionally retained across calls
    to the returned strategy, so Phase-5 State-Holding Worker Leases can be reused.
    ``main_state_provider`` is optional for backward compatibility. When supplied, it is
    called at Commit time to verify that Main's live state identity, Held Stable Snapshot,
    and Replay Prefix still match the ``DecisionContext`` originally passed into this
    strategy call. When omitted, the coordinator preserves the prior always-pass
    invariant behavior.

    Scope limitations documented rather than hidden:
      * Branch faults are retried through the normal BranchWorkerPool dispatch path until
        ``decide_retry()`` returns a final state. ``FORCE_RESTART`` invalidates leases via
        the existing fault path, but this coordinator cannot kill/respawn OS processes
        because BranchWorkerPool does not expose process restart plumbing.
      * ``build_commit_decision`` can return Phase-7 ``MainCombatFaultOutcome`` only if
        that invariant fails. Because ``SearchStrategy`` can only return
        ``SearchSuccess`` or ``SearchEvaluationFailure``, this integration raises
        ``MainInvariantViolatedError`` carrying the fault outcome instead of coercing a
        Main fault into a search evaluation failure. Future Main-loop integration should
        catch that exception type and route ``.outcome`` to Main's own fault path.
    """

    def _strategy(decision_context: DecisionContext):
        original_decision_context = decision_context
        invariant_check: Optional[MainInvariantCheckResult] = None

        def _verify_main_invariant() -> bool:
            nonlocal invariant_check
            if main_state_provider is None:
                invariant_check = MainInvariantCheckResult(ok=True)
                return True
            invariant_check = _check_main_invariant(original_decision_context, main_state_provider)
            return invariant_check.ok

        # The former CardDrawnEntry source_live_state_inconsistency sanitizer was removed
        # after fresh Emulator captures were confirmed naturally restore-eligible. The
        # coordinator now passes Main's root snapshot through unchanged.
        pipeline = build_candidate_pipeline_result(decision_context, width=config.width)
        if isinstance(pipeline, NoViableCandidates):
            return SearchEvaluationFailure(detail=pipeline.detail)

        assert isinstance(pipeline, CandidatePipelineSuccess)
        candidates = _candidate_batch(pipeline)
        if metrics is not None:
            metrics.pipeline_candidate_count = len(pipeline.ranked_candidates)
            metrics.pruned_candidate_count = len(candidates)
        hypothesis_involved = _requires_hypothesis(decision_context, candidates)
        if metrics is not None:
            metrics.hypothesis_involved = hypothesis_involved
        public_multiset_coverage: Optional[CoverageAssessment] = None
        if hypothesis_involved:
            work_items, public_multiset_coverage = _hypothesis_work_items_with_coverage(
                decision_context,
                candidates,
                config=config,
                combat_start_deck_multiset=combat_start_deck_multiset,
            )
        else:
            work_items = _plain_work_items(decision_context, candidates)
        if metrics is not None:
            metrics.work_item_count = len(work_items)
            metrics.hypothesis_count = len({item.search_hypothesis_id for item in work_items if item.search_hypothesis_id is not None})

        final_results = _dispatch_work_items_until_final(
            work_items,
            lease_registry,
            pool=pool,
            config=config,
            metrics=metrics,
        )
        entries = [
            to_decision_log_entry(work_item, branch_result, work_item_state=work_item_state)
            for work_item, branch_result, work_item_state in final_results
        ]
        if public_multiset_coverage is not None:
            coverage_payload = _coverage_diagnostics(public_multiset_coverage)
            entries = [
                dataclasses.replace(
                    entry,
                    diagnostics={**entry.diagnostics, "public_multiset_coverage": coverage_payload},
                )
                for entry in entries
            ]
        aggregation = (
            aggregate_hypothesis_results(entries, min_coverage_fraction=config.min_coverage_fraction)
            if hypothesis_involved
            else aggregate_plain_results(entries)
        )
        if metrics is not None:
            metrics.aggregation_mode = str(aggregation.diagnostics.get("mode"))
            metrics.best_aggregate_score = (
                None if aggregation.best_action is None else float(aggregation.best_action.aggregate_score)
            )
            metrics.aggregation_diagnostics = dict(aggregation.diagnostics)
        decision = build_commit_decision(
            aggregation,
            hypothesis_involved=hypothesis_involved,
            verify_main_invariant=_verify_main_invariant,
        )
        if isinstance(decision, MainCombatFaultOutcome):
            check = invariant_check or MainInvariantCheckResult(ok=False, mismatches=("unknown",))
            decision = dataclasses.replace(
                decision,
                detail=f"{decision.detail}; mismatches={', '.join(check.mismatches) or 'unknown'}",
                diagnostics={**decision.diagnostics, "main_invariant": check.diagnostics},
            )
            raise MainInvariantViolatedError(decision, check)
        return decision

    return _strategy
