"""Tests for Combat/search/main_loop.py - Combat execution infrastructure Phase 3
(Main Process decision-loop state machine, mermaid_combat_main_loop_detail.mermaid).

Native assertion runner, no pytest dependency - same style as `test_decision_context.py`
(this package's Phase 2 tests) and this package's other Combat tests: real Emulator/
`LiveCombatSession`, no mocks of the Emulator itself. A couple of tests use small,
explicitly-documented white-box techniques (wrapping a `LiveCombatSession` instance
method to count/record real calls; one deliberate `boundary_of_battle_state` monkeypatch
to exercise a STEP_PENDING_HOLD branch this repo has no ready-made multi-step-Pending
fixture for) - each is called out in its own docstring, per this task's own "your choice,
document which" allowance.

Run: cd C:\\STS2_RL\\Combat\\tests && python test_main_loop.py
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # this tests/ dir - for test_action_fault_contract import

from battle_emulator import BattleState  # noqa: E402
from live_combat_session import ActionExecutionError, LiveCombatSession  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    CombatStartReplayRoot,
    DecisionSignature,
    SemanticAction,
    boundary_of_battle_state,
)
import search.main_loop as main_loop_module  # noqa: E402
from search.main_loop import (  # noqa: E402
    CombatAbortedByDecisionFailureOutcome,
    CombatTerminalOutcome,
    MainCombatFaultOutcome,
    PendingSearchNotAllowedError,
    PlannedStep,
    ROUTE_DIRECT,
    ROUTE_PENDING_STATIC,
    ROUTE_SEARCH,
    SearchEvaluationFailure,
    SearchSuccess,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    pending_static_select,
    run_until_terminal_or_fault,
)

_run_exec_loop = main_loop_module._run_exec_loop  # noqa: SLF001 - white-box, same pattern used throughout this package
_capture_stable = main_loop_module._capture_stable  # noqa: SLF001
_EXEC_SEQUENCE_EXHAUSTED = main_loop_module._EXEC_SEQUENCE_EXHAUSTED  # noqa: SLF001
_EXEC_GO_TO_BOUNDARY = main_loop_module._EXEC_GO_TO_BOUNDARY  # noqa: SLF001


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _toolbox_pending_spec():
    """TOOLBOX relic presents an immediate StartOfCombat pendingChoice (choiceType=
    "ToolboxChooseCard", scope="StartOfCombat", NOT an ActionContinuation) straight out
    of `start_combat()` - same fixture Phase 2's own tests use
    (`Combat/tests/test_decision_context.py::_toolbox_pending_spec`)."""
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["TOOLBOX"], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _find_action(state: BattleState, action_type: str, card_id=None) -> dict:
    return next(
        a for a in state._cached_legal_actions  # noqa: SLF001
        if a["action_type"] == action_type and (card_id is None or (a.get("parameters") or {}).get("cardId") == card_id)
    )


def _semantic_action_for(action: dict) -> SemanticAction:
    params = action.get("parameters") or {}
    return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))


def _strike_targeting_selector(battle_state: BattleState) -> PlannedStep:
    """A Direct selector that actually targets the enemy (unlike
    `first_candidate_direct_selector`, which deliberately leaves target_index/
    target_enemy_index unset and is unsuitable for an attack card) - used by tests that
    need a real, targeted Strike to land."""
    action = _find_action(battle_state, "card", "STRIKE_IRONCLAD")
    return PlannedStep(semantic_action=_semantic_action_for(action), target_enemy_index=0)


# ---------------------------------------------------------------------------
# Held Stable Snapshot / Replay Prefix bookkeeping
# ---------------------------------------------------------------------------


def test_ordinary_stable_step_keeps_replay_prefix_reset_and_updates_snapshot():
    """STABLE_CAPTURE -> ... -> STEP_STABLE_CAPTURE: a Stable-to-Stable real step (Strike,
    non-lethal) must reset the Replay Prefix to empty both before and after the step, and
    must replace the Held Stable Snapshot with a fresh capture."""
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=999))
    loop_state = initialize_main_loop_state(session, state)

    assert boundary_of_battle_state(loop_state.current_result) == BOUNDARY_STABLE
    _capture_stable(loop_state)
    genesis_snapshot = loop_state.held_stable_snapshot
    assert loop_state.replay_prefix == []
    assert genesis_snapshot is not None

    loop_state.planned_sequence = [_strike_targeting_selector(loop_state.current_result)]
    outcome = _run_exec_loop(loop_state)

    assert outcome == _EXEC_SEQUENCE_EXHAUSTED
    assert boundary_of_battle_state(loop_state.current_result) == BOUNDARY_STABLE
    assert loop_state.replay_prefix == []  # STEP_STABLE_CAPTURE reset it again
    assert loop_state.held_stable_snapshot is not None
    assert loop_state.held_stable_snapshot is not genesis_snapshot  # replaced by a fresh capture


def test_pending_bookkeeping_extends_replay_prefix_then_resets_on_real_stable_resolution():
    """PENDING_HOLD/STEP_PENDING_HOLD (Replay Prefix EXTENDS, Held Stable Snapshot stays
    untouched while still Pending) followed by a real Stable boundary
    (STEP_STABLE_CAPTURE resets it again).

    White-box note: this repo has no ready-made fixture that stays Main-observed-Pending
    across two or more real Steps to exercise STEP_PENDING_HOLD naturally end-to-end. The
    middle assertion block below therefore temporarily monkeypatches
    `main_loop_module.boundary_of_battle_state` to report Pending for the duration of ONE
    real `_run_exec_loop()` call, forcing the STEP_PENDING_HOLD branch to run instead of
    STEP_STABLE_CAPTURE for that one call. The Step itself, the `DecisionSignature`, and
    the `ReplayPrefixEntry`/append are all real and completely unmodified - only which of
    the two (both otherwise real-tested) STEP_BOUNDARY arms executes is redirected.
    Immediately afterward, the patch is removed and the REAL boundary is re-derived from
    the (genuinely Stable) live state to confirm the reset-on-Stable rule."""
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=999))
    loop_state = initialize_main_loop_state(session, state)

    assert boundary_of_battle_state(loop_state.current_result) == BOUNDARY_STABLE
    _capture_stable(loop_state)
    stable_snapshot = loop_state.held_stable_snapshot
    assert loop_state.replay_prefix == []
    assert stable_snapshot is not None

    loop_state.planned_sequence = [_strike_targeting_selector(loop_state.current_result)]

    original_boundary_fn = main_loop_module.boundary_of_battle_state
    try:
        main_loop_module.boundary_of_battle_state = lambda battle_state: BOUNDARY_PENDING
        exec_outcome = _run_exec_loop(loop_state)
    finally:
        main_loop_module.boundary_of_battle_state = original_boundary_fn

    assert exec_outcome == _EXEC_SEQUENCE_EXHAUSTED
    assert len(loop_state.replay_prefix) == 1  # extended, NOT reset - STEP_PENDING_HOLD
    assert loop_state.held_stable_snapshot is stable_snapshot  # untouched while "Pending"

    # Now check the REAL (unpatched) boundary and let the ordinary Stable-reset rule run.
    real_boundary = boundary_of_battle_state(loop_state.current_result)
    assert real_boundary == BOUNDARY_STABLE
    _capture_stable(loop_state)
    assert loop_state.replay_prefix == []
    assert loop_state.held_stable_snapshot is not stable_snapshot


# ---------------------------------------------------------------------------
# PENDING_STATIC: Restore/Step/Worker-free
# ---------------------------------------------------------------------------


def test_pending_static_resolves_real_pending_boundary_without_restore_or_capture():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING

    capture_calls = []
    restore_calls = []
    original_capture = session.capture_snapshot
    original_restore = session.restore_snapshot

    def _tracking_capture(*args, **kwargs):
        capture_calls.append(1)
        return original_capture(*args, **kwargs)

    def _tracking_restore(*args, **kwargs):
        restore_calls.append(1)
        return original_restore(*args, **kwargs)

    session.capture_snapshot = _tracking_capture
    session.restore_snapshot = _tracking_restore
    try:
        planned_step = pending_static_select(state)
    finally:
        session.capture_snapshot = original_capture
        session.restore_snapshot = original_restore

    assert capture_calls == [], "PENDING_STATIC must not CaptureSnapshot"
    assert restore_calls == [], "PENDING_STATIC must not Restore"
    # The picked action must genuinely resolve against this same Pending boundary's own
    # Choice Payload - not a fabricated/placeholder candidate.
    resolved = planned_step.semantic_action.resolve(state._cached_legal_actions)
    assert resolved is not None
    assert planned_step.expected_signature is None  # self-decided, not a prior prediction


def test_genesis_pending_does_not_capture_until_real_stable_and_still_completes():
    session = LiveCombatSession()
    spec = _toolbox_pending_spec()
    spec["enemies"] = [{"monster_id": "CALCIFIED_CULTIST", "hp": 1}]
    state = session.start_combat(spec)
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING
    loop_state = initialize_main_loop_state(session, state)

    capture_boundaries = []
    original_capture = session.capture_snapshot

    def _tracking_capture(*args, **kwargs):
        snapshot = original_capture(*args, **kwargs)
        capture_boundaries.append(snapshot.Metadata.CaptureBoundary)
        return snapshot

    route_observations = []

    def _routing_policy(boundary):
        route_observations.append((boundary, loop_state.held_stable_snapshot))
        if boundary == BOUNDARY_PENDING:
            assert loop_state.held_stable_snapshot is None
            assert capture_boundaries == []
            return ROUTE_PENDING_STATIC
        return ROUTE_DIRECT

    session.capture_snapshot = _tracking_capture
    try:
        outcome = run_until_terminal_or_fault(
            loop_state,
            direct_selector=_strike_targeting_selector,
            routing_policy=_routing_policy,
            max_iterations=10,
        )
    finally:
        session.capture_snapshot = original_capture

    assert isinstance(outcome, CombatTerminalOutcome), outcome
    assert route_observations[0] == (BOUNDARY_PENDING, None)
    assert loop_state.held_stable_snapshot is not None
    assert loop_state.held_stable_snapshot.Metadata.CaptureBoundary == "normal_player_decision"
    assert capture_boundaries
    assert all(boundary == "normal_player_decision" for boundary in capture_boundaries)


# ---------------------------------------------------------------------------
# EXEC_LOOP: re-resolution against the CURRENT Choice Payload, never a stale one
# ---------------------------------------------------------------------------


def test_exec_loop_reresolves_against_current_choice_payload_not_stale():
    """A full, genuinely-differing 'planned vs. executed' Choice Payload divergence would
    require a real Search implementation picking a Planned Sequence against a predicted
    future state (a later phase, stubbed here) - per this task's own documented fallback,
    verified instead as a smaller, self-contained check: this executes a REAL 2-step
    Planned Sequence (the same Semantic Action - Strike a single enemy - twice) and
    proves RESOLVE reads `loop_state.current_result`'s `legal_actions` FRESH on every
    while-loop iteration - never a list captured once before the loop started - by
    temporarily wrapping `SemanticAction.resolve` (class-level; instances are frozen) to
    record the exact `legal_actions` list object passed in on each call.

    (`action_id` alone was tried first and rejected as the proof mechanism: this
    Emulator/Combat stack assigns STRIKE_IRONCLAD-vs-single-enemy the same positional
    action_id on consecutive turns here, since the candidate's position in the legal
    actions list happens to stay stable turn over turn in this simple 1-enemy scenario -
    so an id-equality check alone cannot distinguish "correctly re-resolved" from
    "accidentally reused a stale list", even though the list identity clearly can.)"""
    session = LiveCombatSession()
    state = session.start_combat(
        _simple_spec(hand=["STRIKE_IRONCLAD", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], enemy_hp=999)
    )
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)
    stale_legal_actions = state._cached_legal_actions  # "as planned" - captured before ANY step

    loop_state = initialize_main_loop_state(session, state)
    _capture_stable(loop_state)
    loop_state.planned_sequence = [
        PlannedStep(semantic_action=strike_semantic, target_enemy_index=0),
        PlannedStep(semantic_action=strike_semantic, target_enemy_index=0),
    ]

    captured_legal_action_lists = []
    original_resolve = SemanticAction.resolve

    def _tracking_resolve(self, legal_actions):
        captured_legal_action_lists.append(legal_actions)
        return original_resolve(self, legal_actions)

    SemanticAction.resolve = _tracking_resolve
    try:
        outcome = _run_exec_loop(loop_state)
    finally:
        SemanticAction.resolve = original_resolve

    assert outcome == _EXEC_SEQUENCE_EXHAUSTED
    assert len(captured_legal_action_lists) == 2
    assert captured_legal_action_lists[0] is stale_legal_actions, "Step 1 should resolve against the genesis legal_actions"
    assert captured_legal_action_lists[1] is not stale_legal_actions, (
        "Step 2 must re-resolve against the CURRENT (post-Step-1) legal_actions, "
        "not the list cached before EXEC_LOOP started"
    )


# ---------------------------------------------------------------------------
# Fault path: exception-based, no Transition Record for the failed step
# ---------------------------------------------------------------------------


def test_exec_loop_fault_produces_main_combat_fault_without_appending_record():
    from test_action_fault_contract import _corrupted_console_out  # noqa: PLC0415 - same import-only-where-used pattern as that module's own tests

    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)

    loop_state = initialize_main_loop_state(session, state)
    _capture_stable(loop_state)
    loop_state.planned_sequence = [PlannedStep(semantic_action=strike_semantic, target_enemy_index=0)]

    with _corrupted_console_out():
        outcome = _run_exec_loop(loop_state)

    assert isinstance(outcome, MainCombatFaultOutcome), outcome
    assert isinstance(outcome.error, ActionExecutionError)
    assert loop_state.replay_prefix == [], "no Transition Record may be appended for a faulted Step (NOTE_FAULT_FIRST)"
    assert loop_state.planned_sequence == [], "the remaining Planned Sequence must be abandoned on a Fault"


# ---------------------------------------------------------------------------
# VERIFY_TRANSITION: matching / mismatched / absent Expected Post-Step Signature
# ---------------------------------------------------------------------------


def _dry_run_strike_signature(spec: dict) -> DecisionSignature:
    """Computes the REAL post-step signature a deterministic Strike-at-enemy-0 step
    produces from this exact `spec` - via a throwaway session, so the actual test session
    below starts completely fresh. `matches_for_replay()` ignores `combat_session_id`/
    `resolved_action_id` (Phase 2's own documented reason: neither is stable across
    independent sessions/restores), so two independently-started, identically-specced
    sessions taking the same action are expected to produce matching signatures."""
    dry_session = LiveCombatSession()
    dry_state = dry_session.start_combat(spec)
    dry_strike = _find_action(dry_state, "card", "STRIKE_IRONCLAD")
    dry_semantic = _semantic_action_for(dry_strike)
    dry_result = dry_session.step(dry_state, dry_strike, target_enemy_index=0)
    return DecisionSignature.from_battle_state(
        dry_result, semantic_action=dry_semantic, resolved_action=dry_strike, target_enemy_index=0
    )


def test_verify_transition_matching_expected_signature_does_not_discard():
    spec = _simple_spec()
    expected_sig = _dry_run_strike_signature(spec)

    session = LiveCombatSession()
    state = session.start_combat(spec)
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)

    loop_state = initialize_main_loop_state(session, state)
    _capture_stable(loop_state)
    loop_state.planned_sequence = [
        PlannedStep(semantic_action=strike_semantic, target_enemy_index=0, expected_signature=expected_sig)
    ]

    outcome = _run_exec_loop(loop_state)
    assert outcome == _EXEC_SEQUENCE_EXHAUSTED
    assert loop_state.planned_sequence == []


def test_verify_transition_mismatched_expected_signature_discards_but_keeps_transition_record():
    spec = _simple_spec()
    real_sig = _dry_run_strike_signature(spec)
    tampered_sig = dataclasses.replace(real_sig, resolved_card_id="DEFEND_IRONCLAD")

    session = LiveCombatSession()
    state = session.start_combat(spec)
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)

    loop_state = initialize_main_loop_state(session, state)
    _capture_stable(loop_state)
    loop_state.planned_sequence = [
        PlannedStep(semantic_action=strike_semantic, target_enemy_index=0, expected_signature=tampered_sig)
    ]

    outcome = _run_exec_loop(loop_state)
    assert outcome == _EXEC_GO_TO_BOUNDARY  # DISCARD -> RESYNC
    assert loop_state.planned_sequence == []
    # The Step itself genuinely committed on the live session - APPEND_RECORD happens
    # BEFORE VERIFY_TRANSITION per the diagram's own node order, so the real Transition
    # Record is NOT rolled back merely because the verification afterward disagreed.
    assert len(loop_state.replay_prefix) == 1


def test_verify_transition_no_expected_signature_is_unconditional_match():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)

    loop_state = initialize_main_loop_state(session, state)
    _capture_stable(loop_state)
    loop_state.planned_sequence = [
        PlannedStep(semantic_action=strike_semantic, target_enemy_index=0, expected_signature=None)
    ]

    outcome = _run_exec_loop(loop_state)
    assert outcome == _EXEC_SEQUENCE_EXHAUSTED  # never discarded when there was nothing to compare against
    assert loop_state.planned_sequence == []


# ---------------------------------------------------------------------------
# SearchEvaluationFailure -> CombatAbortedByDecisionFailure, no implicit Direct fallback
# ---------------------------------------------------------------------------


def test_search_evaluation_failure_aborts_without_implicit_direct_fallback():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=999))
    assert boundary_of_battle_state(state) == BOUNDARY_STABLE

    direct_calls = []

    def _tracked_direct_selector(battle_state):
        direct_calls.append(1)
        return first_candidate_direct_selector(battle_state)

    def _always_fail_search(context):
        return SearchEvaluationFailure(detail="stub: all candidates unevaluable")

    loop_state = initialize_main_loop_state(session, state)
    outcome = run_until_terminal_or_fault(
        loop_state,
        direct_selector=_tracked_direct_selector,
        search_strategy=_always_fail_search,
        routing_policy=lambda boundary: ROUTE_SEARCH,
        max_iterations=10,
    )

    assert isinstance(outcome, CombatAbortedByDecisionFailureOutcome), outcome
    assert outcome.detail == "stub: all candidates unevaluable"
    assert isinstance(outcome.search_failure, SearchEvaluationFailure)
    assert direct_calls == [], "no implicit Direct fallback may occur after SearchEvaluationFailure"


# ---------------------------------------------------------------------------
# Structural: Pending must never route into Search
# ---------------------------------------------------------------------------


def test_pending_boundary_cannot_route_to_search():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING

    def _search_that_must_never_run(context):
        raise AssertionError("search_strategy must never be invoked for a Pending boundary")

    loop_state = initialize_main_loop_state(session, state)
    try:
        run_until_terminal_or_fault(
            loop_state,
            direct_selector=first_candidate_direct_selector,
            search_strategy=_search_that_must_never_run,
            routing_policy=lambda boundary: ROUTE_SEARCH,
            max_iterations=5,
        )
        raise AssertionError("expected PendingSearchNotAllowedError")
    except PendingSearchNotAllowedError:
        pass


def test_combat_start_pending_with_replay_root_can_route_to_search_without_pending_guard_error():
    spec = _toolbox_pending_spec()
    session = LiveCombatSession()
    state = session.start_combat(spec)
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING
    loop_state = initialize_main_loop_state(
        session,
        state,
        combat_start_replay_root=CombatStartReplayRoot(spec),
    )
    calls = []

    def _one_choice_search(context):
        calls.append(context)
        choice = next(a for a in state._cached_legal_actions if a["action_type"] == "choice_card")  # noqa: SLF001
        return SearchSuccess([PlannedStep(semantic_action=_semantic_action_for(choice))])

    try:
        run_until_terminal_or_fault(
            loop_state,
            direct_selector=first_candidate_direct_selector,
            search_strategy=_one_choice_search,
            routing_policy=lambda boundary: ROUTE_SEARCH if boundary == BOUNDARY_PENDING and loop_state.held_stable_snapshot is None else ROUTE_DIRECT,
            max_iterations=1,
        )
    except RuntimeError as exc:
        assert "exceeded max_iterations" in str(exc)

    assert calls, "Search must be invoked for genesis Pending when CombatStartReplayRoot is present"
    assert isinstance(calls[0].root_snapshot, CombatStartReplayRoot)
    assert loop_state.held_stable_snapshot is not None
    assert boundary_of_battle_state(loop_state.current_result) == BOUNDARY_STABLE


def test_non_genesis_pending_with_held_snapshot_still_cannot_route_to_search():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING
    loop_state = initialize_main_loop_state(
        session,
        state,
        combat_start_replay_root=CombatStartReplayRoot(_toolbox_pending_spec()),
    )
    loop_state.held_stable_snapshot = object()

    try:
        run_until_terminal_or_fault(
            loop_state,
            direct_selector=first_candidate_direct_selector,
            search_strategy=lambda context: SearchEvaluationFailure("must not run"),
            routing_policy=lambda boundary: ROUTE_SEARCH,
            max_iterations=1,
        )
        raise AssertionError("expected PendingSearchNotAllowedError")
    except PendingSearchNotAllowedError:
        pass


# ---------------------------------------------------------------------------
# End-to-end smoke: reaching a real Terminal outcome
# ---------------------------------------------------------------------------


def test_run_until_terminal_reaches_combat_terminal_outcome():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=1))  # one Strike is lethal
    loop_state = initialize_main_loop_state(session, state)

    outcome = run_until_terminal_or_fault(loop_state, direct_selector=_strike_targeting_selector, max_iterations=20)

    assert isinstance(outcome, CombatTerminalOutcome), outcome
    assert outcome.final_state.is_terminal


def test_routing_policy_rejects_unknown_route():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=999))
    loop_state = initialize_main_loop_state(session, state)
    try:
        run_until_terminal_or_fault(
            loop_state,
            direct_selector=first_candidate_direct_selector,
            routing_policy=lambda boundary: "not_a_real_route",
            max_iterations=3,
        )
        raise AssertionError("expected ValueError for an unknown route")
    except ValueError:
        pass


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
    sys.exit(_run_all())
