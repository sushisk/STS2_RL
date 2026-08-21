"""S5 transaction coverage without constructing CLR workers or a real run."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import game_access  # noqa: E402
from API.identifiers import DecisionPointRegistry  # noqa: E402
from API.instance_whole_run import WholeRunInstance  # noqa: E402
import API.instance_whole_run as whole_run_module  # noqa: E402


class _Game:
    pass


class _Session:
    def __init__(self) -> None:
        self.access = game_access.GameAccess(_Game)
        self.lease_holder = object()
        self.lease = self.access.claim(self.lease_holder, game_access.LeaseState.RUN)
        self.commits = 0

    def begin_lease_transfer(self):
        transfer = self.access.begin(self.lease)
        self.lease = None
        return transfer

    def commit_lease_transfer(self, transfer, target, holder):
        self.commits += 1
        return self.access.commit(transfer, target, holder)

    def rollback_lease_transfer(self, transfer) -> None:
        self.lease = self.access.rollback(transfer)

    def accept_transferred_lease(self, lease) -> None:
        assert lease.holder is self.lease_holder
        assert lease.state is game_access.LeaseState.RUN
        self.access.mutating_game(lease)
        self.lease = lease

    def poison_mutations(self) -> None:
        self.lease = None
        self.access.poison()


class _LiveSession:
    def __init__(self, *, whole_run_mode: bool) -> None:
        assert whole_run_mode is True
        self.lease_holder = object()
        self.lease = None
        self.adopted = 0

    def adopt_current_combat(self):
        self.adopted += 1
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
    def __init__(self, session) -> None:
        self._session = session
        self.closed = 0

    def close(self) -> None:
        self.closed += 1

    def begin_lease_transfer(self):
        return self._session.begin_lease_transfer()


class _Pool:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _DecisionPoints(DecisionPointRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.issued: list[str] = []

    def issue(self, branch_id: str) -> str:
        self.issued.append(branch_id)
        return super().issue(branch_id)


_CURRENT_SESSION: _Session


def _instance() -> WholeRunInstance:
    global _CURRENT_SESSION
    _CURRENT_SESSION = _Session()
    instance = object.__new__(WholeRunInstance)
    instance._session = _CURRENT_SESSION  # noqa: SLF001
    instance._decision_points = _DecisionPoints()  # noqa: SLF001
    instance._decision_points.issue("root")  # noqa: SLF001
    instance._combat_phase = None  # noqa: SLF001
    instance._last_combat_completion = None  # noqa: SLF001
    instance._faulted = False  # noqa: SLF001
    instance.max_branches = 4
    instance._combat_worker_count = 2  # noqa: SLF001
    instance._request_timeout_s = 60.0  # noqa: SLF001
    instance._action_prefix = []  # noqa: SLF001
    instance._pool = _Pool()  # noqa: SLF001
    instance._cancel_and_release_all_branches = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    instance._maybe_capture_map_snapshot = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    return instance


def _install_phase_fakes(monkeypatch, *, adopt=None) -> list[_Phase]:
    phases: list[_Phase] = []
    monkeypatch.setattr(whole_run_module, "LiveCombatSession", _LiveSession)

    class _PhaseFactory:
        @classmethod
        def adopt(cls, session, root_state, **kwargs):
            if adopt is not None:
                adopt()
            phase = _Phase(session)
            phases.append(phase)
            return phase

    monkeypatch.setattr(whole_run_module, "CombatPhase", _PhaseFactory)
    return phases


def test_enter_commits_combat_lease_to_adopted_phase_and_issues_one_root_decision(monkeypatch) -> None:
    instance = _instance()
    phases = _install_phase_fakes(monkeypatch)

    phase = instance.enter_combat_phase()

    assert phase is phases[0]
    assert _CURRENT_SESSION.access.state is game_access.LeaseState.COMBAT
    assert phase._session.lease is not None
    assert phase._session.lease.state is game_access.LeaseState.COMBAT
    assert instance._decision_points.issued == ["root", "root"]  # noqa: SLF001


@pytest.mark.parametrize("failure_step", ["begin", "adopt", "issue", "commit"])
def test_enter_precommit_failures_rollback_to_run_without_a_phase(monkeypatch, failure_step: str) -> None:
    instance = _instance()
    phases = _install_phase_fakes(monkeypatch, adopt=(lambda: (_ for _ in ()).throw(RuntimeError("adopt"))) if failure_step == "adopt" else None)
    original_begin = _CURRENT_SESSION.begin_lease_transfer
    original_issue = instance._decision_points.issue  # noqa: SLF001
    original_commit = _CURRENT_SESSION.commit_lease_transfer
    if failure_step == "begin":
        _CURRENT_SESSION.begin_lease_transfer = lambda: (_ for _ in ()).throw(RuntimeError("begin"))
    elif failure_step == "issue":
        instance._decision_points.issue = lambda branch_id: (_ for _ in ()).throw(RuntimeError("issue"))  # type: ignore[method-assign]  # noqa: SLF001
    elif failure_step == "commit":
        _CURRENT_SESSION.commit_lease_transfer = lambda *args: (_ for _ in ()).throw(RuntimeError("commit"))

    with pytest.raises(RuntimeError, match=failure_step):
        instance.enter_combat_phase()

    assert _CURRENT_SESSION.access.state is game_access.LeaseState.RUN
    assert instance._combat_phase is None  # noqa: SLF001
    assert instance._decision_points.current("root") == "d-root-000001"  # noqa: SLF001
    if phases:
        assert phases[0].closed == 1
    _CURRENT_SESSION.begin_lease_transfer = original_begin
    instance._decision_points.issue = original_issue  # type: ignore[method-assign]  # noqa: SLF001
    _CURRENT_SESSION.commit_lease_transfer = original_commit


def test_leave_releases_branches_before_folding_and_returns_run_lease(monkeypatch) -> None:
    instance = _instance()
    phases = _install_phase_fakes(monkeypatch)
    phase = instance.enter_combat_phase()
    events: list[str] = []
    instance._cancel_and_release_all_branches = lambda: events.append("released")  # type: ignore[method-assign]  # noqa: SLF001
    original_close = phase.close
    phase.close = lambda: (events.append("folded"), original_close())[1]
    monkeypatch.setattr(whole_run_module, "drain_trivial_reward_frontier", lambda session: SimpleNamespace(auto_action_ids=(7,)))
    completion = object()

    instance.leave_combat_phase(completion)

    assert events == ["released", "folded"]
    assert _CURRENT_SESSION.access.state is game_access.LeaseState.RUN
    assert instance._combat_phase is None  # noqa: SLF001
    assert instance._last_combat_completion is completion  # noqa: SLF001
    assert instance._action_prefix == [7]  # noqa: SLF001


def test_post_step_bookkeeping_failure_poisons_and_refuses_later_mutations(monkeypatch) -> None:
    instance = _instance()
    _install_phase_fakes(monkeypatch)
    phase = instance.enter_combat_phase()
    monkeypatch.setattr(whole_run_module, "drain_trivial_reward_frontier", lambda session: SimpleNamespace(auto_action_ids=()))
    instance._maybe_capture_map_snapshot = lambda: (_ for _ in ()).throw(RuntimeError("publication"))  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(RuntimeError, match="publication"):
        instance.leave_combat_phase(object())

    assert _CURRENT_SESSION.access.state is game_access.LeaseState.POISONED
    assert instance._faulted is True  # noqa: SLF001
    assert phase.closed == 1
    assert instance._pool.closed == 1  # noqa: SLF001
    with pytest.raises(Exception, match="faulted"):
        instance.enter_combat_phase()
