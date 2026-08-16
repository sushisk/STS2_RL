"""Privacy-safe exact-instance replay pinning unit regressions for RL PR #64."""

from __future__ import annotations

import hashlib
import hmac
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

_COMBAT_SESSION_ID = "combat-session-visible-id-regression"


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


def _root(draw_cards, *, hand_cards=()):
    return SimpleNamespace(
        Metadata=SimpleNamespace(CombatSessionId=_COMBAT_SESSION_ID),
        Player=SimpleNamespace(Hand=list(hand_cards), DrawPile=list(draw_cards)),
    )


def _public_instance_id(internal_instance_id: str) -> str:
    digest = hmac.new(
        _COMBAT_SESSION_ID.encode("utf-8"),
        internal_instance_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"cardv-{digest[:16].hex()}"


def _state(
    options,
    *,
    scope: str = "ActionContinuation",
    choice_operation: str = "discard",
    source_zone: str = "hand",
    semantic_source_zone: str = "hand",
) -> BattleState:
    return BattleState(
        engine_state={
            "pendingChoice": {
                "scope": scope,
                "choiceOperation": choice_operation,
                "sourceZone": source_zone,
                "choiceSemantics": {"sourceZone": semantic_source_zone},
                "options": list(options),
            }
        },
        is_terminal=False,
        outcome="in_progress",
        turn=1,
    )


def _entry(*constraints):
    return SimpleNamespace(visible_draw_constraints=tuple(constraints))


def _rng() -> SerializableRngSnapshot:
    return SerializableRngSnapshot(Counter=1, State0=2, State1=3, State2=4, State3=5)


def test_visible_constraints_keep_new_acrobatics_draws_when_choice_contains_preexisting_hand() -> None:
    root = _root(
        [
            _card("i-a1", "A"),
            _card("i-a2", "A", upgraded=True),
            _card("i-b", "B"),
            _card("i-c", "C"),
        ],
        hand_cards=[
            _card("i-acrobatics", "ACROBATICS"),
            _card("i-neutralize", "NEUTRALIZE"),
        ],
    )
    visible_hand = _public_instance_id("i-neutralize")
    visible_a2 = _public_instance_id("i-a2")
    visible_b = _public_instance_id("i-b")
    visible_c = _public_instance_id("i-c")

    assert visible_a2.startswith("cardv-") and visible_a2 != "i-a2"
    state = _state(
        [
            {"id": "NEUTRALIZE", "optionId": "o-hand", "cardInstanceId": visible_hand},
            {"id": "A", "optionId": "o-a2", "cardInstanceId": visible_a2},
            {"id": "B", "optionId": "o-b", "cardInstanceId": visible_b},
            {"id": "C", "optionId": "o-c", "cardInstanceId": visible_c},
        ]
    )

    # The pre-existing hand card is verified but is not a draw constraint. Only the
    # newly appended Acrobatics cards are translated to internal Snapshot IDs.
    assert visible_draw_constraints_from_pending_choice(state, root, []) == (
        ("A", "i-a2"),
        ("B", "i-b"),
        ("C", "i-c"),
    )


def test_visible_constraints_fail_closed_without_proven_root_acrobatics_shape() -> None:
    root = _root(
        [_card("i-a", "A"), _card("i-b", "B")],
        hand_cards=[
            _card("i-acrobatics", "ACROBATICS"),
            _card("i-neutralize", "NEUTRALIZE"),
        ],
    )
    options = [
        {
            "id": "NEUTRALIZE",
            "optionId": "o-hand",
            "cardInstanceId": _public_instance_id("i-neutralize"),
        },
        {"id": "A", "optionId": "o-a", "cardInstanceId": _public_instance_id("i-a")},
    ]

    # A later Replay Prefix entry has no safe root-relative draw cursor, so it must not
    # be flattened to position zero.
    assert visible_draw_constraints_from_pending_choice(_state(options), root, [_entry()]) == ()

    # Canonical source-zone and discard/action-continuation shape are part of the proof.
    assert visible_draw_constraints_from_pending_choice(
        _state(options, semantic_source_zone="draw_pile"), root, []
    ) == ()
    assert visible_draw_constraints_from_pending_choice(
        _state(options, choice_operation="exhaust"), root, []
    ) == ()
    assert visible_draw_constraints_from_pending_choice(
        _state(options, scope="TopLevel"), root, []
    ) == ()

    # Reordering the pre-existing hand destroys the append-only Acrobatics proof.
    reordered_root = _root(
        [_card("i-a", "A")],
        hand_cards=[
            _card("i-left", "SURVIVOR"),
            _card("i-acrobatics", "ACROBATICS"),
            _card("i-right", "NEUTRALIZE"),
        ],
    )
    reordered_options = [
        {"id": "NEUTRALIZE", "optionId": "r", "cardInstanceId": _public_instance_id("i-right")},
        {"id": "SURVIVOR", "optionId": "l", "cardInstanceId": _public_instance_id("i-left")},
        {"id": "A", "optionId": "a", "cardInstanceId": _public_instance_id("i-a")},
    ]
    assert visible_draw_constraints_from_pending_choice(
        _state(reordered_options), reordered_root, []
    ) == ()

    # A non-Acrobatics played-card shape is not generalized from root-pile membership.
    non_acrobatics_root = _root(
        [_card("i-a", "A")],
        hand_cards=[_card("i-strike", "STRIKE_SILENT"), _card("i-neutralize", "NEUTRALIZE")],
    )
    assert visible_draw_constraints_from_pending_choice(
        _state(
            [
                {
                    "id": "NEUTRALIZE",
                    "optionId": "h",
                    "cardInstanceId": _public_instance_id("i-neutralize"),
                },
                {"id": "A", "optionId": "a", "cardInstanceId": _public_instance_id("i-a")},
            ]
        ),
        non_acrobatics_root,
        [],
    ) == ()


def test_visible_constraints_reject_malformed_or_non_root_draw_identity() -> None:
    root = _root(
        [_card("i-a", "A"), _card("i-b", "B")],
        hand_cards=[_card("i-acrobatics", "ACROBATICS")],
    )

    assert visible_draw_constraints_from_pending_choice(
        _state([{"id": "A", "optionId": "legacy"}]), root, []
    ) == ()
    assert visible_draw_constraints_from_pending_choice(
        _state(
            [
                {
                    "id": "HAND_ONLY",
                    "optionId": "h",
                    "cardInstanceId": _public_instance_id("i-not-root-draw"),
                }
            ]
        ),
        root,
        [],
    ) == ()


def test_pinned_prefix_accepts_only_first_transition_root_relative_constraints() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B"), _card("i-c", "C")])

    root_only = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[_entry(("A", "i-a"), ("B", "i-b")), _entry()],
    )
    assert _pinned_prefix_visible_draw_constraints(root_only) == (("A", "i-a"), ("B", "i-b"))

    # A later transition's visible cards have an unknown Stable-root-relative draw
    # offset. Do not concatenate them and move them to root position zero.
    later_constraint = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[_entry(("A", "i-a")), _entry(), _entry(("B", "i-b"))],
    )
    assert _pinned_prefix_visible_draw_constraints(later_constraint) == ()

    # A constraint that appears only later is equally unsafe, even if its instance is in
    # the root DrawPile.
    later_only = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[_entry(), _entry(("B", "i-b"))],
    )
    assert _pinned_prefix_visible_draw_constraints(later_only) == ()

    malformed_first = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[_entry(("A", "not-a-root-instance"))],
    )
    assert _pinned_prefix_visible_draw_constraints(malformed_first) == ()


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
