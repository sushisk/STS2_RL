"""Shadow comparison adapter for old HeuristicAgent vs new Search Coordinator.

This module is observational/offline-analysis tooling only. It restores the same
``CombatStateSnapshot`` into disposable sessions, normalizes each path's chosen root
action, and reports agreement without committing any action back to a caller's Main
session. It imports the legacy inference stack (``Combat/legacy/heuristic_agent.py``
etc. - see that package's own docstring for the RL/Training division of responsibility
this module predates) purely to compute the OLD path's action for comparison; it never
uses that action to drive Main, and is not part of the production execution path
(`Combat/search/main_loop.py`/`search_coordinator.py`/`branch_worker_pool.py` never
import this module).
"""

from __future__ import annotations

import dataclasses
import copy
import multiprocessing
import queue
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from combat_state_snapshot import CombatStateSnapshot, canonical_json, restore_input_eligibility
from live_combat_session import ActionExecutionError, LiveCombatSession
from search.branch_worker_pool import BranchWorkerPool, LeaseRegistry
from search.decision_context import SemanticAction
from search.main_loop import SearchEvaluationFailure, SearchSuccess, build_main_decision_context, initialize_main_loop_state
from search.search_coordinator import SearchCoordinatorConfig, SearchCoordinatorMetrics, build_search_strategy


@dataclass(frozen=True)
class ShadowOutcomeMetrics:
    """Observed board metrics after executing the chosen action in a disposable session."""

    outcome: str
    is_terminal: bool
    remaining_player_hp: int
    potion_slots_consumed: int


@dataclass(frozen=True)
class ShadowExecutionMetrics:
    """Common execution counters used by the batch shadow evaluator.

    Old-path restore/step counts are approximated from HeuristicAgent's real evaluated
    candidates because its internal BattleEmulator restores are not instrumented. New-path
    counts come from SearchCoordinator/BranchResult execution modes.
    """

    step_count: int = 0
    restore_count: int = 0
    replay_count: int = 0
    fault_count: int = 0
    retry_count: int = 0
    worker_count: int = 0
    worker_ids_used: tuple[int, ...] = ()
    worker_utilization_fraction: float = 0.0
    hypothesis_count: int = 0
    search_round_count: int = 0
    plan_path_length: int = 0
    bootstrap_step_count: int = 0
    holder_step_count: int = 0
    work_item_count: int = 0


@dataclass(frozen=True)
class ShadowAction:
    """Common root-action identity used for old-vs-new comparison.

    Equality is intentionally semantic rather than byte/type equality: action type,
    card/potion id, target type, and the resolved enemy target are compared. If an
    enemy target is not present, target_index is used as a fallback. Raw action_id is
    not part of equality because it is session-local across Restore boundaries.
    """

    action_type: str
    card_id: "Optional[str]" = None
    potion_id: "Optional[str]" = None
    target_type: "Optional[str]" = None
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
    label: "Optional[str]" = None

    @property
    def comparison_key(self) -> tuple:
        target_key = self.target_enemy_index if self.target_enemy_index is not None else self.target_index
        return (self.action_type, self.card_id, self.potion_id, self.target_type, target_key)


@dataclass(frozen=True)
class OldPathResult:
    action: "Optional[ShadowAction]"
    action_id: "Optional[int]"
    score: "Optional[float]"
    candidate_details: list[dict[str, Any]]
    elapsed_ms: float
    legal_action_count: int
    restored_combat_session_id: "Optional[str]"
    outcome: "Optional[ShadowOutcomeMetrics]" = None
    metrics: ShadowExecutionMetrics = field(default_factory=ShadowExecutionMetrics)


