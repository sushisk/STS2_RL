"""Tests for Combat/search/multi_round_search.py.

Native assertion runner, matching the existing Combat search tests. Fake dispatch is
used for pruning/control-flow tests; one smoke uses the real Emulator + BranchWorkerPool
path to prove the hypothesis boundary terminates cleanly without PREV_CHILD continuation.
"""

from __future__ import annotations

import dataclasses
import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import CombatStateSnapshot  # noqa: E402
from emulator_bridge import ensure_loaded  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    BranchResult,
    BranchTerminalResult,
    BranchWorkerPool,
    LeaseRegistry,
)
from search.candidate_pipeline import (  # noqa: E402
    CandidatePipelineSuccess,
    NoViableCandidates,
    OrderMaskedObservation,
    PipelineCandidateRef,
)
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    BOUNDARY_TERMINAL,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
)
from search.main_loop import SearchEvaluationFailure, SearchSuccess  # noqa: E402
from search.multi_round_search import BeamSearchConfig, build_beam_search_strategy  # noqa: E402
from search.search_coordinator import SearchCoordinatorConfig  # noqa: E402
from verify_restore_bootstrap_phase3b import _make_eligible  # noqa: E402

import search.multi_round_search as multi_round_module  # noqa: E402
import search.search_coordinator as coordinator_module  # noqa: E402


def _simple_spec(hand=None, draw_pile=None, enemy_hp=999):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else [],
        "draw_pile": draw_pile if draw_pile is not None else [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _liquid_memories_spec():
    spec = _simple_spec(hand=[], enemy_hp=999)
    spec["discard_pile"] = ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]
    spec["potions"] = [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}]
    return spec


def _semantic_action_for(action: dict) -> SemanticAction:
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


def _representative_signature(state: BattleState) -> DecisionSignature:
    action = state._cached_legal_actions[0]  # noqa: SLF001
    return DecisionSignature.from_battle_state(state, semantic_action=_semantic_action_for(action), resolved_action=action)


def _eligible_root_snapshot(session: LiveCombatSession) -> CombatStateSnapshot:
    ensure_loaded()
    from System.Text.Json import JsonSerializer  # noqa: PLC0415

    eligible = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    return CombatStateSnapshot.from_json(str(JsonSerializer.Serialize(eligible)))


def _context(spec=None) -> DecisionContext:
    session = LiveCombatSession()
    state = session.start_combat(spec or _simple_spec())
    return DecisionContext.from_main_stable_capture(_eligible_root_snapshot(session), state, _representative_signature(state))


def _observation() -> OrderMaskedObservation:
    return OrderMaskedObservation(
        hand_size=0,
        draw_pile_size=0,
        discard_pile_size=0,
        exhaust_pile_size=0,
        play_pile_size=0,
        alive_enemy_count=1,
        player_energy=3,
        hand_card_id_counts={},
        hand_card_type_counts={},
        draw_pile_card_id_counts={},
        discard_pile_card_id_counts={},
        exhaust_pile_card_id_counts={},
        pending_choice_type=None,
        pending_min_select=None,
        pending_max_select=None,
        pending_selected_count=0,
    )


def _candidate(
    context: DecisionContext,
    *,
    score: float,
    target_index: int | None = 0,
    action_type: str = "system",
    semantic_key="",
    card_id=None,
):
    if card_id is not None and not semantic_key:
        semantic_key = f"0:{card_id}"
    return PipelineCandidateRef(
        current_context_signature=context.current_context_signature,
        semantic_action=SemanticAction(action_type, semantic_key),
        target_index=target_index,
        score=score,
        choice_kind=action_type,
        choice_scope="TopLevel",
    )


def _pipeline_for(context: DecisionContext, refs: list[PipelineCandidateRef]):
    return CandidatePipelineSuccess(
        continuation_candidate=refs[0],
        sub_branch_candidates=refs[1:],
        ranked_candidates=[],
        pruned_candidates=[],
        observation=_observation(),
    )


def _stable_signature(base: DecisionSignature, semantic_action: SemanticAction, *, target_index=None) -> DecisionSignature:
    return dataclasses.replace(
        base,
        semantic_action=semantic_action,
        resolved_semantic_key=semantic_action.semantic_key,
        resolved_target_index=target_index,
        boundary=BOUNDARY_STABLE,
        choice_scope=None,
        choice_kind=None,
        candidate_semantic_keys=None,
    )


def _terminal_signature(base: DecisionSignature, semantic_action: SemanticAction, *, target_index=None) -> DecisionSignature:
    return dataclasses.replace(
        _stable_signature(base, semantic_action, target_index=target_index),
        boundary=BOUNDARY_TERMINAL,
    )


