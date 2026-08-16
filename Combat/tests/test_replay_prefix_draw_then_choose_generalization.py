"""Fail-closed regression coverage for unproven draw-then-choose replay shapes."""

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
from combat_state_snapshot import CardInstanceSnapshot  # noqa: E402
from search.decision_context import (  # noqa: E402
    SemanticAction,
    visible_draw_constraints_from_pending_choice,
)

_COMBAT_SESSION_ID = "combat-session-generic-draw-pinning"


def _card(instance_id: str, card_id: str) -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId=instance_id,
        CardId=card_id,
        Type="Skill",
        Rarity="Common",
        Cost=1,
        TargetType="None",
        IsUpgraded=False,
        UpgradeLevel=0,
    )


def _public_instance_id(internal_instance_id: str) -> str:
    digest = hmac.new(
        _COMBAT_SESSION_ID.encode("utf-8"),
        internal_instance_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"cardv-{digest[:16].hex()}"


def _root(*, hand_cards, draw_cards):
    return SimpleNamespace(
        Metadata=SimpleNamespace(CombatSessionId=_COMBAT_SESSION_ID),
        Player=SimpleNamespace(Hand=list(hand_cards), DrawPile=list(draw_cards)),
    )


def _pending(options) -> BattleState:
    return BattleState(
        engine_state={
            "pendingChoice": {
                "scope": "ActionContinuation",
                "choiceOperation": "discard",
                "sourceZone": "hand",
                "choiceSemantics": {"sourceZone": "hand"},
                "options": list(options),
            }
        },
        is_terminal=False,
        outcome="in_progress",
        turn=1,
    )


def test_prepared_shape_remains_unpinned_without_proven_provenance_contract() -> None:
    """Matching the Acrobatics post-step shape is not proof of root draw position."""
    root = _root(
        hand_cards=[_card("i-prepared", "PREPARED"), _card("i-neutralize", "NEUTRALIZE")],
        draw_cards=[_card("i-a", "A"), _card("i-b", "B")],
    )
    state = _pending(
        [
            {
                "id": "NEUTRALIZE",
                "optionId": "hand",
                "cardInstanceId": _public_instance_id("i-neutralize"),
            },
            {"id": "A", "optionId": "draw-a", "cardInstanceId": _public_instance_id("i-a")},
            {"id": "B", "optionId": "draw-b", "cardInstanceId": _public_instance_id("i-b")},
        ]
    )

    # Exact visible identity, root-DrawPile membership, matching triggering CardId, and
    # append-only option shape still do not establish absolute root draw position for an
    # arbitrary mechanic. Only the cross-repo audited Acrobatics path is currently safe.
    assert visible_draw_constraints_from_pending_choice(
        state,
        root,
        [],
        triggering_action=SemanticAction("card", "4:PREPARED"),
    ) == ()


def test_unproven_card_stays_rejected_even_when_root_also_contains_acrobatics() -> None:
    """Presence of Acrobatics elsewhere in Hand must not accidentally satisfy the gate."""
    root = _root(
        hand_cards=[
            _card("i-acrobatics", "ACROBATICS"),
            _card("i-prepared", "PREPARED"),
            _card("i-neutralize", "NEUTRALIZE"),
        ],
        draw_cards=[_card("i-a", "A")],
    )
    state = _pending(
        [
            {
                "id": "ACROBATICS",
                "optionId": "acrobatics",
                "cardInstanceId": _public_instance_id("i-acrobatics"),
            },
            {
                "id": "NEUTRALIZE",
                "optionId": "neutralize",
                "cardInstanceId": _public_instance_id("i-neutralize"),
            },
            {"id": "A", "optionId": "draw-a", "cardInstanceId": _public_instance_id("i-a")},
        ]
    )

    assert visible_draw_constraints_from_pending_choice(
        state,
        root,
        [],
        triggering_action=SemanticAction("card", "4:PREPARED"),
    ) == ()


def test_nonempty_replay_prefix_remains_fail_closed_without_a_proven_draw_offset() -> None:
    """Even the audited card cannot be flattened to root position zero later in a prefix."""
    root = _root(
        hand_cards=[_card("i-acrobatics", "ACROBATICS")],
        draw_cards=[_card("i-a", "A")],
    )
    state = _pending(
        [{"id": "A", "optionId": "draw-a", "cardInstanceId": _public_instance_id("i-a")}]
    )

    assert visible_draw_constraints_from_pending_choice(
        state,
        root,
        [SimpleNamespace(visible_draw_constraints=())],
        triggering_action=SemanticAction("card", "4:ACROBATICS"),
    ) == ()
