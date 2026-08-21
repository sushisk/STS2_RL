"""Internal combat semantics shared by combat API facades."""

from __future__ import annotations

import os
from typing import Any, Optional

from combat_state_snapshot import CombatStateSnapshot
from live_combat_session import LiveCombatSession
from search.branch_manager import BranchManager
from search.branch_worker_pool import BranchWorkerPool, LeaseRegistry, WORK_KIND_SUB_BRANCH
from search.candidate_pipeline import PipelineCandidateRef
from search.decision_context import (
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    DecisionContext,
    DecisionSignature,
    ReplayPrefixEntry,
    SemanticAction,
    append_replay_prefix_entry,
    boundary_of_battle_state,
    build_decision_context_from_held_stable,
    start_new_replay_prefix_from_stable,
)
from search.replay_draw_restore import visible_draw_transition_evidence_from_committed_transition

from API.combat_rng_mapping import build_single_hypothesis_work_item
from API.identifiers import RngHypothesisTable


DEFAULT_MAX_TIME_MS = 20_000
DEFAULT_ALC_WORKER_COUNT = 8
START_PENDING_UNSUPPORTED = (
    "CombatInstance does not yet support a Start-of-Combat Pending root - "
    "see main_loop.py's CombatStartReplayRoot handling for the mechanism this would need"
)
ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR = (
    "root is not branchable: no stable snapshot anchor is established"
)


def _semantic_action_for(action: dict) -> SemanticAction:
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_params(action: dict) -> tuple:
    params = action.get("parameters") or {}
    return _int_or_none(params.get("targetIndex")), _int_or_none(params.get("enemyIndex"))


