"""Privacy-safe exact-instance replay pinning unit regressions for RL PR #64."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import CardInstanceSnapshot, SerializableRngSnapshot  # noqa: E402
from search.decision_context import visible_draw_constraints_from_pending_choice  # noqa: E402
from search.rng_hypothesis import (  # noqa: E402
    SearchHypothesisId,
    _draw_pile_instances_for_hypothesis,
    _pinned_prefix_visible_draw_constraints,
    _reorder_hypothesis_for_visible_draw_constraints,
)


def _card(instance_id: str, card_id: str, *, upgraded: bool = False) -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId=instance_id,
        CardId=card_id,
        Type="Skill",
        Rarity="Common",
        Cost=1,
        TargetType="None",
        IsUpgraded=upgraded,
        UpgradeLevel=1 if upgraded else 0,
    )


def _root(cards):
    return SimpleNamespace(Player=SimpleNamespace(DrawPile=list(cards)))


def _state(options) -> BattleState:
    return BattleState(
        engine_state={"pendingChoice": {"options": list(options)}},
        is_terminal=False,
        outcome="in_progress",
        turn=1,
    )


def _entry(*constraints):
    return SimpleNamespace(visible_draw_constraints=tuple(constraints))


def _rng() -> SerializableRngSnapshot:
    return SerializableRngSnapshot(Counter=1, State0=2, State1=3, State2=4, State3=5)


def test_visible_constraints_require_complete_exact_identity_from_remaining_root_draw_pile() -> None:
    root = _root([_card("i-a1", "A"), _card("i-a2", "A", upgraded=True), _card("i-b", "B")])
    state = _state(
        [
            {"id": "A", "optionId": "o-a2", "cardInstanceId": "i-a2"},
            {"id": "B", "optionId": "o-b", "cardInstanceId": "i-b"},
        ]
    )
    assert visible_draw_constraints_from_pending_choice(state, root, []) == (
        ("A", "i-a2"),
        ("B", "i-b"),
    )

    # A previously pinned concrete instance is no longer eligible for a later entry.
    assert visible_draw_constraints_from_pending_choice(
        _state([{"id": "A", "optionId": "again", "cardInstanceId": "i-a2"}]),
        root,
        [_entry(("A", "i-a2"))],
    ) == ()

    # Missing exact identity or a visible card not from the remaining root DrawPile is
    # rejected all-or-nothing rather than partially inferred.
    assert visible_draw_constraints_from_pending_choice(
        _state([{"id": "A", "optionId": "legacy"}]), root, []
    ) == ()
    assert visible_draw_constraints_from_pending_choice(
        _state([{"id": "HAND_ONLY", "optionId": "h", "cardInstanceId": "i-hand"}]),
        root,
        [],
    ) == ()


def test_pinned_prefix_validation_stops_before_invalid_tail_entry() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B"), _card("i-c", "C")])
    context = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[
            _entry(("A", "i-a")),
            _entry(),
            _entry(("B", "i-b")),
            _entry(("C", "not-a-root-instance")),
        ],
    )
    assert _pinned_prefix_visible_draw_constraints(context) == (("A", "i-a"), ("B", "i-b"))


def test_hypothesis_reorder_and_concrete_allocation_pin_duplicate_cardid_exact_instance() -> None:
    # i-a2 is upgraded while i-a1 is not. CardId-only canonical allocation may choose
    # either copy; exact-instance pinning must force the already-visible upgraded copy.
    root = _root([
        _card("i-a1", "A", upgraded=False),
        _card("i-a2", "A", upgraded=True),
        _card("i-b", "B"),
    ])
    constraints = (("A", "i-a2"), ("B", "i-b"))
    raw = SearchHypothesisId(
        rng=_rng(),
        ordered_draw_pile_card_ids=("B", "A", "A"),
        hypothesis_index=7,
    )

    pinned = _reorder_hypothesis_for_visible_draw_constraints(raw, constraints)
    assert pinned.ordered_draw_pile_card_ids == ("A", "B", "A")
    assert pinned.hypothesis_index == raw.hypothesis_index

    allocated = _draw_pile_instances_for_hypothesis(
        root,
        pinned.ordered_draw_pile_card_ids,
        pinned_instance_ids=("i-a2", "i-b"),
    )
    assert [card["InstanceId"] for card in allocated] == ["i-a2", "i-b", "i-a1"]
    assert [card["CardId"] for card in allocated] == ["A", "B", "A"]
    assert allocated[0]["IsUpgraded"] is True


def test_exact_instance_allocator_rejects_absent_or_cardid_mismatched_pin() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B")])
    try:
        _draw_pile_instances_for_hypothesis(
            root, ("A", "B"), pinned_instance_ids=("missing",)
        )
    except ValueError as exc:
        assert "absent" in str(exc)
    else:
        raise AssertionError("absent cardInstanceId was accepted")

    try:
        _draw_pile_instances_for_hypothesis(
            root, ("A", "B"), pinned_instance_ids=("i-b",)
        )
    except ValueError as exc:
        assert "requires" in str(exc)
    else:
        raise AssertionError("CardId-mismatched exact instance was accepted")
