"""Regression coverage for Whole Run public ActionId resolution.

The RL/Training contract publishes the Emulator's current LegalAction.action_id as an
opaque string. Whole Run must therefore resolve the returned token by ID equality, not
by treating its numeric spelling as a list position. These tests stay pure-Python and
do not construct the Emulator/CLR runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_whole_run import _View  # noqa: E402
from API.instance_combat import _DecisionView  # noqa: E402
from API.validation import RequestRejected  # noqa: E402


def _view(action_ids: list[int]) -> _View:
    return _View(
        legal_actions_raw=[
            {
                "action_id": action_id,
                "action_type": "test",
                "label": str(action_id),
                "is_available": True,
                "parameters": {},
            }
            for action_id in action_ids
        ],
        boundary="test",
        observation={},
        room_context={},
        map_snapshot=None,
        room_id=None,
        action_prefix=(),
        choice_type="test",
        chain_blocked=False,
        event_rng_state=None,
    )


def test_sparse_reward_skip_action_id_resolves_to_its_position():
    view = _view([0, 3])

    assert view.resolve_action_id("3") == 1
    assert view.legal_actions_raw[view.resolve_action_id("3")]["action_id"] == 3


def test_sparse_shop_leave_action_id_resolves_to_its_position():
    view = _view([0, 10])

    assert view.resolve_action_id("10") == 1
    assert view.legal_actions_raw[view.resolve_action_id("10")]["action_id"] == 10


def test_sparse_map_room_id_resolves_by_id_not_ordinal():
    view = _view([17, 42, 99])

    assert view.resolve_action_id("42") == 1
    assert view.legal_actions_raw[view.resolve_action_id("42")]["action_id"] == 42


def test_combat_sparse_action_id_resolves_to_its_position():
    view = _DecisionView(
        legal_actions_raw=[
            {
                "action_id": action_id,
                "action_type": "test",
                "label": str(action_id),
                "is_available": True,
                "parameters": {},
            }
            for action_id in [0, 1, 2, 4]
        ],
        decision_context=None,
        boundary="test",
    )

    assert view.resolve_action_id("4") == 3
    assert view.legal_actions_raw[view.resolve_action_id("4")]["action_id"] == 4


def test_combat_duplicate_action_id_is_rejected_as_ambiguous():
    view = _DecisionView(
        legal_actions_raw=[
            {
                "action_id": action_id,
                "action_type": "test",
                "label": str(action_id),
                "is_available": True,
                "parameters": {},
            }
            for action_id in [3, 3]
        ],
        decision_context=None,
        boundary="test",
    )

    try:
        view.resolve_action_id("3")
    except RequestRejected as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("duplicate current combat action_id must not resolve silently")


def test_unknown_action_id_is_rejected():
    view = _view([0, 3])

    try:
        view.resolve_action_id("1")
    except RequestRejected as exc:
        assert "not among current legal actions" in str(exc)
    else:
        raise AssertionError("unknown public action_id must be rejected")


def test_duplicate_action_id_is_rejected_as_ambiguous():
    view = _view([3, 3])

    try:
        view.resolve_action_id("3")
    except RequestRejected as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("duplicate current action_id must not resolve silently")
