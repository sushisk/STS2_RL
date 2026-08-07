"""Pure-Python state-transition coverage for CombatInstance.

These tests deliberately avoid constructing the Emulator/CLR runtime. Step 1 only makes
instance/root execution state explicit; asynchronous scheduling remains unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import (  # noqa: E402
    INSTANCE_LIFECYCLE_ACTIVE,
    INSTANCE_LIFECYCLE_CLOSED,
    INSTANCE_LIFECYCLE_CLOSING,
    ROOT_EXECUTION_IDLE,
    ROOT_EXECUTION_RUNNING,
    CombatInstance,
)
from API.validation import RequestRejected  # noqa: E402


class _CleanupRecorder:
    def __init__(self, instance: CombatInstance) -> None:
        self.instance = instance
        self.calls = 0

    def close_all(self) -> None:
        self.calls += 1
        assert self.instance.lifecycle_state == INSTANCE_LIFECYCLE_CLOSING

    def close(self) -> None:
        self.calls += 1
        assert self.instance.lifecycle_state == INSTANCE_LIFECYCLE_CLOSING


class _DecisionPoints:
    def validate(self, branch_id: str, decision_point_id: str) -> None:
        return None


class _FaultingSession:
    def __init__(self, instance: CombatInstance) -> None:
        self.instance = instance

    def step(self, *args, **kwargs):
        assert self.instance.root_execution_state == ROOT_EXECUTION_RUNNING
        raise RuntimeError("synthetic emulator failure")


class _View:
    legal_actions_raw = [{"action_type": "test", "parameters": {}}]

    def resolve_action_id(self, action_id: str) -> int:
        return 0


class _CommitHarness(CombatInstance):
    def __init__(self) -> None:
        self._lifecycle_state = INSTANCE_LIFECYCLE_ACTIVE
        self._root_execution_state = ROOT_EXECUTION_IDLE
        self._decision_points = _DecisionPoints()
        self._session = _FaultingSession(self)
        self._root_state = object()
        self._root_branch_log = []

    def _root_view(self) -> _View:
        return _View()


def test_explicit_state_defaults_and_root_transition() -> None:
    inst = CombatInstance.__new__(CombatInstance)
    inst._lifecycle_state = INSTANCE_LIFECYCLE_ACTIVE
    inst._root_execution_state = ROOT_EXECUTION_IDLE

    assert inst.lifecycle_state == INSTANCE_LIFECYCLE_ACTIVE
    assert inst.root_execution_state == ROOT_EXECUTION_IDLE

    inst._begin_root_execution()
    assert inst.root_execution_state == ROOT_EXECUTION_RUNNING

    with pytest.raises(RequestRejected):
        inst._begin_root_execution()

    inst._end_root_execution()
    assert inst.root_execution_state == ROOT_EXECUTION_IDLE


def test_commit_action_restores_idle_after_emulator_fault() -> None:
    inst = _CommitHarness()
    response = inst.commit_action("d-root-test", "0")
    assert response["status"] == "faulted", response
    assert response["error"] == "synthetic emulator failure"
    assert inst.root_execution_state == ROOT_EXECUTION_IDLE


def test_close_transitions_active_to_closing_to_closed() -> None:
    inst = CombatInstance.__new__(CombatInstance)
    inst._lifecycle_state = INSTANCE_LIFECYCLE_ACTIVE
    inst._root_execution_state = ROOT_EXECUTION_IDLE
    branch_manager = _CleanupRecorder(inst)
    pool = _CleanupRecorder(inst)
    inst._branch_manager = branch_manager
    inst._pool = pool

    inst.close()
    assert inst.lifecycle_state == INSTANCE_LIFECYCLE_CLOSED
    assert branch_manager.calls == 1
    assert pool.calls == 1

    # close remains idempotent once the lifecycle reaches closed.
    inst.close()
    assert branch_manager.calls == 1
    assert pool.calls == 1