@dataclass(frozen=True)
class NewPathResult:
    action: "Optional[ShadowAction]"
    status: str
    detail: "Optional[str]"
    planned_sequence_length: int
    elapsed_ms: float
    restored_combat_session_id: "Optional[str]"
    score: "Optional[float]" = None
    outcome: "Optional[ShadowOutcomeMetrics]" = None
    metrics: ShadowExecutionMetrics = field(default_factory=ShadowExecutionMetrics)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShadowComparisonResult:
    old: OldPathResult
    new: NewPathResult
    actions_agree: bool
    old_elapsed_ms: float
    new_elapsed_ms: float


def _snapshot_json(snapshot: CombatStateSnapshot) -> str:
    return canonical_json(dataclasses.asdict(snapshot), exclude_volatile=False)


def _action_from_legal_action(
    action: dict,
    *,
    target_index: "Optional[int]" = None,
    target_enemy_index: "Optional[int]" = None,
) -> ShadowAction:
    params = action.get("parameters") or {}
    return ShadowAction(
        action_type=str(action.get("action_type")),
        card_id=params.get("cardId"),
        potion_id=params.get("potionId"),
        target_type=params.get("targetType"),
        target_index=target_index if target_index is not None else _int_or_none(params.get("targetIndex")),
        target_enemy_index=(
            target_enemy_index if target_enemy_index is not None else _int_or_none(params.get("enemyIndex"))
        ),
        label=action.get("label"),
    )


def _action_from_semantic_action(
    action: SemanticAction,
    *,
    target_index: "Optional[int]" = None,
    target_enemy_index: "Optional[int]" = None,
) -> ShadowAction:
    return ShadowAction(
        action_type=action.action_type,
        card_id=action.card_id,
        target_type=action.target_type,
        target_index=target_index,
        target_enemy_index=target_enemy_index,
    )


def _int_or_none(value) -> "Optional[int]":
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _actions_agree(old: "Optional[ShadowAction]", new: "Optional[ShadowAction]") -> bool:
    if old is None or new is None:
        return old is None and new is None
    return old.comparison_key == new.comparison_key


def _old_path_restore_compatible_state(battle_state):
    """Return a disposable BattleState copy accepted by old BattleEmulator restores.

    LiveCombatSession.restore_snapshot() can expose the Emulator sentinel ``UNSET_MOVE``
    in enemy move fields. The old single-process restore path treats every non-empty
    move id as authoritative scenario input, so this adapter strips only that sentinel
    from its private old-path copy. The caller's snapshot/session are not modified.
    """

    from battle_emulator import BattleState

    engine_state = copy.deepcopy(battle_state.engine_state)
    for enemy in engine_state.get("enemies") or []:
        intent = enemy.get("intent") or {}
        if intent.get("stateId") == "UNSET_MOVE":
            intent.pop("stateId", None)
        enemy["stateLog"] = [move_id for move_id in (enemy.get("stateLog") or []) if move_id != "UNSET_MOVE"]
    return BattleState(
        engine_state=engine_state,
        is_terminal=battle_state.is_terminal,
        outcome=battle_state.outcome,
        turn=battle_state.turn,
        enemy_max_hps=dict(battle_state.enemy_max_hps),
        shuffle_rng_seed=battle_state.shuffle_rng_seed,
        _cached_legal_actions=copy.deepcopy(battle_state._cached_legal_actions),  # noqa: SLF001
        decision_frame=battle_state.decision_frame,
    )


def _potion_slots_consumed(before_state: dict, after_state: dict) -> int:
    before = len([p for p in (before_state.get("potions") or []) if p])
    after = len([p for p in (after_state.get("potions") or []) if p])
    return max(0, before - after)


def _outcome_metrics(before_state: dict, after_battle_state) -> ShadowOutcomeMetrics:
    state = after_battle_state.engine_state
    return ShadowOutcomeMetrics(
        outcome=after_battle_state.outcome,
        is_terminal=bool(after_battle_state.is_terminal),
        remaining_player_hp=int(state.get("hp") or 0),
        potion_slots_consumed=_potion_slots_consumed(before_state, state),
    )