def _fake_success_dispatch(work_items, lease_registry, *, worker_pool):
    results = []
    for work_item in work_items:
        signature = _stable_signature(
            work_item.decision_context.current_context_signature,
            work_item.candidate.semantic_action,
            target_index=work_item.candidate.target_index,
        )
        results.append(
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=signature,
                child_snapshot={"work_id": work_item.work_id},
            )
        )
    return results


def test_real_worker_hypothesis_boundary_terminates_without_prev_child_continuation():
    context = _context(_simple_spec(hand=["WHIRLWIND"], draw_pile=[], enemy_hp=999))

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        strategy = build_beam_search_strategy(
            pool,
            config=BeamSearchConfig(
                coordinator=SearchCoordinatorConfig(width=1, hypothesis_count=2, max_retries=0, request_timeout_s=120.0),
                beam_width=1,
                max_rounds=3,
            ),
            lease_registry=LeaseRegistry(),
        )
        result = strategy(context)

    assert isinstance(result, SearchSuccess), result
    assert len(result.planned_sequence) == 1
    assert result.planned_sequence[0].semantic_action.action_type == "card"
    assert result.planned_sequence[0].expected_signature is None


def test_beam_pruning_keeps_only_top_width_for_next_round():
    context = _context()
    loaded = []
    original_pipeline = multi_round_module.build_candidate_pipeline_result
    original_dispatch = coordinator_module.dispatch_work_items

    def _pipeline(decision_context, *, width):
        if decision_context is context:
            refs = [
                _candidate(decision_context, score=30.0, target_index=0),
                _candidate(decision_context, score=20.0, target_index=1),
                _candidate(decision_context, score=10.0, target_index=2),
            ]
            return _pipeline_for(decision_context, refs)
        return NoViableCandidates("second round stop", [], _observation())

    def _loader(snapshot):
        loaded.append(snapshot)
        return context.current_decision_result

    multi_round_module.build_candidate_pipeline_result = _pipeline
    coordinator_module.dispatch_work_items = _fake_success_dispatch
    try:
        strategy = build_beam_search_strategy(
            object(),
            config=BeamSearchConfig(coordinator=SearchCoordinatorConfig(width=3, max_retries=0), beam_width=2, max_rounds=2),
            lease_registry=LeaseRegistry(),
            snapshot_state_loader=_loader,
        )
        result = strategy(context)
    finally:
        multi_round_module.build_candidate_pipeline_result = original_pipeline
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert len(loaded) == 2
    assert len(result.planned_sequence) == 1
    assert result.planned_sequence[0].target_index == 0


def test_hypothesis_required_round_terminates_chain_as_completed_candidate():
    context = _context(_simple_spec(hand=["STRIKE_IRONCLAD"], draw_pile=["DEFEND_IRONCLAD"]))
    original_pipeline = multi_round_module.build_candidate_pipeline_result
    original_dispatch = coordinator_module.dispatch_work_items

    def _pipeline(decision_context, *, width):
        return _pipeline_for(
            decision_context,
            [_candidate(decision_context, score=25.0, action_type="card", card_id="STRIKE_IRONCLAD")],
        )

    coordinator_module.dispatch_work_items = _fake_success_dispatch
    multi_round_module.build_candidate_pipeline_result = _pipeline
    try:
        strategy = build_beam_search_strategy(
            object(),
            config=BeamSearchConfig(
                coordinator=SearchCoordinatorConfig(width=1, hypothesis_count=2, max_retries=0),
                beam_width=1,
                max_rounds=3,
            ),
            lease_registry=LeaseRegistry(),
        )
        result = strategy(context)
    finally:
        multi_round_module.build_candidate_pipeline_result = original_pipeline
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert len(result.planned_sequence) == 1
    assert result.planned_sequence[0].semantic_action.action_type == "card"
    assert result.planned_sequence[0].expected_signature is None


