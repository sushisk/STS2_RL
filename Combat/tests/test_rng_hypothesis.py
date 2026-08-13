"""Tests for Combat/search/rng_hypothesis.py - Phase 6 RNG Hypothesis / DrawPile Belief.

Native assertion runner, no pytest dependency. Uses real LiveCombatSession Restore/Step
for Method-B substitution round trips. Run:
cd C:\\STS2_RL\\Combat\\tests && python test_rng_hypothesis.py
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
import traceback
from collections import Counter
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import CombatHistoryEntrySnapshot, SerializableRngSnapshot  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    Lease,
    WorkItem,
    decision_result_digest,
    derive_context_id,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import CombatStartReplayRoot, DecisionContext, DecisionSignature, SemanticAction  # noqa: E402
from search.rng_hypothesis import (  # noqa: E402
    CONSUME_MODE,
    CONSUME_PASSTHROUGH,
    CONSUME_TRUE_RNG_OK,
    HYPOTHESIS_MODE_INDEPENDENT,
    HYPOTHESIS_MODE_STANDARD,
    SearchHypothesisId,
    apply_hypothesis_to_context,
    build_grid,
    compute_public_multiset_for_combat_start,
    compute_public_multiset,
    consume_check,
    derive_substituted_snapshot,
    derive_substituted_replay_root,
    generate_belief_hypotheses,
    with_search_hypothesis,
)


def _simple_spec(hand=None, draw_pile=None, discard_pile=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": draw_pile if draw_pile is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "discard_pile": discard_pile if discard_pile is not None else [],
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


def _context_and_pipeline(hand=None, draw_pile=None, width=2):
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=hand, draw_pile=draw_pile, enemy_hp=999))
    context = DecisionContext.from_main_stable_capture(session.capture_snapshot(), state, _representative_signature(state))
    pipeline = build_candidate_pipeline_result(context, width=width)
    assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
    return context, pipeline


def _without_history(snapshot):
    return dataclasses.replace(
        snapshot,
        CombatHistory=dataclasses.replace(snapshot.CombatHistory, Entries=[]),
    )


def _starter_multiset(**overrides):
    base = Counter(
        {
            "STRIKE_IRONCLAD": 2,
            "DEFEND_IRONCLAD": 2,
            "BASH": 1,
        }
    )
    base.update(overrides)
    return dict(base)


def _rng(index: int) -> SerializableRngSnapshot:
    return SerializableRngSnapshot(
        Counter=index,
        State0=0x1000000000000000 + index,
        State1=0x2000000000000000 + index * 3,
        State2=0x3000000000000000 + index * 5,
        State3=0x4000000000000000 + index * 7,
    )


def _pile_card_ids(cards) -> list[str]:
    return [str(card.CardId) for card in cards]


def test_compute_public_multiset_from_real_captured_snapshot():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = session.capture_snapshot()

    got = compute_public_multiset(snapshot, combat_start_deck_multiset=_starter_multiset())

    expected = dict(Counter({"STRIKE_IRONCLAD": 1, "DEFEND_IRONCLAD": 1, "BASH": 1}))
    assert got == expected, got


def test_compute_public_multiset_for_combat_start_subtracts_visible_scenario_piles():
    spec = _simple_spec(
        hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        draw_pile=["BASH", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        discard_pile=["BASH"],
    )
    spec["exhaust_pile"] = ["DEFEND_IRONCLAD"]
    spec["play_pile"] = ["STRIKE_IRONCLAD"]
    combat_start = {
        "STRIKE_IRONCLAD": 3,
        "DEFEND_IRONCLAD": 3,
        "BASH": 2,
    }

    got = compute_public_multiset_for_combat_start(spec, combat_start_deck_multiset=combat_start)

    assert got == {"BASH": 1, "DEFEND_IRONCLAD": 1, "STRIKE_IRONCLAD": 1}, got


def test_derive_substituted_replay_root_replaces_only_scenario_draw_pile():
    spec = _simple_spec(
        hand=["STRIKE_IRONCLAD"],
        draw_pile=["BASH", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        discard_pile=["DEFEND_IRONCLAD"],
    )
    spec["relics"] = ["TOOLBOX"]
    root = CombatStartReplayRoot(scenario_spec=spec)
    hypothesis = SearchHypothesisId(
        rng=_rng(23),
        ordered_draw_pile_card_ids=("STRIKE_IRONCLAD", "BASH", "DEFEND_IRONCLAD"),
        hypothesis_index=23,
    )

    derived = derive_substituted_replay_root(root, hypothesis)

    assert derived.scenario_spec["draw_pile"] == ["STRIKE_IRONCLAD", "BASH", "DEFEND_IRONCLAD"]
    assert derived.scenario_spec["hand"] == spec["hand"]
    assert derived.scenario_spec["discard_pile"] == spec["discard_pile"]
    assert derived.scenario_spec["relics"] == ["TOOLBOX"]
    assert root.scenario_spec["draw_pile"] == ["BASH", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]


def test_compute_public_multiset_includes_card_generated_entry_with_card_id():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = session.capture_snapshot()
    generated = CombatHistoryEntrySnapshot(
        EntryType="CardGeneratedEntry",
        ActorInstanceId=snapshot.Player.CreatureInstanceId,
        RoundNumber=snapshot.RoundNumber,
        CurrentSide=snapshot.CurrentSide,
        PlayerTurnNumbers={str(snapshot.Player.NetId): snapshot.TurnNumber},
        Fields={"cardId": "WOUND", "creatorInstanceId": snapshot.Player.InstanceId},
    )
    snapshot = dataclasses.replace(
        snapshot,
        CombatHistory=dataclasses.replace(
            snapshot.CombatHistory,
            Entries=[*snapshot.CombatHistory.Entries, generated],
        ),
    )

    got = compute_public_multiset(snapshot, combat_start_deck_multiset=_starter_multiset())

    assert got["WOUND"] == 1, got
    assert got["BASH"] == 1, got


def test_compute_public_multiset_overcounts_after_card_transformed_away():
    """Known bug: sushisk/STS2_Emulator#8, sushisk/STS2_RL#43.

    CardCmd.Transform (real Emulator code) records a CardGeneratedEntry for the
    replacement card but nothing at all for the original - CombatHistory has no
    CardRemoved/CardTransformed entry type. Playing PRIMAL_FORCE (transforms every
    transformable Attack in hand into GIANT_ROCK) makes compute_public_multiset() believe
    the transformed-away STRIKE_IRONCLAD cards are still hidden in the DrawPile, which then
    makes belief-hypothesis derivation fail once a hypothesis is actually built from that
    wrong multiset - exactly what broke real Oracle collection (STS2_Training PR #58/#59
    local validation) against a harvested scenario containing PRIMAL_FORCE.

    This pins TODAY's (buggy) failure. Once Emulator#8 lands and this module is updated to
    subtract a CardTransformedEntry, rewrite this test to assert success instead (no
    phantom cards, derive_substituted_snapshot succeeds).
    """
    session = LiveCombatSession()
    spec = _simple_spec(
        hand=["PRIMAL_FORCE", "STRIKE_IRONCLAD", "STRIKE_IRONCLAD"],
        draw_pile=[],
        discard_pile=[],
    )
    state = session.start_combat(spec)
    combat_start_deck_multiset = {"PRIMAL_FORCE": 1, "STRIKE_IRONCLAD": 2}

    primal_force = next(
        a
        for a in state._cached_legal_actions  # noqa: SLF001
        if a["action_type"] == "card" and a["parameters"].get("cardId") == "PRIMAL_FORCE"
    )
    session.step(state, primal_force)
    snapshot = session.capture_snapshot()

    # Confirm the fixture genuinely exercises the transform-away bug: both STRIKE_IRONCLAD
    # instances must be gone from every real (non-hidden) pile, not just moved around.
    all_visible_ids = (
        _pile_card_ids(snapshot.Player.Hand)
        + _pile_card_ids(snapshot.Player.DiscardPile)
        + _pile_card_ids(snapshot.Player.ExhaustPile)
        + _pile_card_ids(snapshot.Player.PlayPile)
        + _pile_card_ids(snapshot.Player.DrawPile)
    )
    assert "STRIKE_IRONCLAD" not in all_visible_ids, all_visible_ids
    assert all_visible_ids.count("GIANT_ROCK") == 2, all_visible_ids

    public_multiset = compute_public_multiset(snapshot, combat_start_deck_multiset=combat_start_deck_multiset)

    # BUG: the transformed-away cards are believed still-hidden in the DrawPile.
    assert public_multiset.get("STRIKE_IRONCLAD") == 2, public_multiset

    hypotheses = generate_belief_hypotheses(public_multiset, count=1, rng_seed_source=_rng)
    try:
        derive_substituted_snapshot(snapshot, hypotheses[0])
    except ValueError as exc:
        assert "does not match root snapshot" in str(exc), exc
    else:
        raise AssertionError(
            "expected derive_substituted_snapshot to fail on a phantom-card hypothesis - "
            "if this now passes, Emulator#8/RL#43 may already be fixed; update this test "
            "to assert successful, phantom-free restoration instead"
        )


def test_consume_check_passthrough_mode_independent_and_true_rng_ok():
    context, _pipeline = _context_and_pipeline(width=2)
    end_turn = {"action_type": "system", "parameters": {}}
    strike = {"action_type": "card", "parameters": {"cardId": "STRIKE_IRONCLAD"}}

    passthrough = consume_check(end_turn, context)
    needs_hypothesis = consume_check(strike, context)
    independent = consume_check(strike, context, mode=HYPOTHESIS_MODE_INDEPENDENT)
    true_rng = consume_check(strike, context, true_rng_ok=True)

    assert passthrough.kind == CONSUME_PASSTHROUGH, passthrough
    assert needs_hypothesis.kind == CONSUME_MODE and needs_hypothesis.mode == HYPOTHESIS_MODE_STANDARD, needs_hypothesis
    assert independent.kind == CONSUME_MODE and independent.mode == HYPOTHESIS_MODE_INDEPENDENT, independent
    assert true_rng.kind == CONSUME_TRUE_RNG_OK, true_rng


def test_generate_belief_hypotheses_are_seeded_permutations():
    public_multiset = {"STRIKE_IRONCLAD": 2, "DEFEND_IRONCLAD": 2, "BASH": 1}
    hypotheses = generate_belief_hypotheses(public_multiset, count=5, rng_seed_source=_rng)

    assert len(hypotheses) == 5
    assert len({h.to_slot_value() for h in hypotheses}) == 5
    for h in hypotheses:
        assert Counter(h.ordered_draw_pile_card_ids) == Counter(public_multiset), h
    assert len({h.ordered_draw_pile_card_ids for h in hypotheses}) > 1


def test_derive_substituted_snapshot_restore_round_trip_changes_draw_behavior_only_in_scope():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(hand=["DEFEND_IRONCLAD"], draw_pile=["BASH", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]))
    root = _without_history(session.capture_snapshot())
    original_order = _pile_card_ids(root.Player.DrawPile)
    hypothesis = SearchHypothesisId(
        rng=_rng(11),
        ordered_draw_pile_card_ids=tuple(reversed(original_order)),
        hypothesis_index=11,
    )

    derived = derive_substituted_snapshot(root, hypothesis)
    assert _pile_card_ids(derived.Player.DrawPile) == list(reversed(original_order))
    assert derived.Rng.RunRng["Shuffle"] == hypothesis.rng
    assert derived.Player.Hand == root.Player.Hand
    assert derived.Player.DiscardPile == root.Player.DiscardPile
    assert derived.Player.ExhaustPile == root.Player.ExhaustPile
    assert derived.Player.Hp == root.Player.Hp
    assert derived.Player.Relics == root.Player.Relics

    restore_session = LiveCombatSession()
    restored = restore_session.restore_snapshot(derived)
    defend = next(a for a in restored._cached_legal_actions if a["action_type"] == "card")  # noqa: SLF001
    after_defend = restore_session.step(restored, defend)
    assert [c["id"] for c in after_defend.engine_state["drawPile"]] == list(reversed(original_order))

    original_session = LiveCombatSession()
    original_after = original_session.restore_snapshot(root)
    original_defend = next(a for a in original_after._cached_legal_actions if a["action_type"] == "card")  # noqa: SLF001
    original_after = original_session.step(original_after, original_defend)
    assert [c["id"] for c in original_after.engine_state["drawPile"]] == original_order


def test_apply_hypothesis_to_context_matches_manual_replace_plus_with_search_hypothesis():
    context, _pipeline = _context_and_pipeline()
    original_order = _pile_card_ids(context.root_snapshot.Player.DrawPile)
    hypothesis = SearchHypothesisId(
        rng=_rng(11),
        ordered_draw_pile_card_ids=tuple(reversed(original_order)),
        hypothesis_index=11,
    )

    got = apply_hypothesis_to_context(context, hypothesis)

    expected_root = derive_substituted_snapshot(context.root_snapshot, hypothesis)
    expected = with_search_hypothesis(dataclasses.replace(context, root_snapshot=expected_root), hypothesis)
    assert got == expected


def test_apply_hypothesis_to_context_uses_replay_root_branch_for_combat_start():
    spec = _simple_spec(
        hand=["STRIKE_IRONCLAD"],
        draw_pile=["BASH", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        discard_pile=["DEFEND_IRONCLAD"],
    )
    root = CombatStartReplayRoot(scenario_spec=spec)
    base_context, _pipeline = _context_and_pipeline()
    context = dataclasses.replace(base_context, root_snapshot=root)
    hypothesis = SearchHypothesisId(
        rng=_rng(23),
        ordered_draw_pile_card_ids=("STRIKE_IRONCLAD", "BASH", "DEFEND_IRONCLAD"),
        hypothesis_index=23,
    )

    got = apply_hypothesis_to_context(context, hypothesis)

    expected_root = derive_substituted_replay_root(root, hypothesis)
    expected = with_search_hypothesis(dataclasses.replace(context, root_snapshot=expected_root), hypothesis)
    assert got == expected
    assert got.root_snapshot.scenario_spec["draw_pile"] == ["STRIKE_IRONCLAD", "BASH", "DEFEND_IRONCLAD"]


def test_build_grid_reuses_same_hypothesis_set_for_each_root_action():
    context, pipeline = _context_and_pipeline(width=2)
    public_multiset = compute_public_multiset(context.root_snapshot, combat_start_deck_multiset=_starter_multiset())
    hypotheses = generate_belief_hypotheses(public_multiset, count=2, rng_seed_source=_rng)
    actions = [pipeline.continuation_candidate, pipeline.sub_branch_candidates[0]]

    cells = build_grid(actions, hypotheses, context.root_snapshot)

    assert len(cells) == 4
    assert [c.search_hypothesis_id for c in cells[:2]] == [h.to_slot_value() for h in hypotheses]
    assert [c.search_hypothesis_id for c in cells[2:]] == [h.to_slot_value() for h in hypotheses]


def test_search_hypothesis_id_scopes_decision_context_id_and_lease_validation():
    context, pipeline = _context_and_pipeline(width=1)
    public_multiset = compute_public_multiset(context.root_snapshot, combat_start_deck_multiset=_starter_multiset())
    h1, h2 = generate_belief_hypotheses(public_multiset, count=2, rng_seed_source=_rng)
    ctx1 = with_search_hypothesis(context, h1)
    ctx2 = with_search_hypothesis(context, h2)

    assert ctx1.search_hypothesis_id != ctx2.search_hypothesis_id
    assert derive_context_id(ctx1) != derive_context_id(ctx2)

    item1 = WorkItem.from_candidate_ref(ctx1, pipeline.continuation_candidate, work_kind="continuation")
    item2 = WorkItem.from_candidate_ref(ctx2, pipeline.continuation_candidate, work_kind="continuation")
    sig = context.current_context_signature
    lease = Lease(
        worker_id=0,
        worker_generation=1,
        context_id=item1.context_id,
        search_hypothesis_id=item1.search_hypothesis_id,
        state_epoch=0,
        combat_session_id=sig.combat_session_id,
        step_index=sig.step_index,
        decision_result_digest=decision_result_digest(sig),
    )

    assert lease.is_valid_for(item1, worker_generation=1)
    assert not lease.is_valid_for(item2, worker_generation=1)


def test_public_api_does_not_accept_authoritative_order_for_belief_generation():
    sig = inspect.signature(generate_belief_hypotheses)
    assert list(sig.parameters) == ["public_multiset", "count", "rng_seed_source"]
    assert "draw" not in " ".join(sig.parameters).lower()
    assert "pile" not in " ".join(sig.parameters).lower()


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
