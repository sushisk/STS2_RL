"""Regressions for public Hand-transfer evidence and replay draw materialization."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState
from combat_state_snapshot import CardInstanceSnapshot, LocalCostModifierSnapshot, SerializableRngSnapshot
from search.replay_draw_restore import (
    card_id_from_observable_key,
    observable_card_key_from_public,
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


def _evidence(pre, post, prefix=()):
    return visible_draw_transition_evidence_from_committed_transition(
        post,
        list(prefix),
        pre_battle_state=pre,
    )


def test_acrobatics_shape_uses_drawpile_diff_and_appended_hand_suffix() -> None:
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


def test_pending_choice_semantics_and_options_are_not_safety_inputs() -> None:
    h = _public("H")
    a = _public("A")
    pre = _state(hand=[_public("PREPARED"), h], draw=[a])
    post = _state(
        hand=[h, a],
        draw=[],
        options=[_public("UNRELATED")],
        scope="SomethingElse",
        choiceOperation="select",
        sourceZone="mystery",
        originEntityId="NOT_PREPARED",
    )
    result = _evidence(pre, post)
    assert result.blocks_later_pinning is False
    assert len(result.constraints) == 1
    assert card_id_from_observable_key(result.constraints[0][1]) == "A"


def test_drawn_only_choice_without_hand_transfer_fails_closed() -> None:
    a, b, c, d = (_public(name) for name in ("A", "B", "C", "D"))
    h = _public("H")
    pre = _state(hand=[h], draw=[a, b, c, d])
    post = _state(hand=[h], draw=[d], options=[a, b, c])

    result = _evidence(pre, post)
    assert result.constraints == ()
    assert result.blocks_later_pinning is True
    assert result.tracking_error is not None
    assert "Hand consumption" in result.tracking_error


def test_drawpile_publication_order_is_not_evidence() -> None:
    played = _public("PLAYED")
    h = _public("H")
    a, b, c = (_public(name) for name in ("A", "B", "C"))
    post = _state(hand=[h, a, b], draw=[c], options=[h, a, b])
    first = _evidence(_state(hand=[played, h], draw=[a, b, c]), post)
    second = _evidence(_state(hand=[played, h], draw=[b, a, c]), post)
    assert first.constraints == second.constraints


def test_non_preserving_pre_hand_prefix_fails_closed() -> None:
    played = _public("PLAYED")
    h = _public("H")
    x = _public("X")
    a, b, c = (_public(name) for name in ("A", "B", "C"))
    pre = _state(hand=[played, h, x], draw=[a, b, c])
    post = _state(hand=[x, h, a, b], draw=[c], options=[x, h, a, b])

    result = _evidence(pre, post)
    assert result.constraints == ()
    assert result.blocks_later_pinning is True


def test_transition_requires_exactly_one_pre_hand_card_consumed() -> None:
    h = _public("H")
    a = _public("A")
    pre = _state(hand=[h], draw=[a])
    post = _state(hand=[h, a], draw=[], options=[h, a])

    result = _evidence(pre, post)
    assert result.constraints == ()
    assert result.blocks_later_pinning is True


def test_invalid_public_card_field_types_fail_closed() -> None:
    invalid_upgraded = _public("A")
    invalid_upgraded["upgraded"] = "false"
    invalid_enchantment = _public("B")
    invalid_enchantment["enchantment"] = "not-an-object"
    invalid_tinker = _public("C")
    invalid_tinker["tinkerTimeType"] = 123

    assert observable_card_key_from_public(invalid_upgraded) is None
    assert observable_card_key_from_public(invalid_enchantment) is None
    assert observable_card_key_from_public(invalid_tinker) is None

    played = _public("PLAYED")
    pre = _state(hand=[played], draw=[invalid_upgraded])
    post = _state(hand=[_public("A")], draw=[])
    result = _evidence(pre, post)
    assert result.constraints == ()
    assert result.blocks_later_pinning is True


def test_valid_public_enchantment_shape_is_preserved() -> None:
    card = _public("A")
    card["enchantment"] = {"id": "BURNING", "amount": 2, "status": "Active"}

    key = observable_card_key_from_public(card)
    assert key is not None
    assert key[-1] == ("BURNING", 2, "Active")


@pytest.mark.parametrize(
    "enchantment",
    [
        {},
        {"foo": "bar"},
        {"id": "BURNING", "amount": 1, "status": "Active", "extra": 1},
        {"id": 1, "amount": 1, "status": "Active"},
        {"id": "BURNING", "amount": True, "status": "Active"},
        {"id": "BURNING", "amount": 1, "status": None},
    ],
)
def test_malformed_public_enchantment_shapes_fail_closed(enchantment: object) -> None:
    card = _public("A")
    card["enchantment"] = enchantment
    assert observable_card_key_from_public(card) is None


def test_unaccounted_drawpile_mutation_blocks_later_root_relative_pinning() -> None:
    played = _public("PLAYED")
    a, b = _public("A"), _public("B")
    pre = _state(hand=[played], draw=[a, b])
    post = _state(hand=[], draw=[b], options=[_public("X")])
    blocked = _evidence(pre, post)
    assert blocked.constraints == ()
    assert blocked.blocks_later_pinning is True

    later_pre = _state(hand=[played], draw=[b])
    later_post = _state(hand=[b], draw=[], options=[b])
    later = _evidence(later_pre, later_post, [_entry(blocked=True)])
    assert later.constraints == ()
    assert later.blocks_later_pinning is True


def test_zero_draw_transition_does_not_block_cursor() -> None:
    a = _public("A")
    pre = _state(hand=[_public("PLAYED"), _public("H")], draw=[a])
    post = _state(hand=[_public("H")], draw=[a], options=[_public("X")])
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

    out_of_order = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[_entry((1, b_key), (0, a_key))],
    )
    with pytest.raises(ValueError, match="ordered contiguous prefix"):
        _pinned_prefix_visible_draw_constraints(out_of_order)


def test_observable_state_pin_distinguishes_upgraded_copy_without_instance_contract() -> None:
    root = _root([
        _card("i-a", "A"),
        _card("i-a-up", "A", upgraded=True),
        _card("i-b", "B"),
    ])
    upgraded_key = observable_card_key_from_public(_public("A", upgraded=True))
    b_key = observable_card_key_from_public(_public("B"))
    assert upgraded_key is not None and b_key is not None
    constraints = ((0, upgraded_key), (1, b_key))
    raw = SearchHypothesisId(
        rng=_rng(), ordered_draw_pile_card_ids=("B", "A", "A"), hypothesis_index=7
    )
    pinned = _reorder_hypothesis_for_visible_draw_constraints(raw, constraints)
    assert pinned.ordered_draw_pile_card_ids == ("A", "B", "A")
    allocated = _draw_pile_instances_for_hypothesis(
        root,
        pinned.ordered_draw_pile_card_ids,
        pinned_observable_keys=constraints,
    )
    assert allocated[0]["CardId"] == "A"
    assert allocated[0]["IsUpgraded"] is True
    assert allocated[1]["CardId"] == "B"


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
