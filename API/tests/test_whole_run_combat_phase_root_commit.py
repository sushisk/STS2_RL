"""S6 root commits must be executed by the adopted combat phase."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import game_access  # noqa: E402
import API.instance_whole_run as whole_run_module  # noqa: E402
from API.history_builder import HistoryBuilder  # noqa: E402
from API.identifiers import DecisionPointRegistry  # noqa: E402
from API.instance_whole_run import WholeRunInstance  # noqa: E402


class _Game:
    def __init__(self) -> None:
        self.reset_from_scenario_calls = 0

    def ResetFromScenario(self, scenario) -> None:  # noqa: N802 - CLR spelling
        del scenario
        self.reset_from_scenario_calls += 1
        raise AssertionError("whole-run combat must never ResetFromScenario")


class _Session:
    """A WholeRunSession spy which rejects mutation while a phase is live."""

    def __init__(self) -> None:
        self.game = _Game()
        self.access = game_access.GameAccess(lambda: self.game)
        self.lease_holder = object()
        self.lease = self.access.claim(self.lease_holder, game_access.LeaseState.RUN)
        self.phase_active = False
        self.mutating_calls_while_phase_active: list[str] = []
        self.choose_room_calls = 0
        self.step_calls = 0
        self.in_combat = False

    def _record_mutation(self, name: str) -> None:
        if self.phase_active:
            self.mutating_calls_while_phase_active.append(name)
            raise AssertionError(f"WholeRunSession.{name} called during an active combat phase")

    def begin_lease_transfer(self):
        transfer = self.access.begin(self.lease)
        self.lease = None
        return transfer

    def commit_lease_transfer(self, transfer, target, holder):
        return self.access.commit(transfer, target, holder)

    def rollback_lease_transfer(self, transfer) -> None:
        self.lease = self.access.rollback(transfer)

    def accept_transferred_lease(self, lease) -> None:
        assert lease.holder is self.lease_holder
        assert lease.state is game_access.LeaseState.RUN
        self.lease = lease

    def poison_mutations(self) -> None:
        self.lease = None
        self.access.poison()

    def get_observation(self) -> dict:
        return {
            "boundary": "stable" if self.in_combat else "map_select",
            "state": {"hp": 80},
            "outcome": "in_progress",
        }

    def get_map_rooms(self) -> list[dict]:
        return [{"room_id": 7, "point_type": "Monster", "column": 1, "row": 2}]

    def get_legal_actions(self) -> list[dict]:
        assert self.in_combat
        return [{"action_id": 10, "action_type": "card", "label": "Strike", "is_available": True, "parameters": {}}]

    def get_room_context(self) -> dict:
        return {
            "in_room": self.in_combat,
            "room_type": "CombatRoom" if self.in_combat else "MapRoom",
            "column": 1,
            "row": 2,
        }

    def choose_room(self, room_id: int) -> dict:
        self._record_mutation("choose_room")
        assert room_id == 7
        self.choose_room_calls += 1
        self.in_combat = True
        return {}

    def step(self, action_id: int) -> dict:
        self._record_mutation("step")
        self.step_calls += 1
        raise AssertionError(f"combat action {action_id} was routed through WholeRunSession.step")

    def save_state(self) -> str:
        return "{}"


class _LiveSession:
    def __init__(self, *, whole_run_mode: bool) -> None:
        assert whole_run_mode is True
        self.lease_holder = object()
        self.lease = None

    def adopt_current_combat(self):
        return object()

    def accept_transferred_lease(self, lease) -> None:
        assert lease.holder is self.lease_holder
        assert lease.state is game_access.LeaseState.COMBAT
        self.lease = lease

    def begin_lease_transfer(self):
        transfer = _CURRENT_SESSION.access.begin(self.lease)
        self.lease = None
        return transfer


class _Phase:
    def __init__(self, session: _LiveSession, *, completion_on_commit: int | None = None) -> None:
        self._session = session
        self._root_state = SimpleNamespace(combat_completion=None)
        self._advanced = False
        self.commit_calls = 0
        self.closed = False
        self.completion_on_commit = completion_on_commit

    @property
    def root_state(self):
        return self._root_state

    def commit_root_action(self, chosen: dict) -> None:
        assert chosen["action_id"] == 10
        self.commit_calls += 1
        self._advanced = True
        if self.completion_on_commit == self.commit_calls:
            self._root_state.combat_completion = object()

    def root_commit_advanced(self) -> bool:
        return self._advanced

    def begin_lease_transfer(self):
        return self._session.begin_lease_transfer()

    def close(self) -> None:
        self.closed = True
        _CURRENT_SESSION.phase_active = False


class _DecisionPoints(DecisionPointRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.issued: list[str] = []

    def issue(self, branch_id: str) -> str:
        self.issued.append(branch_id)
        return super().issue(branch_id)


_CURRENT_SESSION: _Session


def _instance(monkeypatch, *, completion_on_commit: int | None = None) -> tuple[WholeRunInstance, list[_Phase]]:
    global _CURRENT_SESSION
    _CURRENT_SESSION = _Session()
    phases: list[_Phase] = []
    monkeypatch.setattr(whole_run_module, "LiveCombatSession", _LiveSession)

    class _PhaseFactory:
        @classmethod
        def adopt(cls, session, root_state, **kwargs):
            del cls, root_state, kwargs
            phase = _Phase(session, completion_on_commit=completion_on_commit)
            phases.append(phase)
            _CURRENT_SESSION.phase_active = True
            return phase

    monkeypatch.setattr(whole_run_module, "CombatPhase", _PhaseFactory)
    monkeypatch.setattr(
        whole_run_module,
        "drain_trivial_reward_frontier",
        lambda session: SimpleNamespace(auto_action_ids=()),
    )
    instance = object.__new__(WholeRunInstance)
    instance._session = _CURRENT_SESSION  # noqa: SLF001
    instance._decision_points = _DecisionPoints()  # noqa: SLF001
    instance._decision_points.issue("root")  # noqa: SLF001
    instance._combat_phase = None  # noqa: SLF001
    instance._last_combat_completion = None  # noqa: SLF001
    instance._faulted = False  # noqa: SLF001
    instance._map_snapshot = None  # noqa: SLF001
    instance._room_id = None  # noqa: SLF001
    instance._root_branch_log = []  # noqa: SLF001
    instance._root_history = HistoryBuilder()  # noqa: SLF001
    instance._map_snapshot = "{}"  # noqa: SLF001
    instance._room_id = None  # noqa: SLF001
    instance._action_prefix = []  # noqa: SLF001
    instance._cancel_and_release_all_branches = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    instance._maybe_capture_map_snapshot = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    instance._pool = None  # noqa: SLF001
    instance.max_branches = 4
    instance._combat_worker_count = 1  # noqa: SLF001
    instance._request_timeout_s = 1.0  # noqa: SLF001
    return instance, phases


def test_root_combat_commits_use_phase_preserve_root_shape_and_issue_one_id_per_decision(monkeypatch) -> None:
    instance, phases = _instance(monkeypatch)

    entered = instance.commit_action("d-root-000001", "7")
    combat = instance.commit_action(entered["decision_point_id"], "10")

    assert set(entered) == set(combat) == {
        "status", "branch_id", "decision_point_id", "branch_log", "masked_emulator_dto"
    }
    assert phases[0].commit_calls == 1
    assert _CURRENT_SESSION.choose_room_calls == 1
    assert _CURRENT_SESSION.step_calls == 0
    assert _CURRENT_SESSION.mutating_calls_while_phase_active == []
    assert _CURRENT_SESSION.game.reset_from_scenario_calls == 0
    assert instance._decision_points.issued == ["root", "root", "root"]  # noqa: SLF001


def test_combat_completion_leaves_phase_returns_run_and_still_issues_one_decision(monkeypatch) -> None:
    instance, phases = _instance(monkeypatch, completion_on_commit=1)

    entered = instance.commit_action("d-root-000001", "7")
    completed = instance.commit_action(entered["decision_point_id"], "10")

    assert completed["status"] == "completed"
    assert phases[0].closed is True
    assert instance._combat_phase is None  # noqa: SLF001
    assert _CURRENT_SESSION.access.state is game_access.LeaseState.RUN
    assert instance._decision_points.issued == ["root", "root", "root"]  # noqa: SLF001


def test_post_step_combat_phase_fault_poisons_instead_of_retaining_a_stale_frame(monkeypatch) -> None:
    instance, phases = _instance(monkeypatch)
    entered = instance.commit_action("d-root-000001", "7")

    def _advance_then_fail(chosen: dict) -> None:
        assert chosen["action_id"] == 10
        phases[0]._advanced = True  # noqa: SLF001
        raise RuntimeError("publication failed after Step")

    phases[0].commit_root_action = _advance_then_fail  # type: ignore[method-assign]
    response = instance.commit_action(entered["decision_point_id"], "10")

    assert response["status"] == "faulted"
    assert instance._faulted is True  # noqa: SLF001
    assert phases[0].closed is True
    assert _CURRENT_SESSION.access.state is game_access.LeaseState.POISONED
