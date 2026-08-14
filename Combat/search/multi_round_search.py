"""Multi-round PREV_CHILD beam search for RNG-independent Combat chains.

This module deliberately sits beside ``search_coordinator.py`` instead of changing its
single-round ``build_search_strategy()`` entry point. The existing coordinator remains
the reference implementation for one Root Action Evaluation Segment; this module reuses
its importable helpers for candidate batching, passthrough-vs-hypothesis classification,
WorkItem construction, retry dispatch, and Main invariant checking.

Scoring rule: beam scores are the SUM of each committed round's aggregate score. The
candidate pipeline's score is local to the immediate decision boundary, so summing keeps
longer plans competitive only when they keep producing good local choices. Ties are
broken deterministically by shorter plan length and then stable root-action keys.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Callable, Optional

from combat_state_snapshot import CombatStateSnapshot
from search.branch_worker_pool import BRANCH_STATUS_SUCCESS, BranchResult, BranchWorkerPool, LeaseRegistry
from search.candidate_pipeline import (
    CandidatePipelineSuccess,
    NoViableCandidates,
    PipelineCandidateRef,
    build_candidate_pipeline_result,
)
from search.decision_context import (
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    BOUNDARY_TERMINAL,
    DecisionContext,
    DecisionSignature,
    ReplayPrefixEntry,
)
from search.fault_taxonomy import (
    MainCombatFaultOutcome,
    SRC_MAIN_INVARIANT,
    aggregate_hypothesis_results,
    aggregate_plain_results,
    build_commit_decision,
    to_decision_log_entry,
)
from search.main_loop import MainLoopState, PlannedStep, SearchEvaluationFailure, SearchStrategy, SearchSuccess
from search.search_coordinator import (
    MainInvariantCheckResult,
    MainInvariantViolatedError,
    SearchCoordinatorConfig,
    _candidate_batch,
    _check_main_invariant,
    _dispatch_work_items_until_final,
    _hypothesis_work_items,
    _plain_work_items,
    _requires_hypothesis,
)


SnapshotStateLoader = Callable[[CombatStateSnapshot], object]


@dataclass(frozen=True)
class BeamSearchConfig:
    """Tunables for bounded PREV_CHILD beam search."""

    coordinator: SearchCoordinatorConfig = field(default_factory=SearchCoordinatorConfig)
    beam_width: int = 2
    max_rounds: int = 2

    def __post_init__(self) -> None:
        if self.beam_width <= 0:
            raise ValueError("beam_width must be positive")
        if self.max_rounds <= 0:
            raise ValueError("max_rounds must be positive")


@dataclass(frozen=True)
class _BeamEntry:
    context: DecisionContext
    cumulative_score: float
    sort_key: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CompletedBeam:
    plan_path: tuple[ReplayPrefixEntry, ...]
    cumulative_score: float
    sort_key: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class _ExpansionCandidate:
    parent: _BeamEntry
    branch_result: BranchResult
    entry: ReplayPrefixEntry
    round_score: float
    root_action_key: str

    @property
    def plan_path(self) -> tuple[ReplayPrefixEntry, ...]:
        return (*self.parent.context.plan_path, self.entry)

    @property
    def cumulative_score(self) -> float:
        return self.parent.cumulative_score + self.round_score

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (*self.parent.sort_key, self.root_action_key)


def _default_snapshot_state_loader(snapshot: CombatStateSnapshot) -> object:
    from live_combat_session import LiveCombatSession

    return LiveCombatSession().restore_snapshot(snapshot)


def _planned_sequence_from_path(path: tuple[ReplayPrefixEntry, ...]) -> list[PlannedStep]:
    return [
        PlannedStep(
            semantic_action=entry.semantic_action,
            target_index=entry.target_index,
            target_enemy_index=entry.target_enemy_index,
            expected_signature=entry.expected_signature,
        )
        for entry in path
    ]


def _completed_from_expansion(candidate: _ExpansionCandidate, *, reason: str) -> _CompletedBeam:
    return _CompletedBeam(
        plan_path=candidate.plan_path,
        cumulative_score=candidate.cumulative_score,
        sort_key=candidate.sort_key,
        reason=reason,
    )


def _rank_completed(item: _CompletedBeam) -> tuple[float, int, tuple[str, ...]]:
    return (-item.cumulative_score, len(item.plan_path), item.sort_key)


def _rank_expansion(item: _ExpansionCandidate) -> tuple[float, int, tuple[str, ...]]:
    return (-item.cumulative_score, len(item.plan_path), item.sort_key)


def _entry_from_branch(candidate: PipelineCandidateRef, signature: Optional[DecisionSignature]) -> ReplayPrefixEntry:
    if signature is None:
        raise ValueError("cannot build a ReplayPrefixEntry without a result signature")
    return ReplayPrefixEntry(
        semantic_action=candidate.semantic_action,
        expected_signature=signature,
        target_index=candidate.target_index,
        target_enemy_index=candidate.target_enemy_index,
    )


def _completed_hypothesis_round(
    entry: _BeamEntry,
    candidates: list[PipelineCandidateRef],
    *,
    pool: BranchWorkerPool,
    config: BeamSearchConfig,
    lease_registry: LeaseRegistry,
) -> Optional[_CompletedBeam]:
    work_items = _hypothesis_work_items(
        entry.context,
        candidates,
        config=config.coordinator,
    )
    final_results = _dispatch_work_items_until_final(
        work_items,
        lease_registry,
        pool=pool,
        config=config.coordinator,
    )
    entries = [
        to_decision_log_entry(work_item, branch_result, work_item_state=work_item_state)
        for work_item, branch_result, work_item_state in final_results
    ]
    aggregation = aggregate_hypothesis_results(
        entries,
        min_coverage_fraction=config.coordinator.min_coverage_fraction,
    )
    decision = build_commit_decision(
        aggregation,
        hypothesis_involved=True,
        verify_main_invariant=lambda: True,
    )
    if isinstance(decision, SearchEvaluationFailure):
        return _CompletedBeam(
            plan_path=tuple(entry.context.plan_path),
            cumulative_score=entry.cumulative_score,
            sort_key=entry.sort_key,
            reason="hypothesis_zero_viable",
        )
    if isinstance(decision, MainCombatFaultOutcome):
        raise RuntimeError("unexpected Main invariant outcome from local hypothesis commit")
    assert isinstance(decision, SearchSuccess)
    best = aggregation.best_action
    assert best is not None
    planned_step = decision.planned_sequence[0]
    transition = ReplayPrefixEntry(
        semantic_action=planned_step.semantic_action,
        expected_signature=planned_step.expected_signature,
        target_index=planned_step.target_index,
        target_enemy_index=planned_step.target_enemy_index,
    )
    return _CompletedBeam(
        plan_path=(*entry.context.plan_path, transition),
        cumulative_score=entry.cumulative_score + best.aggregate_score,
        sort_key=(*entry.sort_key, best.root_action_key),
        reason="hypothesis_required",
    )


def _plain_round_expansions(
    entry: _BeamEntry,
    candidates: list[PipelineCandidateRef],
    *,
    pool: BranchWorkerPool,
    config: BeamSearchConfig,
    lease_registry: LeaseRegistry,
) -> list[_ExpansionCandidate]:
    work_items = _plain_work_items(entry.context, candidates)
    final_results = _dispatch_work_items_until_final(
        work_items,
        lease_registry,
        pool=pool,
        config=config.coordinator,
    )
    branch_by_work_id = {work_item.work_id: branch_result for work_item, branch_result, _ in final_results}
    candidate_by_work_id = {work_item.work_id: work_item.candidate for work_item, _, _ in final_results}
    entries = [
        to_decision_log_entry(work_item, branch_result, work_item_state=work_item_state)
        for work_item, branch_result, work_item_state in final_results
    ]
    aggregation = aggregate_plain_results(entries)
    expansions: list[_ExpansionCandidate] = []
    for action in aggregation.viable_actions:
        log_entry = action.representative_entry
        branch = branch_by_work_id[log_entry.work_id]
        if branch.status != BRANCH_STATUS_SUCCESS:
            continue
        pipeline_candidate = candidate_by_work_id[log_entry.work_id]
        expansions.append(
            _ExpansionCandidate(
                parent=entry,
                branch_result=branch,
                entry=_entry_from_branch(pipeline_candidate, branch.result_signature),
                round_score=action.aggregate_score,
                root_action_key=action.root_action_key,
            )
        )
    return expansions


def build_beam_search_strategy(
    pool: BranchWorkerPool,
    *,
    config: BeamSearchConfig,
    lease_registry: LeaseRegistry,
    main_state_provider: Optional[Callable[[], MainLoopState]] = None,
    snapshot_state_loader: Optional[SnapshotStateLoader] = None,
) -> SearchStrategy:
    """Build a drop-in Phase-3 ``SearchStrategy`` using PREV_CHILD beam continuation.

    Only passthrough/plain rounds are expanded via ``DecisionContext.from_prev_child``.
    If an active beam reaches a hypothesis-required comparison batch, that beam performs
    one single-round hypothesis commit and then terminates.
    """

    load_state = snapshot_state_loader or _default_snapshot_state_loader

    def _strategy(decision_context: DecisionContext):
        original_decision_context = decision_context
        invariant_check: Optional[MainInvariantCheckResult] = None

        active = [_BeamEntry(context=decision_context, cumulative_score=0.0)]
        completed: list[_CompletedBeam] = []
        produced_any_viable = False

        for round_index in range(config.max_rounds):
            round_expansions: list[_ExpansionCandidate] = []

            for beam in active:
                pipeline = build_candidate_pipeline_result(beam.context, width=config.coordinator.width)
                if isinstance(pipeline, NoViableCandidates):
                    if round_index == 0 and not produced_any_viable:
                        continue
                    completed.append(
                        _CompletedBeam(
                            plan_path=tuple(beam.context.plan_path),
                            cumulative_score=beam.cumulative_score,
                            sort_key=beam.sort_key,
                            reason="no_viable_candidates",
                        )
                    )
                    continue

                assert isinstance(pipeline, CandidatePipelineSuccess)
                candidates = _candidate_batch(pipeline)
                if _requires_hypothesis(beam.context, candidates):
                    completed_hypothesis = _completed_hypothesis_round(
                        beam,
                        candidates,
                        pool=pool,
                        config=config,
                        lease_registry=lease_registry,
                    )
                    if completed_hypothesis is not None:
                        produced_any_viable = True
                        completed.append(completed_hypothesis)
                    continue

                expansions = _plain_round_expansions(
                    beam,
                    candidates,
                    pool=pool,
                    config=config,
                    lease_registry=lease_registry,
                )
                if expansions:
                    produced_any_viable = True
                    round_expansions.extend(expansions)
                elif round_index > 0:
                    completed.append(
                        _CompletedBeam(
                            plan_path=tuple(beam.context.plan_path),
                            cumulative_score=beam.cumulative_score,
                            sort_key=beam.sort_key,
                            reason="zero_successful_plain_results",
                        )
                    )

            if not round_expansions:
                break

            survivors = sorted(round_expansions, key=_rank_expansion)[: config.beam_width]
            next_active: list[_BeamEntry] = []
            for candidate in survivors:
                signature = candidate.branch_result.result_signature
                if signature is None:
                    raise ValueError("successful expansion did not carry result_signature")
                if signature.boundary == BOUNDARY_TERMINAL:
                    completed.append(_completed_from_expansion(candidate, reason="terminal_boundary"))
                    continue
                if signature.boundary == BOUNDARY_PENDING:
                    if round_index + 1 >= config.max_rounds:
                        completed.append(_completed_from_expansion(candidate, reason="max_rounds"))
                        continue
                    pending_context = candidate.branch_result.pending_decision_context
                    if pending_context is None:
                        raise ValueError("Pending expansion did not carry pending_decision_context")
                    next_active.append(
                        _BeamEntry(
                            context=dataclasses.replace(pending_context, plan_path=list(candidate.plan_path)),
                            cumulative_score=candidate.cumulative_score,
                            sort_key=candidate.sort_key,
                        )
                    )
                    continue
                if signature.boundary != BOUNDARY_STABLE or candidate.branch_result.child_snapshot is None:
                    completed.append(_completed_from_expansion(candidate, reason="non_stable_boundary"))
                    continue
                if round_index + 1 >= config.max_rounds:
                    completed.append(_completed_from_expansion(candidate, reason="max_rounds"))
                    continue

                state = load_state(candidate.branch_result.child_snapshot)
                next_context = DecisionContext.from_prev_child(
                    root_snapshot=candidate.branch_result.child_snapshot,
                    current_decision_result=state,
                    current_context_signature=signature,
                    parent_plan_path=list(candidate.plan_path),
                )
                next_active.append(
                    _BeamEntry(
                        context=next_context,
                        cumulative_score=candidate.cumulative_score,
                        sort_key=candidate.sort_key,
                    )
                )

            active = next_active
            if not active:
                break

        if not produced_any_viable:
            return SearchEvaluationFailure(detail="search evaluation produced zero viable Root Actions")

        if active:
            completed.extend(
                _CompletedBeam(
                    plan_path=tuple(beam.context.plan_path),
                    cumulative_score=beam.cumulative_score,
                    sort_key=beam.sort_key,
                    reason="exhausted_active",
                )
                for beam in active
                if beam.context.plan_path
            )

        if not completed:
            return SearchEvaluationFailure(detail="search evaluation produced zero completed beam entries")

        if main_state_provider is not None:
            invariant_check = _check_main_invariant(original_decision_context, main_state_provider)
            if not invariant_check.ok:
                outcome = MainCombatFaultOutcome(
                    fault_source=SRC_MAIN_INVARIANT,
                    detail=(
                        "Main state identity changed during multi-round search aggregation; "
                        f"mismatches={', '.join(invariant_check.mismatches) or 'unknown'}"
                    ),
                    diagnostics={"main_invariant": invariant_check.diagnostics},
                )
                raise MainInvariantViolatedError(outcome, invariant_check)

        best = sorted(completed, key=_rank_completed)[0]
        return SearchSuccess(planned_sequence=_planned_sequence_from_path(best.plan_path))

    return _strategy
