"""Search Coordinator integration - Combat execution infrastructure Phase 8.

This module is intentionally thin assembly over Phases 2-7. It builds a Phase-3
``SearchStrategy`` by composing the existing Candidate Pipeline, RNG Hypothesis helpers,
Branch Worker Pool, and Commit aggregation. Faulted branch work is retried before final
Commit aggregation according to Phase 7's retry taxonomy; deeper tree search is still
outside this assembly layer.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from combat_state_snapshot import CombatStateSnapshot, restore_input_eligibility, validate_snapshot_references
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
from search.main_loop import SearchEvaluationFailure, SearchStrategy
from search.rng_hypothesis import (
    build_grid,
    compute_public_multiset,
    consume_check,
    generate_belief_hypotheses,
    with_search_hypothesis,
)


_COMBAT_HISTORY_ENTRY_INDEX_RE = re.compile(r"^CombatHistory\.Entries\[(\d+)\]")
_KNOWN_BENIGN_DANGLING_ENTRY_TYPE = "CardDrawnEntry"
_KNOWN_BENIGN_DANGLING_CAUSE = "source_live_state_inconsistency"


@dataclass(frozen=True)
class SearchCoordinatorConfig:
    """Tunables consumed by the composed Phase 4-7 search pieces."""

    width: int = 8
    hypothesis_count: int = 4
    min_coverage_fraction: float = 0.5
    worker_count: int = 2
    max_retries: int = 1
    request_timeout_s: float = 120.0


def _candidate_batch(pipeline: CandidatePipelineSuccess) -> list[PipelineCandidateRef]:
    return [pipeline.continuation_candidate, *pipeline.sub_branch_candidates]


def _strip_known_benign_dangling_entries(snapshot: CombatStateSnapshot) -> CombatStateSnapshot:
    """Remove only the documented initial-hand ``CardDrawnEntry`` restore blocker.

    Raises ``ValueError`` if the snapshot contains any other dangling-reference profile,
    or if the resulting snapshot still fails ``restore_input_eligibility()``. This keeps
    the workaround narrow and honest: it never changes the Emulator's own completeness
    verdict or unsupported-field metadata.
    """

    report = validate_snapshot_references(snapshot)
    entry_indices_to_strip: set[int] = set()
    unexpected = []

    for dangling in report.dangling_references:
        if (
            dangling.entry_type == _KNOWN_BENIGN_DANGLING_ENTRY_TYPE
            and dangling.cause == _KNOWN_BENIGN_DANGLING_CAUSE
        ):
            match = _COMBAT_HISTORY_ENTRY_INDEX_RE.match(dangling.field_path)
            if match is None:
                unexpected.append(dangling)
                continue
            entry_indices_to_strip.add(int(match.group(1)))
        else:
            unexpected.append(dangling)

    if unexpected:
        details = ", ".join(
            f"{dangling.field_path} entry_type={dangling.entry_type!r} cause={dangling.cause!r}"
            for dangling in unexpected
        )
        raise ValueError(f"unexpected dangling snapshot reference(s): {details}")

    sanitized = dataclasses.replace(
        snapshot,
        CombatHistory=dataclasses.replace(
            snapshot.CombatHistory,
            Entries=[
                entry
                for index, entry in enumerate(snapshot.CombatHistory.Entries)
                if index not in entry_indices_to_strip
            ],
        ),
    )
    eligible, reasons = restore_input_eligibility(sanitized)
    if not eligible:
        raise ValueError(f"snapshot is still not restore-eligible after known-benign cleanup: {reasons}")
    return sanitized


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
    public_multiset = compute_public_multiset(
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
    return work_items


def _dispatch_work_items_until_final(
    work_items: list[WorkItem],
    lease_registry: LeaseRegistry,
    *,
    pool: BranchWorkerPool,
    config: SearchCoordinatorConfig,
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
            retry = decide_retry(attempt, fault_kind, max_retries=config.max_retries)
            attempts[work_item.work_id] = dataclasses.replace(
                attempt,
                attempt_count=retry.attempt_count,
                state=retry.next_state,
                worker_generation=retry.worker_generation,
            )
            if retry.should_retry:
                retry_next_round.append(by_work_id[work_item.work_id])
                continue

            final_results[work_item.work_id] = branch_result
            final_states[work_item.work_id] = WORK_ITEM_FINAL_FAULT

        pending = retry_next_round

    return [(item, final_results[item.work_id], final_states[item.work_id]) for item in work_items]


def build_search_strategy(
    pool: BranchWorkerPool,
    *,
    config: SearchCoordinatorConfig,
    combat_start_deck_multiset: dict[str, int],
    lease_registry: LeaseRegistry,
) -> SearchStrategy:
    """Build a Phase-3 ``SearchStrategy`` over the real Phase 4-7 components.

    ``lease_registry`` is supplied by the caller and intentionally retained across calls
    to the returned strategy, so Phase-5 State-Holding Worker Leases can be reused.

    Scope limitations documented rather than hidden:
      * Branch faults are retried through the normal BranchWorkerPool dispatch path until
        ``decide_retry()`` returns a final state. ``FORCE_RESTART`` invalidates leases via
        the existing fault path, but this coordinator cannot kill/respawn OS processes
        because BranchWorkerPool does not expose process restart plumbing.
      * ``verify_main_invariant`` is ``lambda: True``. A strategy call dispatches one
        synchronous worker batch and Main does not interleave live steps while waiting,
        so this phase has no real changing Main state to compare. If a future coordinator
        overlaps Main activity with search, that caller must provide a real invariant.
      * ``build_commit_decision`` can return Phase-7 ``MainCombatFaultOutcome`` only if
        that invariant fails. With the always-true invariant, the path is unreachable; if
        it appears anyway, this integration raises instead of coercing it into a normal
        search evaluation failure.
    """

    def _strategy(decision_context: DecisionContext):
        # Interim Emulator-side data-quality workaround: fresh scenario snapshots retain
        # stale initial-hand CardDrawnEntry references classified as
        # source_live_state_inconsistency after the authoritative hand setup overwrites
        # those card instance IDs. Strip only those known-benign history entries so
        # Worker Restore can proceed. This is not a general "make restorable" shim; the
        # proper fix belongs in the Emulator capture/setup path.
        decision_context = dataclasses.replace(
            decision_context,
            root_snapshot=_strip_known_benign_dangling_entries(decision_context.root_snapshot),
        )
        pipeline = build_candidate_pipeline_result(decision_context, width=config.width)
        if isinstance(pipeline, NoViableCandidates):
            return SearchEvaluationFailure(detail=pipeline.detail)

        assert isinstance(pipeline, CandidatePipelineSuccess)
        candidates = _candidate_batch(pipeline)
        hypothesis_involved = _requires_hypothesis(decision_context, candidates)
        if hypothesis_involved:
            work_items = _hypothesis_work_items(
                decision_context,
                candidates,
                config=config,
                combat_start_deck_multiset=combat_start_deck_multiset,
            )
        else:
            work_items = _plain_work_items(decision_context, candidates)

        final_results = _dispatch_work_items_until_final(
            work_items,
            lease_registry,
            pool=pool,
            config=config,
        )
        entries = [
            to_decision_log_entry(work_item, branch_result, work_item_state=work_item_state)
            for work_item, branch_result, work_item_state in final_results
        ]
        aggregation = (
            aggregate_hypothesis_results(entries, min_coverage_fraction=config.min_coverage_fraction)
            if hypothesis_involved
            else aggregate_plain_results(entries)
        )
        decision = build_commit_decision(
            aggregation,
            hypothesis_involved=hypothesis_involved,
            verify_main_invariant=lambda: True,
        )
        if isinstance(decision, MainCombatFaultOutcome):
            raise RuntimeError(f"unreachable Main invariant fault from synchronous SearchCoordinator: {decision}")
        return decision

    return _strategy
