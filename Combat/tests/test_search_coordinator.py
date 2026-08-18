"""Tests for Combat/search/search_coordinator.py - Phase 8 final assembly.

Native assertion runner, no pytest dependency. The end-to-end smoke uses the real
MainLoop + SearchCoordinator + BranchWorkerPool path; narrower wiring tests use real
DecisionContexts/Candidate Pipeline inputs and dependency-injected worker dispatch where
that keeps the assertion focused.
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
from combat_state_snapshot import (  # noqa: E402
    CombatHistoryEntrySnapshot,
    CombatStateSnapshot,
    restore_input_eligibility,
    validate_snapshot_references,
)
from emulator_bridge import ensure_loaded  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    BranchResult,
    BranchWorkerPool,
    Lease,
    LeaseRegistry,
    decision_result_digest,
    dispatch_work_items as phase5_dispatch_work_items,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import BOUNDARY_PENDING, CombatStartReplayRoot, DecisionContext, DecisionSignature, SemanticAction  # noqa: E402
from search.fault_taxonomy import SRC_MAIN_INVARIANT, WORK_ITEM_FINAL_FAULT  # noqa: E402
from search.main_loop import (  # noqa: E402
    ROUTE_SEARCH,
    CombatTerminalOutcome,
    SearchEvaluationFailure,
    SearchSuccess,
    build_main_decision_context,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    run_until_terminal_or_fault,
)
import search.search_coordinator as coordinator_module  # noqa: E402
from search.search_coordinator import (  # noqa: E402
    MainInvariantViolatedError,
    SearchCoordinatorConfig,
    build_search_strategy,
)
from verify_restore_bootstrap_phase3b import _make_eligible  # noqa: E402


def _simple_spec(hand=None, draw_pile=None, enemy_hp=48):
    cards = hand if hand is not None else ["WHIRLWIND"]
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": cards,
        "draw_pile": draw_pile if draw_pile is not None else [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _simple_spec_with_relics(hand=None, draw_pile=None, relics=None, enemy_hp=48):
    spec = _simple_spec(hand=hand, draw_pile=draw_pile, enemy_hp=enemy_hp)
    spec["relics"] = relics if relics is not None else []
    return spec


def _toolbox_pending_spec():
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "deck": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "player_powers": [],
        "relics": ["TOOLBOX"],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _semantic_action_for(action: dict) -> SemanticAction:
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


def _representative_signature(state: BattleState) -> DecisionSignature:
    action = state._cached_legal_actions[0]  # noqa: SLF001
    return DecisionSignature.from_battle_state(state, semantic_action=_semantic_action_for(action), resolved_action=action)


def _eligible_root_snapshot(session: LiveCombatSession):
    ensure_loaded()
    from System.Text.Json import JsonSerializer  # noqa: PLC0415

    eligible = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    return CombatStateSnapshot.from_json(str(JsonSerializer.Serialize(eligible)))


def _context(spec: dict) -> DecisionContext:
    session = LiveCombatSession()
    state = session.start_combat(spec)
    return DecisionContext.from_main_stable_capture(_eligible_root_snapshot(session), state, _representative_signature(state))


def _main_loop_state_with_held_snapshot(spec: dict):
    session = LiveCombatSession()
    state = session.start_combat(spec)
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = _eligible_root_snapshot(session)
    loop_state.replay_prefix = []
    return loop_state


def _deck_multiset(*card_ids: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card_id in card_ids:
        counts[card_id] = counts.get(card_id, 0) + 1
    return counts


def _with_synthetic_dangling_draw(snapshot: CombatStateSnapshot) -> CombatStateSnapshot:
    dangling_draw = CombatHistoryEntrySnapshot(
        EntryType="CardDrawnEntry",
        RoundNumber=snapshot.RoundNumber,
        CurrentSide=snapshot.CurrentSide,
        PlayerTurnNumbers={},
        Fields={"cardInstanceId": "SYNTHETIC_DANGLING_DRAWN_CARD", "fromHandDraw": True},
    )
    return dataclasses.replace(
        snapshot,
        CombatHistory=dataclasses.replace(
            snapshot.CombatHistory,
            Entries=[*snapshot.CombatHistory.Entries, dangling_draw],
        ),
    )


def test_end_to_end_main_loop_invokes_real_search_and_reaches_terminal():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["WHIRLWIND"], enemy_hp=1))
    loop_state = initialize_main_loop_state(session, state)
    registry = LeaseRegistry()
    calls = []

    with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
        strategy = build_search_strategy(
            pool,
            config=SearchCoordinatorConfig(width=1, hypothesis_count=2),
            lease_registry=registry,
            main_state_provider=lambda: loop_state,
        )

        def _tracked_strategy(context):
            calls.append(context)
            return strategy(context)

        outcome = run_until_terminal_or_fault(
            loop_state,
            direct_selector=first_candidate_direct_selector,
            search_strategy=_tracked_strategy,
            routing_policy=lambda boundary: ROUTE_SEARCH,
            max_iterations=10,
        )

    assert calls, "SearchStrategy must be invoked from the real Stable main-loop boundary"
    assert isinstance(outcome, CombatTerminalOutcome), outcome
    assert outcome.final_state.is_terminal


def test_fresh_root_snapshot_is_eligible_and_synthetic_dangling_history_faults_search():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["WHIRLWIND"], enemy_hp=48))
    fresh_snapshot = session.capture_snapshot()
    fresh_report = validate_snapshot_references(fresh_snapshot)
    fresh_eligible, fresh_reasons = restore_input_eligibility(fresh_snapshot)

    assert fresh_report.dangling_references == []
    assert fresh_eligible, fresh_reasons

    invalid_snapshot = _with_synthetic_dangling_draw(fresh_snapshot)
    invalid_report = validate_snapshot_references(invalid_snapshot)
    invalid_eligible, invalid_reasons = restore_input_eligibility(invalid_snapshot)

    assert len(invalid_report.dangling_references) == 1
    assert invalid_report.dangling_references[0].entry_type == "CardDrawnEntry"
    assert invalid_report.dangling_references[0].cause == "source_live_state_inconsistency"
    assert invalid_eligible is False
    assert any("dangling_references=1" in reason for reason in invalid_reasons)

    context = DecisionContext.from_main_stable_capture(
        invalid_snapshot,
        state,
        _representative_signature(state),
    )
    captured_branch_results = []
    original_dispatch = coordinator_module.dispatch_work_items

    def _capturing_real_dispatch(work_items, lease_registry, *, worker_pool):
        results = original_dispatch(work_items, lease_registry, worker_pool=worker_pool)
        captured_branch_results.extend(results)
        return results

    coordinator_module.dispatch_work_items = _capturing_real_dispatch
    try:
        with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
            strategy = build_search_strategy(
                pool,
                config=SearchCoordinatorConfig(width=1, max_retries=0),
                lease_registry=LeaseRegistry(),
            )
            result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchEvaluationFailure), result
    assert any(branch.status == BRANCH_STATUS_FAULT for branch in captured_branch_results), captured_branch_results
    assert any(
        branch.diagnostics.get("fault_kind") in {"validation_rejection", "worker_exception"}
        for branch in captured_branch_results
    ), [branch.diagnostics for branch in captured_branch_results]


def test_search_strategy_without_main_state_provider_keeps_backward_compatible_commit():
    context = _context(_simple_spec(hand=[], enemy_hp=999))
    registry = LeaseRegistry()
    original_dispatch = coordinator_module.dispatch_work_items

    def _single_success(work_items, lease_registry, *, worker_pool):
        work_item = work_items[0]
        return [
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=work_item.decision_context.current_context_signature,
                child_snapshot=object(),
            )
        ]

    coordinator_module.dispatch_work_items = _single_success
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1),
            lease_registry=registry,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result


def test_search_strategy_with_real_main_state_provider_passes_when_unchanged():
    loop_state = _main_loop_state_with_held_snapshot(_simple_spec(hand=[], enemy_hp=999))
    context = build_main_decision_context(loop_state)
    registry = LeaseRegistry()
    original_dispatch = coordinator_module.dispatch_work_items

    def _single_success(work_items, lease_registry, *, worker_pool):
        work_item = work_items[0]
        return [
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=work_item.decision_context.current_context_signature,
                child_snapshot=object(),
            )
        ]

    coordinator_module.dispatch_work_items = _single_success
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1),
            lease_registry=registry,
            main_state_provider=lambda: loop_state,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result


def test_search_strategy_raises_main_invariant_error_when_state_identity_changes():
    original_loop_state = _main_loop_state_with_held_snapshot(_simple_spec(hand=[], enemy_hp=999))
    changed_loop_state = _main_loop_state_with_held_snapshot(_simple_spec(hand=[], enemy_hp=999))
    context = build_main_decision_context(original_loop_state)
    registry = LeaseRegistry()
    original_dispatch = coordinator_module.dispatch_work_items

    def _single_success(work_items, lease_registry, *, worker_pool):
        work_item = work_items[0]
        return [
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=work_item.decision_context.current_context_signature,
                child_snapshot=object(),
            )
        ]

    coordinator_module.dispatch_work_items = _single_success
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1),
            lease_registry=registry,
            main_state_provider=lambda: changed_loop_state,
        )
        try:
            strategy(context)
        except MainInvariantViolatedError as exc:
            error = exc
        else:
            raise AssertionError("MainInvariantViolatedError was not raised")
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert error.outcome.fault_source == SRC_MAIN_INVARIANT
    assert "state_identity" in str(error)
    assert "state_identity" in error.check_result.mismatches
    assert error.outcome.diagnostics["main_invariant"]["expected_state_identity"] != error.outcome.diagnostics[
        "main_invariant"
    ]["live_state_identity"]


def test_combat_start_main_invariant_accepts_absent_held_snapshot_and_rejects_capture():
    spec = _toolbox_pending_spec()
    session = LiveCombatSession()
    state = session.start_combat(spec)
    context = DecisionContext.from_combat_start_pending(
        CombatStartReplayRoot(spec),
        state,
        _representative_signature(state),
    )
    loop_state = initialize_main_loop_state(session, state, combat_start_replay_root=CombatStartReplayRoot(spec))

    ok = coordinator_module._check_main_invariant(context, lambda: loop_state)  # noqa: SLF001
    assert ok.ok is True, ok
    assert ok.mismatches == ()

    loop_state.held_stable_snapshot = object()
    bad = coordinator_module._check_main_invariant(context, lambda: loop_state)  # noqa: SLF001
    assert bad.ok is False, bad
    assert "held_snapshot" in bad.mismatches


def test_hypothesis_path_builds_distinct_hypothesis_work_items_and_commit_first_only():
    context = _context(_simple_spec(hand=["WHIRLWIND"], enemy_hp=999))
    registry = LeaseRegistry()
    captured = {}
    original_dispatch = coordinator_module.dispatch_work_items

    def _capturing_dispatch(work_items, lease_registry, *, worker_pool):
        captured["work_items"] = list(work_items)
        results = original_dispatch(work_items, lease_registry, worker_pool=worker_pool)
        captured["branch_results"] = list(results)
        return results

    with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
        coordinator_module.dispatch_work_items = _capturing_dispatch
        try:
            strategy = build_search_strategy(
                pool,
                config=SearchCoordinatorConfig(width=1, hypothesis_count=2),
                lease_registry=registry,
            )
            result = strategy(context)
        finally:
            coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), [
        branch_result.diagnostics for branch_result in captured.get("branch_results", [])
    ]
    work_items = captured["work_items"]
    hypothesis_ids = {item.search_hypothesis_id for item in work_items}
    assert None not in hypothesis_ids
    assert len(hypothesis_ids) == 2
    assert len(work_items) == 2  # 1 pruned card candidate x 2 hypotheses
    assert result.planned_sequence[0].expected_signature is None


def test_hypothesis_entries_include_public_multiset_coverage_diagnostics():
    context = _context(
        _simple_spec_with_relics(
            hand=["STRIKE_IRONCLAD"],
            draw_pile=["BASH"],
            relics=["BIIIG_HUG"],
            enemy_hp=999,
        )
    )
    captured_entries = {}
    original_aggregate = coordinator_module.aggregate_hypothesis_results

    def _capturing_hypothesis_aggregate(entries, *, min_coverage_fraction):
        captured_entries["entries"] = list(entries)
        return original_aggregate(entries, min_coverage_fraction=min_coverage_fraction)

    coordinator_module.aggregate_hypothesis_results = _capturing_hypothesis_aggregate
    try:
        with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
            strategy = build_search_strategy(
                pool,
                config=SearchCoordinatorConfig(width=1, hypothesis_count=2),
                lease_registry=LeaseRegistry(),
            )
            result = strategy(context)
    finally:
        coordinator_module.aggregate_hypothesis_results = original_aggregate

    assert isinstance(result, SearchSuccess), result
    diagnostics = [entry.diagnostics["public_multiset_coverage"] for entry in captured_entries["entries"]]
    assert diagnostics
    assert all(item["is_complete"] is True for item in diagnostics)
    assert all(item["uncertain_sources"] == [] for item in diagnostics)
    assert all("Player.CardInstances" in item["reason"] for item in diagnostics)


def test_passthrough_path_returns_expected_signature_from_real_worker_result():
    context = _context(_simple_spec(hand=[], enemy_hp=999))
    registry = LeaseRegistry()
    original_dispatch = coordinator_module.dispatch_work_items

    def _single_success(work_items, lease_registry, *, worker_pool):
        work_item = work_items[0]
        return [
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=work_item.decision_context.current_context_signature,
                child_snapshot=object(),
            )
        ]

    coordinator_module.dispatch_work_items = _single_success
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1),
            lease_registry=registry,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert len(result.planned_sequence) == 1
    step = result.planned_sequence[0]
    assert step.semantic_action.action_type == "system"
    assert step.expected_signature is not None


def test_no_viable_candidates_returns_search_evaluation_failure():
    context = _context(_simple_spec(hand=["WHIRLWIND"], enemy_hp=999))

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        strategy = build_search_strategy(
            pool,
            config=SearchCoordinatorConfig(width=0),
            lease_registry=LeaseRegistry(),
        )
        result = strategy(context)

    assert isinstance(result, SearchEvaluationFailure), result
    assert "no viable candidates" in result.detail


def test_shared_lease_registry_allows_holder_reuse_across_strategy_calls():
    context = _context(_simple_spec(hand=[], enemy_hp=999))
    registry = LeaseRegistry()
    calls = []
    original_dispatch = coordinator_module.dispatch_work_items

    def _fake_execute(worker_id, request):
        calls.append((worker_id, request.execution_mode, request.expected_lease))
        work_item = request.work_item
        stable_sig = work_item.decision_context.current_context_signature
        if len(calls) == 1:
            pending_sig = dataclasses.replace(
                stable_sig,
                boundary=BOUNDARY_PENDING,
                choice_scope="TopLevel",
                choice_kind="SyntheticPendingForCoordinatorLeaseTest",
                candidate_semantic_keys=((("system", None, None), 1),),
            )
            lease = Lease(
                worker_id=worker_id,
                worker_generation=1,
                context_id=work_item.context_id,
                search_hypothesis_id=work_item.search_hypothesis_id,
                state_epoch=1,
                combat_session_id=stable_sig.combat_session_id,
                step_index=stable_sig.step_index,
                decision_result_digest=decision_result_digest(stable_sig),
            )
            pipeline = build_candidate_pipeline_result(work_item.decision_context, width=1)
            assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
            return BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=request.execution_mode,
                worker_id=worker_id,
                worker_generation=1,
                result_signature=pending_sig,
                pending_decision_context=dataclasses.replace(
                    work_item.decision_context,
                    current_context_signature=pending_sig,
                ),
                pending_pipeline_result=pipeline,
                established_lease=lease,
            )
        return BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=work_item,
            execution_mode=request.execution_mode,
            worker_id=worker_id,
            worker_generation=1,
            result_signature=stable_sig,
            child_snapshot=object(),
        )

    def _dispatch_via_fake_pool(work_items, lease_registry, *, worker_pool):
        return phase5_dispatch_work_items(
            work_items,
            lease_registry,
            execute_request=_fake_execute,
            worker_ids=[0],
            worker_generations={0: 1},
        )

    coordinator_module.dispatch_work_items = _dispatch_via_fake_pool
    try:
        strategy = build_search_strategy(
            object(),  # worker_pool is unused by the injected Phase-5 fake dispatcher.
            config=SearchCoordinatorConfig(width=1),
            lease_registry=registry,
        )
        first = strategy(context)
        assert isinstance(first, SearchSuccess), first
        assert registry.worker_ids_holding_leases() == {0}
        second = strategy(context)
        assert isinstance(second, SearchSuccess), second
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert calls[0][1] == EXECUTION_MODE_BOOTSTRAP_STEP
    assert calls[1][1] == EXECUTION_MODE_HOLDER_STEP
    assert calls[1][2] is not None


def test_retry_loop_resubmits_force_restart_fault_and_commits_retry_success():
    context = _context(_simple_spec(hand=[], enemy_hp=999))
    registry = LeaseRegistry()
    calls_by_work_id = {}
    original_dispatch = coordinator_module.dispatch_work_items

    def _fault_then_success(work_items, lease_registry, *, worker_pool):
        results = []
        for work_item in work_items:
            calls_by_work_id[work_item.work_id] = calls_by_work_id.get(work_item.work_id, 0) + 1
            if calls_by_work_id[work_item.work_id] == 1:
                results.append(
                    BranchResult(
                        status=BRANCH_STATUS_FAULT,
                        work_item=work_item,
                        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                        worker_id=0,
                        worker_generation=1,
                        diagnostics={"fault_kind": "worker_exception", "exception_type": "ActionExecutionError"},
                    )
                )
            else:
                results.append(
                    BranchResult(
                        status=BRANCH_STATUS_SUCCESS,
                        work_item=work_item,
                        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                        worker_id=0,
                        worker_generation=1,
                        result_signature=work_item.decision_context.current_context_signature,
                        child_snapshot=object(),
                    )
                )
        return results

    coordinator_module.dispatch_work_items = _fault_then_success
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1, max_retries=1),
            lease_registry=registry,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert set(calls_by_work_id.values()) == {2}
    assert result.planned_sequence[0].expected_signature is not None


def test_retry_loop_resubmits_reuse_safe_validation_rejection():
    context = _context(_simple_spec(hand=[], enemy_hp=999))
    registry = LeaseRegistry()
    calls_by_work_id = {}
    original_dispatch = coordinator_module.dispatch_work_items

    def _validation_rejection_then_success(work_items, lease_registry, *, worker_pool):
        work_item = work_items[0]
        calls_by_work_id[work_item.work_id] = calls_by_work_id.get(work_item.work_id, 0) + 1
        if calls_by_work_id[work_item.work_id] == 1:
            return [
                BranchResult(
                    status=BRANCH_STATUS_FAULT,
                    work_item=work_item,
                    execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                    worker_id=0,
                    worker_generation=1,
                    diagnostics={"fault_kind": "validation_rejection"},
                )
            ]
        return [
            BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                result_signature=work_item.decision_context.current_context_signature,
                child_snapshot=object(),
            )
        ]

    coordinator_module.dispatch_work_items = _validation_rejection_then_success
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1, max_retries=1),
            lease_registry=registry,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), result
    assert set(calls_by_work_id.values()) == {2}


def test_retry_loop_final_fault_after_max_retries_is_excluded_from_plain_aggregation():
    context = _context(_simple_spec(hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], enemy_hp=999))
    registry = LeaseRegistry()
    calls_by_work_id = {}
    captured_entries = {}
    faulted_work_id = None
    original_dispatch = coordinator_module.dispatch_work_items
    original_aggregate = coordinator_module.aggregate_plain_results
    original_hypothesis_aggregate = coordinator_module.aggregate_hypothesis_results

    def _one_candidate_always_faults(work_items, lease_registry, *, worker_pool):
        nonlocal faulted_work_id
        if faulted_work_id is None:
            faulted_work_id = work_items[0].work_id
        results = []
        for work_item in work_items:
            calls_by_work_id[work_item.work_id] = calls_by_work_id.get(work_item.work_id, 0) + 1
            if work_item.work_id == faulted_work_id:
                results.append(
                    BranchResult(
                        status=BRANCH_STATUS_FAULT,
                        work_item=work_item,
                        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                        worker_id=0,
                        worker_generation=1,
                        diagnostics={"fault_kind": "worker_exception", "exception_type": "ActionExecutionError"},
                    )
                )
            else:
                results.append(
                    BranchResult(
                        status=BRANCH_STATUS_SUCCESS,
                        work_item=work_item,
                        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                        worker_id=1,
                        worker_generation=1,
                        result_signature=work_item.decision_context.current_context_signature,
                        child_snapshot=object(),
                    )
                )
        return results

    def _capturing_aggregate(entries):
        captured_entries["entries"] = list(entries)
        return original_aggregate(entries)

    def _capturing_hypothesis_aggregate(entries, *, min_coverage_fraction):
        captured_entries["entries"] = list(entries)
        return original_hypothesis_aggregate(entries, min_coverage_fraction=min_coverage_fraction)

    coordinator_module.dispatch_work_items = _one_candidate_always_faults
    coordinator_module.aggregate_plain_results = _capturing_aggregate
    coordinator_module.aggregate_hypothesis_results = _capturing_hypothesis_aggregate
    try:
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=2, max_retries=1),
            lease_registry=registry,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch
        coordinator_module.aggregate_plain_results = original_aggregate
        coordinator_module.aggregate_hypothesis_results = original_hypothesis_aggregate

    assert isinstance(result, SearchSuccess), result
    assert calls_by_work_id[faulted_work_id] == 2
    fault_entry = next(entry for entry in captured_entries["entries"] if entry.work_id == faulted_work_id)
    assert fault_entry.work_item_state == WORK_ITEM_FINAL_FAULT
    assert captured_entries["entries"]


def test_retry_loop_defensive_cap_stays_above_decide_retry_bounded_rounds():
    context = _context(_simple_spec(hand=[], enemy_hp=999))
    registry = LeaseRegistry()
    calls = []
    original_dispatch = coordinator_module.dispatch_work_items

    def _always_fault(work_items, lease_registry, *, worker_pool):
        calls.append([work_item.work_id for work_item in work_items])
        return [
            BranchResult(
                status=BRANCH_STATUS_FAULT,
                work_item=work_item,
                execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
                worker_id=0,
                worker_generation=1,
                diagnostics={"fault_kind": "worker_exception", "exception_type": "ActionExecutionError"},
            )
            for work_item in work_items
        ]

    coordinator_module.dispatch_work_items = _always_fault
    try:
        max_retries = 3
        strategy = build_search_strategy(
            object(),
            config=SearchCoordinatorConfig(width=1, max_retries=max_retries),
            lease_registry=registry,
        )
        result = strategy(context)
    finally:
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchEvaluationFailure), result
    assert len(calls) == max_retries + 1


def test_real_worker_faults_are_retried_and_successful_candidates_still_commit():
    context = _context(_simple_spec(hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], enemy_hp=999))
    registry = LeaseRegistry()
    original_pipeline = coordinator_module.build_candidate_pipeline_result
    original_dispatch = coordinator_module.dispatch_work_items
    dispatch_rounds = []

    def _pipeline_with_invalid_sub_branch(decision_context, *, width):
        pipeline = original_pipeline(decision_context, width=width)
        assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
        invalid = dataclasses.replace(
            pipeline.continuation_candidate,
            semantic_action=SemanticAction("card", "0:NOT_A_REAL_CARD"),
            target_enemy_index=0,
            score=pipeline.continuation_candidate.score - 100.0,
        )
        return dataclasses.replace(pipeline, sub_branch_candidates=[invalid])

    def _capturing_real_dispatch(work_items, lease_registry, *, worker_pool):
        results = original_dispatch(work_items, lease_registry, worker_pool=worker_pool)
        dispatch_rounds.append([(result.work_item.work_id, result.status, dict(result.diagnostics)) for result in results])
        return results

    coordinator_module.build_candidate_pipeline_result = _pipeline_with_invalid_sub_branch
    coordinator_module.dispatch_work_items = _capturing_real_dispatch
    try:
        with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
            strategy = build_search_strategy(
                pool,
                config=SearchCoordinatorConfig(width=2, max_retries=1),
                lease_registry=registry,
            )
            result = strategy(context)
    finally:
        coordinator_module.build_candidate_pipeline_result = original_pipeline
        coordinator_module.dispatch_work_items = original_dispatch

    assert isinstance(result, SearchSuccess), (result, dispatch_rounds)
    assert len(dispatch_rounds) == 2
    assert any(status == BRANCH_STATUS_FAULT for _work_id, status, _diagnostics in dispatch_rounds[0])
    assert any(status == BRANCH_STATUS_FAULT for _work_id, status, _diagnostics in dispatch_rounds[1])


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
