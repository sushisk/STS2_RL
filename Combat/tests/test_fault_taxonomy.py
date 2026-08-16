"""Tests for Combat/search/fault_taxonomy.py - Phase 7 fault taxonomy + Commit.

Native assertion runner, no pytest dependency. Most checks are pure logic over Phase 5
``BranchResult``/``WorkItem`` fixtures; the final tests spawn the real Branch Worker Pool
to verify diagnostics produced by existing worker fault paths.
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
    BranchResult,
    BranchWorkerPool,
    LeaseRegistry,
    WorkItem,
    derive_context_id,
)
from search.candidate_pipeline import PipelineCandidateRef  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_STABLE,
    DecisionContext,
    DecisionSignature,
    ReplayPrefixEntry,
    SemanticAction,
)
from search.fault_taxonomy import (  # noqa: E402
    FAULT_ACTION_FAULT,
    FAULT_DEPTH_CAP_EXCEEDED,
    FAULT_POST_TEARDOWN_RESTORE_FAILURE,
    FAULT_REPLAY_MISMATCH,
    FAULT_TASK_TIMEOUT,
    FAULT_VALIDATION_REJECTION,
    FAULT_WORKER_PROCESS_CRASH,
    FAULT_ZERO_CANDIDATES,
    FORCE_RESTART,
    REUSE_SAFE,
    SRC_MAIN_INVARIANT,
    WORK_ITEM_FINAL_FAULT,
    WORK_ITEM_FINAL_SUCCESS,
    WORK_ITEM_RETRYING,
    WORK_ITEM_RUNNING,
    AggregationResult,
    BranchDecisionLogEntry,
    MainCombatFaultOutcome,
    WorkItemAttempt,
    aggregate_hypothesis_results,
    aggregate_plain_results,
    build_commit_decision,
    classify_fault,
    decide_retry,
    depth_cap_fault,
    to_decision_log_entry,
    worker_reuse_policy,
)
from search.main_loop import SearchEvaluationFailure, SearchSuccess  # noqa: E402
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
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


def _find_action(state: BattleState, action_type: str, card_id=None) -> dict:
    return next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001
        if action["action_type"] == action_type and (card_id is None or (action.get("parameters") or {}).get("cardId") == card_id)
    )


def _with_synthetic_dangling_draw(snapshot):
    from System import Array  # noqa: PLC0415
    from System import Int32, Object, String  # noqa: PLC0415
    from System.Collections.Generic import Dictionary  # noqa: PLC0415
    from Sts2Emulator.Dto.Snapshot import CombatHistoryEntrySnapshot  # noqa: PLC0415

    dangling_draw = CombatHistoryEntrySnapshot()
    dangling_draw.EntryType = "CardDrawnEntry"
    dangling_draw.RoundNumber = snapshot.RoundNumber
    dangling_draw.CurrentSide = snapshot.CurrentSide
    dangling_draw.PlayerTurnNumbers = Dictionary[String, Int32]()
    fields = Dictionary[String, Object]()
    fields["cardInstanceId"] = "SYNTHETIC_DANGLING_DRAWN_CARD"
    fields["fromHandDraw"] = True
    dangling_draw.Fields = fields
    snapshot.CombatHistory.Entries = Array[CombatHistoryEntrySnapshot]([*snapshot.CombatHistory.Entries, dangling_draw])
    return snapshot


def _sig(action: SemanticAction | None = None, *, boundary=BOUNDARY_STABLE) -> DecisionSignature:
    action = action or SemanticAction("card", "0:STRIKE_IRONCLAD")
    return DecisionSignature(
        combat_session_id="session-a",
        step_index=0,
        continuation_step_index=None,
        semantic_action=action,
        resolved_action_id=1,
        resolved_semantic_key=action.semantic_key,
        resolved_target_index=None,
        resolved_target_slot_index=0,
        boundary=boundary,
    )


def _dummy_context(signature: DecisionSignature | None = None, *, hypothesis_id=None, plan_path=None, replay_prefix=None):
    signature = signature or _sig()
    return DecisionContext(
        root_snapshot=object(),
        replay_prefix=list(replay_prefix or []),
        plan_path=list(plan_path or []),
        current_decision_result=object(),
        current_context_signature=signature,
        search_hypothesis_id=hypothesis_id,
    )


def _candidate(action: SemanticAction, score: float, signature: DecisionSignature | None = None) -> PipelineCandidateRef:
    return PipelineCandidateRef(
        current_context_signature=signature or _sig(action),
        semantic_action=action,
        target_enemy_index=0 if action.semantic_key == "SingleEnemy" else None,
        score=score,
        choice_kind=action.action_type,
        choice_scope="TopLevel",
    )


def _work_item(action: SemanticAction, score: float, *, hypothesis_id=None, work_id="work") -> WorkItem:
    signature = _sig(action)
    ctx = _dummy_context(signature, hypothesis_id=hypothesis_id)
    return WorkItem.from_candidate_ref(
        ctx,
        _candidate(action, score, signature),
        work_kind="continuation",
        context_id=f"ctx-{work_id}",
        work_id=work_id,
    )


def _success_log(root: str, hyp: str | None, score: float, *, work_id=None) -> BranchDecisionLogEntry:
    action = SemanticAction("card", root)
    return BranchDecisionLogEntry(
        status="success",
        root_action_key=root,
        hypothesis_id=hyp,
        plan_path=(),
        replay_prefix_digest="prefix",
        work_id=work_id or f"{root}-{hyp}",
        work_item_state=WORK_ITEM_FINAL_SUCCESS,
        score=score,
        semantic_action=action,
        target_enemy_index=0,
        result_signature=_sig(action),
    )


def _fault_log(root: str, hyp: str | None, *, state=WORK_ITEM_FINAL_FAULT, work_id=None) -> BranchDecisionLogEntry:
    return BranchDecisionLogEntry(
        status="action_fault",
        root_action_key=root,
        hypothesis_id=hyp,
        plan_path=(),
        replay_prefix_digest="prefix",
        work_id=work_id or f"{root}-{hyp}-fault",
        work_item_state=state,
        fault_kind=FAULT_ACTION_FAULT,
        diagnostics={"fault_kind": "worker_exception", "exception_type": "ActionExecutionError"},
    )


def test_classify_fault_maps_all_taxonomy_categories_from_realistic_diagnostics():
    """Some shapes below are projected Phase 7+ coordinator/worker-management shapes:
    Phase 5 naturally produces replay_mismatch and worker_exception diagnostics today,
    while timeout/process-crash/depth/zero-candidates are taxonomy targets for later
    integration points."""
    cases = [
        ({"fault_kind": "worker_exception", "exception_type": "SnapshotRestoreRejectedError"}, FAULT_VALIDATION_REJECTION),
        ({"fault_kind": "replay_mismatch", "stage": "signature"}, FAULT_REPLAY_MISMATCH),
        ({"fault_kind": "worker_exception", "exception_type": "SnapshotRestoreFailedError"}, FAULT_POST_TEARDOWN_RESTORE_FAILURE),
        ({"fault_kind": "worker_exception", "exception_type": "ActionExecutionError"}, FAULT_ACTION_FAULT),
        ({"fault_kind": "task_timeout"}, FAULT_TASK_TIMEOUT),
        ({"fault_kind": "worker_process_crash"}, FAULT_WORKER_PROCESS_CRASH),
        ({"fault_kind": "depth_cap_exceeded"}, FAULT_DEPTH_CAP_EXCEEDED),
        ({"fault_kind": "zero_candidates"}, FAULT_ZERO_CANDIDATES),
    ]
    for diagnostics, expected in cases:
        assert classify_fault(diagnostics) == expected


def test_worker_reuse_policy_matches_fault_contract_table():
    assert worker_reuse_policy(FAULT_VALIDATION_REJECTION) == REUSE_SAFE
    for fault_kind in (
        FAULT_REPLAY_MISMATCH,
        FAULT_POST_TEARDOWN_RESTORE_FAILURE,
        FAULT_ACTION_FAULT,
        FAULT_TASK_TIMEOUT,
        FAULT_WORKER_PROCESS_CRASH,
        FAULT_DEPTH_CAP_EXCEEDED,
        FAULT_ZERO_CANDIDATES,
    ):
        assert worker_reuse_policy(fault_kind) == FORCE_RESTART
    try:
        worker_reuse_policy(SRC_MAIN_INVARIANT)
        raise AssertionError("Main fault sources must not enter Branch Worker reuse policy")
    except ValueError:
        pass


def test_retry_decision_transitions_and_aggregation_rejects_retrying_entries():
    running = WorkItemAttempt(work_id="a", attempt_count=0, state=WORK_ITEM_RUNNING, worker_generation=1)
    retry = decide_retry(running, FAULT_ACTION_FAULT, max_retries=1)
    assert retry.next_state == WORK_ITEM_RETRYING
    assert retry.should_retry
    assert retry.worker_generation == 2

    retrying = WorkItemAttempt(work_id="a", attempt_count=1, state=WORK_ITEM_RETRYING, worker_generation=2)
    final = decide_retry(retrying, FAULT_ACTION_FAULT, max_retries=1)
    assert final.next_state == WORK_ITEM_FINAL_FAULT
    assert not final.should_retry
    assert final.worker_generation == 3

    try:
        aggregate_plain_results([_fault_log("A", None, state=WORK_ITEM_RETRYING)])
        raise AssertionError("Retrying entries must not be scored or silently ignored")
    except ValueError:
        pass


def test_aggregate_hypothesis_results_applies_root_only_grouping_coverage_and_pessimistic_fill():
    entries = [
        _success_log("A", "H1", 10.0),
        _success_log("A", "H2", 8.0),
        _fault_log("A", "H3"),
        _success_log("B", "H1", 100.0),
        _fault_log("B", "H2"),
        _fault_log("B", "H3"),
        _success_log("C", "H1", 7.0),
        _success_log("C", "H2", 7.0),
        _success_log("C", "H3", 7.0),
    ]

    result = aggregate_hypothesis_results(entries)

    assert result.diagnostics["total_hypotheses"] == 3
    assert result.diagnostics["required_valid_samples"] == 2
    assert result.diagnostics["worst_valid_score_all_roots"] == 7.0
    excluded_keys = {item["root_action_key"] for item in result.diagnostics["excluded_root_actions"]}
    assert excluded_keys == {"B"}
    by_root = {item.root_action_key: item for item in result.viable_actions}
    assert by_root["A"].missing_sample_count == 1
    assert by_root["A"].filled_score == 7.0
    assert by_root["A"].aggregate_score == (10.0 + 8.0 + 7.0) / 3
    assert by_root["C"].aggregate_score == 7.0
    assert result.best_action.root_action_key == "A"


def test_aggregate_plain_results_excludes_faults_and_selects_best_score():
    entries = [_success_log("A", None, 1.0), _fault_log("B", None), _success_log("C", None, 5.0)]
    result = aggregate_plain_results(entries)
    assert result.best_action.root_action_key == "C"
    assert result.best_action.aggregate_score == 5.0
    assert result.diagnostics["faulted_or_excluded_entries"] == 1


def test_build_commit_decision_shapes_hypothesis_plain_invariant_and_empty_paths():
    action = SemanticAction("card", "0:STRIKE_IRONCLAD")
    hyp_aggregation = AggregationResult(
        viable_actions=(
            dataclasses.replace(
                aggregate_hypothesis_results([_success_log("A", "H1", 2.0)]).best_action,
                representative_entry=_success_log("A", "H1", 2.0),
            ),
        ),
        best_action=dataclasses.replace(
            aggregate_hypothesis_results([_success_log("A", "H1", 2.0)]).best_action,
            representative_entry=_success_log("A", "H1", 2.0),
        ),
        diagnostics={},
    )
    hyp_success = build_commit_decision(hyp_aggregation, hypothesis_involved=True, verify_main_invariant=lambda: True)
    assert isinstance(hyp_success, SearchSuccess)
    assert len(hyp_success.planned_sequence) == 1
    assert hyp_success.planned_sequence[0].expected_signature is None

    plain_entry = _success_log("plain", None, 4.0)
    plain_result = aggregate_plain_results([plain_entry])
    plain_success = build_commit_decision(plain_result, hypothesis_involved=False, verify_main_invariant=lambda: True)
    assert isinstance(plain_success, SearchSuccess)
    assert len(plain_success.planned_sequence) == 1
    assert plain_success.planned_sequence[0].expected_signature == plain_entry.result_signature

    invariant_fault = build_commit_decision(plain_result, hypothesis_involved=False, verify_main_invariant=lambda: False)
    assert isinstance(invariant_fault, MainCombatFaultOutcome)
    assert invariant_fault.fault_source == SRC_MAIN_INVARIANT

    empty = AggregationResult(viable_actions=(), best_action=None, diagnostics={})
    failure = build_commit_decision(empty, hypothesis_involved=False, verify_main_invariant=lambda: True)
    assert isinstance(failure, SearchEvaluationFailure)
    assert action.semantic_key == "0:STRIKE_IRONCLAD"  # keeps this fixture's action used and explicit


def test_depth_cap_helper_triggers_at_or_above_threshold_only():
    assert depth_cap_fault(49, 50) is None
    at_cap = depth_cap_fault(50, 50)
    assert at_cap is not None
    assert at_cap.fault_kind == FAULT_DEPTH_CAP_EXCEEDED
    above_cap = depth_cap_fault(51, 50)
    assert above_cap is not None
    assert classify_fault({"fault_kind": above_cap.fault_kind}) == FAULT_DEPTH_CAP_EXCEEDED


def test_decision_log_entry_wraps_success_and_fault_results():
    action = SemanticAction("card", "0:STRIKE_IRONCLAD")
    item = _work_item(action, 12.5, hypothesis_id="H1", work_id="wrap-success")
    success = BranchResult(
        status=BRANCH_STATUS_SUCCESS,
        work_item=item,
        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
        worker_id=0,
        worker_generation=1,
        result_signature=_sig(action),
        child_snapshot=object(),
    )
    entry = to_decision_log_entry(item, success)
    assert entry.status == "success"
    assert entry.hypothesis_id == "H1"
    assert entry.score == 12.5
    assert entry.work_item_state == WORK_ITEM_FINAL_SUCCESS
    assert entry.replay_prefix_digest

    fault = BranchResult(
        status=BRANCH_STATUS_FAULT,
        work_item=item,
        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
        worker_id=0,
        worker_generation=1,
        diagnostics={"fault_kind": "replay_mismatch", "stage": "resolve"},
    )
    fault_entry = to_decision_log_entry(item, fault)
    assert fault_entry.status == "replay_mismatch"
    assert fault_entry.fault_kind == FAULT_REPLAY_MISMATCH
    assert fault_entry.work_item_state == WORK_ITEM_FINAL_FAULT


def test_real_worker_post_teardown_restore_failure_diagnostics_classify_as_restore_failure():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    ineligible_snapshot = _with_synthetic_dangling_draw(_make_eligible(session._game.CaptureSnapshot()))  # noqa: SLF001
    validation = LiveCombatSession().validate_restore_snapshot(ineligible_snapshot)
    assert validation.eligible is False, validation
    assert any("reference_integrity" in code for code in validation.rejection_codes), validation.rejection_codes
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    semantic = _semantic_action_for(strike)
    signature = DecisionSignature.from_battle_state(state, semantic_action=semantic, resolved_action=strike, target_enemy_index=0)
    context = DecisionContext.from_main_stable_capture(ineligible_snapshot, state, signature)
    item = WorkItem.from_candidate_ref(
        context,
        PipelineCandidateRef(
            current_context_signature=signature,
            semantic_action=semantic,
            target_enemy_index=0,
            score=3.0,
            choice_kind="card",
            choice_scope="TopLevel",
        ),
        work_kind="continuation",
        context_id=derive_context_id(context),
        work_id="real-restore-rejection",
    )

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        result = pool.dispatch_work_items([item], LeaseRegistry())[0]

    assert result.status == BRANCH_STATUS_FAULT, result
    assert result.diagnostics["fault_kind"] == "worker_exception"
    assert classify_fault(result.diagnostics) == FAULT_POST_TEARDOWN_RESTORE_FAILURE, result.diagnostics


def test_real_worker_replay_mismatch_diagnostics_classify_as_replay_mismatch():
    record_session = LiveCombatSession()
    state0 = record_session.start_combat(_simple_spec())
    root_snapshot = _make_eligible(record_session._game.CaptureSnapshot())  # noqa: SLF001
    strike = _find_action(state0, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)
    state1 = record_session.step(state0, strike, target_enemy_index=0)
    sig1 = DecisionSignature.from_battle_state(state1, semantic_action=strike_semantic, resolved_action=strike, target_enemy_index=0)
    tampered = ReplayPrefixEntry(
        semantic_action=SemanticAction("card", "0:A_CARD_THAT_DOES_NOT_EXIST"),
        expected_signature=sig1,
        target_enemy_index=0,
    )
    context = DecisionContext(
        root_snapshot=root_snapshot,
        replay_prefix=[tampered],
        plan_path=[tampered],
        current_decision_result=state1,
        current_context_signature=sig1,
        search_hypothesis_id=None,
    )
    defend = _find_action(state1, "card", "DEFEND_IRONCLAD")
    defend_semantic = _semantic_action_for(defend)
    item = WorkItem.from_candidate_ref(
        context,
        PipelineCandidateRef(
            current_context_signature=sig1,
            semantic_action=defend_semantic,
            score=1.0,
            choice_kind="card",
            choice_scope="TopLevel",
        ),
        work_kind="continuation",
        context_id=derive_context_id(context),
        work_id="real-replay-mismatch",
    )

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        result = pool.dispatch_work_items([item], LeaseRegistry())[0]

    assert result.status == BRANCH_STATUS_FAULT, result
    assert result.diagnostics["fault_kind"] == "replay_mismatch"
    assert classify_fault(result.diagnostics) == FAULT_REPLAY_MISMATCH


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
