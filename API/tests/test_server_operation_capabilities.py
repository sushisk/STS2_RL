"""Protocol capability regressions for instance-specific operations."""

from __future__ import annotations

from types import SimpleNamespace

from API.dto import (
    INSTANCE_TYPE_WHOLE_RUN,
    OP_EMULATE_ACTIONS,
    SCHEMA_VERSION,
    STATUS_COMPLETED,
    request_id_for,
)
from API.instance_whole_run_beam import _is_combat_view
from API.server import RLApiServer


class _WholeRunStub:
    instance_type = INSTANCE_TYPE_WHOLE_RUN

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def emulate_actions(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": STATUS_COMPLETED, "branch_results": {}}


def _view(*actions: dict) -> SimpleNamespace:
    return SimpleNamespace(legal_actions_raw=list(actions))


def test_emulate_actions_against_whole_run_is_dispatched() -> None:
    server = RLApiServer(server_epoch="epoch-capabilities")
    session_id = "session-capabilities"
    instance_id = "inst-whole-run"
    stub = _WholeRunStub()

    ledger = server._get_or_create_session(session_id, 1)  # noqa: SLF001
    ledger.active_instance_id = instance_id
    server._instances[instance_id] = stub  # noqa: SLF001

    items = [
        {
            "parent_branch_id": "root",
            "branch_id": "branch-1",
            "rng_id": 1,
            "decision_point_id": "decision-1",
            "action_id": "action-1",
        }
    ]
    response = server.handle_request(
        {
            "schema_version": SCHEMA_VERSION,
            "client_session_id": session_id,
            "request_seq": 1,
            "request_id": request_id_for(session_id, 1),
            "operation": OP_EMULATE_ACTIONS,
            "instance_id": instance_id,
            "items": items,
        }
    )

    assert response["status"] == STATUS_COMPLETED
    assert response["operation"] == OP_EMULATE_ACTIONS
    assert response["instance_id"] == instance_id
    assert stub.calls == [{"items": items, "simulation_options": None}]


def test_whole_run_combat_action_types_are_branchable() -> None:
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


def test_whole_run_non_combat_action_types_stay_out_of_scope() -> None:
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
