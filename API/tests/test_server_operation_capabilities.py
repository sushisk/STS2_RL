"""Protocol capability regressions for instance-specific operations."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.dto import (  # noqa: E402
    INSTANCE_TYPE_WHOLE_RUN,
    OP_EMULATE_ACTIONS,
    SCHEMA_VERSION,
    STATUS_COMPLETED,
    request_id_for,
)
from API.instance_whole_run_beam import WholeRunInstance, _is_combat_view  # noqa: E402
from API.server import RLApiServer  # noqa: E402


class _WholeRunStub:
    instance_type = INSTANCE_TYPE_WHOLE_RUN

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def emulate_actions(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": STATUS_COMPLETED, "branch_results": {}}


def _view(*actions: dict) -> SimpleNamespace:
    return SimpleNamespace(legal_actions_raw=list(actions))


class _CombatView:
    boundary = "combat"
    map_snapshot = "{}"
    room_id = 1
    action_prefix = ()
    choice_type = "combat"

    def __init__(self) -> None:
        self.legal_actions_raw = [
            {"action_id": 1, "action_type": "card", "is_available": True},
            {"action_id": 2, "action_type": "system", "is_available": True},
        ]

    def resolve_action_id(self, action_id: str) -> int:
        for index, action in enumerate(self.legal_actions_raw):
            if str(action["action_id"]) == action_id:
                return index
        raise AssertionError(f"unexpected action_id: {action_id}")


class _BranchIds:
    def __init__(self) -> None:
        self.known: set[str] = set()

    def is_known(self, branch_id: str) -> bool:
        return branch_id in self.known

    def register(self, branch_id: str) -> None:
        self.known.add(branch_id)


class _DecisionPoints:
    def validate(self, branch_id: str, decision_point_id: str) -> None:
        assert branch_id == "root"
        assert decision_point_id == "decision-1"


class _History:
    def fork(self):
        return self


class _Pool:
    def __init__(self) -> None:
        self.calls: list[list] = []

    def dispatch_choice_work_items(self, work_items, lease_registry):
        del lease_registry
        self.calls.append(list(work_items))
        return [
            SimpleNamespace(
                status="fault",
                diagnostics={"message": "expected test fault", "fault_kind": "emulator_error"},
            )
            for _ in work_items
        ]


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


def test_whole_run_combat_batch_uses_one_worker_pool_dispatch() -> None:
    instance = object.__new__(WholeRunInstance)
    instance.max_branches = 8
    instance._branch_ids = _BranchIds()  # noqa: SLF001
    instance._decision_points = _DecisionPoints()  # noqa: SLF001
    instance._bookkeeping = {}  # noqa: SLF001
    instance._root_history = _History()  # noqa: SLF001
    instance._root_branch_log = []  # noqa: SLF001
    instance._lease_registry = object()  # noqa: SLF001
    instance._pool = _Pool()  # noqa: SLF001
    combat_view = _CombatView()
    instance._view_for = lambda branch_id: combat_view  # type: ignore[method-assign]  # noqa: SLF001

    items = [
        {
            "parent_branch_id": "root",
            "branch_id": "branch-1",
            "rng_id": 1,
            "decision_point_id": "decision-1",
            "action_id": "1",
        },
        {
            "parent_branch_id": "root",
            "branch_id": "branch-2",
            "rng_id": 2,
            "decision_point_id": "decision-1",
            "action_id": "2",
        },
    ]

    response = instance.emulate_actions(items=items, simulation_options=None)

    assert response["status"] == STATUS_COMPLETED
    assert set(response["branch_results"]) == {"branch-1", "branch-2"}
    assert len(instance._pool.calls) == 1  # noqa: SLF001
    assert len(instance._pool.calls[0]) == 2  # noqa: SLF001


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
