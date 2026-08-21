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
    FAULT_TASK_TIMEOUT,
    INSTANCE_TYPE_WHOLE_RUN,
    OP_EMULATE_ACTIONS,
    SCHEMA_VERSION,
    STATUS_COMPLETED,
    STATUS_FAULTED,
    request_id_for,
)
from API.instance_whole_run_beam import WholeRunInstance, _is_combat_view  # noqa: E402
from API.server import RLApiServer  # noqa: E402
from API.validation import RequestRejected  # noqa: E402


class _WholeRunStub:
    instance_type = INSTANCE_TYPE_WHOLE_RUN

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def emulate_actions(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": STATUS_COMPLETED, "branch_results": {}}


def _view(
    *actions: dict,
    boundary: str = "stable",
    room_type: str = "CombatRoom",
) -> SimpleNamespace:
    """A Whole Run view stub.

    `room_context` is a declared field of the real view and is part of what decides
    whether a view is a Combat view: a rest site's smith prompt and a card reward publish
    `choice_card` / `choice_confirm` / `choice_skip`, which are combat action types, so
    boundary and action type alone cannot tell them apart from combat.
    """
    return SimpleNamespace(
        boundary=boundary,
        room_context={"room_type": room_type},
        legal_actions_raw=list(actions),
    )


class _CombatView:
    boundary = "stable"
    room_context = {"room_type": "CombatRoom"}
    observation = {"state": {"stepIndex": 7, "totalFloor": 3, "hp": 60, "energy": 3}}
    map_snapshot = "{}"
    room_id = 1
    action_prefix = ()
    choice_type = "stable"

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
    def __init__(self) -> None:
        self._current = {"root": "decision-1"}
        self._counter = 1

    def validate(self, branch_id: str, decision_point_id: str) -> None:
        assert branch_id == "root"
        assert decision_point_id == "decision-1"

    def issue(self, branch_id: str) -> None:
        self._counter += 1
        self._current[branch_id] = f"decision-{self._counter}"

    def current(self, branch_id: str) -> str:
        return self._current[branch_id]


class _History:
    def fork(self):
        return self

    def observe_room_context(self, room_context) -> None:
        del room_context

    def to_public_list(self) -> list:
        return []


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


class _CombatCompletionPool:
    def dispatch_choice_work_items(self, work_items, lease_registry):
        del lease_registry
        return [
            SimpleNamespace(
                status="success",
                diagnostics={},
                step=SimpleNamespace(
                    step_result={
                        "transition": {
                            "kind": "combat_completed",
                            "victory": True,
                            "combat_session_id": "hidden-combat-session",
                            "final_observation": {
                                "seed": 123456,
                                "state": {"hp": 42, "maxHp": 80},
                            },
                        }
                    },
                    settled_observation={
                        "boundary": "reward_select",
                        "state": {"hp": 42, "maxHp": 80},
                    },
                    settled_room_context={"boundary": "reward_select"},
                    settled_legal_actions=[
                        {
                            "action_id": 10,
                            "action_type": "choice_reward_card",
                            "is_available": True,
                        }
                    ],
                    auto_action_ids=(),
                ),
            )
            for _ in work_items
        ]


class _TimeoutPool:
    def dispatch_choice_work_items(self, work_items, lease_registry):
        del work_items, lease_registry
        raise TimeoutError("worker batch timed out")


def _combat_instance(pool) -> WholeRunInstance:
    instance = object.__new__(WholeRunInstance)
    instance.max_branches = 8
    instance._branch_ids = _BranchIds()  # noqa: SLF001
    instance._decision_points = _DecisionPoints()  # noqa: SLF001
    instance._bookkeeping = {}  # noqa: SLF001
    instance._root_history = _History()  # noqa: SLF001
    instance._root_branch_log = []  # noqa: SLF001
    instance._lease_registry = object()  # noqa: SLF001
    instance._pool = pool  # noqa: SLF001
    instance._session = SimpleNamespace(  # noqa: SLF001
        get_observation=lambda: {"boundary": "stable"}
    )
    combat_view = _CombatView()
    instance._view_for = lambda branch_id: combat_view  # type: ignore[method-assign]  # noqa: SLF001
    return instance


def _combat_items() -> list[dict]:
    return [
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
    pool = _Pool()
    instance = _combat_instance(pool)

    response = instance.emulate_actions(items=_combat_items(), simulation_options=None)

    assert response["status"] == STATUS_COMPLETED
    assert set(response["branch_results"]) == {"branch-1", "branch-2"}
    assert len(pool.calls) == 1
    assert len(pool.calls[0]) == 2


def test_whole_run_combat_timeout_is_reported_per_branch() -> None:
    instance = _combat_instance(_TimeoutPool())

    response = instance.emulate_actions(items=_combat_items(), simulation_options=None)

    assert response["status"] == STATUS_COMPLETED
    assert set(response["branch_results"]) == {"branch-1", "branch-2"}
    for result in response["branch_results"].values():
        assert result["status"] == STATUS_FAULTED
        assert result["fault_kind"] == FAULT_TASK_TIMEOUT
        assert result["error"] == "worker batch timed out"


def test_whole_run_single_combat_timeout_keeps_branch_fault_semantics() -> None:
    instance = _combat_instance(_TimeoutPool())

    response = instance.emulate_action(
        parent_branch_id="root",
        branch_id="branch-1",
        rng_id=1,
        decision_point_id="decision-1",
        action_id="1",
        simulation_options=None,
    )

    assert response["status"] == STATUS_FAULTED
    assert response["branch_id"] == "branch-1"
    assert response["fault_kind"] == FAULT_TASK_TIMEOUT
    assert response["error"] == "worker batch timed out"


def test_whole_run_combat_completion_transition_is_preserved_and_masked() -> None:
    instance = _combat_instance(_CombatCompletionPool())

    response = instance.emulate_action(
        parent_branch_id="root",
        branch_id="branch-1",
        rng_id=1,
        decision_point_id="decision-1",
        action_id="1",
        simulation_options=None,
    )

    dto = response["masked_emulator_dto"]
    assert response["status"] == STATUS_COMPLETED
    assert dto["boundary"] == "reward_select"
    assert dto["transition"]["kind"] == "combat_completed"
    assert dto["transition"]["victory"] is True
    assert "combat_session_id" not in dto["transition"]
    assert "seed" not in dto["transition"]["final_observation"]
    assert dto["legal_actions"][0]["action_type"] == "choice_reward_card"


def test_unavailable_whole_run_combat_action_is_rejected_before_dispatch() -> None:
    pool = _Pool()
    instance = _combat_instance(pool)
    view = instance._view_for("root")  # noqa: SLF001
    view.legal_actions_raw[0]["is_available"] = False

    try:
        instance.emulate_action(
            parent_branch_id="root",
            branch_id="branch-1",
            rng_id=1,
            decision_point_id="decision-1",
            action_id="1",
            simulation_options=None,
        )
    except RequestRejected as exc:
        assert "not currently available" in exc.error
    else:
        raise AssertionError("unavailable combat action must be rejected")

    assert pool.calls == []


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
            boundary="pending_choice",
        )
    )


def test_non_combat_boundary_stays_out_of_scope_even_with_combat_action_types() -> None:
    assert not _is_combat_view(
        _view(
            {"action_id": 1, "action_type": "choice_confirm", "is_available": True},
            {"action_id": 2, "action_type": "choice_skip", "is_available": True},
            boundary="event_choice",
        )
    )


def test_a_non_combat_room_stays_out_of_scope_with_combat_action_types() -> None:
    """A rest site's smith prompt publishes only `choice_card`, a combat action type."""
    assert not _is_combat_view(
        _view(
            {"action_id": 1, "action_type": "choice_card", "is_available": True},
            {"action_id": 2, "action_type": "choice_confirm", "is_available": True},
            boundary="pending_choice",
            room_type="RestSiteRoom",
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
