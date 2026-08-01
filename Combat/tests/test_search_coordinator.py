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
from search.decision_context import BOUNDARY_PENDING, DecisionContext, DecisionSignature, SemanticAction  # noqa: E402
from search.main_loop import (  # noqa: E402
    ROUTE_SEARCH,
    CombatTerminalOutcome,
    SearchEvaluationFailure,
    SearchSuccess,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    run_until_terminal_or_fault,
)
import search.search_coordinator as coordinator_module  # noqa: E402
from search.search_coordinator import (  # noqa: E402
    SearchCoordinatorConfig,
    _strip_known_benign_dangling_entries,
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


def _semantic_action_for(action: dict) -> SemanticAction:
    params = action.get("parameters") or {}
    return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))


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


def _deck_multiset(*card_ids: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card_id in card_ids:
        counts[card_id] = counts.get(card_id, 0) + 1
    return counts


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
            combat_start_deck_multiset=_deck_multiset("WHIRLWIND"),
            lease_registry=registry,
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


def test_sanitizer_strips_only_known_benign_dangling_card_drawn_entries():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(hand=["WHIRLWIND"], enemy_hp=48))
    snapshot = session.capture_snapshot()
    report = validate_snapshot_references(snapshot)
    matched_indices = {
        int(dangling.field_path.split("CombatHistory.Entries[", 1)[1].split("]", 1)[0])
        for dangling in report.dangling_references
        if dangling.entry_type == "CardDrawnEntry" and dangling.cause == "source_live_state_inconsistency"
    }

    assert report.dangling_references
    assert len(matched_indices) > 0
    assert len(matched_indices) <= len(snapshot.CombatHistory.Entries)
    assert all(
        dangling.entry_type == "CardDrawnEntry" and dangling.cause == "source_live_state_inconsistency"
        for dangling in report.dangling_references
    )

    sentinel = CombatHistoryEntrySnapshot(
        EntryType="CardGeneratedEntry",
        RoundNumber=snapshot.RoundNumber,
        CurrentSide=snapshot.CurrentSide,
        PlayerTurnNumbers={},
        Fields={"CardId": "SENTINEL_CARD"},
    )
    snapshot_with_sentinel = dataclasses.replace(
        snapshot,
        CombatHistory=dataclasses.replace(
            snapshot.CombatHistory,
            Entries=[*snapshot.CombatHistory.Entries, sentinel],
        ),
    )

    sanitized = _strip_known_benign_dangling_entries(snapshot_with_sentinel)
    eligible, reasons = restore_input_eligibility(sanitized)

    assert eligible, reasons
    assert sanitized is not snapshot_with_sentinel
    assert snapshot_with_sentinel.CombatHistory.Entries[-1] is sentinel
    assert sanitized.CombatHistory.Entries[-1] is sentinel
    assert len(sanitized.CombatHistory.Entries) == len(snapshot_with_sentinel.CombatHistory.Entries) - len(matched_indices)


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
                combat_start_deck_multiset=_deck_multiset("WHIRLWIND"),
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
            combat_start_deck_multiset={},
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
            combat_start_deck_multiset=_deck_multiset("WHIRLWIND"),
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
            combat_start_deck_multiset={},
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
