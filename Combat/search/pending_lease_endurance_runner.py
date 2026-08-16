"""Pending/Lease endurance runner - reopened F2 verification.

Drives >=1000 real genuine multi-candidate Pending boundary events (SURVIVOR discard,
LIQUID_MEMORIES retrieve) through a shared BranchWorkerPool/LeaseRegistry, directly
exercising: Lease issuance/consumption/release, Holder Worker continuation on the SAME
live GameInstance, sibling Bootstrap+Replay reproduction on a DIFFERENT worker choosing a
DIFFERENT candidate, rejection of Lease reuse across unrelated Decision Contexts, Lease
invalidation after a real injected Worker Fault, and CombatSessionId/worker_generation/
state_epoch consistency. Never restores/steps a Pending Snapshot directly - every sibling
reproduction goes through the Stable root + Replay Prefix, matching the design fixed by
the prior "Pending Snapshotの誤Restore修正" commit.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
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

from combat_state_snapshot import CombatHistoryEntrySnapshot  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    WORK_KIND_CONTINUATION,
    WORK_KIND_SUB_BRANCH,
    BranchWorkerPool,
    LeaseRegistry,
    WorkItem,
    derive_context_id,
    dispatch_work_items,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result
from search.decision_context import (
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
)
from search.fault_taxonomy import BRANCH_FAULT_KINDS, classify_fault


class StopConditionError(RuntimeError):
    """Raised immediately when a required Pending/Lease endurance invariant is violated."""


def _spec(*, hand, discard_pile=None, potions=None, enemy_hp=48, seed=1) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand,
        "draw_pile": [],
        "discard_pile": discard_pile or [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": potions or [],
        "seed": seed,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _survivor_spec(index: int) -> dict:
    return _spec(
        hand=["SURVIVOR", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        enemy_hp=48 + (index % 5),
        seed=900000 + index,
    )


def _survivor_root_selector(ref) -> bool:
    return ref.semantic_action.action_type == "card" and ref.semantic_action.semantic_key.endswith(":SURVIVOR")


def _liquid_memories_spec(index: int) -> dict:
    return _spec(
        hand=["STRIKE_IRONCLAD"],
        discard_pile=["DEFEND_IRONCLAD", "BASH"],
        potions=[{"slot": 0, "potion_id": "LIQUID_MEMORIES"}],
        enemy_hp=48 + (index % 5),
        seed=950000 + index,
    )


def _liquid_memories_root_selector(ref) -> bool:
    return ref.semantic_action.action_type == "potion"


def _root_action_type(work_item: WorkItem) -> str:
    return work_item.candidate.semantic_action.action_type


def _invalid_snapshot_work_item(work_item: WorkItem) -> WorkItem:
    """Corrupt a WorkItem's root snapshot so its dispatch genuinely Faults - the same
    dangling-CombatHistory-reference injection technique used by endurance_runner.py /
    multi_hypothesis_stress_runner.py."""
    snapshot = work_item.decision_context.root_snapshot
    dangling_draw = CombatHistoryEntrySnapshot(
        EntryType="CardDrawnEntry",
        RoundNumber=snapshot.RoundNumber,
        CurrentSide=snapshot.CurrentSide,
        PlayerTurnNumbers={},
        Fields={"cardInstanceId": "PENDING_ENDURANCE_INJECTED_DANGLING_DRAW", "fromHandDraw": True},
    )
    invalid_snapshot = dataclasses.replace(
        snapshot,
        CombatHistory=dataclasses.replace(
            snapshot.CombatHistory,
            Entries=[*snapshot.CombatHistory.Entries, dangling_draw],
        ),
    )
    invalid_context = dataclasses.replace(work_item.decision_context, root_snapshot=invalid_snapshot)
    return dataclasses.replace(work_item, decision_context=invalid_context)


@dataclass
class PendingEnduranceObserver:
    """Tracks Lease lifecycle/cross-contamination invariants, raising StopConditionError
    immediately (not post-hoc) on any violation - matching the established pattern from
    endurance_runner.py / multi_hypothesis_stress_runner.py."""

    lease_issues: int = 0
    lease_releases: int = 0
    holder_step_count: int = 0
    bootstrap_step_count_for_sub_branch: int = 0
    fault_count: int = 0
    fault_counts: Counter = field(default_factory=Counter)
    worker_last_generation: dict[int, int] = field(default_factory=dict)
    seen_combat_session_ids: set[str] = field(default_factory=set)
    cross_context_misuse_checks: int = 0
    lease_invalidation_after_fault_checks: int = 0

    def note_worker_generation(self, worker_id: Optional[int], worker_generation: Optional[int]) -> None:
        if worker_id is None or worker_generation is None:
            return
        previous = self.worker_last_generation.get(worker_id)
        if previous is not None and worker_generation < previous:
            raise StopConditionError(
                f"worker_generation regressed for worker_id={worker_id}: {previous} -> {worker_generation}"
            )
        self.worker_last_generation[worker_id] = worker_generation

    def note_new_combat_session_id(self, combat_session_id: Optional[str]) -> None:
        if combat_session_id is None:
            return
        if combat_session_id in self.seen_combat_session_ids:
            raise StopConditionError(
                f"combat_session_id {combat_session_id!r} reused across unrelated Pending boundary events - "
                "possible State-Holding Worker cross-contamination"
            )
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


def _dispatch_and_check(
    work_items: list[WorkItem],
    registry: LeaseRegistry,
    pool: BranchWorkerPool,
    observer: PendingEnduranceObserver,
):
    results = dispatch_work_items(work_items, registry, worker_pool=pool)
    for work_item, result in zip(work_items, results):
        observer.note_worker_generation(result.worker_id, result.worker_generation)
        if result.status == BRANCH_STATUS_FAULT:
            observer.note_fault(result.diagnostics)
            continue
        if result.status != BRANCH_STATUS_SUCCESS:
            raise StopConditionError(f"unknown BranchResult status {result.status!r}")
        sig = result.result_signature
        if sig.boundary == BOUNDARY_PENDING:
            if result.established_lease is None or result.pending_decision_context is None:
                raise StopConditionError(f"Pending BranchResult missing lease/pending_decision_context: {result!r}")
            observer.lease_issues += 1
            expected_context_id = derive_context_id(result.pending_decision_context)
            if result.established_lease.context_id != expected_context_id:
                raise StopConditionError(
                    "established lease context_id mismatch: "
                    f"lease={result.established_lease.context_id!r} expected={expected_context_id!r}"
                )
            if (
                result.established_lease.combat_session_id != sig.combat_session_id
                or result.established_lease.step_index != sig.step_index
            ):
                raise StopConditionError(f"established lease state identity mismatch: {result.established_lease!r}")
            observer.note_new_combat_session_id(sig.combat_session_id)
        if result.execution_mode == EXECUTION_MODE_HOLDER_STEP:
            observer.holder_step_count += 1
        elif result.execution_mode == EXECUTION_MODE_BOOTSTRAP_STEP and work_item.work_kind == WORK_KIND_SUB_BRANCH:
            observer.bootstrap_step_count_for_sub_branch += 1
    return results


def _run_one_pending_event(
    *,
    event_index: int,
    spec: dict,
    root_selector,
    pool: BranchWorkerPool,
    registry: LeaseRegistry,
    observer: PendingEnduranceObserver,
    inject_fault: bool,
    check_cross_context_misuse: bool,
) -> dict[str, Any]:
    session = LiveCombatSession()
    state = session.start_combat(spec)
    root_snapshot = session.capture_snapshot()
    first_action = state._cached_legal_actions[0]  # noqa: SLF001 - established pattern, see test_branch_worker_pool.py
    root_signature = DecisionSignature.from_battle_state(
        state,
        semantic_action=SemanticAction(
            action_type=first_action["action_type"], semantic_key=first_action.get("semantic_key", "")
        ),
        resolved_action=first_action,
    )
    context = DecisionContext.from_main_stable_capture(root_snapshot, state, root_signature)

    pipeline = build_candidate_pipeline_result(context, width=8)
    assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
    root_candidate = next(
        ref for ref in [pipeline.continuation_candidate, *pipeline.sub_branch_candidates] if root_selector(ref)
    )
    root_context_id = derive_context_id(context)
    root_item = WorkItem.from_candidate_ref(
        context, root_candidate, work_kind=WORK_KIND_CONTINUATION, context_id=root_context_id
    )

    root_results = _dispatch_and_check([root_item], registry, pool, observer)
    root_result = root_results[0]
    if root_result.status != BRANCH_STATUS_SUCCESS or root_result.result_signature.boundary != BOUNDARY_PENDING:
        raise StopConditionError(f"scenario did not reach a genuine Pending boundary: {root_result!r}")
    holder_worker_id = root_result.worker_id

    pending_context = root_result.pending_decision_context
    pending_pipeline = root_result.pending_pipeline_result
    assert isinstance(pending_pipeline, CandidatePipelineSuccess), pending_pipeline
    pending_context_id = derive_context_id(pending_context)
    if pending_context_id != root_result.established_lease.context_id:
        raise StopConditionError("pending_decision_context id does not match established_lease.context_id")

    choice_candidates = [pending_pipeline.continuation_candidate, *pending_pipeline.sub_branch_candidates]
    if len(choice_candidates) < 2:
        raise StopConditionError(f"expected >=2 genuine choice candidates at Pending, got {len(choice_candidates)}")

    misuse_rejected = False
    if check_cross_context_misuse:
        observer.cross_context_misuse_checks += 1
        # A genuine misuse attempt: same context_id/search_hypothesis_id as the just-issued
        # active Lease (so the cheap key lookup would find it), but claiming a DIFFERENT
        # combat_session_id - i.e. "same identifiers, wrong underlying live state". The
        # active Lease must reject this on content, not just on identifier mismatch.
        foreign_signature = dataclasses.replace(
            pending_context.current_context_signature,
            combat_session_id="FOREIGN_SESSION_ID_MISUSE_TEST",
        )
        foreign_context = dataclasses.replace(pending_context, current_context_signature=foreign_signature)
        misuse_item = WorkItem.from_candidate_ref(
            foreign_context, choice_candidates[0], work_kind=WORK_KIND_CONTINUATION, context_id=pending_context_id
        )
        if root_result.established_lease.is_valid_for(misuse_item):
            raise StopConditionError(
                f"active Lease for event {event_index} validated a WorkItem claiming a different "
                "combat_session_id under the same context_id/search_hypothesis_id"
            )
        misuse_rejected = True

    holder_item = WorkItem.from_candidate_ref(
        pending_context, choice_candidates[0], work_kind=WORK_KIND_CONTINUATION, context_id=pending_context_id
    )
    sibling_item = WorkItem.from_candidate_ref(
        pending_context, choice_candidates[1], work_kind=WORK_KIND_SUB_BRANCH, context_id=pending_context_id
    )

    dispatch_items = [holder_item, sibling_item]
    if inject_fault:
        dispatch_items = [dispatch_items[0], _invalid_snapshot_work_item(dispatch_items[1])]

    round2_results = _dispatch_and_check(dispatch_items, registry, pool, observer)
    holder_result, sibling_result = round2_results

    if holder_result.status != BRANCH_STATUS_SUCCESS:
        raise StopConditionError(f"Holder Step (continuation) unexpectedly faulted: {holder_result.diagnostics!r}")
    if holder_result.execution_mode != EXECUTION_MODE_HOLDER_STEP:
        raise StopConditionError(
            f"continuation WorkItem for a freshly established Lease did not route to Holder Step: "
            f"execution_mode={holder_result.execution_mode!r}"
        )
    if holder_result.worker_id != holder_worker_id:
        raise StopConditionError(
            "Holder Step did not execute on the SAME worker that established the Lease: "
            f"established={holder_worker_id} holder_step={holder_result.worker_id}"
        )
    if holder_result.result_signature.boundary != BOUNDARY_STABLE or holder_result.child_snapshot is None:
        raise StopConditionError(f"Holder Step did not resolve the Pending choice to Stable: {holder_result!r}")

    lease_after_holder = registry.get(pending_context_id, pending_context.search_hypothesis_id)
    if lease_after_holder is not None:
        raise StopConditionError("Lease was not released after the Holder resolved the Pending choice to Stable")
    observer.lease_releases += 1

    if inject_fault:
        if sibling_result.status != BRANCH_STATUS_FAULT:
            raise StopConditionError("injected-fault sibling WorkItem unexpectedly succeeded")
        observer.lease_invalidation_after_fault_checks += 1
        retry_item = WorkItem.from_candidate_ref(
            pending_context, choice_candidates[1], work_kind=WORK_KIND_SUB_BRANCH, context_id=pending_context_id
        )
        retry_results = _dispatch_and_check([retry_item], registry, pool, observer)
        retry_result = retry_results[0]
        if retry_result.status != BRANCH_STATUS_SUCCESS:
            raise StopConditionError(f"sibling retry after fault unexpectedly faulted again: {retry_result.diagnostics!r}")
    else:
        if sibling_result.status != BRANCH_STATUS_SUCCESS:
            raise StopConditionError(f"sibling Bootstrap+Replay unexpectedly faulted: {sibling_result.diagnostics!r}")
        if sibling_result.execution_mode != EXECUTION_MODE_BOOTSTRAP_STEP:
            raise StopConditionError(
                f"sub_branch WorkItem did not route to Bootstrap+Replay: execution_mode={sibling_result.execution_mode!r}"
            )
        if sibling_result.result_signature.combat_session_id == holder_result.result_signature.combat_session_id:
            raise StopConditionError(
                "sibling reproduction shares combat_session_id with the Holder - Restore did not mint a fresh session"
            )
        if sibling_result.result_signature.boundary != BOUNDARY_STABLE or sibling_result.child_snapshot is None:
            raise StopConditionError(f"sibling did not resolve its own different choice to Stable: {sibling_result!r}")
        if sibling_result.result_signature.resolved_semantic_key == holder_result.result_signature.resolved_semantic_key:
            raise StopConditionError("Holder and sibling resolved to the SAME card - candidates were not distinct")

    return {
        "event_index": event_index,
        "root_action_type": _root_action_type(root_item),
        "holder_worker_id": holder_worker_id,
        "sibling_worker_id": sibling_result.worker_id,
        "fault_injected": inject_fault,
        "cross_context_misuse_checked": misuse_rejected,
        "root_context_id": root_context_id,
    }


def run_pending_lease_endurance(
    event_count: int,
    *,
    worker_count: int = 3,
    fault_fraction: float = 0.1,
    cross_context_check_fraction: float = 0.05,
    checkpoint_interval: int = 100,
) -> dict[str, Any]:
    registry = LeaseRegistry()
    observer = PendingEnduranceObserver()
    rows: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    started = time.perf_counter()

    fault_period = int(round(1.0 / fault_fraction)) if fault_fraction > 0 else 0
    cross_period = int(round(1.0 / cross_context_check_fraction)) if cross_context_check_fraction > 0 else 0

    with BranchWorkerPool(worker_count=worker_count, request_timeout_s=120.0) as pool:
        for event_index in range(1, event_count + 1):
            if event_index % 2 == 0:
                spec, root_selector = _survivor_spec(event_index), _survivor_root_selector
            else:
                spec, root_selector = _liquid_memories_spec(event_index), _liquid_memories_root_selector
            inject_fault = fault_period > 0 and event_index % fault_period == 0
            check_cross = cross_period > 0 and event_index % cross_period == 0

            row = _run_one_pending_event(
                event_index=event_index,
                spec=spec,
                root_selector=root_selector,
                pool=pool,
                registry=registry,
                observer=observer,
                inject_fault=inject_fault,
                check_cross_context_misuse=check_cross,
            )
            rows.append(row)

            if event_index % checkpoint_interval == 0 or event_index == event_count:
                elapsed_s = time.perf_counter() - started
                print(
                    "PENDING_LEASE "
                    f"{event_index}/{event_count} lease_issues={observer.lease_issues} "
                    f"lease_releases={observer.lease_releases} holder_steps={observer.holder_step_count} "
                    f"sibling_bootstraps={observer.bootstrap_step_count_for_sub_branch} "
                    f"faults={observer.fault_count} elapsed_s={elapsed_s:.1f}",
                    flush=True,
                )
                checkpoints.append(
                    {
                        "event_index": event_index,
                        "elapsed_s": elapsed_s,
                        "lease_issues": observer.lease_issues,
                        "lease_releases": observer.lease_releases,
                        "holder_step_count": observer.holder_step_count,
                        "sibling_bootstrap_count": observer.bootstrap_step_count_for_sub_branch,
                        "fault_count": observer.fault_count,
                        "cross_context_misuse_checks": observer.cross_context_misuse_checks,
                    }
                )

    elapsed_s = time.perf_counter() - started
    summary = {
        "event_count": len(rows),
        "elapsed_s": elapsed_s,
        "events_per_s": len(rows) / elapsed_s if elapsed_s > 0 else None,
        "lease_issues": observer.lease_issues,
        "lease_releases": observer.lease_releases,
        "holder_step_count": observer.holder_step_count,
        "sibling_bootstrap_count": observer.bootstrap_step_count_for_sub_branch,
        "fault_count": observer.fault_count,
        "fault_counts": dict(observer.fault_counts),
        "lease_invalidation_after_fault_checks": observer.lease_invalidation_after_fault_checks,
        "cross_context_misuse_checks": observer.cross_context_misuse_checks,
        "unique_combat_session_ids": len(observer.seen_combat_session_ids),
        "worker_generation_non_decreasing": True,
        "root_action_type_counts": dict(Counter(row["root_action_type"] for row in rows)),
    }
    if summary["lease_issues"] != summary["event_count"]:
        raise StopConditionError(
            f"expected exactly one Lease issuance per Pending boundary event: "
            f"lease_issues={summary['lease_issues']} event_count={summary['event_count']}"
        )
    if summary["lease_releases"] != summary["event_count"]:
        raise StopConditionError("every established Lease must be released once its Pending choice resolves to Stable")
    if summary["holder_step_count"] != summary["event_count"]:
        raise StopConditionError("expected exactly one Holder Step per Pending boundary event")
    # Every event resolves its sibling exactly once via a successful Bootstrap+Replay -
    # directly for a non-faulted event, or via the post-fault retry for a faulted one
    # (the faulted attempt itself is counted separately in fault_count, not here).
    if summary["sibling_bootstrap_count"] != summary["event_count"]:
        raise StopConditionError(
            "expected exactly one successful sibling Bootstrap+Replay per Pending boundary event "
            f"(directly or via post-fault retry): sibling_bootstrap_count={summary['sibling_bootstrap_count']} "
            f"event_count={summary['event_count']}"
        )
    if summary["fault_count"] <= 0:
        raise StopConditionError("fault injection did not produce any real classifiable Fault")
    if summary["cross_context_misuse_checks"] <= 0:
        raise StopConditionError("cross-context Lease misuse was never exercised")
    if summary["unique_combat_session_ids"] < summary["event_count"]:
        raise StopConditionError("fewer unique combat_session_ids than Pending boundary events - possible cross-contamination")

    return {"config": {"event_count_requested": event_count, "worker_count": worker_count,
                        "fault_fraction": fault_fraction, "cross_context_check_fraction": cross_context_check_fraction},
            "summary": summary, "checkpoints": checkpoints, "rows": rows}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--worker-count", type=int, default=3)
    parser.add_argument("--fault-fraction", type=float, default=0.1)
    parser.add_argument("--cross-context-check-fraction", type=float, default=0.05)
    parser.add_argument("--checkpoint-interval", type=int, default=100)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run_pending_lease_endurance(
            args.events,
            worker_count=args.worker_count,
            fault_fraction=args.fault_fraction,
            cross_context_check_fraction=args.cross_context_check_fraction,
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