def _old_path_worker(snapshot_json: str, repo_root: "str | None", out_queue) -> None:
    try:
        from battle_emulator import BattleEmulator
        from legacy.heuristic_agent import HeuristicAgent
        from legacy.potion_value_table import PotionValueTable
        from legacy.state_evaluator import DEFAULT_WEIGHTS, StateEvaluator

        snapshot = CombatStateSnapshot.from_json(snapshot_json)
        session = LiveCombatSession(repo_root=Path(repo_root) if repo_root is not None else None)
        battle_state = session.restore_snapshot(snapshot)
        battle_state = _old_path_restore_compatible_state(battle_state)
        legal_actions = list(battle_state._cached_legal_actions or session.get_legal_actions())  # noqa: SLF001

        # Canonical old-path construction copied from Combat/policy_agent.py:273-275
        # and Combat/main.py:99-103: BattleEmulator + StateEvaluator(PotionValueTable())
        # + dict(DEFAULT_WEIGHTS), with no beam/lookahead searcher.
        emulator = BattleEmulator(repo_root=Path(repo_root) if repo_root is not None else None)
        evaluator = StateEvaluator(PotionValueTable())
        agent = HeuristicAgent(emulator, evaluator, dict(DEFAULT_WEIGHTS))

        t0 = time.perf_counter()
        chosen, candidate_details = agent.choose_action_with_detail(battle_state)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        evaluated_count = sum(1 for detail in candidate_details if detail.get("score") is not None)
        fault_count = sum(1 for detail in candidate_details if detail.get("score") is None and detail.get("exception_type"))
        outcome_state = emulator.apply_action(
            battle_state,
            chosen.action,
            target_index=chosen.target_index,
            continuation_resolver=agent._choose_action_continuation_live,  # noqa: SLF001
        )
        target_enemy_index = next(
            (
                _int_or_none(detail.get("enemy_index"))
                for detail in candidate_details
                if detail.get("action_id") == chosen.action_id and detail.get("target_index") == chosen.target_index
            ),
            None,
        )
        frame = battle_state.decision_frame
        result = OldPathResult(
            action=_action_from_legal_action(
                chosen.action,
                target_index=chosen.target_index,
                target_enemy_index=target_enemy_index,
            ),
            action_id=chosen.action_id,
            score=chosen.score,
            candidate_details=candidate_details,
            elapsed_ms=elapsed_ms,
            legal_action_count=len(legal_actions),
            restored_combat_session_id=frame.combat_session_id if frame is not None else None,
            outcome=_outcome_metrics(battle_state.engine_state, outcome_state),
            metrics=ShadowExecutionMetrics(
                step_count=evaluated_count + 1,
                restore_count=1 + evaluated_count + 1,
                replay_count=0,
                fault_count=fault_count,
                retry_count=0,
                worker_count=0,
                worker_ids_used=(),
                worker_utilization_fraction=0.0,
                hypothesis_count=0,
                search_round_count=1,
                plan_path_length=1,
                bootstrap_step_count=0,
                holder_step_count=0,
                work_item_count=evaluated_count,
            ),
        )
        out_queue.put(("ok", result))
    except BaseException as exc:  # noqa: BLE001
        out_queue.put(("error", type(exc).__name__, str(exc), traceback.format_exc()))


def run_old_path(
    snapshot: CombatStateSnapshot,
    *,
    repo_root: "Path | str | None" = None,
    timeout_s: float = 120.0,
) -> OldPathResult:
    """Evaluate the old HeuristicAgent path against a disposable restored process."""

    eligible, reasons = restore_input_eligibility(snapshot)
    if not eligible:
        raise ValueError(f"snapshot is not restore-eligible: {reasons}")

    ctx = multiprocessing.get_context("spawn")
    out_queue = ctx.Queue()
    process = ctx.Process(
        target=_old_path_worker,
        args=(_snapshot_json(snapshot), str(Path(repo_root).resolve()) if repo_root is not None else None, out_queue),
    )
    process.start()
    try:
        status, *payload = out_queue.get(timeout=timeout_s)
    except queue.Empty as exc:
        process.terminate()
        process.join(timeout=5)
        raise TimeoutError(f"old path shadow execution timed out after {timeout_s:.1f}s") from exc
    finally:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    if status == "ok":
        return payload[0]
    exc_type, message, tb = payload
    raise RuntimeError(f"old path shadow execution failed: {exc_type}: {message}\n{tb}")


