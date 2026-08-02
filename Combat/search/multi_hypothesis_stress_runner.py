"""Multi-candidate x multi-hypothesis stress runner for the Search Coordinator.

This runner drives real combats through ``build_search_strategy()`` with width > 1 and
multiple RNG hypotheses. It verifies the Standard-mode GRID invariant from actual
dispatched WorkItems, injects real worker faults by corrupting selected dispatch copies,
and checks COMMIT_FIRST_ONLY immediately on every hypothesis-backed SearchSuccess.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from combat_state_snapshot import CombatHistoryEntrySnapshot  # noqa: E402
from emulator_bridge import to_plain  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import BRANCH_STATUS_FAULT, BranchWorkerPool, Lease, LeaseRegistry, derive_context_id  # noqa: E402
from search.fault_taxonomy import BRANCH_FAULT_KINDS, classify_fault, worker_reuse_policy  # noqa: E402
from search.main_loop import (  # noqa: E402
    SearchSuccess,
    build_main_decision_context,
    initialize_main_loop_state,
)
from search.search_coordinator import SearchCoordinatorConfig, SearchCoordinatorMetrics, build_search_strategy  # noqa: E402

import search.search_coordinator as coordinator_module  # noqa: E402


class StopConditionError(RuntimeError):
    """Raised when a required multi-hypothesis stress invariant is violated."""


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
        "enemies": enemies if enemies is not None else [{"monster_id": "CALCIFIED_CULTIST", "hp": 35}],
    }


def _scenario_specs(count: int) -> list[dict]:
    templates = [
        {"hand": ["STRIKE_IRONCLAD", "BASH", "WHIRLWIND"], "draw_pile": ["DEFEND_IRONCLAD"], "enemy_hp": 5},
        {"hand": ["WHIRLWIND", "BASH", "STRIKE_IRONCLAD"], "draw_pile": ["DEFEND_IRONCLAD"], "enemy_hp": 5},
        {
            "hand": ["STRIKE_IRONCLAD", "WHIRLWIND", "BASH"],
            "draw_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
            "discard_pile": ["STRIKE_IRONCLAD"],
            "enemy_hp": 5,
        },
        {
            "hand": ["BASH", "STRIKE_IRONCLAD", "WHIRLWIND"],
            "draw_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
            "enemy_hp": 5,
        },
        {
            "hand": ["WHIRLWIND", "STRIKE_IRONCLAD", "BASH"],
            "draw_pile": ["BASH", "STRIKE_IRONCLAD"],
            "relics": ["ANCHOR"],
            "enemy_hp": 5,
        },
        {
            "hand": ["STRIKE_IRONCLAD", "BASH", "WHIRLWIND"],
            "draw_pile": ["WHIRLWIND", "STRIKE_IRONCLAD"],
            "potions": [{"slot": 0, "potion_id": "FIRE_POTION"}],
            "relics": ["POTION_BELT"],
            "enemy_hp": 5,
        },
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
                enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": int(template["enemy_hp"]) + (index % 4)}],
                player_hp=62 - (index % 10),
                player_max_hp=80,
                seed=740000 + index,
            )
        )
    return specs


def _deck_multiset(spec: dict) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for pile_name in ("hand", "draw_pile", "discard_pile", "exhaust_pile"):
        counts.update(spec.get(pile_name) or [])
    return dict(counts)


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


def _root_action_key(work_item) -> str:
    action = work_item.candidate.semantic_action
    return json.dumps(
        {
            "action_type": action.action_type,
            "card_id": action.card_id,
            "target_type": action.target_type,
            "target_index": work_item.candidate.target_index,
            "target_enemy_index": work_item.candidate.target_enemy_index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _action_label(step) -> str:
    action = step.semantic_action
    return json.dumps(
        {
            "action_type": action.action_type,
            "card_id": action.card_id,
            "target_type": action.target_type,
            "target_index": step.target_index,
            "target_enemy_index": step.target_enemy_index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass
class FaultInjectionController:
    fraction: float
    decision_count: int = 0
    injected_decisions: int = 0
    active: bool = False
    injection_kind: Optional[str] = None

    def begin_decision(self) -> bool:
        self.decision_count += 1
        period = int(round(1.0 / self.fraction)) if self.fraction > 0.0 else 0
        self.active = period > 0 and self.decision_count % period == 0
        self.injection_kind = None
        if self.active:
            self.injected_decisions += 1
            self.injection_kind = "root_exclusion" if self.injected_decisions % 3 == 0 else "partial_missing_sample"
        return self.active

    def end_decision(self) -> None:
        self.active = False
        self.injection_kind = None


@dataclass
class DecisionObservation:
    decision_index: int
    combat_index: int
    work_item_count: int
    root_action_count: int
    hypothesis_ids: tuple[str, ...]
    grid_ok: bool
    injected_work_ids: tuple[str, ...] = ()
    injection_kind: Optional[str] = None


@dataclass
class StressObserver:
    controller: FaultInjectionController
    decision_index: int = 0
    current_combat_index: int = 0
    observations: list[DecisionObservation] = field(default_factory=list)
    fault_counts: Counter[str] = field(default_factory=Counter)
    dispatch_calls: int = 0
    last_grid_decision: int = 0
    retry_rounds_observed: int = 0
    worker_last_generation: dict[int, int] = field(default_factory=dict)
    lease_consumptions: int = 0
    lease_invalid_consumptions: int = 0
    lease_issues: int = 0
    partial_missing_injections: int = 0
    root_exclusion_injections: int = 0

    def observe_dispatch(self, work_items, registry: LeaseRegistry, pool: BranchWorkerPool, original_dispatch):
        self.dispatch_calls += 1
        self.decision_index = self.controller.decision_count
        hypothesis_items = [item for item in work_items if item.search_hypothesis_id is not None]
        if hypothesis_items and self.last_grid_decision != self.decision_index:
            self._verify_grid(hypothesis_items)
            self.last_grid_decision = self.decision_index
        for work_item in work_items:
            lease = registry.get(work_item.context_id, work_item.search_hypothesis_id)
            if lease is not None:
                generation = pool.worker_generations.get(lease.worker_id)
                if lease.is_valid_for(work_item, worker_generation=generation):
                    self.lease_consumptions += 1
                else:
                    self.lease_invalid_consumptions += 1
                    raise StopConditionError(
                        "Lease observed invalid for routed WorkItem: "
                        f"lease={dataclasses.asdict(lease)!r} work_id={work_item.work_id!r}"
                    )

        dispatch_items, injected = self._maybe_inject_faults(work_items)
        results = original_dispatch(dispatch_items, registry, worker_pool=pool)
        if any(result.status == BRANCH_STATUS_FAULT for result in results):
            self.retry_rounds_observed += 1
        remapped = []
        original_by_work_id = {item.work_id: item for item in work_items}
        for result in results:
            original_item = original_by_work_id[result.work_item.work_id]
            if result.work_item is not original_item:
                result = dataclasses.replace(result, work_item=original_item)
            remapped.append(result)
            if result.worker_id is not None and result.worker_generation is not None:
                previous = self.worker_last_generation.get(result.worker_id)
                if previous is not None and result.worker_generation < previous:
                    raise StopConditionError(
                        f"worker_generation regressed for worker_id={result.worker_id}: "
                        f"{previous} -> {result.worker_generation}"
                    )
                self.worker_last_generation[result.worker_id] = result.worker_generation
            if result.status == BRANCH_STATUS_FAULT:
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

        if hypothesis_items and self.observations and self.observations[-1].decision_index == self.decision_index:
            self.observations[-1].injected_work_ids = tuple(injected)
        return remapped

    def _verify_grid(self, work_items) -> None:
        by_root: dict[str, set[str]] = {}
        for work_item in work_items:
            assert work_item.search_hypothesis_id is not None
            by_root.setdefault(_root_action_key(work_item), set()).add(work_item.search_hypothesis_id)
        sets = {root: tuple(sorted(ids)) for root, ids in by_root.items()}
        expected = next(iter(sets.values()))
        mismatches = {root: ids for root, ids in sets.items() if ids != expected}
        if mismatches:
            raise StopConditionError(
                "GRID invariant violated: root actions received different Search Hypothesis sets; "
                f"expected={expected!r} mismatches={mismatches!r}"
            )
        self.observations.append(
            DecisionObservation(
                decision_index=self.decision_index,
                combat_index=self.current_combat_index,
                work_item_count=len(work_items),
                root_action_count=len(by_root),
                hypothesis_ids=expected,
                grid_ok=True,
                injection_kind=self.controller.injection_kind if self.controller.active else None,
            )
        )

    def _maybe_inject_faults(self, work_items) -> tuple[list[Any], list[str]]:
        if not self.controller.active:
            return list(work_items), []
        hypothesis_items = [item for item in work_items if item.search_hypothesis_id is not None]
        if not hypothesis_items:
            return list(work_items), []
        by_root: dict[str, list[Any]] = {}
        for item in hypothesis_items:
            by_root.setdefault(_root_action_key(item), []).append(item)
        if len(by_root) < 2:
            target_ids = {item.work_id for item in hypothesis_items}
            injected_items = [
                self._invalid_snapshot_copy(item) if item.work_id in target_ids else item
                for item in work_items
            ]
            return injected_items, sorted(target_ids)
        ordered_roots = sorted(by_root)
        targets = (
            by_root[ordered_roots[-1]]
            if self.controller.injection_kind == "root_exclusion"
            else [sorted(by_root[ordered_roots[0]], key=lambda item: item.search_hypothesis_id or "")[0]]
        )
        if self.controller.injection_kind == "root_exclusion":
            self.root_exclusion_injections += 1
        else:
            self.partial_missing_injections += 1
        target_ids = {item.work_id for item in targets}
        injected_items = [
            self._invalid_snapshot_copy(item) if item.work_id in target_ids else item
            for item in work_items
        ]
        return injected_items, sorted(target_ids)

    @staticmethod
    def _invalid_snapshot_copy(work_item):
        snapshot = work_item.decision_context.root_snapshot
        dangling_draw = CombatHistoryEntrySnapshot(
            EntryType="CardDrawnEntry",
            RoundNumber=snapshot.RoundNumber,
            CurrentSide=snapshot.CurrentSide,
            PlayerTurnNumbers={},
            Fields={"cardInstanceId": "MULTIHYP_STRESS_INJECTED_DANGLING_DRAW", "fromHandDraw": True},
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


def _install_spies(controller: FaultInjectionController, observer: StressObserver, pool: BranchWorkerPool):
    original_dispatch = coordinator_module.dispatch_work_items

    def _observing_dispatch(work_items, lease_registry, *, worker_pool):
        return observer.observe_dispatch(work_items, lease_registry, pool, original_dispatch)

    coordinator_module.dispatch_work_items = _observing_dispatch
    return original_dispatch


def _restore_spies(original_dispatch) -> None:
    coordinator_module.dispatch_work_items = original_dispatch


def _chosen_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(row["chosen_action"] for row in rows if row.get("chosen_action") is not None))


def _aggregate(rows: list[dict[str, Any]], observer: StressObserver, *, elapsed_s: float, main_unchanged: bool) -> dict[str, Any]:
    metrics = [row["metrics"] for row in rows]
    hypothesis_rows = [row for row in rows if row["metrics"]["hypothesis_involved"]]
    total_work = sum(int(item["work_item_count"]) for item in metrics)
    total_faults = sum(observer.fault_counts.values())
    return {
        "combat_count": len({row["combat_index"] for row in rows}),
        "decision_count": len(rows),
        "hypothesis_decision_count": len(hypothesis_rows),
        "elapsed_s": elapsed_s,
        "chosen_action_counts": _chosen_counts(rows),
        "decision_elapsed_ms": _dist([float(row["elapsed_ms"]) for row in rows]),
        "step_count": _dist([float(item["branch_step_count"]) for item in metrics]),
        "restore_count": _dist([float(item["bootstrap_step_count"]) for item in metrics]),
        "replay_count": _dist([float(item["replay_count"]) for item in metrics]),
        "fault_count": _dist([float(item["fault_count"]) for item in metrics]),
        "retry_count": _dist([float(item["retry_count"]) for item in metrics]),
        "fault_rate_per_work_item": total_faults / float(total_work or 1),
        "fault_counts": dict(observer.fault_counts),
        "injected_decisions": observer.controller.injected_decisions,
        "partial_missing_injections": observer.partial_missing_injections,
        "root_exclusion_injections": observer.root_exclusion_injections,
        "retry_rounds_observed": observer.retry_rounds_observed,
        "grid_checks": len(observer.observations),
        "grid_fairness_passed": all(obs.grid_ok for obs in observer.observations),
        "root_action_count": _dist([float(obs.root_action_count) for obs in observer.observations]),
        "hypothesis_count": _dist([float(item["hypothesis_count"]) for item in metrics]),
        "search_round_count": _dist([float(item["dispatch_round_count"]) for item in metrics]),
        "plan_path_length": _dist([float(row["plan_path_length"]) for row in rows]),
        "work_item_count": _dist([float(item["work_item_count"]) for item in metrics]),
        "pessimistic_fill_actions": sum(int(row["pessimistic_fill_action_count"]) for row in rows),
        "excluded_root_actions": sum(int(row["excluded_root_action_count"]) for row in rows),
        "lease_issues": observer.lease_issues,
        "lease_consumptions": observer.lease_consumptions,
        "lease_invalid_consumptions": observer.lease_invalid_consumptions,
        "worker_generation_non_decreasing": True,
        "main_session_unchanged": main_unchanged,
    }


def run_multi_hypothesis_stress(
    combat_count: int,
    *,
    worker_count: int = 3,
    width: int = 3,
    hypothesis_count: int = 4,
    max_retries: int = 1,
    min_coverage_fraction: float = 0.5,
    fault_fraction: float = 0.2,
    checkpoint_interval: int = 25,
    max_iterations_per_combat: int = 80,
) -> dict[str, Any]:
    specs = _scenario_specs(combat_count)
    registry = LeaseRegistry()
    controller = FaultInjectionController(fraction=fault_fraction)
    observer = StressObserver(controller=controller)
    rows: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    rolling_faults: deque[int] = deque(maxlen=max(1, checkpoint_interval))

    main_session = LiveCombatSession()
    main_session.start_combat(_spec(hand=["WHIRLWIND"], enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 1}], seed=424242))
    main_reference_snapshot = main_session.capture_snapshot()
    main_before = _stable_session_json(main_session)

    started = time.perf_counter()
    with BranchWorkerPool(worker_count=worker_count, request_timeout_s=120.0) as pool:
        original_dispatch = _install_spies(controller, observer, pool)
        try:
            for combat_index, spec in enumerate(specs, start=1):
                observer.current_combat_index = combat_index
                session = LiveCombatSession()
                state = session.start_combat(spec)
                loop_state = initialize_main_loop_state(session, state)
                config = SearchCoordinatorConfig(
                    width=width,
                    hypothesis_count=hypothesis_count,
                    min_coverage_fraction=min_coverage_fraction,
                    max_retries=max_retries,
                    worker_count=worker_count,
                    request_timeout_s=120.0,
                )
                loop_state.held_stable_snapshot = session.capture_snapshot()
                loop_state.replay_prefix = []
                context = build_main_decision_context(loop_state)
                combat_start_deck_multiset = _deck_multiset(spec)

                def _strategy(context):
                    metrics = SearchCoordinatorMetrics()
                    controller.begin_decision()
                    decision_started = time.perf_counter()
                    try:
                        strategy = build_search_strategy(
                            pool,
                            config=config,
                            combat_start_deck_multiset=combat_start_deck_multiset,
                            lease_registry=registry,
                            main_state_provider=lambda: loop_state,
                            metrics=metrics,
                        )
                        result = strategy(context)
                        if metrics.hypothesis_involved:
                            if not isinstance(result, SearchSuccess):
                                raise StopConditionError(f"hypothesis decision did not produce SearchSuccess: {result!r}")
                            if len(result.planned_sequence) != 1:
                                raise StopConditionError(
                                    "COMMIT_FIRST_ONLY violated: "
                                    f"planned_sequence length={len(result.planned_sequence)}"
                                )
                            if result.planned_sequence[0].expected_signature is not None:
                                raise StopConditionError("COMMIT_FIRST_ONLY violated: expected_signature was not None")
                        elapsed_ms = (time.perf_counter() - decision_started) * 1000.0
                        diagnostics = dict(metrics.aggregation_diagnostics)
                        viable = diagnostics.get("viable_actions") or []
                        rows.append(
                            {
                                "combat_index": combat_index,
                                "decision_index": controller.decision_count,
                                "chosen_action": (
                                    _action_label(result.planned_sequence[0])
                                    if isinstance(result, SearchSuccess) and result.planned_sequence
                                    else None
                                ),
                                "elapsed_ms": elapsed_ms,
                                "plan_path_length": len(result.planned_sequence) if isinstance(result, SearchSuccess) else 0,
                                "metrics": dataclasses.asdict(metrics),
                                "excluded_root_action_count": len(diagnostics.get("excluded_root_actions") or []),
                                "pessimistic_fill_action_count": sum(
                                    1 for item in viable if isinstance(item, dict) and int(item.get("missing_sample_count") or 0) > 0
                                ),
                            }
                        )
                        return result
                    finally:
                        controller.end_decision()

                combat_started = time.perf_counter()
                result = _strategy(context)
                elapsed_ms = (time.perf_counter() - combat_started) * 1000.0
                if not isinstance(result, SearchSuccess):
                    raise StopConditionError(f"Search evaluation failed in combat {combat_index}: {result!r}")
                previous_total_faults = rolling_faults[-1] if rolling_faults else 0
                current_total_faults = sum(observer.fault_counts.values())
                rolling_faults.append(current_total_faults)
                print(
                    "MULTIHYP "
                    f"{combat_index}/{combat_count} decisions=1 "
                    f"total_decisions={len(rows)} faults={current_total_faults} "
                    f"elapsed_ms={elapsed_ms:.1f}",
                    flush=True,
                )
                if combat_index % checkpoint_interval == 0 or combat_index == combat_count:
                    recent = rows[-max(1, min(len(rows), checkpoint_interval)) :]
                    checkpoints.append(
                        {
                            "combat_index": combat_index,
                            "decision_count": len(rows),
                            "elapsed_s": time.perf_counter() - started,
                            "recent_decision_elapsed_ms": _dist([float(row["elapsed_ms"]) for row in recent]),
                            "total_faults": dict(observer.fault_counts),
                            "recent_faults": int(current_total_faults - previous_total_faults),
                            "grid_checks": len(observer.observations),
                            "excluded_root_actions": sum(int(row["excluded_root_action_count"]) for row in rows),
                            "pessimistic_fill_actions": sum(int(row["pessimistic_fill_action_count"]) for row in rows),
                        }
                    )
        finally:
            _restore_spies(original_dispatch)

    LiveCombatSession().restore_snapshot(main_reference_snapshot)
    main_after = _stable_session_json(main_session)
    main_unchanged = main_before == main_after
    if not main_unchanged:
        raise StopConditionError("Main reference session changed during multi-hypothesis stress run")

    elapsed_s = time.perf_counter() - started
    summary = _aggregate(rows, observer, elapsed_s=elapsed_s, main_unchanged=main_unchanged)
    if summary["hypothesis_decision_count"] <= 0:
        raise StopConditionError("no hypothesis-backed decisions were observed")
    if summary["grid_checks"] != summary["hypothesis_decision_count"]:
        raise StopConditionError(
            f"grid check count mismatch: grid_checks={summary['grid_checks']} "
            f"hypothesis_decisions={summary['hypothesis_decision_count']}"
        )
    if summary["partial_missing_injections"] <= 0 or summary["root_exclusion_injections"] <= 0:
        raise StopConditionError("fault injection did not exercise both partial missing and root exclusion patterns")
    if summary["pessimistic_fill_actions"] <= 0:
        raise StopConditionError("aggregation did not observe any pessimistic-fill action")
    if summary["excluded_root_actions"] <= 0:
        raise StopConditionError("aggregation did not observe any minimum-coverage root exclusion")

    return {
        "config": {
            "combat_count_requested": combat_count,
            "worker_count": worker_count,
            "width": width,
            "hypothesis_count": hypothesis_count,
            "max_retries": max_retries,
            "min_coverage_fraction": min_coverage_fraction,
            "fault_fraction": fault_fraction,
            "checkpoint_interval": checkpoint_interval,
            "max_iterations_per_combat": max_iterations_per_combat,
        },
        "metric_definitions": {
            "step_count": "Branch worker Step attempts observed through SearchCoordinatorMetrics.branch_step_count.",
            "restore_count": "BranchResult bootstrap_step count used as the branch Restore proxy.",
            "search_round_count": "SearchCoordinator dispatch retry-loop rounds.",
            "plan_path_length": "Length of SearchSuccess.planned_sequence; hypothesis path is required to stay at 1.",
            "pessimistic_fill_actions": "Root action aggregations with one or more missing hypothesis samples filled by worst valid score.",
            "excluded_root_actions": "Root actions excluded for failing minimum valid hypothesis coverage.",
        },
        "summary": summary,
        "checkpoints": checkpoints,
        "grid_observations": [dataclasses.asdict(obs) for obs in observer.observations],
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--combats", type=int, default=120)
    parser.add_argument("--worker-count", type=int, default=3)
    parser.add_argument("--width", type=int, default=3)
    parser.add_argument("--hypothesis-count", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--min-coverage-fraction", type=float, default=0.5)
    parser.add_argument("--fault-fraction", type=float, default=0.2)
    parser.add_argument("--checkpoint-interval", type=int, default=25)
    parser.add_argument("--max-iterations-per-combat", type=int, default=80)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run_multi_hypothesis_stress(
            args.combats,
            worker_count=args.worker_count,
            width=args.width,
            hypothesis_count=args.hypothesis_count,
            max_retries=args.max_retries,
            min_coverage_fraction=args.min_coverage_fraction,
            fault_fraction=args.fault_fraction,
            checkpoint_interval=args.checkpoint_interval,
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