class CombatPhase:
    def __init__(
        self,
        scenario_spec: dict,
        *,
        worker_count: int | None,
        request_timeout_s: float,
        max_branches: int,
        worker_pool_backend: str | None,
    ) -> None:
        session = LiveCombatSession()
        root_state = session.start_combat(scenario_spec)
        self._initialize_live_combat(
            session,
            root_state,
            worker_count=worker_count,
            request_timeout_s=request_timeout_s,
            max_branches=max_branches,
            worker_pool_backend=worker_pool_backend,
        )

    @classmethod
    def adopt(
        cls,
        session: LiveCombatSession,
        root_state,
        *,
        worker_count: int | None,
        request_timeout_s: float,
        max_branches: int,
        worker_pool_backend: str | None,
    ) -> "CombatPhase":
        """Take ownership of an already-live combat without reconstructing its board."""
        phase = cls.__new__(cls)
        phase._initialize_live_combat(
            session,
            root_state,
            worker_count=worker_count,
            request_timeout_s=request_timeout_s,
            max_branches=max_branches,
            worker_pool_backend=worker_pool_backend,
        )
        return phase

    def _initialize_live_combat(
        self,
        session: LiveCombatSession,
        root_state,
        *,
        worker_count: int | None,
        request_timeout_s: float,
        max_branches: int,
        worker_pool_backend: str | None,
    ) -> None:
        self._session = session
        self._root_state = root_state
        self._held_stable_snapshot: Optional[CombatStateSnapshot] = None
        self._replay_prefix: list[ReplayPrefixEntry] = []
        root_boundary = boundary_of_battle_state(self._root_state)
        if root_boundary == BOUNDARY_STABLE:
            self._establish_stable_anchor()
        # Reserved for future true/root draw-hypothesis bookkeeping; current branch
        # decision logic still uses Training-provided rng_id mappings exclusively.
        self.true_draw_hypothesis_index = 0
        self._pool = _make_branch_pool(
            worker_count=worker_count,
            request_timeout_s=request_timeout_s,
            worker_pool_backend=worker_pool_backend,
            max_branches=max_branches,
        )
        self._lease_registry = LeaseRegistry()
        self._branch_manager = BranchManager(self._pool, self._lease_registry, max_branches=max_branches)
        self._rng_table = RngHypothesisTable()
        self._root_commit_advanced = False

    def _establish_stable_anchor(self) -> None:
        self._held_stable_snapshot = self._session.capture_snapshot()
        self._replay_prefix = start_new_replay_prefix_from_stable()

    @property
    def max_branches(self) -> int:
        return self._branch_manager.max_branches

    def root_decision(self) -> tuple[list, DecisionContext | None, str]:
        legal = list(self._root_state._cached_legal_actions or [])
        if self._root_state.is_terminal:
            legal = []
        elif not legal:
            raise RuntimeError("non-terminal combat state has no cached legal actions")
        boundary = boundary_of_battle_state(self._root_state)
        if self._held_stable_snapshot is None:
            return legal, None, boundary
        context = build_decision_context_from_held_stable(
            self._held_stable_snapshot, self._replay_prefix, self._root_state
        )
        return legal, context, boundary

    @property
    def root_state(self):
        return self._root_state

    @property
    def root_branching_unavailable_reason(self) -> str | None:
        if self._held_stable_snapshot is None:
            return ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR
        return None

    def commit_root_action(self, chosen: dict) -> None:
        self._root_commit_advanced = False
        target_index, target_enemy_index = _target_params(chosen)
        pre_state = self._root_state
        next_state = self._session.step(
            pre_state, chosen, target_index=target_index,
            target_enemy_index=target_enemy_index, stop_at_pending=True,
        )
        # The live session has already irreversibly advanced at this point (Step
        # succeeded), so a failure anywhere below must not leave root state pointing
        # at the stale pre-Step state while the live session has moved on.
        self._root_state = next_state
        self._root_commit_advanced = True
        observed_signature = DecisionSignature.from_battle_state(
            next_state,
            semantic_action=_semantic_action_for(chosen),
            resolved_action=chosen,
            target_index=target_index,
            target_enemy_index=target_enemy_index,
        )
        boundary = boundary_of_battle_state(next_state)
        if boundary == BOUNDARY_STABLE:
            self._establish_stable_anchor()
        elif boundary == BOUNDARY_PENDING:
            if self._held_stable_snapshot is not None:
                draw_evidence = visible_draw_transition_evidence_from_committed_transition(
                    next_state, self._replay_prefix, pre_battle_state=pre_state,
                )
                entry = ReplayPrefixEntry(
                    semantic_action=_semantic_action_for(chosen),
                    expected_signature=observed_signature,
                    target_index=target_index,
                    target_enemy_index=target_enemy_index,
                    visible_draw_constraints=draw_evidence.constraints,
                    visible_draw_tracking_blocked=draw_evidence.blocks_later_pinning,
                    visible_draw_tracking_error=draw_evidence.tracking_error,
                )
                self._replay_prefix = append_replay_prefix_entry(self._replay_prefix, entry)

    def root_commit_advanced(self) -> bool:
        return self._root_commit_advanced

    def build_work_item(self, context: DecisionContext, chosen: dict, parent_key: str, decision_key: str, rng_id: int):
        target_index, target_enemy_index = _target_params(chosen)
        candidate = PipelineCandidateRef(
            current_context_signature=context.current_context_signature,
            semantic_action=_semantic_action_for(chosen),
            target_index=target_index,
            target_enemy_index=target_enemy_index,
        )
        hypothesis_index = self._rng_table.hypothesis_index_for(parent_key, decision_key, rng_id)
        return build_single_hypothesis_work_item(
            context, candidate, hypothesis_index, work_kind=WORK_KIND_SUB_BRANCH,
        )

    def snapshot_rng_hypotheses(self):
        return self._rng_table.snapshot()

    def restore_rng_hypotheses(self, snapshot) -> None:
        self._rng_table.restore(snapshot)

    def submit(self, work_items: list, *, parent_internal_id: str | None) -> tuple[str, ...]:
        return self._branch_manager.submit(work_items, parent_branch_id=parent_internal_id)

    def submit_many(self, work_items_with_parents: list[tuple]) -> tuple[str, ...]:
        return self._branch_manager.submit_many(work_items_with_parents)

    def poll(self, *, timeout: float, branch_ids: list[str]) -> dict:
        return self._branch_manager.poll(timeout=timeout, branch_ids=branch_ids)

    def branch_status(self, internal_id: str) -> str:
        return self._branch_manager.get_branch_status([internal_id])[internal_id]

    def active_branch_count(self) -> int:
        return self._branch_manager.active_branch_count()

    def cancel(self, internal_ids: list[str]) -> None:
        self._branch_manager.cancel_branches(internal_ids)

    def release(self, internal_ids: list[str]) -> None:
        self._branch_manager.release_branches(internal_ids)

    def close(self) -> None:
        try:
            self._branch_manager.close_all()
        finally:
            try:
                self._pool.close()
            finally:
                self._session.close()


def _alc_worker_count_from_env() -> int | None:
    raw = os.environ.get("STS2_COMBAT_ALC_WORKERS")
    if raw is None or raw.strip() == "":
        return None
    try:
        worker_count = int(raw)
    except ValueError as exc:
        raise ValueError("STS2_COMBAT_ALC_WORKERS must be a positive integer") from exc
    if worker_count <= 0:
        raise ValueError("STS2_COMBAT_ALC_WORKERS must be a positive integer")
    return worker_count


def _make_branch_pool(*, worker_count: int | None, request_timeout_s: float, worker_pool_backend: str | None, max_branches: int):
    backend = (worker_pool_backend or os.environ.get("STS2_COMBAT_BRANCH_POOL") or "multiprocessing").strip().lower()
    if backend in {"multiprocessing", "process", "processes", "mp", "branch_worker_pool"}:
        if worker_count is None:
            worker_count = 2
        return BranchWorkerPool(worker_count=worker_count, request_timeout_s=request_timeout_s)
    if backend in {"alc", "assemblyloadcontext", "assembly_load_context", "isolated"}:
        from search.alc_worker_pool import AlcBranchWorkerPool

        if worker_count is None:
            worker_count = _alc_worker_count_from_env() or DEFAULT_ALC_WORKER_COUNT
        return AlcBranchWorkerPool(worker_count=worker_count, request_timeout_s=request_timeout_s)
    raise ValueError(f"unknown combat branch worker pool backend {backend!r}")