def _build_shadow_context(snapshot: CombatStateSnapshot, *, repo_root: "Path | str | None" = None):
    session = LiveCombatSession(repo_root=repo_root)
    state = session.restore_snapshot(snapshot)
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = snapshot
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)
    return session, state, loop_state, context


def _capture_current_process_snapshot(repo_root: "Path | str | None" = None) -> "CombatStateSnapshot | None":
    """Best-effort guard for the singleton GameInstance in this process."""

    try:
        from emulator_bridge import shared_game_instance

        game = shared_game_instance(repo_root)
        return CombatStateSnapshot.from_json(str(game.CaptureSnapshotJson()))
    except Exception:  # noqa: BLE001
        return None


def _restore_current_process_snapshot(
    snapshot: "CombatStateSnapshot | None",
    *,
    repo_root: "Path | str | None" = None,
) -> None:
    if snapshot is None:
        return
    LiveCombatSession(repo_root=repo_root).restore_snapshot(snapshot)


def run_new_path(
    snapshot: CombatStateSnapshot,
    *,
    pool: BranchWorkerPool,
    config: "SearchCoordinatorConfig | None" = None,
    repo_root: "Path | str | None" = None,
) -> NewPathResult:
    """Evaluate the new single-round Search Coordinator path once against a fresh Restore."""

    eligible, reasons = restore_input_eligibility(snapshot)
    if not eligible:
        raise ValueError(f"snapshot is not restore-eligible: {reasons}")

    effective_config = config or SearchCoordinatorConfig(width=4, hypothesis_count=2, max_retries=0)
    coordinator_metrics = SearchCoordinatorMetrics()
    preserve_snapshot = _capture_current_process_snapshot(repo_root)
    outcome_fault_detail: "str | None" = None
    try:
        session, state, loop_state, context = _build_shadow_context(snapshot, repo_root=repo_root)
        strategy = build_search_strategy(
            pool,
            config=effective_config,
            lease_registry=LeaseRegistry(),
            main_state_provider=lambda: loop_state,
            metrics=coordinator_metrics,
        )

        t0 = time.perf_counter()
        result = strategy(context)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        frame = loop_state.current_result.decision_frame
        restored_id = frame.combat_session_id if frame is not None else None
        outcome = None
        if isinstance(result, SearchSuccess) and result.planned_sequence:
            first = result.planned_sequence[0]
            resolved_action = first.semantic_action.resolve(state._cached_legal_actions or session.get_legal_actions())  # noqa: SLF001
            # This step is purely to measure outcome telemetry for the comparison report - the
            # search itself already succeeded above. A transient combat-session fault here (this
            # module isn't part of the production path, see module docstring) shouldn't crash the
            # whole comparison; degrade to no outcome instead, same as SearchEvaluationFailure below.
            try:
                outcome_state = session.step(
                    state,
                    resolved_action,
                    target_index=first.target_index,
                    target_enemy_index=first.target_enemy_index,
                )
            except ActionExecutionError as exc:
                outcome_fault_detail = str(exc)
            else:
                outcome = _outcome_metrics(state.engine_state, outcome_state)
    finally:
        _restore_current_process_snapshot(preserve_snapshot, repo_root=repo_root)

    execution_metrics = ShadowExecutionMetrics(
        step_count=coordinator_metrics.branch_step_count + (1 if outcome is not None else 0),
        restore_count=1 + coordinator_metrics.bootstrap_step_count + 1,
        replay_count=coordinator_metrics.replay_count,
        fault_count=coordinator_metrics.fault_count,
        retry_count=coordinator_metrics.retry_count,
        worker_count=len(pool.worker_ids),
        worker_ids_used=tuple(sorted(coordinator_metrics.worker_ids_used)),
        worker_utilization_fraction=(
            len(coordinator_metrics.worker_ids_used) / float(len(pool.worker_ids)) if pool.worker_ids else 0.0
        ),
        hypothesis_count=coordinator_metrics.hypothesis_count,
        search_round_count=coordinator_metrics.dispatch_round_count,
        plan_path_length=len(result.planned_sequence) if isinstance(result, SearchSuccess) else 0,
        bootstrap_step_count=coordinator_metrics.bootstrap_step_count,
        holder_step_count=coordinator_metrics.holder_step_count,
        work_item_count=coordinator_metrics.work_item_count,
    )

    if isinstance(result, SearchEvaluationFailure):
        return NewPathResult(
            action=None,
            status="evaluation_failure",
            detail=result.detail,
            planned_sequence_length=0,
            elapsed_ms=elapsed_ms,
            restored_combat_session_id=restored_id,
            score=coordinator_metrics.best_aggregate_score,
            outcome=None,
            metrics=execution_metrics,
        )
    if not isinstance(result, SearchSuccess):
        raise TypeError(f"unexpected search result type: {type(result).__name__}")
    if not result.planned_sequence:
        return NewPathResult(
            action=None,
            status="no_decision",
            detail="SearchSuccess returned an empty planned_sequence",
            planned_sequence_length=0,
            elapsed_ms=elapsed_ms,
            restored_combat_session_id=restored_id,
            score=coordinator_metrics.best_aggregate_score,
            outcome=None,
            metrics=execution_metrics,
        )
    first = result.planned_sequence[0]
    return NewPathResult(
        action=_action_from_semantic_action(
            first.semantic_action,
            target_index=first.target_index,
            target_enemy_index=first.target_enemy_index,
        ),
        status="success",
        detail=None,
        planned_sequence_length=len(result.planned_sequence),
        elapsed_ms=elapsed_ms,
        restored_combat_session_id=restored_id,
        score=coordinator_metrics.best_aggregate_score,
        outcome=outcome,
        metrics=execution_metrics,
        diagnostics={
            "expected_signature_present": first.expected_signature is not None,
            "outcome_fault_detail": outcome_fault_detail,
        },
    )


