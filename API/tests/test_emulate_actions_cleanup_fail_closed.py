"""Fail-closed regression tests for emulate_actions cleanup failures."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402
from API.validation import RequestRejected  # noqa: E402


def _combat_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _items(start: dict) -> list[dict]:
    action_id = start["masked_emulator_dto"]["legal_actions"][0]["action_id"]
    return [
        {
            "parent_branch_id": "root",
            "branch_id": "cleanup-a",
            "rng_id": 1,
            "decision_point_id": start["decision_point_id"],
            "action_id": action_id,
        },
        {
            "parent_branch_id": "root",
            "branch_id": "cleanup-b",
            "rng_id": 2,
            "decision_point_id": start["decision_point_id"],
            "action_id": action_id,
        },
    ]


def _assert_closed(inst: CombatInstance) -> None:
    assert inst._closed is True  # noqa: SLF001
    try:
        inst.get_decision("root")
    except RequestRejected as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("poisoned instance must reject further API operations")


def test_cancel_cleanup_failure_closes_instance() -> None:
    inst = CombatInstance("cleanup-cancel-failure", _combat_config(), worker_count=2)
    original_poll = inst._branch_manager.poll  # noqa: SLF001
    original_cancel = inst._branch_manager.cancel_branches  # noqa: SLF001

    def _poll_failure(*args, **kwargs):
        raise RuntimeError("synthetic poll failure")

    def _cancel_failure(branch_ids):
        raise RuntimeError("synthetic cancel cleanup failure")

    start = inst.start_instance_response()
    inst._branch_manager.poll = _poll_failure  # type: ignore[method-assign]  # noqa: SLF001
    inst._branch_manager.cancel_branches = _cancel_failure  # type: ignore[method-assign]  # noqa: SLF001
    try:
        try:
            inst.emulate_actions(items=_items(start), simulation_options=None)
        except RuntimeError as exc:
            assert "closed to prevent ghost Branch execution" in str(exc)
        else:
            raise AssertionError("cleanup failure must fail closed")
        _assert_closed(inst)
    finally:
        inst._branch_manager.poll = original_poll  # type: ignore[method-assign]  # noqa: SLF001
        inst._branch_manager.cancel_branches = original_cancel  # type: ignore[method-assign]  # noqa: SLF001
        inst.close()


def test_release_cleanup_failure_closes_instance() -> None:
    inst = CombatInstance("cleanup-release-failure", _combat_config(), worker_count=2)
    original_poll = inst._branch_manager.poll  # noqa: SLF001
    original_release = inst._branch_manager.release_branches  # noqa: SLF001

    def _poll_failure(*args, **kwargs):
        raise RuntimeError("synthetic poll failure")

    def _release_failure(branch_ids):
        raise RuntimeError("synthetic release cleanup failure")

    start = inst.start_instance_response()
    inst._branch_manager.poll = _poll_failure  # type: ignore[method-assign]  # noqa: SLF001
    inst._branch_manager.release_branches = _release_failure  # type: ignore[method-assign]  # noqa: SLF001
    try:
        try:
            inst.emulate_actions(items=_items(start), simulation_options=None)
        except RuntimeError as exc:
            assert "closed to prevent ghost Branch execution" in str(exc)
        else:
            raise AssertionError("cleanup failure must fail closed")
        _assert_closed(inst)
    finally:
        inst._branch_manager.poll = original_poll  # type: ignore[method-assign]  # noqa: SLF001
        inst._branch_manager.release_branches = original_release  # type: ignore[method-assign]  # noqa: SLF001
        inst.close()
