"""Scope checks for Whole Run Combat Beam branching."""

from __future__ import annotations

from types import SimpleNamespace

from API.instance_whole_run_beam import _is_combat_view


def _view(*actions: dict) -> SimpleNamespace:
    return SimpleNamespace(legal_actions_raw=list(actions))


def test_combat_action_types_are_branchable() -> None:
    assert _is_combat_view(
        _view(
            {"action_id": 1, "action_type": "card", "is_available": True},
            {"action_id": 2, "action_type": "system", "is_available": True},
        )
    )
    assert _is_combat_view(
        _view(
            {"action_id": 3, "action_type": "choice_target", "is_available": True},
            {"action_id": 4, "action_type": "choice_skip", "is_available": True},
        )
    )


def test_non_combat_whole_run_actions_stay_out_of_scope() -> None:
    assert not _is_combat_view(
        _view({"action_id": 1, "action_type": "choice_reward_card", "is_available": True})
    )
    assert not _is_combat_view(
        _view(
            {"action_id": 1, "action_type": "card", "is_available": True},
            {"action_id": 2, "action_type": "map_room", "is_available": True},
        )
    )


def test_unavailable_non_combat_action_does_not_block_combat_scope() -> None:
    assert _is_combat_view(
        _view(
            {"action_id": 1, "action_type": "card", "is_available": True},
            {"action_id": 2, "action_type": "map_room", "is_available": False},
        )
    )
