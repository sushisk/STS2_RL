"""Tests for Combat/search/candidate_pipeline.py - Combat execution infrastructure Phase 4.

Native assertion runner, no pytest dependency. Uses real Emulator/LiveCombatSession
fixtures and never mocks the Emulator. Run:
cd C:\\STS2_RL\\Combat\\tests && python test_candidate_pipeline.py
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
import traceback
from pathlib import Path
from typing import get_origin

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from battle_emulator import BattleState, is_action_continuation_pending_choice  # noqa: E402
from emulator_bridge import legal_actions_to_list  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    CHOICE_SCOPE_ACTION_CONTINUATION,
    CHOICE_SCOPE_TOP_LEVEL,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
    boundary_of_battle_state,
)
import search.main_loop as main_loop_module  # noqa: E402
from search.main_loop import initialize_main_loop_state  # noqa: E402
from search.candidate_pipeline import (  # noqa: E402
    Candidate,
    CandidatePipelineSuccess,
    NoViableCandidates,
    OrderMaskedObservation,
    PipelineCandidateRef,
    build_candidate_pipeline_result,
    build_order_masked_observation,
    extract_candidates,
    rank_candidates,
    score_candidate,
)
from verify_restore_bootstrap_phase3b import _make_eligible  # noqa: E402


def _simple_spec(hand=None, enemy_hp=48, enemies=None, potions=None, relics=None):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": relics or [],
        "potions": potions or [],
        "seed": 1,
        "enemies": enemies or [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _toolbox_pending_spec():
    return _simple_spec(hand=["STRIKE_IRONCLAD"], relics=["TOOLBOX"])


def _regent_target_choice_spec():
    return {
        "character_id": "REGENT",
        "player_hp": 80,
        "player_max_hp": 80,
        "seed": 1,
        "stars": 6,
        "hand_cards": [{"card_id": "STRIKE_REGENT", "is_upgraded": False}],
        "draw_pile_cards": [],
        "discard_pile_cards": [],
        "exhaust_pile_cards": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "enemies": [
            {"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40},
            {"monster_id": "CALCIFIED_CULTIST", "hp": 35, "max_hp": 35},
        ],
    }


def _find_action(state: BattleState, action_type: str, card_id=None) -> dict:
    return next(
        a
        for a in state._cached_legal_actions  # noqa: SLF001
        if a["action_type"] == action_type
        and (card_id is None or (a.get("parameters") or {}).get("cardId") == card_id)
    )


def _semantic_action_for(action: dict) -> SemanticAction:
    params = action.get("parameters") or {}
    return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))


def _representative_signature(state: BattleState) -> DecisionSignature:
    action = state._cached_legal_actions[0]  # noqa: SLF001
    return DecisionSignature.from_battle_state(state, semantic_action=_semantic_action_for(action), resolved_action=action)


def _eligible_root_snapshot(session: LiveCombatSession):
    return _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001


def _raw_step_to_target_choice(session: LiveCombatSession) -> BattleState:
    """Use a real LiveCombatSession and raw Step to inspect the intermediate
    ActionContinuation target choice before LiveCombatSession.step() auto-resolves it."""
    state = session.start_combat(_regent_target_choice_spec())
    strike = _find_action(state, "card", "STRIKE_REGENT")
    result = session._game.Step(strike["action_id"])  # noqa: SLF001
    legal = legal_actions_to_list(result.LegalActions)
    wrapped = session._emulator._wrap(  # noqa: SLF001
        result.Observation,
        turn=state.turn,
        enemy_max_hps=state.enemy_max_hps,
        legal_actions=legal,
    )
    assert not is_action_continuation_pending_choice(wrapped.engine_state), wrapped.engine_state.get("pendingChoice")
    assert {a["action_type"] for a in legal} == {"choice_target"}
    return wrapped


def test_extract_candidates_from_real_stable_boundary_shape():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    candidates = extract_candidates(state)

    assert len(candidates) == len(state._cached_legal_actions)  # noqa: SLF001
    assert {c.choice_kind for c in candidates} == {a["action_type"] for a in state._cached_legal_actions}  # noqa: SLF001
    assert all(c.choice_scope == CHOICE_SCOPE_TOP_LEVEL for c in candidates)
    strike = next(c for c in candidates if c.semantic_action.card_id == "STRIKE_IRONCLAD")
    assert strike.choice_kind == "card"
    assert strike.semantic_action.target_type in ("SingleEnemy", "AnyEnemy")
    assert strike.target_index is None
    assert strike.target_enemy_index is None


def test_extract_candidates_from_real_pending_boundary_derives_top_level_scope():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING

    candidates = extract_candidates(state)
    assert len(candidates) == len(state._cached_legal_actions)  # noqa: SLF001
    assert {c.choice_kind for c in candidates} == {"choice_card", "choice_skip"}
    assert all(c.choice_scope == CHOICE_SCOPE_TOP_LEVEL for c in candidates)
    assert any(c.semantic_action.card_id == "EQUILIBRIUM" for c in candidates if c.choice_kind == "choice_card")
    assert any(c.choice_kind == "choice_skip" for c in candidates)


def test_extract_candidates_from_real_action_continuation_pending_derives_scope_and_target_params():
    state = _raw_step_to_target_choice(LiveCombatSession())
    candidates = extract_candidates(state)

    assert candidates
    assert all(c.choice_kind == "choice_target" for c in candidates)
    assert all(c.choice_scope == CHOICE_SCOPE_ACTION_CONTINUATION for c in candidates)
    assert {c.target_enemy_index for c in candidates} == {0, 1}


def test_choice_kind_routing_uses_actual_action_type_vocabulary():
    session = LiveCombatSession()
    stable = session.start_combat(_simple_spec())
    stable_obs = build_order_masked_observation(stable)
    stable_scored = rank_candidates(stable_obs, extract_candidates(stable))
    assert {s.evaluator_name for s in stable_scored if s.candidate.choice_kind == "card"} == {"card"}
    assert {s.evaluator_name for s in stable_scored if s.candidate.choice_kind == "system"} == {"other"}

    pending = LiveCombatSession().start_combat(_toolbox_pending_spec())
    pending_scored = rank_candidates(build_order_masked_observation(pending), extract_candidates(pending))
    assert {s.evaluator_name for s in pending_scored if s.candidate.choice_kind == "choice_card"} == {"hand"}

    target_state = _raw_step_to_target_choice(LiveCombatSession())
    target_scored = rank_candidates(build_order_masked_observation(target_state), extract_candidates(target_state))
    assert {s.evaluator_name for s in target_scored if s.candidate.choice_kind == "choice_target"} == {"target"}


def test_order_masked_observation_has_no_ordered_card_list_fields():
    forbidden_exact = {"hand", "draw_pile", "drawpile", "discard_pile", "exhaust_pile", "play_pile", "deck"}
    for field in dataclasses.fields(OrderMaskedObservation):
        name = field.name.lower()
        assert name not in forbidden_exact, f"forbidden raw pile field {field.name!r}"
        assert not name.endswith("_cards"), f"field {field.name!r} looks capable of holding ordered cards"
        assert get_origin(field.type) not in (list, tuple), f"field {field.name!r} must not be a sequence type"
    assert "extra" not in [f.name for f in dataclasses.fields(OrderMaskedObservation)]
    assert "metadata" not in [f.name for f in dataclasses.fields(OrderMaskedObservation)]


def test_evaluator_signature_and_runtime_guard_reject_raw_battle_state():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    candidate = extract_candidates(state)[0]

    sig = inspect.signature(score_candidate)
    assert list(sig.parameters) == ["observation", "candidate"]
    assert sig.parameters["observation"].annotation in (OrderMaskedObservation, "OrderMaskedObservation")
    try:
        score_candidate(state, candidate)  # type: ignore[arg-type]
        raise AssertionError("expected TypeError when an evaluator receives raw BattleState")
    except TypeError:
        pass


def test_pruning_width_keeps_top_ranked_candidates_only():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]))
    context = DecisionContext.from_main_stable_capture(_eligible_root_snapshot(session), state, _representative_signature(state))

    result = build_candidate_pipeline_result(context, width=2)
    assert isinstance(result, CandidatePipelineSuccess), result
    assert len(result.pruned_candidates) == 2
    assert result.pruned_candidates == result.ranked_candidates[:2]
    assert result.continuation_candidate.semantic_action == result.pruned_candidates[0].candidate.semantic_action
    assert len(result.sub_branch_candidates) == 1


def test_zero_candidates_after_pruning_returns_typed_no_viable_result():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    context = DecisionContext.from_main_stable_capture(_eligible_root_snapshot(session), state, _representative_signature(state))

    result = build_candidate_pipeline_result(context, width=0)
    assert isinstance(result, NoViableCandidates), result
    assert result.ranked_candidates
    assert "no viable candidates" in result.detail

    result2 = build_candidate_pipeline_result(context, width=8, score_threshold=1_000_000.0)
    assert isinstance(result2, NoViableCandidates), result2


def test_split_result_has_one_continuation_rest_sub_branches_and_no_expected_signature():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]))
    context = DecisionContext.from_main_stable_capture(_eligible_root_snapshot(session), state, _representative_signature(state))

    result = build_candidate_pipeline_result(context, width=3)
    assert isinstance(result, CandidatePipelineSuccess), result
    refs = [result.continuation_candidate, *result.sub_branch_candidates]
    assert len(refs) == len(result.pruned_candidates)
    assert len([result.continuation_candidate]) == 1
    assert len(result.sub_branch_candidates) == len(result.pruned_candidates) - 1
    assert "expected_signature" not in [f.name for f in dataclasses.fields(PipelineCandidateRef)]
    assert all(ref.current_context_signature is context.current_context_signature for ref in refs)


def test_build_candidate_pipeline_result_end_to_end_from_main_decision_context():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(enemy_hp=999))
    loop_state = initialize_main_loop_state(session, state)
    main_loop_module._capture_stable(loop_state)  # noqa: SLF001 - same test style as test_main_loop.py

    context = main_loop_module.build_main_decision_context(loop_state)
    result = build_candidate_pipeline_result(context, width=4)

    assert isinstance(result, CandidatePipelineSuccess), result
    assert result.continuation_candidate.semantic_action.resolve(state._cached_legal_actions) is not None  # noqa: SLF001
    assert len(result.pruned_candidates) <= 4
    assert result.observation.draw_pile_size == 0


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
