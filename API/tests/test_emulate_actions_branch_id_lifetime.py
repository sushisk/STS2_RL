"""Branch-ID lifetime regression coverage for DTO v0.7 batch failure atomicity."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def _item(start: dict, branch_id: str) -> dict:
    return {
        "parent_branch_id": "root",
        "branch_id": branch_id,
        "rng_id": 1,
        "decision_point_id": start["decision_point_id"],
        "action_id": start["masked_emulator_dto"]["legal_actions"][0]["action_id"],
    }


def test_submitted_batch_failure_permanently_consumes_public_branch_id() -> None:
    inst = CombatInstance("branch-id-lifetime", _combat_config(), worker_count=1)
    original_poll = inst._phase._branch_manager.poll  # noqa: SLF001

    def _fail_poll(*args, **kwargs):
        raise RuntimeError("synthetic coordinator failure after submit")

    try:
        start = inst.start_instance_response()
        item = _item(start, "burned-after-submit")
        inst._phase._branch_manager.poll = _fail_poll  # type: ignore[method-assign]  # noqa: SLF001

        with pytest.raises(RuntimeError, match="synthetic coordinator failure"):
            inst.emulate_actions(items=[item], simulation_options=None)

        # Failure atomicity restores live execution/bookkeeping state, but public IDs are
        # intentionally monotonic once manager submission has occurred. Reusing the ID
        # must be rejected rather than silently aliasing a prior failed attempt.
        assert inst._phase._branch_manager.active_branch_count() == 0  # noqa: SLF001
        assert inst._branch_ids.is_known("burned-after-submit")  # noqa: SLF001

        inst._phase._branch_manager.poll = original_poll  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(RequestRejected, match="already used"):
            inst.emulate_actions(items=[item], simulation_options=None)
    finally:
        inst._phase._branch_manager.poll = original_poll  # type: ignore[method-assign]  # noqa: SLF001
        inst.close()
