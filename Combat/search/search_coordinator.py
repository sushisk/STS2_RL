"""Search Coordinator integration - Combat execution infrastructure Phase 8.

This module is intentionally thin assembly over Phases 2-7. It builds a Phase-3
``SearchStrategy`` by composing the existing Candidate Pipeline, RNG Hypothesis helpers,
Branch Worker Pool, and Commit aggregation. No retry/resubmit loop or deeper tree search
is implemented here; branch faults are converted to final decision-log entries for this
single evaluation round.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from combat_state_snapshot import CombatStateSnapshot, restore_input_eligibility, validate_snapshot_references
from search.branch_worker_pool import (
    WORK_KIND_CONTINUATION,
    WORK_KIND_SUB_BRANCH,
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
    to_decision_log_entry,
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
      * Branch faults are classified/logged as ``FinalFault`` entries immediately. Phase
        7's retry decision primitive is not driven here because this phase is final
        assembly only, not retry orchestration.
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

        branch_results = dispatch_work_items(work_items, lease_registry, worker_pool=pool)
        entries = [
            to_decision_log_entry(work_item, branch_result)
            for work_item, branch_result in zip(work_items, branch_results)
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
