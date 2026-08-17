"""Regressions for observable transfer proof and replay draw materialization."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState
from combat_state_snapshot import CardInstanceSnapshot, LocalCostModifierSnapshot, SerializableRngSnapshot
from search.decision_context import SemanticAction
import search.replay_draw_restore as replay_draw_restore
from search.replay_draw_restore import (
    card_id_from_observable_key,
    observable_card_key_from_public,
    observable_transfer_evidence,
    visible_draw_transition_evidence_from_committed_transition,
)
from search.rng_hypothesis import (
    SearchHypothesisId,
    _draw_pile_instances_for_hypothesis,
    _pinned_prefix_visible_draw_constraints,
    _reorder_hypothesis_for_visible_draw_constraints,
)


def _card(instance_id: str, card_id: str, *, upgraded: bool = False, cost: int = 1, modifiers=()) -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId=instance_id,
        CardId=card_id,
        Type="Skill",
        Rarity="Common",
        Cost=cost,
        TargetType="None",
        IsUpgraded=upgraded,
        UpgradeLevel=1 if upgraded else 0,
        LocalCostModifiers=list(modifiers),
    )


def _public(card_id: str, *, upgraded: bool = False, cost: int = 1, option_id: str | None = None) -> dict:
    card = {
        "id": card_id,
        "type": "Skill",
        "rarity": "Common",
        "cost": cost,
        "targetType": "None",
        "upgraded": upgraded,
        "upgradeLevel": 1 if upgraded else 0,
        "tinkerTimeType": None,
        "tinkerTimeRider": None,
        "enchantment": None,
    }
    if option_id is not None:
        card["optionId"] = option_id
    return card


def _state(*, hand, draw, options=None, **pending_overrides) -> BattleState:
    engine = {
        "hand": list(hand),
        "drawPile": list(draw),
        "discardPile": [],
        "exhaustPile": [],
        "playPile": [],
    }
    if options is not None:
        pending = {
            "scope": "ActionContinuation",
            "choiceOperation": "discard",
            "sourceZone": "hand",
            "originEntityType": "card",
            "originEntityId": "ACROBATICS",
            "options": list(options),
        }
        pending.update(pending_overrides)
        engine["pendingChoice"] = pending
    return BattleState(engine_state=engine, is_terminal=False, outcome="in_progress", turn=1)


def _root(draw_cards):
    return SimpleNamespace(Player=SimpleNamespace(DrawPile=list(draw_cards)))


def _entry(*constraints, blocked: bool = False, error: str | None = None):
    return SimpleNamespace(
        visible_draw_constraints=tuple(constraints),
        visible_draw_tracking_blocked=blocked,
        visible_draw_tracking_error=error,
    )


def _rng() -> SerializableRngSnapshot:
    return SerializableRngSnapshot(Counter=1, State0=2, State1=3, State2=4, State3=5)


def _root_from_public(cards):
    return _root([
        _card(
            f"root-{index}",
            card["id"],
            upgraded=bool(card.get("upgraded", False)),
            cost=int(card.get("cost", 0)),
        )
        for index, card in enumerate(cards)
    ])


def _evidence(pre, post, prefix=(), *, root_draw=None):
    if root_draw is None:
        root_draw = pre.engine_state["drawPile"]
    return visible_draw_transition_evidence_from_committed_transition(
        post,
        _root_from_public(root_draw),
        list(prefix),
        triggering_action=SemanticAction("card", "0:ANY_CARD"),
        pre_battle_state=pre,
    )


def test_acrobatics_shape_uses_generic_transfer_and_hand_append_order() -> None:
    h = _public("NEUTRALIZE", option_id="h")
    a_up = _public("A", upgraded=True, option_id="a-up")
    a = _public("A", option_id="a")
    b = _public("B", option_id="b")
    c = _public("C")
    pre = _state(hand=[_public("ACROBATICS"), h], draw=[a_up, a, b, c])
    post = _state(hand=[h, a_up, a, b], draw=[c], options=[h, a_up, a, b])

    result = _evidence(pre, post)
    assert result.blocks_later_pinning is False
    assert [offset for offset, _key in result.constraints] == [0, 1, 2]
    assert [card_id_from_observable_key(key) for _offset, key in result.constraints] == ["A", "A", "B"]
    assert [key[5] for _offset, key in result.constraints[:2]] == [True, False]


def test_card_allowlist_and_choice_semantics_are_not_safety_gates() -> None:
    h = _public("H")
    a = _public("A")
    pre = _state(hand=[_public("PREPARED"), h], draw=[a])
    post = _state(
        hand=[h, a],
        draw=[],
        options=[h, a],
        scope="SomethingElse",
        choiceOperation="select",
        sourceZone="mystery",
        originEntityId="NOT_PREPARED",
    )
    result = visible_draw_transition_evidence_from_committed_transition(
        post,
        _root_from_public([a]),
        [],
        triggering_action=SemanticAction("potion", "unrelated"),
        pre_battle_state=pre,
    )
    assert len(result.constraints) == 1
    assert card_id_from_observable_key(result.constraints[0][1]) == "A"


def test_drawn_only_choice_uses_generic_gate_b_without_mechanic_specific_prover() -> None:
    a, b, c, d = (_public(name) for name in ("A", "B", "C", "D"))
    h = _public("H")
    pre = _state(hand=[h], draw=[a, b, c, d])
    post = _state(hand=[h], draw=[d], options=[a, b, c])

    transfer = observable_transfer_evidence(pre, post)
    assert transfer is not None
    sequence = replay_draw_restore.ordered_draw_sequence(pre, post, transfer)
    assert sequence is not None
    assert [card_id_from_observable_key(key) for key in sequence] == ["A", "B", "C"]

    result = _evidence(pre, post)
    assert result.blocks_later_pinning is False
    assert result.tracking_error is None
    assert [card_id_from_observable_key(key) for _offset, key in result.constraints] == ["A", "B", "C"]


def test_drawpile_publication_order_is_not_gate_a_evidence() -> None:
    h = _public("H")
    a, b, c = (_public(name) for name in ("A", "B", "C"))
    post = _state(hand=[h, a, b], draw=[c], options=[h, a, b])
    root_draw = [a, b, c]
    first = _evidence(_state(hand=[h], draw=[a, b, c]), post, root_draw=root_draw)
    second = _evidence(_state(hand=[h], draw=[b, a, c]), post, root_draw=root_draw)
    assert first.constraints == second.constraints


def test_gate_b_rejects_wrong_option_order_against_stable_root_and_records_error() -> None:
    a, b, c, d = (_public(name) for name in ("A", "B", "C", "D"))
    pre = _state(hand=[], draw=[a, b, c, d])
    post = _state(hand=[], draw=[d], options=[c, b, a])

    result = _evidence(pre, post, root_draw=[a, b, c, d])
    assert result.constraints == ()
    assert result.blocks_later_pinning is True
    assert result.tracking_error is not None
    assert "option-order contract rejected" in result.tracking_error
    assert "['C', 'B', 'A']" in result.tracking_error
    assert "['A', 'B', 'C']" in result.tracking_error


def test_gate_b_fails_closed_when_r_and_e_occurrences_make_order_ambiguous() -> None:
    a, b, c = (_public(name) for name in ("A", "B", "C"))
    pre = _state(hand=[a], draw=[a, b, c])
    post = _state(hand=[a], draw=[c], options=[a, b, a])

    result = _evidence(pre, post, root_draw=[a, b, c])
    assert result.constraints == ()
    assert result.blocks_later_pinning is True
    assert result.tracking_error is not None
    assert "ambiguous under observable card equality" in result.tracking_error


def test_unaccounted_drawpile_mutation_blocks_later_root_relative_pinning() -> None:
    a, b = _public("A"), _public("B")
    pre = _state(hand=[], draw=[a, b])
    post = _state(hand=[], draw=[b], options=[_public("X")])
    blocked = _evidence(pre, post)
    assert blocked.constraints == ()
    assert blocked.blocks_later_pinning is True

    later_pre = _state(hand=[], draw=[b])
    later_post = _state(hand=[b], draw=[], options=[b])
    later = _evidence(later_pre, later_post, [_entry(blocked=True)])
    assert later.constraints == ()
    assert later.blocks_later_pinning is True


def test_zero_draw_transition_does_not_block_cursor() -> None:
    a = _public("A")
    pre = _state(hand=[], draw=[a])
    post = _state(hand=[], draw=[a], options=[_public("X")])
    result = _evidence(pre, post)
    assert result.constraints == ()
    assert result.blocks_later_pinning is False


def test_consumer_stops_at_block_and_requires_contiguous_prefix() -> None:
    a_key = observable_card_key_from_public(_public("A"))
    b_key = observable_card_key_from_public(_public("B"))
    assert a_key is not None and b_key is not None
    root = _root([_card("i-a", "A"), _card("i-b", "B")])
    context = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[
            _entry((0, a_key)),
            _entry(blocked=True),
            _entry((1, b_key)),
        ],
    )
    assert _pinned_prefix_visible_draw_constraints(context) == ((0, a_key),)

    invalid = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((1, b_key))])
    with pytest.raises(ValueError, match="contiguous prefix"):
        _pinned_prefix_visible_draw_constraints(invalid)


def test_observable_state_pin_distinguishes_upgraded_copy_without_instance_contract() -> None:
    root = _root([
        _card("i-a", "A"),
        _card("i-a-up", "A", upgraded=True),
        _card("i-b", "B"),
    ])
    upgraded_key = observable_card_key_from_public(_public("A", upgraded=True))
    b_key = observable_card_key_from_public(_public("B"))
    assert upgraded_key is not None and b_key is not None
    constraints = ((0, upgraded_key), (2, b_key))
    raw = SearchHypothesisId(
        rng=_rng(), ordered_draw_pile_card_ids=("B", "A", "A"), hypothesis_index=7
    )
    pinned = _reorder_hypothesis_for_visible_draw_constraints(raw, constraints)
    assert pinned.ordered_draw_pile_card_ids == ("A", "A", "B")
    allocated = _draw_pile_instances_for_hypothesis(
        root,
        pinned.ordered_draw_pile_card_ids,
        pinned_observable_keys=constraints,
    )
    assert allocated[0]["CardId"] == "A"
    assert allocated[0]["IsUpgraded"] is True
    assert allocated[2]["CardId"] == "B"


def test_hidden_gameplay_state_ambiguity_fails_closed() -> None:
    mod_a = LocalCostModifierSnapshot(Amount=-1, Type="A", Expiration=1, IsReduceOnly=False)
    mod_b = LocalCostModifierSnapshot(Amount=-1, Type="B", Expiration=2, IsReduceOnly=False)
    root = _root([
        _card("i-a1", "A", cost=0, modifiers=[mod_a]),
        _card("i-a2", "A", cost=0, modifiers=[mod_b]),
    ])
    key = observable_card_key_from_public(_public("A", cost=0))
    assert key is not None
    with pytest.raises(ValueError, match="hidden gameplay states"):
        _draw_pile_instances_for_hypothesis(
            root,
            ("A", "A"),
            pinned_observable_keys=((0, key),),
        )
