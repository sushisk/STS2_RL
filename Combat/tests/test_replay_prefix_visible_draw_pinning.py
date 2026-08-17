"""Regressions for producer-specific proof and source-agnostic replay pinning."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState
from combat_state_snapshot import CardInstanceSnapshot, SerializableRngSnapshot
import search.decision_context as decision_context
from search.decision_context import SemanticAction, visible_draw_constraints_from_committed_transition
from search.rng_hypothesis import (
    SearchHypothesisId,
    _draw_pile_instances_for_hypothesis,
    _pinned_prefix_visible_draw_constraints,
    _reorder_hypothesis_for_visible_draw_constraints,
    apply_hypothesis_to_context,
)


def _card(instance_id: str, card_id: str, *, upgraded: bool = False, cost: int = 1) -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId=instance_id, CardId=card_id, Type="Skill", Rarity="Common", Cost=cost,
        TargetType="None", IsUpgraded=upgraded, UpgradeLevel=1 if upgraded else 0,
    )


def _option(card_id: str, *, upgraded: bool = False, cost: int = 1, option_id: str = "o") -> dict:
    return {
        "id": card_id, "type": "Skill", "rarity": "Common", "cost": cost,
        "targetType": "None", "upgraded": upgraded,
        "upgradeLevel": 1 if upgraded else 0, "tinkerTimeType": None,
        "tinkerTimeRider": None, "optionId": option_id,
    }


def _root(draw_cards, *, hand_cards=()):
    return SimpleNamespace(Player=SimpleNamespace(Hand=list(hand_cards), DrawPile=list(draw_cards)))


def _state(options, *, source_card: str = "ACROBATICS") -> BattleState:
    return BattleState(
        engine_state={"pendingChoice": {
            "scope": "ActionContinuation", "choiceOperation": "discard", "sourceZone": "hand",
            "originEntityType": "card", "originEntityId": source_card,
            "sourceEffectId": f"card:{source_card}", "choiceSemantics": {"sourceZone": "hand"},
            "options": list(options),
        }},
        is_terminal=False, outcome="in_progress", turn=1,
    )


def _target_prefix(card_id: str):
    return SimpleNamespace(
        semantic_action=SemanticAction("card", f"0:{card_id}"), visible_draw_constraints=(),
        expected_signature=SimpleNamespace(
            candidate_semantic_keys=((('choice_target', '0'), 1), (('choice_target', '1'), 1)),
        ),
    )


def _entry(*constraints, action_type: str = "card"):
    return SimpleNamespace(
        semantic_action=SemanticAction(action_type, f"0:{action_type.upper()}_SOURCE"),
        visible_draw_constraints=tuple(constraints),
    )


def _context(root, *entries):
    return decision_context.DecisionContext(
        root_snapshot=root,
        replay_prefix=list(entries),
        plan_path=[],
        current_decision_result=SimpleNamespace(),
        current_context_signature=SimpleNamespace(),
        search_hypothesis_id=None,
    )


def _rng() -> SerializableRngSnapshot:
    return SerializableRngSnapshot(Counter=1, State0=2, State1=3, State2=4, State3=5)


def test_acrobatics_uses_visible_state_and_root_position_without_public_instance_token() -> None:
    root = _root(
        [_card("i-a-up", "A", upgraded=True), _card("i-a", "A"), _card("i-b", "B"), _card("i-c", "C")],
        hand_cards=[_card("i-acro", "ACROBATICS"), _card("i-neutralize", "NEUTRALIZE")],
    )
    state = _state([
        _option("NEUTRALIZE", option_id="hand"), _option("A", upgraded=True, option_id="a-up"),
        _option("A", option_id="a"), _option("B", option_id="b"),
    ])
    assert all("cardInstanceId" not in option for option in state.engine_state["pendingChoice"]["options"])
    assert visible_draw_constraints_from_committed_transition(
        state, root, [], triggering_action=SemanticAction("card", "0:ACROBATICS")
    ) == ((0, "A", "i-a-up"), (1, "A", "i-a"), (2, "B", "i-b"))


def test_same_cardid_different_state_is_disambiguated_by_visible_card_state() -> None:
    root = _root(
        [_card("i-a-up", "A", upgraded=True), _card("i-a", "A")],
        hand_cards=[_card("i-acro", "ACROBATICS")],
    )
    assert visible_draw_constraints_from_committed_transition(
        _state([_option("A", upgraded=True)]), root, [],
        triggering_action=SemanticAction("card", "0:ACROBATICS"),
    ) == ((0, "A", "i-a-up"),)
    assert visible_draw_constraints_from_committed_transition(
        _state([_option("A", upgraded=False)]), root, [],
        triggering_action=SemanticAction("card", "0:ACROBATICS"),
    ) == ()


def test_non_audited_similar_shape_fails_closed() -> None:
    root = _root([_card("i-a", "A")], hand_cards=[_card("i-prepared", "PREPARED")])
    assert visible_draw_constraints_from_committed_transition(
        _state([_option("A")], source_card="PREPARED"), root, [],
        triggering_action=SemanticAction("card", "0:PREPARED"),
    ) == ()


def test_targeted_dagger_throw_two_hop_is_the_supported_nonempty_prefix_shape() -> None:
    root = _root(
        [_card("i-a", "A")],
        hand_cards=[_card("i-dagger", "DAGGER_THROW"), _card("i-neutralize", "NEUTRALIZE")],
    )
    state = _state([_option("NEUTRALIZE", option_id="hand"), _option("A", option_id="draw")], source_card="DAGGER_THROW")
    prefix = [_target_prefix("DAGGER_THROW")]
    assert visible_draw_constraints_from_committed_transition(
        state, root, prefix, triggering_action=SemanticAction("choice_target", "0")
    ) == ((0, "A", "i-a"),)

    unsafe = [SimpleNamespace(
        semantic_action=SemanticAction("card", "0:STRIKE_SILENT"), visible_draw_constraints=(),
        expected_signature=prefix[0].expected_signature,
    )]
    assert visible_draw_constraints_from_committed_transition(
        state, root, unsafe, triggering_action=SemanticAction("choice_target", "0")
    ) == ()


def test_committed_transition_dispatcher_does_not_pre_gate_relic_or_potion_producers() -> None:
    original = decision_context._VISIBLE_DRAW_CONSTRAINT_PRODUCERS
    seen: list[str] = []

    def synthetic_non_card_producer(
        battle_state, root_snapshot, replay_prefix, *, triggering_action
    ):
        seen.append(triggering_action.action_type)
        return ((1, "B", "i-b"),)

    try:
        decision_context._VISIBLE_DRAW_CONSTRAINT_PRODUCERS = (synthetic_non_card_producer,)
        root = _root([_card("i-a", "A"), _card("i-b", "B")])
        state = BattleState(engine_state={}, is_terminal=False, outcome="in_progress", turn=1)
        for action_type in ("relic", "potion"):
            assert visible_draw_constraints_from_committed_transition(
                state, root, [], triggering_action=SemanticAction(action_type, f"0:{action_type.upper()}_SOURCE")
            ) == ((1, "B", "i-b"),)
        assert seen == ["relic", "potion"]
    finally:
        decision_context._VISIBLE_DRAW_CONSTRAINT_PRODUCERS = original


def test_offset_aware_consumer_is_source_agnostic_across_card_relic_potion_entries() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B"), _card("i-c", "C")])
    context = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[
            _entry((2, "C", "i-c"), action_type="relic"),
            _entry(action_type="potion"),
            _entry((0, "A", "i-a"), action_type="card"),
        ],
    )
    assert _pinned_prefix_visible_draw_constraints(context) == ((0, "A", "i-a"), (2, "C", "i-c"))


def test_hypothesis_reorder_and_allocator_pin_internal_snapshot_instances_at_offsets() -> None:
    root = _root([_card("i-a", "A"), _card("i-a-up", "A", upgraded=True), _card("i-b", "B")])
    constraints = ((0, "A", "i-a-up"), (2, "B", "i-b"))
    raw = SearchHypothesisId(rng=_rng(), ordered_draw_pile_card_ids=("B", "A", "A"), hypothesis_index=7)
    pinned = _reorder_hypothesis_for_visible_draw_constraints(raw, constraints)
    assert pinned.ordered_draw_pile_card_ids == ("A", "A", "B")
    allocated = _draw_pile_instances_for_hypothesis(
        root, pinned.ordered_draw_pile_card_ids, pinned_instances=((0, "i-a-up"), (2, "i-b"))
    )
    assert [card["InstanceId"] for card in allocated] == ["i-a-up", "i-a", "i-b"]
    assert allocated[0]["IsUpgraded"] is True


def test_consumer_rejects_duplicate_offsets_or_mismatched_snapshot_instance() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B")])
    duplicate = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((0, "A", "i-a"), (0, "B", "i-b"))])
    with pytest.raises(ValueError, match="duplicate root-relative offsets"):
        _pinned_prefix_visible_draw_constraints(duplicate)

    mismatch = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((0, "A", "i-b"))])
    with pytest.raises(ValueError, match="not claimed CardId"):
        _pinned_prefix_visible_draw_constraints(mismatch)


def test_apply_hypothesis_rejects_constraint_that_conflicts_with_hypothesis() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B")])
    context = _context(root, _entry((0, "A", "i-a")))
    hypothesis = SearchHypothesisId(
        rng=_rng(),
        ordered_draw_pile_card_ids=("B", "B"),
        hypothesis_index=3,
    )

    with pytest.raises(ValueError, match="absent from this hypothesis multiset"):
        apply_hypothesis_to_context(context, hypothesis)
