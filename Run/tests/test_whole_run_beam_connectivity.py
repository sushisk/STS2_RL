"""Real-Emulator connectivity regression for Whole Run Combat Beam branching.

This intentionally does not mock ``WholeRunSession`` or ``WholeRunWorkerPool``. It
walks a real run to the first Combat decision, then creates a child and a grandchild
through ``API.instance_whole_run_beam.WholeRunInstance.emulate_actions``.

Run directly from the repository root or this directory::

    python Run/tests/test_whole_run_beam_connectivity.py
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUN_DIR = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _RUN_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from API.instance_whole_run_beam import WholeRunInstance  # noqa: E402

_COMBAT_BOUNDARIES = frozenset({"stable", "pending_choice"})
_COMBAT_ACTION_TYPES = frozenset(
    {
        "system",
        "card",
        "potion",
        "choice_target",
        "choice_card",
        "choice_confirm",
        "choice_skip",
    }
)


def _available_actions(dto: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    legal_actions = dto.get("legal_actions")
    if not isinstance(legal_actions, Sequence) or isinstance(legal_actions, (str, bytes)):
        return []
    return [
        action
        for action in legal_actions
        if isinstance(action, Mapping)
        and action.get("is_available") is not False
        and isinstance(action.get("action_id"), str)
    ]


def _is_combat_decision(dto: Mapping[str, Any]) -> bool:
    if dto.get("boundary") not in _COMBAT_BOUNDARIES:
        return False
    actions = _available_actions(dto)
    action_types = {
        action.get("action_type")
        for action in actions
        if isinstance(action.get("action_type"), str)
    }
    return bool(action_types) and action_types <= _COMBAT_ACTION_TYPES


def _pick_nonterminal_friendly_action(dto: Mapping[str, Any]) -> str:
    """Prefer the system action (normally End Turn) so the branch stays in Combat."""
    actions = _available_actions(dto)
    for action_type in ("system", "choice_skip", "choice_confirm", "potion", "card"):
        for action in actions:
            if action.get("action_type") == action_type:
                return str(action["action_id"])
    if not actions:
        raise AssertionError("expected an available action")
    return str(actions[0]["action_id"])


def _advance_root_to_combat(instance: WholeRunInstance) -> dict[str, Any]:
    response = instance.start_instance_response()
    for _ in range(80):
        dto = response.get("masked_emulator_dto")
        assert isinstance(dto, Mapping), response
        if _is_combat_decision(dto):
            return response

        actions = _available_actions(dto)
        assert actions, f"root stalled before Combat at boundary={dto.get('boundary')!r}"
        response = instance.commit_action(
            response["decision_point_id"],
            str(actions[0]["action_id"]),
        )
        assert response.get("status") == "completed", response

    raise AssertionError("expected to reach a Combat decision within 80 root decisions")


def _emulate_one_depth(
    instance: WholeRunInstance,
    parent: Mapping[str, Any],
    *,
    branch_id: str,
    rng_id: int,
) -> dict[str, Any]:
    dto = parent.get("masked_emulator_dto")
    assert isinstance(dto, Mapping) and _is_combat_decision(dto), parent
    response = instance.emulate_actions(
        items=[
            {
                "parent_branch_id": parent["branch_id"],
                "branch_id": branch_id,
                "rng_id": rng_id,
                "decision_point_id": parent["decision_point_id"],
                "action_id": _pick_nonterminal_friendly_action(dto),
            }
        ],
        simulation_options={"stop_condition": "next_decision"},
    )
    assert response.get("status") == "completed", response
    result = (response.get("branch_results") or {}).get(branch_id)
    assert isinstance(result, dict), response
    assert result.get("status") == "completed", result
    return result


def test_real_emulator_whole_run_beam_supports_two_depths() -> None:
    instance = WholeRunInstance(
        "real-whole-run-beam-connectivity",
        {"seed": 1, "character_id": "Ironclad", "ascension": 0},
        branch_worker_count=2,
        request_timeout_s=60.0,
        max_branches=8,
    )
    created: list[str] = []
    try:
        root = _advance_root_to_combat(instance)
        first = _emulate_one_depth(instance, root, branch_id="real-beam-d1", rng_id=1)
        created.append("real-beam-d1")

        first_dto = first.get("masked_emulator_dto")
        assert isinstance(first_dto, Mapping), first
        assert _is_combat_decision(first_dto), (
            "the first real branch unexpectedly left Combat; the preferred system action "
            "should leave a follow-up Combat decision",
            first,
        )

        second = _emulate_one_depth(
            instance,
            first,
            branch_id="real-beam-d2",
            rng_id=first["rng_id"],
        )
        created.append("real-beam-d2")

        assert second["parent_branch_id"] == "real-beam-d1"
        assert second["rng_id"] == first["rng_id"]
        assert len(second.get("branch_log") or []) >= 2
    finally:
        if created:
            instance.release_branches(created)
        instance.close()


if __name__ == "__main__":
    test_real_emulator_whole_run_beam_supports_two_depths()
    print("PASS test_real_emulator_whole_run_beam_supports_two_depths")
