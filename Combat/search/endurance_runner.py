"""Long-running shared BranchWorkerPool endurance runner.

The runner executes many independent real combats through one long-lived
BranchWorkerPool and one long-lived LeaseRegistry. A small fraction of search decisions
deliberately receives an invalid sub-branch candidate, which produces a real worker
fault through the normal dispatch/retry path while leaving a valid candidate available
for commit.
"""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import json
import statistics
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from ctypes import wintypes

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from combat_state_snapshot import restore_input_eligibility, validate_snapshot_references  # noqa: E402
from emulator_bridge import to_plain  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import BranchWorkerPool, Lease, LeaseRegistry, derive_context_id  # noqa: E402
from search.candidate_pipeline import CandidatePipelineSuccess  # noqa: E402
from search.decision_context import BOUNDARY_PENDING, BOUNDARY_STABLE, SemanticAction  # noqa: E402
from search.fault_taxonomy import BRANCH_FAULT_KINDS, classify_fault, worker_reuse_policy  # noqa: E402
from search.main_loop import (  # noqa: E402
    ROUTE_PENDING_STATIC,
    ROUTE_SEARCH,
    CombatAbortedByDecisionFailureOutcome,
    CombatTerminalOutcome,
    MainCombatFaultOutcome,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    run_until_terminal_or_fault,
)
from search.multi_round_search import BeamSearchConfig, build_beam_search_strategy  # noqa: E402
from search.search_coordinator import SearchCoordinatorConfig, SearchCoordinatorMetrics, build_search_strategy  # noqa: E402

import search.multi_round_search as multi_round_module  # noqa: E402
import search.search_coordinator as coordinator_module  # noqa: E402


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
_PSAPI_CONFIGURED = False


class StopConditionError(RuntimeError):
    """Raised when a required endurance stop condition is observed."""


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _working_set_bytes_for_pid(pid: int) -> Optional[int]:
    if sys.platform != "win32":
        return None
    _configure_psapi()
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, int(pid))
    if not handle:
        return None
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    finally:
        kernel32.CloseHandle(handle)


def _current_working_set_bytes() -> Optional[int]:
    if sys.platform != "win32":
        return None
    _configure_psapi()
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return int(counters.WorkingSetSize) if ok else None


def _configure_psapi() -> None:
    global _PSAPI_CONFIGURED
    if _PSAPI_CONFIGURED:
        return
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _PSAPI_CONFIGURED = True