def compare_paths(
    snapshot: CombatStateSnapshot,
    *,
    pool: BranchWorkerPool,
    config: "SearchCoordinatorConfig | None" = None,
    repo_root: "Path | str | None" = None,
    old_timeout_s: float = 120.0,
) -> ShadowComparisonResult:
    """Run both paths from the same snapshot and return a pure comparison artifact."""

    old = run_old_path(snapshot, repo_root=repo_root, timeout_s=old_timeout_s)
    new = run_new_path(
        snapshot,
        pool=pool,
        config=config,
        repo_root=repo_root,
    )
    return ShadowComparisonResult(
        old=old,
        new=new,
        actions_agree=_actions_agree(old.action, new.action),
        old_elapsed_ms=old.elapsed_ms,
        new_elapsed_ms=new.elapsed_ms,
    )


def run_shadow_comparison_over_snapshots(
    snapshots: list[CombatStateSnapshot],
    *,
    pool: BranchWorkerPool,
    config: "SearchCoordinatorConfig | None" = None,
    repo_root: "Path | str | None" = None,
    old_timeout_s: float = 120.0,
) -> list[ShadowComparisonResult]:
    """Sequential batch helper for comparing many independently captured boards."""

    return [
        compare_paths(
            snapshot,
            pool=pool,
            config=config,
            repo_root=repo_root,
            old_timeout_s=old_timeout_s,
        )
        for snapshot in snapshots
    ]
