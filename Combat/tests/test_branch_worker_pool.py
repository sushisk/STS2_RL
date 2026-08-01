"""Tests for Combat/search/branch_worker_pool.py - Phase 5 Branch Worker Pool.

Native assertion runner, no pytest dependency. Unit tests use fake worker execution for
fast routing checks; the end-to-end smoke test spawns real worker processes with real
``LiveCombatSession`` instances. Run:
cd C:\\STS2_RL\\Combat\\tests && python test_branch_worker_pool.py
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
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    BranchResult,
    BranchTerminalResult,
    BranchWorkerPool,
    Lease,
    LeaseRegistry,
    WorkItem,
    decision_result_digest,
    derive_context_id,
    dispatch_work_items,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    BOUNDARY_TERMINAL,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
    boundary_of_battle_state,
)
from verify_restore_bootstrap_phase3b import _make_eligible  # noqa: E402


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
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


def _eligible_root_snapshot(session: LiveCombatSession):
    return _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001


def _representative_signature(state: BattleState) -> DecisionSignature:
    action = state._cached_legal_actions[0]  # noqa: SLF001
    return DecisionSignature.from_battle_state(state, semantic_action=_semantic_action_for(action), resolved_action=action)


def _context_and_pipeline(enemy_hp=999, width=2, hand=None):
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=enemy_hp, hand=hand or ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"]))
    context = DecisionContext.from_main_stable_capture(_eligible_root_snapshot(session), state, _representative_signature(state))
    pipeline = build_candidate_pipeline_result(context, width=width)
    assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
    return context, pipeline


def _pending_signature(base: DecisionSignature) -> DecisionSignature:
    return dataclasses.replace(
        base,
        boundary=BOUNDARY_PENDING,
        choice_scope="TopLevel",
        choice_kind="SyntheticPendingForRoutingTest",
        candidate_semantic_keys=((("system", None, None), 1),),
    )


def test_work_item_from_candidate_ref_derives_stable_context_identity():
    context, pipeline = _context_and_pipeline(width=2)
    first = WorkItem.from_candidate_ref(context, pipeline.continuation_candidate, work_kind="continuation")
    second = WorkItem.from_candidate_ref(context, pipeline.continuation_candidate, work_kind="continuation")

    assert first.context_id == second.context_id
    assert first.context_id == derive_context_id(context)
    assert first.search_hypothesis_id is None
    assert first.work_kind == "continuation"


def test_lease_validity_checks_hypothesis_worker_generation_and_decision_result_identity():
    context, pipeline = _context_and_pipeline(width=2)
    item = WorkItem.from_candidate_ref(context, pipeline.continuation_candidate, work_kind="continuation")
    sig = context.current_context_signature
    lease = Lease(
        worker_id=1,
        worker_generation=3,
        context_id=item.context_id,
        search_hypothesis_id=None,
        state_epoch=7,
        combat_session_id=sig.combat_session_id,
        step_index=sig.step_index,
        decision_result_digest=decision_result_digest(sig),
    )

    assert lease.is_valid_for(item, worker_generation=3)
    assert not lease.is_valid_for(item, worker_generation=4)
    assert not dataclasses.replace(lease, search_hypothesis_id="H2").is_valid_for(item, worker_generation=3)
    assert not dataclasses.replace(lease, step_index=sig.step_index + 1).is_valid_for(item, worker_generation=3)
    stale_sig = dataclasses.replace(sig, resolved_card_id="A_DIFFERENT_CARD")
    assert not dataclasses.replace(lease, decision_result_digest=decision_result_digest(stale_sig)).is_valid_for(
        item, worker_generation=3
    )


def test_lease_registry_set_get_invalidate_and_worker_invalidation():
    registry = LeaseRegistry()
    lease_a = Lease(0, 1, "ctx-a", None, 1, "session-a", 2, "digest-a")
    lease_b = Lease(1, 1, "ctx-b", "H1", 1, "session-b", 3, "digest-b")

    registry.set(lease_a)
    registry.set(lease_b)
    assert registry.get("ctx-a", None) == lease_a
    assert registry.get("ctx-b", "H1") == lease_b
    assert registry.worker_ids_holding_leases() == {0, 1}

    registry.invalidate("ctx-a", None)
    assert registry.get("ctx-a", None) is None
    registry.invalidate_worker(1)
    assert registry.get("ctx-b", "H1") is None


def test_branch_result_invariant_for_stable_terminal_pending_and_fault():
    context, pipeline = _context_and_pipeline(width=2)
    item = WorkItem.from_candidate_ref(context, pipeline.continuation_candidate, work_kind="continuation")
    stable_sig = dataclasses.replace(context.current_context_signature, boundary=BOUNDARY_STABLE)
    terminal_sig = dataclasses.replace(context.current_context_signature, boundary=BOUNDARY_TERMINAL)
    pending_sig = _pending_signature(context.current_context_signature)
    lease = Lease(0, 1, "next", None, 1, pending_sig.combat_session_id, pending_sig.step_index, decision_result_digest(pending_sig))

    try:
        BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=item,
            execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
            worker_id=0,
            worker_generation=1,
            result_signature=stable_sig,
        )
        raise AssertionError("expected Stable success without child_snapshot to be rejected")
    except ValueError:
        pass

    BranchResult(
        status=BRANCH_STATUS_SUCCESS,
        work_item=item,
        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
        worker_id=0,
        worker_generation=1,
        result_signature=terminal_sig,
        terminal_result=BranchTerminalResult(True, "victory"),
    )
    BranchResult(
        status=BRANCH_STATUS_SUCCESS,
        work_item=item,
        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
        worker_id=0,
        worker_generation=1,
        result_signature=pending_sig,
        pending_decision_context=dataclasses.replace(context, current_context_signature=pending_sig),
        pending_pipeline_result=pipeline,
        established_lease=lease,
    )
    BranchResult(
        status=BRANCH_STATUS_FAULT,
        work_item=item,
        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
        worker_id=0,
        worker_generation=1,
        diagnostics={"fault_kind": "synthetic"},
    )


def test_dispatch_routing_uses_holder_for_valid_continuation_and_bootstrap_for_sub_branch():
    context, pipeline = _context_and_pipeline(width=2)
    context_id = derive_context_id(context)
    continuation = WorkItem.from_candidate_ref(
        context, pipeline.continuation_candidate, work_kind="continuation", context_id=context_id, work_id="cont"
    )
    sub = WorkItem.from_candidate_ref(
        context, pipeline.sub_branch_candidates[0], work_kind="sub_branch", context_id=context_id, work_id="sub"
    )
    lease = Lease(
        worker_id=1,
        worker_generation=4,
        context_id=context_id,
        search_hypothesis_id=None,
        state_epoch=5,
        combat_session_id=context.current_context_signature.combat_session_id,
        step_index=context.current_context_signature.step_index,
        decision_result_digest=decision_result_digest(context.current_context_signature),
    )
    registry = LeaseRegistry()
    registry.set(lease)
    calls = []

    def _fake_execute(worker_id, request):
        calls.append((worker_id, request.execution_mode, request.expected_lease))
        sig = dataclasses.replace(request.work_item.decision_context.current_context_signature, boundary=BOUNDARY_STABLE)
        return BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=request.work_item,
            execution_mode=request.execution_mode,
            worker_id=worker_id,
            worker_generation=4,
            result_signature=sig,
            child_snapshot=object(),
        )

    results = dispatch_work_items(
        [continuation, sub],
        registry,
        execute_request=_fake_execute,
        worker_ids=[0, 1],
        worker_generations={0: 4, 1: 4},
    )

    assert len(results) == 2
    assert calls[0] == (1, EXECUTION_MODE_HOLDER_STEP, lease)
    assert calls[1][1] == EXECUTION_MODE_BOOTSTRAP_STEP
    assert calls[1][0] == 0, "Sub Branch must avoid the leased holder when an unleased worker exists"
    assert registry.get(context_id, None) is None, "Stable results release the old Decision Context lease"


def test_dispatch_invalidates_stale_continuation_lease_and_bootstraps():
    context, pipeline = _context_and_pipeline(width=2)
    context_id = derive_context_id(context)
    continuation = WorkItem.from_candidate_ref(
        context, pipeline.continuation_candidate, work_kind="continuation", context_id=context_id, work_id="cont"
    )
    stale = Lease(
        worker_id=1,
        worker_generation=4,
        context_id=context_id,
        search_hypothesis_id=None,
        state_epoch=5,
        combat_session_id="not-this-session",
        step_index=context.current_context_signature.step_index,
        decision_result_digest=decision_result_digest(context.current_context_signature),
    )
    registry = LeaseRegistry()
    registry.set(stale)
    calls = []

    def _fake_execute(worker_id, request):
        calls.append((worker_id, request.execution_mode))
        sig = dataclasses.replace(request.work_item.decision_context.current_context_signature, boundary=BOUNDARY_STABLE)
        return BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=request.work_item,
            execution_mode=request.execution_mode,
            worker_id=worker_id,
            worker_generation=4,
            result_signature=sig,
            child_snapshot=object(),
        )

    dispatch_work_items(
        [continuation],
        registry,
        execute_request=_fake_execute,
        worker_ids=[0, 1],
        worker_generations={0: 4, 1: 4},
    )

    assert calls == [(0, EXECUTION_MODE_BOOTSTRAP_STEP)]
    assert registry.get(context_id, None) is None


def test_dispatch_pending_result_establishes_new_context_lease_and_next_continuation_reuses_holder():
    context, pipeline = _context_and_pipeline(width=2)
    context_id = derive_context_id(context)
    first = WorkItem.from_candidate_ref(
        context, pipeline.continuation_candidate, work_kind="continuation", context_id=context_id, work_id="first"
    )
    pending_sig = _pending_signature(context.current_context_signature)
    pending_context = dataclasses.replace(context, current_context_signature=pending_sig)
    next_context_id = "next-pending-context"
    next_lease = Lease(
        worker_id=0,
        worker_generation=1,
        context_id=next_context_id,
        search_hypothesis_id=None,
        state_epoch=1,
        combat_session_id=pending_sig.combat_session_id,
        step_index=pending_sig.step_index,
        decision_result_digest=decision_result_digest(pending_sig),
    )
    second = WorkItem.from_candidate_ref(
        pending_context,
        dataclasses.replace(pipeline.continuation_candidate, current_context_signature=pending_sig),
        work_kind="continuation",
        context_id=next_context_id,
        work_id="second",
    )
    calls = []

    def _fake_execute(worker_id, request):
        calls.append((worker_id, request.execution_mode))
        if request.work_item.work_id == "first":
            return BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=request.work_item,
                execution_mode=request.execution_mode,
                worker_id=worker_id,
                worker_generation=1,
                result_signature=pending_sig,
                pending_decision_context=pending_context,
                pending_pipeline_result=pipeline,
                established_lease=next_lease,
            )
        stable_sig = dataclasses.replace(pending_sig, boundary=BOUNDARY_STABLE, choice_scope=None, choice_kind=None, candidate_semantic_keys=None)
        return BranchResult(
            status=BRANCH_STATUS_SUCCESS,
            work_item=request.work_item,
            execution_mode=request.execution_mode,
            worker_id=worker_id,
            worker_generation=1,
            result_signature=stable_sig,
            child_snapshot=object(),
        )

    registry = LeaseRegistry()
    dispatch_work_items([first], registry, execute_request=_fake_execute, worker_ids=[0, 1], worker_generations={0: 1, 1: 1})
    assert registry.get(next_context_id, None) == next_lease
    dispatch_work_items([second], registry, execute_request=_fake_execute, worker_ids=[0, 1], worker_generations={0: 1, 1: 1})

    assert calls == [(0, EXECUTION_MODE_BOOTSTRAP_STEP), (0, EXECUTION_MODE_HOLDER_STEP)]
    assert registry.get(next_context_id, None) is None


def test_real_multiprocess_pool_bootstrap_steps_continuation_and_sub_branch_on_two_workers():
    context, pipeline = _context_and_pipeline(enemy_hp=999, width=2, hand=["DEFEND_IRONCLAD", "DEFEND_IRONCLAD"])
    context_id = derive_context_id(context)
    work_items = [
        WorkItem.from_candidate_ref(
            context, pipeline.continuation_candidate, work_kind="continuation", context_id=context_id, work_id="real-cont"
        ),
        WorkItem.from_candidate_ref(
            context, pipeline.sub_branch_candidates[0], work_kind="sub_branch", context_id=context_id, work_id="real-sub"
        ),
    ]

    registry = LeaseRegistry()
    with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
        results = pool.dispatch_work_items(work_items, registry)

    assert len(results) == 2
    assert {r.status for r in results} == {BRANCH_STATUS_SUCCESS}, [r.diagnostics for r in results]
    assert {r.execution_mode for r in results} == {EXECUTION_MODE_BOOTSTRAP_STEP}
    assert {r.worker_id for r in results} == {0, 1}
    assert all(r.result_signature.boundary in {BOUNDARY_STABLE, BOUNDARY_TERMINAL} for r in results)
    assert all((r.child_snapshot is not None) ^ (r.terminal_result is not None) for r in results)
    assert registry.worker_ids_holding_leases() == set()


def test_real_multiprocess_pool_can_return_terminal_result_without_snapshot():
    context, pipeline = _context_and_pipeline(enemy_hp=1, width=1)
    candidate = dataclasses.replace(pipeline.continuation_candidate, target_enemy_index=0)
    item = WorkItem.from_candidate_ref(
        context, candidate, work_kind="continuation", context_id=derive_context_id(context), work_id="terminal"
    )

    registry = LeaseRegistry()
    with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
        results = pool.dispatch_work_items([item], registry)

    result = results[0]
    assert result.status == BRANCH_STATUS_SUCCESS, result.diagnostics
    assert result.result_signature.boundary == BOUNDARY_TERMINAL
    assert result.terminal_result is not None
    assert result.terminal_result.outcome == "victory"
    assert result.child_snapshot is None


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