def test_terminal_mid_chain_is_completed_and_considered_final():
    context = _context()
    original_pipeline = multi_round_module.build_candidate_pipeline_result
    original_dispatch = coordinator_module.dispatch_work_items

    def _pipeline(decision_context, *, width):
        return _pipeline_for(decision_context, [_candidate(decision_context, score=9.0)])

    def _terminal_dispatch(work_items, lease_registry, *, worker_pool):
        work_item = work_items[0]
        signature = _terminal_signature(
            work_item.decision_context.current_context_signature,
            work_item.candidate.semantic_action,
            target_index=work_item.candidate.target_index,
        )
        return [
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=signature,
                terminal_result=BranchTerminalResult(is_terminal=True, outcome="Victory"),
            )
        ]

    multi_round_module.build_candidate_pipeline_result = _pipeline
    coordinator_module.dispatch_work_items = _terminal_dispatch
    try:
        strategy = build_beam_search_strategy(
            object(),
            config=BeamSearchConfig(coordinator=SearchCoordinatorConfig(width=1, max_retries=0), beam_width=1, max_rounds=3),
            lease_registry=LeaseRegistry(),
        )
        result = strategy(context)
    finally:
        multi_round_module.build_candidate_pipeline_result = original_pipeline
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert len(result.planned_sequence) == 1
    assert result.planned_sequence[0].expected_signature.boundary == BOUNDARY_TERMINAL


def test_real_pending_mid_chain_continues_with_holder_and_sibling_replay():
    context = _context(_liquid_memories_spec())
    original_requires_hypothesis = multi_round_module._requires_hypothesis  # noqa: SLF001
    original_dispatch = coordinator_module.dispatch_work_items
    dispatch_calls = []

    def _dispatch(work_items, lease_registry, *, worker_pool):
        results = original_dispatch(work_items, lease_registry, worker_pool=worker_pool)
        dispatch_calls.append(
            [
                (
                    item.work_kind,
                    result.execution_mode,
                    result.worker_id,
                    result.result_signature.boundary if result.result_signature is not None else None,
                    item.candidate.semantic_action.action_type,
                    item.candidate.semantic_action.semantic_key,
                )
                for item, result in zip(work_items, results)
            ]
        )
        return results

    multi_round_module._requires_hypothesis = lambda _context, _candidates: False  # noqa: SLF001
    coordinator_module.dispatch_work_items = _dispatch
    try:
        with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
            strategy = build_beam_search_strategy(
                pool,
                config=BeamSearchConfig(
                    coordinator=SearchCoordinatorConfig(width=8, max_retries=0, request_timeout_s=120.0),
                    beam_width=1,
                    max_rounds=2,
                ),
                lease_registry=LeaseRegistry(),
            )
            result = strategy(context)
    finally:
        multi_round_module._requires_hypothesis = original_requires_hypothesis  # noqa: SLF001
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert len(dispatch_calls) == 2
    assert len(result.planned_sequence) == 2
    assert result.planned_sequence[0].expected_signature.boundary == BOUNDARY_PENDING
    assert dispatch_calls[0][0][3] == BOUNDARY_PENDING
    assert any(call[1] == EXECUTION_MODE_HOLDER_STEP for call in dispatch_calls[1]), dispatch_calls
    assert any(call[0] == "sub_branch" and call[1] == EXECUTION_MODE_BOOTSTRAP_STEP for call in dispatch_calls[1]), dispatch_calls


def test_zero_viable_candidates_on_first_round_returns_failure():
    context = _context()
    strategy = build_beam_search_strategy(
        object(),
        config=BeamSearchConfig(coordinator=SearchCoordinatorConfig(width=0), beam_width=1, max_rounds=2),
        lease_registry=LeaseRegistry(),
        snapshot_state_loader=lambda _snapshot: context.current_decision_result,
    )
    result = strategy(context)

    assert isinstance(result, SearchEvaluationFailure), result
    assert "zero viable Root Actions" in result.detail


def test_max_rounds_bounds_search_and_keeps_expected_signatures():
    context = _context()
    original_pipeline = multi_round_module.build_candidate_pipeline_result
    original_dispatch = coordinator_module.dispatch_work_items
    dispatch_calls = []

    def _pipeline(decision_context, *, width):
        return _pipeline_for(decision_context, [_candidate(decision_context, score=8.0)])

    def _dispatch(work_items, lease_registry, *, worker_pool):
        dispatch_calls.append(list(work_items))
        return _fake_success_dispatch(work_items, lease_registry, worker_pool=worker_pool)

    multi_round_module.build_candidate_pipeline_result = _pipeline
    coordinator_module.dispatch_work_items = _dispatch
    try:
        strategy = build_beam_search_strategy(
            object(),
            config=BeamSearchConfig(coordinator=SearchCoordinatorConfig(width=1, max_retries=0), beam_width=1, max_rounds=2),
            lease_registry=LeaseRegistry(),
            snapshot_state_loader=lambda _snapshot: context.current_decision_result,
        )
        result = strategy(context)
    finally:
        multi_round_module.build_candidate_pipeline_result = original_pipeline
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert len(dispatch_calls) == 2
    assert len(result.planned_sequence) == 2
    assert all(step.expected_signature is not None for step in result.planned_sequence)


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(_run_all())
