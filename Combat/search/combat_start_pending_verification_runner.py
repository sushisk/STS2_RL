"""Start-of-Combat Pending verification runner.

Drives real TOOLBOX / CHOICES_PARADOX / GAMBLING_CHIP Start-of-Combat Pending
events through a shared BranchWorkerPool and LeaseRegistry. It verifies sibling
Bootstrap+Replay from a CombatStartReplayRoot, scenario-spec fault regeneration, and
worker/session cross-contamination invariants.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    WORK_KIND_CONTINUATION,
    WORK_KIND_SUB_BRANCH,
    BranchResult,
    BranchWorkerPool,
    LeaseRegistry,
    WorkItem,
    derive_context_id,
    dispatch_work_items,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    CombatStartReplayRoot,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
    boundary_of_battle_state,
)
from search.fault_taxonomy import BRANCH_FAULT_KINDS, classify_fault  # noqa: E402


class StopConditionError(RuntimeError):
    """Raised immediately when a required invariant is violated."""


def _base_spec(*, relic: str, deck: list[str], seed: int = 1) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "deck": deck,
        "player_powers": [],
        "relics": [relic],
        "potions": [],
        "seed": seed,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48 + (seed % 5)}],
    }


def _spec_for(relic: str, index: int) -> dict:
    seed = 1_200_000 + index
    if relic == "GAMBLING_CHIP":
        return _base_spec(
            relic=relic,
            deck=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
            seed=seed,
        )
    return _base_spec(relic=relic, deck=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], seed=seed)


def _semantic_action_for(action: dict) -> SemanticAction:
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


def _signature_for_first_choice(state) -> DecisionSignature:
    action = state._cached_legal_actions[0]  # noqa: SLF001
    return DecisionSignature.from_battle_state(state, semantic_action=_semantic_action_for(action), resolved_action=action)


def _context_and_pipeline(spec: dict) -> tuple[DecisionContext, CandidatePipelineSuccess]:
    session = LiveCombatSession()
    state = session.start_combat(spec)
    if boundary_of_battle_state(state) != BOUNDARY_PENDING:
        raise StopConditionError(f"scenario did not start at Pending: relics={spec.get('relics')} state={state.engine_state}")
    context = DecisionContext.from_combat_start_pending(
        CombatStartReplayRoot(spec),
        state,
        _signature_for_first_choice(state),
    )
    pipeline = build_candidate_pipeline_result(context, width=12)
    if not isinstance(pipeline, CandidatePipelineSuccess):
        raise StopConditionError(f"candidate pipeline failed for Start-of-Combat Pending: {pipeline!r}")
    return context, pipeline


def _candidate_batch(pipeline: CandidatePipelineSuccess):
    return [pipeline.continuation_candidate, *pipeline.sub_branch_candidates]


def _branch_candidates_for_relic(relic: str, pipeline: CandidatePipelineSuccess):
    candidates = _candidate_batch(pipeline)
    if relic in {"TOOLBOX", "CHOICES_PARADOX"}:
        distinct = []
        seen = set()
        for candidate in candidates:
            if candidate.semantic_action.action_type != "choice_card":
                continue
            semantic_key = candidate.semantic_action.semantic_key
            if semantic_key in seen:
                continue
            seen.add(semantic_key)
            distinct.append(candidate)
        if len(distinct) < 2:
            raise StopConditionError(f"{relic} exposed fewer than two distinct choice_card candidates: {candidates!r}")
        return distinct[:2]
    if relic == "GAMBLING_CHIP":
        card = next((c for c in candidates if c.semantic_action.action_type == "choice_card"), None)
        skip = next((c for c in candidates if c.semantic_action.action_type == "choice_skip"), None)
        if card is None or skip is None:
            raise StopConditionError(f"GAMBLING_CHIP did not expose both choice_card and choice_skip: {candidates!r}")
        return [card, skip]
    raise StopConditionError(f"unknown relic {relic!r}")


def _invalid_scenario_work_item(work_item: WorkItem) -> WorkItem:
    root = work_item.decision_context.root_snapshot
    assert isinstance(root, CombatStartReplayRoot)
    invalid_spec = dict(root.scenario_spec)
    invalid_spec["character_id"] = "NOT_A_REAL_CHARACTER"
    invalid_context = dataclasses.replace(
        work_item.decision_context,
        root_snapshot=CombatStartReplayRoot(invalid_spec),
    )
    return dataclasses.replace(work_item, decision_context=invalid_context)


@dataclass
class CombatStartPendingObserver:
    fault_count: int = 0
    fault_counts: Counter = field(default_factory=Counter)
    worker_last_generation: dict[int, int] = field(default_factory=dict)
    seen_combat_session_ids: set[str] = field(default_factory=set)
    holder_step_count: int = 0
    lease_issues: int = 0
    lease_releases: int = 0

    def note_worker_generation(self, worker_id: Optional[int], worker_generation: Optional[int]) -> None:
        if worker_id is None or worker_generation is None:
            return
        previous = self.worker_last_generation.get(worker_id)
        if previous is not None and worker_generation < previous:
            raise StopConditionError(
                f"worker_generation regressed for worker_id={worker_id}: {previous} -> {worker_generation}"
            )
        self.worker_last_generation[worker_id] = worker_generation

    def note_combat_session_id(self, combat_session_id: Optional[str]) -> None:
        if combat_session_id is None:
            return
        if combat_session_id in self.seen_combat_session_ids:
            raise StopConditionError(f"combat_session_id reused across events/results: {combat_session_id!r}")
        self.seen_combat_session_ids.add(combat_session_id)

    def note_fault(self, diagnostics: dict) -> None:
        self.fault_count += 1
        try:
            fault_kind = classify_fault(diagnostics)
        except ValueError as exc:
            raise StopConditionError(f"unclassified Branch Worker fault: {diagnostics!r}") from exc
        if fault_kind not in BRANCH_FAULT_KINDS:
            raise StopConditionError(f"unexpected non-branch fault kind: {fault_kind!r}")
        self.fault_counts[fault_kind] += 1


def _dispatch_and_observe(
    work_items: list[WorkItem],
    registry: LeaseRegistry,
    pool: BranchWorkerPool,
    observer: CombatStartPendingObserver,
    *,
    note_session_ids: bool = True,
) -> list[BranchResult]:
    results = dispatch_work_items(work_items, registry, worker_pool=pool)
    for result in results:
        observer.note_worker_generation(result.worker_id, result.worker_generation)
        if result.status == BRANCH_STATUS_FAULT:
            observer.note_fault(result.diagnostics)
            continue
        if result.status != BRANCH_STATUS_SUCCESS:
            raise StopConditionError(f"unknown BranchResult status {result.status!r}")
        if result.execution_mode == EXECUTION_MODE_HOLDER_STEP:
            observer.holder_step_count += 1
        if result.established_lease is not None:
            observer.lease_issues += 1
        if note_session_ids:
            observer.note_combat_session_id(result.result_signature.combat_session_id)
    return results


def _finish_pending_if_needed(
    result: BranchResult,
    registry: LeaseRegistry,
    pool: BranchWorkerPool,
    observer: CombatStartPendingObserver,
) -> BranchResult:
    if result.status != BRANCH_STATUS_SUCCESS or result.result_signature.boundary != BOUNDARY_PENDING:
        return result
    pending_context = result.pending_decision_context
    pending_pipeline = result.pending_pipeline_result
    if pending_context is None or not isinstance(pending_pipeline, CandidatePipelineSuccess):
        raise StopConditionError(f"Pending result lacks follow-up context/pipeline: {result!r}")
    context_id = derive_context_id(pending_context)
    if registry.get(context_id, pending_context.search_hypothesis_id) is None:
        return result

    candidates = _candidate_batch(pending_pipeline)
    follow_up = next(
        (
            c
            for c in candidates
            if c.semantic_action.action_type in {"choice_confirm", "choice_skip"}
        ),
        candidates[0],
    )
    item = WorkItem.from_candidate_ref(
        pending_context,
        follow_up,
        work_kind=WORK_KIND_CONTINUATION,
        context_id=context_id,
        work_id=f"{result.work_item.work_id}-follow-up",
    )
    [follow_result] = _dispatch_and_observe([item], registry, pool, observer, note_session_ids=False)
    if follow_result.status != BRANCH_STATUS_SUCCESS:
        return result
    if follow_result.execution_mode == EXECUTION_MODE_HOLDER_STEP and follow_result.result_signature.boundary == BOUNDARY_STABLE:
        observer.lease_releases += 1
    return follow_result


def _resolved_marker(result: BranchResult) -> tuple[str, Optional[str]]:
    sig = result.result_signature
    candidate_action = result.work_item.candidate.semantic_action
    return (
        sig.boundary,
        sig.resolved_semantic_key or candidate_action.semantic_key or candidate_action.action_type,
    )


def _run_one_event(
    *,
    event_index: int,
    relic: str,
    spec: dict,
    pool: BranchWorkerPool,
    registry: LeaseRegistry,
    observer: CombatStartPendingObserver,
    inject_fault: bool,
) -> dict[str, Any]:
    context, pipeline = _context_and_pipeline(spec)
    candidates = _branch_candidates_for_relic(relic, pipeline)
    context_id = derive_context_id(context)
    good_items = [
        WorkItem.from_candidate_ref(
            context,
            candidates[0],
            work_kind=WORK_KIND_CONTINUATION,
            context_id=context_id,
            work_id=f"{event_index}-{relic}-a",
        ),
        WorkItem.from_candidate_ref(
            context,
            candidates[1],
            work_kind=WORK_KIND_SUB_BRANCH,
            context_id=context_id,
            work_id=f"{event_index}-{relic}-b",
        ),
    ]

    branch_results = _dispatch_and_observe(good_items, registry, pool, observer)
    if any(result.status != BRANCH_STATUS_SUCCESS for result in branch_results):
        raise StopConditionError(f"{relic} sibling branch unexpectedly faulted: {[r.diagnostics for r in branch_results]}")
    if any(result.execution_mode != EXECUTION_MODE_BOOTSTRAP_STEP for result in branch_results):
        raise StopConditionError(f"{relic} genesis siblings did not use bootstrap_step: {branch_results!r}")
    if len({result.result_signature.combat_session_id for result in branch_results}) != 2:
        raise StopConditionError(f"{relic} sibling branches shared a combat_session_id")

    final_results = [_finish_pending_if_needed(result, registry, pool, observer) for result in branch_results]
    markers = {_resolved_marker(result) for result in final_results}
    if len(markers) < 2:
        raise StopConditionError(f"{relic} sibling branches did not reach distinct resolution markers: {markers!r}")
    if not all(result.result_signature.boundary in {BOUNDARY_STABLE, BOUNDARY_PENDING} for result in final_results):
        raise StopConditionError(f"{relic} produced unexpected boundary: {final_results!r}")

    fault_checked = False
    if inject_fault:
        bad_item = _invalid_scenario_work_item(good_items[0])
        [fault_result] = _dispatch_and_observe([bad_item], registry, pool, observer, note_session_ids=False)
        if fault_result.status != BRANCH_STATUS_FAULT:
            raise StopConditionError("invalid scenario_spec WorkItem unexpectedly succeeded")
        [retry_result] = _dispatch_and_observe([good_items[0]], registry, pool, observer)
        if retry_result.status != BRANCH_STATUS_SUCCESS:
            raise StopConditionError(f"retry after scenario_spec fault failed: {retry_result.diagnostics!r}")
        if retry_result.execution_mode != EXECUTION_MODE_BOOTSTRAP_STEP:
            raise StopConditionError(f"retry after fault used unexpected mode: {retry_result.execution_mode!r}")
        fault_checked = True

    return {
        "event_index": event_index,
        "relic": relic,
        "choice_types": [candidate.semantic_action.action_type for candidate in candidates],
        "resolved_markers": list(markers),
        "fault_checked": fault_checked,
    }


def run_combat_start_pending_verification(
    repeats_per_relic: int,
    *,
    worker_count: int = 3,
    fault_fraction: float = 0.1,
    checkpoint_interval: int = 25,
) -> dict[str, Any]:
    if repeats_per_relic < 30:
        raise StopConditionError("repeats_per_relic must be at least 30")
    relics = ["TOOLBOX", "CHOICES_PARADOX", "GAMBLING_CHIP"]
    total_events = repeats_per_relic * len(relics)
    if total_events < 100:
        raise StopConditionError("total Start-of-Combat Pending event count must be at least 100")

    registry = LeaseRegistry()
    observer = CombatStartPendingObserver()
    rows: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    started = time.perf_counter()
    fault_period = int(round(1.0 / fault_fraction)) if fault_fraction > 0 else 0

    with BranchWorkerPool(worker_count=worker_count, request_timeout_s=120.0) as pool:
        event_index = 0
        for repeat in range(repeats_per_relic):
            for relic in relics:
                event_index += 1
                inject_fault = fault_period > 0 and event_index % fault_period == 0
                row = _run_one_event(
                    event_index=event_index,
                    relic=relic,
                    spec=_spec_for(relic, event_index + repeat * 10_000),
                    pool=pool,
                    registry=registry,
                    observer=observer,
                    inject_fault=inject_fault,
                )
                rows.append(row)
                if event_index % checkpoint_interval == 0 or event_index == total_events:
                    elapsed_s = time.perf_counter() - started
                    print(
                        "COMBAT_START_PENDING "
                        f"{event_index}/{total_events} faults={observer.fault_count} "
                        f"leases={observer.lease_issues} holder_steps={observer.holder_step_count} "
                        f"elapsed_s={elapsed_s:.1f}",
                        flush=True,
                    )
                    checkpoints.append(
                        {
                            "event_index": event_index,
                            "elapsed_s": elapsed_s,
                            "fault_count": observer.fault_count,
                            "lease_issues": observer.lease_issues,
                            "holder_step_count": observer.holder_step_count,
                        }
                    )

    elapsed_s = time.perf_counter() - started
    relic_counts = Counter(row["relic"] for row in rows)
    if any(count < 30 for count in relic_counts.values()):
        raise StopConditionError(f"per-relic count below 30: {dict(relic_counts)}")
    if observer.fault_count <= 0:
        raise StopConditionError("fault injection did not produce any real fault")

    summary = {
        "event_count": len(rows),
        "elapsed_s": elapsed_s,
        "events_per_s": len(rows) / elapsed_s if elapsed_s > 0 else None,
        "relic_counts": dict(relic_counts),
        "fault_count": observer.fault_count,
        "fault_counts": dict(observer.fault_counts),
        "unique_combat_session_ids": len(observer.seen_combat_session_ids),
        "worker_generation_non_decreasing": True,
        "lease_issues": observer.lease_issues,
        "lease_releases": observer.lease_releases,
        "holder_step_count": observer.holder_step_count,
        "gambling_chip_lease_observed": any(row["relic"] == "GAMBLING_CHIP" for row in rows) and observer.lease_issues > 0,
    }
    return {
        "config": {
            "repeats_per_relic": repeats_per_relic,
            "worker_count": worker_count,
            "fault_fraction": fault_fraction,
        },
        "summary": summary,
        "checkpoints": checkpoints,
        "rows": rows,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats-per-relic", type=int, default=34)
    parser.add_argument("--worker-count", type=int, default=3)
    parser.add_argument("--fault-fraction", type=float, default=0.1)
    parser.add_argument("--checkpoint-interval", type=int, default=25)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run_combat_start_pending_verification(
            args.repeats_per_relic,
            worker_count=args.worker_count,
            fault_fraction=args.fault_fraction,
            checkpoint_interval=args.checkpoint_interval,
        )
    except StopConditionError as exc:
        print(f"STOP_CONDITION {exc}", file=sys.stderr, flush=True)
        return 2

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text, encoding="utf-8")
    print("SUMMARY_JSON_START")
    print(json.dumps(report["summary"], indent=2, sort_keys=True, default=str))
    print("SUMMARY_JSON_END")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
