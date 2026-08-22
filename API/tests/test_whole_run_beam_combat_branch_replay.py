"""Combat branch replay: which views are combat, and what a fault may publish.

Two failures motivated this coverage.

A Whole Run evaluation aborted twice with `AllBranchesFaultedError` because a replayed
Combat branch stepped an opaque `action_id` that meant a different action in the replayed
state (`Illegal action: N`). Non-combat screens reached the same path: a rest site's smith
prompt and a card reward publish `choice_card` / `choice_confirm` / `choice_skip`, which
are all inside `_COMBAT_ACTION_TYPES`, so boundary and action type alone cannot tell them
apart from combat.

The fault diagnostics added to investigate that then carried the worker's raw
`session.get_observation()` - run seed and hidden pile order included - onto a wire where
everything else is masked.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_whole_run_beam import (  # noqa: E402
    _is_combat_view,
    _public_fault_diagnostics,
    _semantic_key_and_ordinal,
)


class _View:
    def __init__(self, boundary, room_type, legal_actions):
        self.boundary = boundary
        self.room_context = None if room_type is None else {"room_type": room_type}
        self.legal_actions_raw = legal_actions


def _card(action_id, card_id="STRIKE_IRONCLAD", cost=1):
    return {
        "action_id": action_id,
        "action_type": "card",
        "label": card_id,
        "is_available": True,
        "parameters": {"cardId": card_id, "cost": cost, "targetType": "AnyEnemy"},
    }


def _choice_card(action_id, option_id):
    return {
        "action_id": action_id,
        "action_type": "choice_card",
        "label": "UPGRADE",
        "is_available": True,
        "parameters": {"optionId": option_id},
    }


# -- which views are combat ---------------------------------------------------------


def test_combat_room_with_card_actions_is_a_combat_view():
    view = _View("stable", "CombatRoom", [_card(0), {"action_id": 1, "action_type": "system", "label": "End Turn", "is_available": True, "parameters": {}}])

    assert _is_combat_view(view) is True


def test_rest_site_card_choice_is_not_a_combat_view():
    """The smith prompt publishes only `choice_card`, which is a combat action type.

    Without the room check this reached Combat branch replay, where an opaque action_id
    from a different decision was stepped verbatim.
    """
    view = _View("pending_choice", "RestSiteRoom", [_choice_card(0, "opt-1"), _choice_card(1, "opt-2")])

    assert _is_combat_view(view) is False


def test_a_view_without_a_room_context_is_not_a_combat_view():
    view = _View("stable", None, [_card(0)])

    assert _is_combat_view(view) is False


# -- re-resolving the chosen action -------------------------------------------------


def test_the_only_copy_of_a_card_has_ordinal_zero():
    actions = [_card(0, "BASH", cost=2), _card(1, "STRIKE_IRONCLAD")]

    key, ordinal = _semantic_key_and_ordinal(actions, 1)

    assert ordinal == 0
    assert "STRIKE_IRONCLAD" in key


def test_duplicate_cards_are_told_apart_by_ordinal():
    """Two identical Strikes share a semantic key; no card instance id breaks the tie."""
    actions = [_card(0, "STRIKE_IRONCLAD"), _card(1, "BASH", cost=2), _card(2, "STRIKE_IRONCLAD")]

    first_key, first_ordinal = _semantic_key_and_ordinal(actions, 0)
    second_key, second_ordinal = _semantic_key_and_ordinal(actions, 2)

    assert first_key == second_key
    assert (first_ordinal, second_ordinal) == (0, 1)


def test_an_upgraded_copy_is_a_different_action():
    actions = [_card(0, "STRIKE_IRONCLAD"), _card(1, "STRIKE_IRONCLAD", cost=0)]

    plain_key, _ = _semantic_key_and_ordinal(actions, 0)
    cheap_key, cheap_ordinal = _semantic_key_and_ordinal(actions, 1)

    assert plain_key != cheap_key
    assert cheap_ordinal == 0


def test_an_unknown_action_id_fails_loudly():
    try:
        _semantic_key_and_ordinal([_card(0)], 7)
    except RuntimeError as exc:
        assert "7" in str(exc)
    else:  # pragma: no cover - the assertion below reports the miss
        raise AssertionError("expected a RuntimeError for an action_id that is not legal")


# -- what a fault may publish -------------------------------------------------------


def _raw_diagnostics():
    return {
        "fault_kind": "replay_mismatch",
        "message": "boundary mismatch",
        "expected_boundary": "stable",
        "actual_boundary": "pending_choice",
        "actual_choice_scope": "TopLevel",
        "actual_choice_kind": "target",
        "actual_room_context": {"room_type": "CombatRoom"},
        "actual_legal_actions": [_card(0)],
        "actual_observation": {
            "boundary": "pending_choice",
            "seed": 1953870616,
            "combat_session_id": "cs-42",
            "state": {
                "hp": 39,
                "maxHp": 68,
                "seed": 1953870616,
                "drawPile": [
                    {"id": "STRIKE_IRONCLAD", "type": "Attack", "rarity": "Basic", "cost": 1,
                     "targetType": "AnyEnemy", "upgraded": False, "upgradeLevel": 0,
                     "tinkerTimeType": None, "tinkerTimeRider": None, "enchantment": None},
                ],
            },
        },
    }


def _values(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _values(item)
    else:
        yield node


def test_the_raw_observation_never_reaches_the_wire():
    public = _public_fault_diagnostics(_raw_diagnostics())

    assert "actual_observation" not in public
    emitted = list(_values(public))
    assert 1953820616 not in emitted
    assert 1953870616 not in emitted
    assert "cs-42" not in emitted
    assert not any(isinstance(name, str) and name.lower() == "seed" for name in emitted)


def test_the_landing_state_is_still_published_as_a_masked_dto():
    public = _public_fault_diagnostics(_raw_diagnostics())

    masked = public["actual_masked_emulator_dto"]
    assert masked["hp"] == 39
    assert masked["maxHp"] == 68
    assert masked["mask_version"]
    assert masked["dto_version"]
    assert [action["label"] for action in masked["legal_actions"]] == ["STRIKE_IRONCLAD"]


def test_the_public_boundary_fields_cross_unchanged():
    raw = _raw_diagnostics()

    public = _public_fault_diagnostics(raw)

    for key in (
        "fault_kind",
        "message",
        "expected_boundary",
        "actual_boundary",
        "actual_choice_scope",
        "actual_choice_kind",
        "actual_room_context",
    ):
        assert public[key] == raw[key]


def test_legal_actions_are_masked_even_without_an_observation():
    raw = _raw_diagnostics()
    raw.pop("actual_observation")

    public = _public_fault_diagnostics(raw)

    assert "actual_masked_emulator_dto" not in public
    assert [action["label"] for action in public["actual_legal_actions"]] == ["STRIKE_IRONCLAD"]


def test_the_intended_position_crosses_with_the_reached_one():
    """A faulted rebuild reported only where it landed.

    That cannot separate "the replay diverged" from "the replay ran past the point it was
    asked for": in the field the same fault covered both. `expected_position` is public
    because every field in it is already published as part of a normal decision.
    """
    raw = _raw_diagnostics()
    raw["expected_position"] = {
        "prefix_length": 9,
        "room_id": 4,
        "boundary": "stable",
        "stepIndex": 110,
        "totalFloor": 15,
        "hp": 71,
        "energy": 1,
        "turnNumber": 1,
    }

    public = _public_fault_diagnostics(raw)

    assert public["expected_position"]["prefix_length"] == 9
    assert public["expected_position"]["stepIndex"] == 110


def test_diagnostics_without_state_are_passed_through_safely():
    public = _public_fault_diagnostics({"fault_kind": "task_timeout"})

    assert public == {"fault_kind": "task_timeout"}
