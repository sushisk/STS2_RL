"""Hosted coverage for Active Event batches on Whole Run Beam Search.

These tests stay pure-Python: they build frontier views and a fake WorkerPool without
constructing the Emulator/CLR runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.dto import ROOT_BRANCH_ID, STATUS_COMPLETED, STATUS_FAULTED  # noqa: E402
from API.identifiers import BranchIdRegistry, DecisionPointRegistry  # noqa: E402
from API.instance_whole_run import (  # noqa: E402
    EVENT_CHOICE,
    WholeRunInstance as BaseWholeRunInstance,
    _View,
)
from API.instance_whole_run_beam import WholeRunInstance  # noqa: E402
from API.validation import RequestRejected  # noqa: E402
from API.whole_run_event_rng import EventRngHypothesisRegistry  # noqa: E402


class _FakeHistory:
    def fork(self) -> "_FakeHistory":
        return _FakeHistory()

    def observe_room_context(self, room_context: dict) -> None:
        return None


class _FaultingPool:
    def __init__(self) -> None:
        self.calls: list[list[object]] = []

    def dispatch_choice_work_items(self, work_items, lease_registry):
        self.calls.append(list(work_items))
        return [
            SimpleNamespace(
                status="fault",
                diagnostics={
                    "message": "synthetic worker result",
                    "fault_kind": "synthetic_fault",
                },
                step=None,
            )
            for _ in work_items
        ]


def _event_rng_state() -> dict:
    stream = {"counter": 0, "s0": 1, "s1": 2, "s2": 3, "s3": 4}
    return {
        "event_id": "TEST_EVENT",
        "event_rng": dict(stream),
        "player_rewards_rng": dict(stream),
        "player_shops_rng": dict(stream),
        "player_transformations_rng": dict(stream),
    }


def _event_view() -> _View:
    return _View(
        legal_actions_raw=[
            {
                "action_id": 101,
                "action_type": "choice_event_option",
                "label": "left",
                "is_available": True,
                "parameters": {},
            },
            {
                "action_id": 102,
                "action_type": "choice_event_option",
                "label": "right",
                "is_available": True,
                "parameters": {},
            },
        ],
        boundary=EVENT_CHOICE,
        observation={"boundary": EVENT_CHOICE},
        room_context={},
        map_snapshot="map-snapshot",
        room_id=17,
        action_prefix=(7, 8),
        choice_type=EVENT_CHOICE,
        chain_blocked=False,
        event_rng_state=_event_rng_state(),
    )


def _combat_view() -> _View:
    return _View(
        legal_actions_raw=[
            {
                "action_id": 201,
                "action_type": "card",
                "label": "strike",
                "is_available": True,
                "parameters": {},
            }
        ],
        boundary="stable",
        observation={"boundary": "stable"},
        room_context={},
        map_snapshot="map-snapshot",
        room_id=17,
        action_prefix=(7, 8),
        choice_type="stable",
        chain_blocked=False,
        event_rng_state=None,
    )


def _instance(views: dict[str, _View]) -> tuple[WholeRunInstance, _FaultingPool]:
    inst = object.__new__(WholeRunInstance)
    inst.max_branches = 16
    inst._branch_ids = BranchIdRegistry()
    inst._decision_points = DecisionPointRegistry()
    for branch_id in views:
        inst._decision_points.issue(branch_id)
    inst._bookkeeping = {}
    inst._root_history = _FakeHistory()
    inst._root_branch_log = []
    inst._event_rng_registry = EventRngHypothesisRegistry()
    inst._lease_registry = object()
    pool = _FaultingPool()
    inst._pool = pool
    inst._view_for = lambda branch_id: views[branch_id]
    return inst, pool


def _event_items(decision_point_id: str) -> list[dict]:
    return [
        {
            "parent_branch_id": ROOT_BRANCH_ID,
            "branch_id": "event-left",
            "rng_id": 1,
            "decision_point_id": decision_point_id,
            "action_id": "101",
        },
        {
            "parent_branch_id": ROOT_BRANCH_ID,
            "branch_id": "event-right",
            "rng_id": 2,
            "decision_point_id": decision_point_id,
            "action_id": "102",
        },
    ]


def test_start_response_advertises_event_batch_semantic_capability() -> None:
    inst = object.__new__(WholeRunInstance)
    inst.max_branches = 16
    with patch.object(
        BaseWholeRunInstance,
        "start_instance_response",
        return_value={"status": STATUS_COMPLETED, "instance_id": "inst"},
    ):
        response = inst.start_instance_response()

    assert response["max_emulate_actions_items"] == 16
    assert EVENT_CHOICE in response["emulate_actions_boundaries"]
    assert set(response["emulate_actions_boundaries"]) == {
        EVENT_CHOICE,
        "pending_choice",
        "stable",
    }


def test_event_frontier_uses_one_worker_pool_batch_with_rng_plans() -> None:
    view = _event_view()
    inst, pool = _instance({ROOT_BRANCH_ID: view})
    decision_point_id = inst._decision_points.current(ROOT_BRANCH_ID)
    assert decision_point_id is not None

    response = inst.emulate_actions(
        items=_event_items(decision_point_id),
        simulation_options={"stop_condition": "next_decision"},
    )

    assert response["status"] == STATUS_COMPLETED
    assert response["branch_results"]["event-left"]["status"] == STATUS_FAULTED
    assert response["branch_results"]["event-right"]["status"] == STATUS_FAULTED
    assert len(pool.calls) == 1
    assert len(pool.calls[0]) == 2

    left, right = pool.calls[0]
    assert left.event_rng_plan is not None
    assert right.event_rng_plan is not None
    assert left.event_rng_plan.hypothesis_key == (
        ROOT_BRANCH_ID,
        decision_point_id,
        1,
    )
    assert right.event_rng_plan.hypothesis_key == (
        ROOT_BRANCH_ID,
        decision_point_id,
        2,
    )
    assert left.event_rng_plan.apply_before_action_index == len(view.action_prefix)
    assert right.event_rng_plan.apply_before_action_index == len(view.action_prefix)


def test_same_event_rng_id_still_shares_one_hypothesis_state() -> None:
    view = _event_view()
    inst, pool = _instance({ROOT_BRANCH_ID: view})
    decision_point_id = inst._decision_points.current(ROOT_BRANCH_ID)
    assert decision_point_id is not None

    inst.emulate_actions(
        items=[
            {
                "parent_branch_id": ROOT_BRANCH_ID,
                "branch_id": "event-left",
                "rng_id": 7,
                "decision_point_id": decision_point_id,
                "action_id": "101",
            },
            {
                "parent_branch_id": ROOT_BRANCH_ID,
                "branch_id": "event-right",
                "rng_id": 7,
                "decision_point_id": decision_point_id,
                "action_id": "102",
            },
        ],
        simulation_options=None,
    )

    left, right = pool.calls[0]
    assert left.event_rng_plan is not None
    assert right.event_rng_plan is not None
    assert left.event_rng_plan.hypothesis_key == right.event_rng_plan.hypothesis_key
    assert left.event_rng_plan.override_state == right.event_rng_plan.override_state
    assert inst._event_rng_registry.generation_of(
        left.event_rng_plan.hypothesis_key
    ) == 1


def test_event_and_combat_parents_cannot_be_mixed_in_one_batch() -> None:
    event_view = _event_view()
    combat_view = _combat_view()
    inst, pool = _instance(
        {
            ROOT_BRANCH_ID: event_view,
            "combat-parent": combat_view,
        }
    )
    event_dp = inst._decision_points.current(ROOT_BRANCH_ID)
    combat_dp = inst._decision_points.current("combat-parent")
    assert event_dp is not None
    assert combat_dp is not None

    try:
        inst.emulate_actions(
            items=[
                {
                    "parent_branch_id": ROOT_BRANCH_ID,
                    "branch_id": "event-child",
                    "rng_id": 1,
                    "decision_point_id": event_dp,
                    "action_id": "101",
                },
                {
                    "parent_branch_id": "combat-parent",
                    "branch_id": "combat-child",
                    "rng_id": 1,
                    "decision_point_id": combat_dp,
                    "action_id": "201",
                },
            ],
            simulation_options=None,
        )
    except RequestRejected as exc:
        assert "cannot mix Active Event and non-Event" in str(exc)
    else:
        raise AssertionError("mixed Event/Combat batch must be rejected")

    assert pool.calls == []
    assert not inst._branch_ids.is_known("event-child")
    assert not inst._branch_ids.is_known("combat-child")


def test_event_prepare_failure_does_not_publish_branch_or_rng_ownership() -> None:
    view = _event_view()
    inst, pool = _instance({ROOT_BRANCH_ID: view})
    decision_point_id = inst._decision_points.current(ROOT_BRANCH_ID)
    assert decision_point_id is not None

    original = inst._branch_history_and_log

    def fail_second(spec):
        if spec.branch_id == "event-right":
            raise RuntimeError("synthetic prepare failure")
        return original(spec)

    inst._branch_history_and_log = fail_second
    try:
        inst.emulate_actions(
            items=_event_items(decision_point_id),
            simulation_options=None,
        )
    except RuntimeError as exc:
        assert "synthetic prepare failure" in str(exc)
    else:
        raise AssertionError("prepare failure must propagate")

    assert pool.calls == []
    assert inst._bookkeeping == {}
    assert not inst._branch_ids.is_known("event-left")
    assert not inst._branch_ids.is_known("event-right")
    assert inst._event_rng_registry.generation_of(
        (ROOT_BRANCH_ID, decision_point_id, 1)
    ) == 0
    assert inst._event_rng_registry.generation_of(
        (ROOT_BRANCH_ID, decision_point_id, 2)
    ) == 0


def test_event_commit_failure_rolls_back_earlier_batch_commits() -> None:
    view = _event_view()
    inst, pool = _instance({ROOT_BRANCH_ID: view})
    decision_point_id = inst._decision_points.current(ROOT_BRANCH_ID)
    assert decision_point_id is not None

    original_commit = inst._event_rng_registry.commit_branch

    def fail_second(key, state, branch_id):
        if branch_id == "event-right":
            raise RuntimeError("synthetic commit failure")
        return original_commit(key, state, branch_id)

    inst._event_rng_registry.commit_branch = fail_second
    try:
        inst.emulate_actions(
            items=_event_items(decision_point_id),
            simulation_options=None,
        )
    except RuntimeError as exc:
        assert "synthetic commit failure" in str(exc)
    else:
        raise AssertionError("commit failure must propagate")

    assert pool.calls == []
    assert inst._bookkeeping == {}
    assert not inst._branch_ids.is_known("event-left")
    assert not inst._branch_ids.is_known("event-right")
    assert not inst._event_rng_registry.is_live(
        (ROOT_BRANCH_ID, decision_point_id, 1)
    )
    assert not inst._event_rng_registry.is_live(
        (ROOT_BRANCH_ID, decision_point_id, 2)
    )
    assert inst._event_rng_registry.generation_of(
        (ROOT_BRANCH_ID, decision_point_id, 1)
    ) == 0
    assert inst._event_rng_registry.generation_of(
        (ROOT_BRANCH_ID, decision_point_id, 2)
    ) == 0