def _spec(
    *,
    hand: list[str],
    draw_pile: list[str] | None = None,
    discard_pile: list[str] | None = None,
    relics: list[str] | None = None,
    potions: list[dict] | None = None,
    enemies: list[dict] | None = None,
    player_hp: int | None = None,
    player_max_hp: int | None = None,
    seed: int = 1,
) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": player_hp,
        "player_max_hp": player_max_hp,
        "hand": hand,
        "draw_pile": draw_pile if draw_pile is not None else [],
        "discard_pile": discard_pile if discard_pile is not None else [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": relics if relics is not None else [],
        "potions": potions if potions is not None else [],
        "seed": seed,
        "enemies": enemies if enemies is not None else [{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
    }


def _scenario_specs(count: int) -> list[dict]:
    templates = [
        {"hand": ["WHIRLWIND"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 1},
        {"hand": ["STRIKE_IRONCLAD"], "draw_pile": ["BASH"], "enemy_hp": 5},
        {"hand": ["BASH"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 8},
        {"hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], "draw_pile": ["BASH"], "enemy_hp": 5},
        {"hand": ["WHIRLWIND", "DEFEND_IRONCLAD"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 1, "relics": ["ANCHOR"]},
        {
            "hand": ["STRIKE_IRONCLAD", "BASH"],
            "draw_pile": ["DEFEND_IRONCLAD"],
            "discard_pile": ["STRIKE_IRONCLAD"],
            "enemy_hp": 6,
        },
        {
            "hand": ["WHIRLWIND"],
            "draw_pile": ["BASH", "DEFEND_IRONCLAD"],
            "enemy_hp": 1,
            "relics": ["POTION_BELT"],
            "potions": [{"slot": 0, "potion_id": "FIRE_POTION"}],
        },
        {"hand": ["STRIKE_IRONCLAD"], "draw_pile": ["DEFEND_IRONCLAD", "BASH"], "enemy_hp": 6, "relics": ["BIIIG_HUG"]},
        {"hand": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 5},
        {"hand": ["WHIRLWIND", "BASH"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 1},
        {"hand": ["STRIKE_IRONCLAD", "STRIKE_IRONCLAD"], "draw_pile": ["DEFEND_IRONCLAD"], "enemy_hp": 6},
        {"hand": ["BASH", "DEFEND_IRONCLAD"], "draw_pile": ["STRIKE_IRONCLAD", "STRIKE_IRONCLAD"], "enemy_hp": 8},
    ]
    specs: list[dict] = []
    for index in range(count):
        template = templates[index % len(templates)]
        specs.append(
            _spec(
                hand=list(template["hand"]),
                draw_pile=list(template.get("draw_pile") or []),
                discard_pile=list(template.get("discard_pile") or []),
                relics=list(template.get("relics") or []),
                potions=[dict(p) for p in (template.get("potions") or [])],
                enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": int(template["enemy_hp"])}],
                player_hp=65 - (index % 11),
                player_max_hp=80,
                seed=900000 + index,
            )
        )
    return specs


def _deck_multiset(spec: dict) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for pile_name in ("hand", "draw_pile", "discard_pile", "exhaust_pile"):
        counts.update(spec.get(pile_name) or [])
    return dict(counts)


def _routing_policy(boundary: str) -> str:
    if boundary == BOUNDARY_STABLE:
        return ROUTE_SEARCH
    if boundary == BOUNDARY_PENDING:
        return ROUTE_PENDING_STATIC
    raise AssertionError(f"unexpected boundary {boundary!r}")


def _gameplay_projection(state: dict) -> dict:
    return {
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "hand": state.get("hand"),
        "drawPile": state.get("drawPile"),
        "discardPile": state.get("discardPile"),
        "exhaustPile": state.get("exhaustPile"),
        "playerPowers": state.get("playerPowers"),
        "relics": state.get("relics"),
        "potions": state.get("potions"),
        "enemies": [
            {
                "id": enemy.get("id"),
                "hp": enemy.get("hp"),
                "maxHp": enemy.get("maxHp"),
                "block": enemy.get("block"),
                "isAlive": enemy.get("isAlive"),
                "powers": enemy.get("powers"),
            }
            for enemy in (state.get("enemies") or [])
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def _stable_session_json(session: LiveCombatSession) -> str:
    return json.dumps(
        {
            "legal_actions": session.get_legal_actions(),
            "state": _gameplay_projection(to_plain(session.get_observation().State)),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * (len(ordered) - 1)))))
    return float(ordered[index])


def _dist(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p95": None, "min": None, "max": None}
    return {
        "mean": float(statistics.fmean(values)),
        "median": float(statistics.median(values)),
        "p95": _pct(values, 95),
        "min": float(min(values)),
        "max": float(max(values)),
    }


@dataclass
class FaultInjectionController:
    fraction: float
    decision_count: int = 0
    injected_decisions: int = 0
    active: bool = False

    def begin_decision(self) -> bool:
        self.decision_count += 1
        period = int(round(1.0 / self.fraction)) if self.fraction > 0.0 else 0
        self.active = period > 0 and self.decision_count % period == 0
        if self.active:
            self.injected_decisions += 1
        return self.active

    def end_decision(self) -> None:
        self.active = False


@dataclass
class EnduranceObserver:
    session_ids: set[str] = field(default_factory=set)
    worker_last_generation: dict[int, int] = field(default_factory=dict)
    lease_issues: int = 0
    lease_consumptions: int = 0
    lease_invalid_consumptions: int = 0
    fault_counts: Counter[str] = field(default_factory=Counter)
    retry_rounds_observed: int = 0
    dispatch_calls: int = 0

    def observe_dispatch(self, work_items, registry: LeaseRegistry, pool: BranchWorkerPool, original_dispatch):
        self.dispatch_calls += 1
        for work_item in work_items:
            lease = registry.get(work_item.context_id, work_item.search_hypothesis_id)
            if lease is not None:
                generation = pool.worker_generations.get(lease.worker_id)
                if lease.is_valid_for(work_item, worker_generation=generation):
                    self.lease_consumptions += 1
                else:
                    self.lease_invalid_consumptions += 1
                    raise StopConditionError(
                        "Lease observed invalid for its routed WorkItem: "
                        f"lease={dataclasses.asdict(lease)!r} work_id={work_item.work_id!r}"
                    )
        results = original_dispatch(work_items, registry, worker_pool=pool)
        if any(result.status == "fault" for result in results):
            self.retry_rounds_observed += 1
        for result in results:
            if result.worker_id is not None and result.worker_generation is not None:
                previous = self.worker_last_generation.get(result.worker_id)
                if previous is not None and result.worker_generation < previous:
                    raise StopConditionError(
                        f"worker_generation regressed for worker_id={result.worker_id}: "
                        f"{previous} -> {result.worker_generation}"
                    )
                self.worker_last_generation[result.worker_id] = result.worker_generation
            if result.status == "fault":
                try:
                    fault_kind = classify_fault(result.diagnostics)
                except ValueError as exc:
                    raise StopConditionError(f"unclassified Branch Worker fault: {result.diagnostics!r}") from exc
                if fault_kind not in BRANCH_FAULT_KINDS:
                    raise StopConditionError(f"unexpected non-branch fault kind: {fault_kind!r}")
                self.fault_counts[fault_kind] += 1
                worker_reuse_policy(fault_kind)
            lease = result.established_lease
            if lease is not None:
                self._assert_established_lease(result.work_item, lease, result.pending_decision_context)
                self.lease_issues += 1
        return results

    @staticmethod
    def _assert_established_lease(work_item, lease: Lease, pending_context) -> None:
        if pending_context is None:
            raise StopConditionError(f"established lease without pending context for work_id={work_item.work_id}")
        expected_context_id = derive_context_id(pending_context)
        if lease.context_id != expected_context_id:
            raise StopConditionError(
                f"established lease context mismatch: lease={lease.context_id!r} expected={expected_context_id!r}"
            )
        signature = pending_context.current_context_signature
        if lease.combat_session_id != signature.combat_session_id or lease.step_index != signature.step_index:
            raise StopConditionError(f"established lease state identity mismatch: {dataclasses.asdict(lease)!r}")


def _worker_memory_sample(pool: BranchWorkerPool) -> dict[int, dict[str, int | None]]:
    sample = {}
    for worker_id, handle in pool._workers.items():  # noqa: SLF001
        pid = getattr(handle.process, "pid", None)
        sample[int(worker_id)] = {
            "pid": None if pid is None else int(pid),
            "working_set_bytes": None if pid is None else _working_set_bytes_for_pid(int(pid)),
        }
    return sample


def _snapshot_check(index: int, spec: dict) -> dict[str, Any]:
    session = LiveCombatSession()
    session.start_combat(spec)
    snapshot = session.capture_snapshot()
    report = validate_snapshot_references(snapshot)
    eligible, reasons = restore_input_eligibility(snapshot)
    if report.dangling_references or report.duplicate_instance_ids or not eligible:
        raise StopConditionError(
            f"snapshot integrity check failed at combat {index}: "
            f"dangling={report.dangling_references!r} duplicates={report.duplicate_instance_ids!r} reasons={reasons!r}"
        )
    return {
        "combat_index": index,
        "snapshot_id": snapshot.Metadata.SnapshotId,
        "combat_session_id": snapshot.Metadata.CombatSessionId,
        "eligible": eligible,
        "dangling_references": 0,
        "duplicate_instance_ids": 0,
    }


def _install_spies(controller: FaultInjectionController, observer: EnduranceObserver, pool: BranchWorkerPool):
    original_sc_pipeline = coordinator_module.build_candidate_pipeline_result
    original_mr_pipeline = multi_round_module.build_candidate_pipeline_result
    original_dispatch = coordinator_module.dispatch_work_items

    def _injecting_pipeline(decision_context, *, width):
        pipeline = original_sc_pipeline(decision_context, width=width)
        if controller.active and isinstance(pipeline, CandidatePipelineSuccess):
            invalid = dataclasses.replace(
                pipeline.continuation_candidate,
                semantic_action=SemanticAction("card", "NOT_A_REAL_CARD", "SingleEnemy"),
                target_enemy_index=0,
                score=pipeline.continuation_candidate.score - 100.0,
            )
            return dataclasses.replace(pipeline, sub_branch_candidates=[*pipeline.sub_branch_candidates, invalid])
        return pipeline

    def _observing_dispatch(work_items, lease_registry, *, worker_pool):
        return observer.observe_dispatch(work_items, lease_registry, pool, original_dispatch)

    coordinator_module.build_candidate_pipeline_result = _injecting_pipeline
    multi_round_module.build_candidate_pipeline_result = _injecting_pipeline
    coordinator_module.dispatch_work_items = _observing_dispatch
    return original_sc_pipeline, original_mr_pipeline, original_dispatch


def _restore_spies(originals) -> None:
    original_sc_pipeline, original_mr_pipeline, original_dispatch = originals
    coordinator_module.build_candidate_pipeline_result = original_sc_pipeline
    multi_round_module.build_candidate_pipeline_result = original_mr_pipeline
    coordinator_module.dispatch_work_items = original_dispatch


def run_endurance(
    combat_count: int,
    *,
    worker_count: int = 3,
    width: int = 1,
    hypothesis_count: int = 1,
    max_retries: int = 1,
    fault_fraction: float = 0.1,
    checkpoint_interval: int = 100,
    use_beam_every: int = 10,
    max_iterations_per_combat: int = 80,
) -> dict[str, Any]:
    specs = _scenario_specs(combat_count)
    registry = LeaseRegistry()
    observer = EnduranceObserver()
    controller = FaultInjectionController(fraction=fault_fraction)
    rows: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    snapshot_checks: list[dict[str, Any]] = []
    decision_latencies: list[float] = []
    rolling_faults: deque[int] = deque(maxlen=100)

    main_session = LiveCombatSession()
    main_session.start_combat(_spec(hand=["WHIRLWIND"], enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 1}], seed=424242))
    main_reference_snapshot = main_session.capture_snapshot()
    main_before = _stable_session_json(main_session)

    started = time.perf_counter()
    with BranchWorkerPool(worker_count=worker_count, request_timeout_s=120.0) as pool:
        originals = _install_spies(controller, observer, pool)
        try:
            snapshot_checks.append(_snapshot_check(0, specs[0]))
            for index, spec in enumerate(specs, start=1):
                session = LiveCombatSession()
                state = session.start_combat(spec)
                loop_state = initialize_main_loop_state(session, state)
                combat_session_id = state.decision_frame.combat_session_id
                if combat_session_id in observer.session_ids:
                    raise StopConditionError(f"CombatSessionId collision: {combat_session_id!r}")
                observer.session_ids.add(combat_session_id)

                metrics = SearchCoordinatorMetrics()
                config = SearchCoordinatorConfig(
                    width=width,
                    hypothesis_count=hypothesis_count,
                    max_retries=max_retries,
                    request_timeout_s=120.0,
                )
                strategy_kind = "beam" if use_beam_every > 0 and index % use_beam_every == 0 else "single_round"
                if strategy_kind == "beam":
                    inner_strategy = build_beam_search_strategy(
                        pool,
                        config=BeamSearchConfig(coordinator=config, beam_width=2, max_rounds=2),
                        combat_start_deck_multiset=_deck_multiset(spec),
                        lease_registry=registry,
                        main_state_provider=lambda: loop_state,
                    )
                else:
                    inner_strategy = build_search_strategy(
                        pool,
                        config=config,
                        combat_start_deck_multiset=_deck_multiset(spec),
                        lease_registry=registry,
                        main_state_provider=lambda: loop_state,
                        metrics=metrics,
                    )

                search_calls_before = controller.decision_count
                faults_before = sum(observer.fault_counts.values())

                def _strategy(context):
                    injected = controller.begin_decision()
                    decision_started = time.perf_counter()
                    try:
                        result = inner_strategy(context)
                        return result
                    finally:
                        elapsed_ms = (time.perf_counter() - decision_started) * 1000.0
                        decision_latencies.append(elapsed_ms)
                        controller.end_decision()
                        if injected:
                            print(f"INJECT decision={controller.decision_count} combat={index}", flush=True)

                combat_started = time.perf_counter()
                outcome = run_until_terminal_or_fault(
                    loop_state,
                    direct_selector=first_candidate_direct_selector,
                    search_strategy=_strategy,
                    routing_policy=_routing_policy,
                    max_iterations=max_iterations_per_combat,
                )
                elapsed_s = time.perf_counter() - combat_started
                if isinstance(outcome, MainCombatFaultOutcome):
                    raise StopConditionError(f"Main combat fault in combat {index}: {outcome!r}")
                if isinstance(outcome, CombatAbortedByDecisionFailureOutcome):
                    raise StopConditionError(f"Search evaluation aborted combat {index}: {outcome.detail}")
                if not isinstance(outcome, CombatTerminalOutcome):
                    raise StopConditionError(f"unexpected combat outcome in combat {index}: {outcome!r}")

                search_calls = controller.decision_count - search_calls_before
                fault_delta = sum(observer.fault_counts.values()) - faults_before
                rolling_faults.append(fault_delta)
                rows.append(
                    {
                        "combat_index": index,
                        "strategy_kind": strategy_kind,
                        "combat_session_id": combat_session_id,
                        "outcome": outcome.final_state.outcome,
                        "search_decisions": search_calls,
                        "elapsed_ms": elapsed_s * 1000.0,
                        "fault_count": fault_delta,
                        "metrics": dataclasses.asdict(metrics) if strategy_kind == "single_round" else None,
                    }
                )
                print(
                    "ENDURANCE "
                    f"{index}/{combat_count} strategy={strategy_kind} decisions={controller.decision_count} "
                    f"faults={sum(observer.fault_counts.values())} elapsed_ms={elapsed_s * 1000.0:.1f}",
                    flush=True,
                )

                if index % checkpoint_interval == 0 or index == combat_count:
                    check = _snapshot_check(index, spec)
                    snapshot_checks.append(check)
                    recent_latencies = decision_latencies[-max(1, min(len(decision_latencies), checkpoint_interval)) :]
                    checkpoints.append(
                        {
                            "combat_index": index,
                            "decision_count": controller.decision_count,
                            "elapsed_s": time.perf_counter() - started,
                            "coordinator_working_set_bytes": _current_working_set_bytes(),
                            "worker_memory": _worker_memory_sample(pool),
                            "recent_decision_latency_ms": _dist(recent_latencies),
                            "all_decision_latency_ms": _dist(decision_latencies),
                            "total_faults": dict(observer.fault_counts),
                            "rolling_100_combat_faults": int(sum(rolling_faults)),
                            "lease_issues": observer.lease_issues,
                            "lease_consumptions": observer.lease_consumptions,
                            "worker_generations": dict(pool.worker_generations),
                            "snapshot_check": check,
                        }
                    )
        finally:
            _restore_spies(originals)

        pool_alive = {worker_id: handle.process.is_alive() for worker_id, handle in pool._workers.items()}  # noqa: SLF001

    LiveCombatSession().restore_snapshot(main_reference_snapshot)
    main_after = _stable_session_json(main_session)
    main_unchanged = main_before == main_after
    if not main_unchanged:
        raise StopConditionError("Main reference session changed during endurance run")

    elapsed_s = time.perf_counter() - started
    return {
        "config": {
            "combat_count_requested": combat_count,
            "worker_count": worker_count,
            "width": width,
            "hypothesis_count": hypothesis_count,
            "max_retries": max_retries,
            "fault_fraction": fault_fraction,
            "checkpoint_interval": checkpoint_interval,
            "use_beam_every": use_beam_every,
            "max_iterations_per_combat": max_iterations_per_combat,
        },
        "summary": {
            "combat_count": len(rows),
            "decision_count": controller.decision_count,
            "elapsed_s": elapsed_s,
            "injected_decisions": controller.injected_decisions,
            "fault_counts": dict(observer.fault_counts),
            "retry_rounds_observed": observer.retry_rounds_observed,
            "unique_combat_session_ids": len(observer.session_ids),
            "combat_session_id_unique": len(observer.session_ids) == len(rows),
            "lease_issues": observer.lease_issues,
            "lease_consumptions": observer.lease_consumptions,
            "lease_invalid_consumptions": observer.lease_invalid_consumptions,
            "worker_generation_non_decreasing": True,
            "main_session_unchanged": main_unchanged,
            "pool_alive_before_close": pool_alive,
            "decision_latency_ms": _dist(decision_latencies),
            "combat_elapsed_ms": _dist([float(row["elapsed_ms"]) for row in rows]),
            "status_counts": dict(Counter(row["outcome"] for row in rows)),
        },
        "checkpoints": checkpoints,
        "snapshot_checks": snapshot_checks,
        "rows": rows,
        "measurement_notes": {
            "memory": (
                "Windows working-set bytes sampled with ctypes/psapi.GetProcessMemoryInfo for the coordinator "
                "process and each BranchWorkerPool multiprocessing.Process.pid."
            ),
            "fault_injection": (
                "Every configured fraction of SearchStrategy calls appends an invalid SemanticAction sub-branch "
                "to the real candidate pipeline; worker execution classifies it through the normal retry loop."
            ),
            "lease_registry_scope": (
                "One LeaseRegistry is shared for the whole run, matching build_search_strategy()'s documented "
                "caller's-retained registry contract."
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--combats", type=int, default=1000)
    parser.add_argument("--worker-count", type=int, default=3)
    parser.add_argument("--width", type=int, default=1)
    parser.add_argument("--hypothesis-count", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--fault-fraction", type=float, default=0.1)
    parser.add_argument("--checkpoint-interval", type=int, default=100)
    parser.add_argument("--use-beam-every", type=int, default=10)
    parser.add_argument("--max-iterations-per-combat", type=int, default=80)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run_endurance(
            args.combats,
            worker_count=args.worker_count,
            width=args.width,
            hypothesis_count=args.hypothesis_count,
            max_retries=args.max_retries,
            fault_fraction=args.fault_fraction,
            checkpoint_interval=args.checkpoint_interval,
            use_beam_every=args.use_beam_every,
            max_iterations_per_combat=args.max_iterations_per_combat,
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
    print("CHECKPOINTS_JSON_START")
    print(json.dumps(report["checkpoints"], indent=2, sort_keys=True, default=str))
    print("CHECKPOINTS_JSON_END")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
