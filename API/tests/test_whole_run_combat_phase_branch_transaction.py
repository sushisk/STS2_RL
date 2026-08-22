"""S7: adopted combat Branches are one Whole Run transaction, never prefix replay."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from API.dto import ROOT_BRANCH_ID  # noqa: E402
from API.history_builder import HistoryBuilder  # noqa: E402
from API.identifiers import BranchIdRegistry, DecisionPointRegistry  # noqa: E402
from API.instance_whole_run_beam import WholeRunInstance  # noqa: E402
from API.validation import RequestRejected  # noqa: E402


class _Session:
    def get_room_context(self) -> dict:
        return {"in_room": True, "room_type": "CombatRoom", "column": 1, "row": 2}

    def load_state(self, *_args, **_kwargs) -> None:
        raise AssertionError("combat phase Branch must not load a Whole Run snapshot")

    def choose_room(self, *_args, **_kwargs) -> None:
        raise AssertionError("combat phase Branch must not choose a Whole Run room")


class _Phase:
    def __init__(self, *, terminal: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.terminal = terminal
        self._next = 0

    @property
    def root_state(self):
        return SimpleNamespace(engine_state={})

    def root_decision(self):
        return ([{"action_id": 7, "action_type": "card", "is_available": True, "parameters": {}}], object(), "stable")

    def snapshot_rng_hypotheses(self):
        return 0

    def restore_rng_hypotheses(self, snapshot) -> None:
        self.calls.append(("restore_rng", snapshot))

    def build_work_item(self, context, chosen, parent, decision, rng):
        self.calls.append(("build", (context, chosen, parent, decision, rng)))
        return object()

    def submit_many(self, entries):
        self.calls.append(("submit", list(entries)))
        result = []
        for _ in entries:
            self._next += 1
            result.append(f"internal-{self._next}")
        return result

    def poll(self, *, timeout, branch_ids):
        self.calls.append(("poll", list(branch_ids)))
        if self.terminal:
            result = SimpleNamespace(
                status="success",
                result_signature=SimpleNamespace(boundary="terminal"),
                # A terminal branch must say how the combat ended: the wire contract
                # requires an outcome wherever `terminal` is set, and combat_completed
                # alone does not answer that.
                terminal_result=SimpleNamespace(outcome="victory"),
            )
        else:
            result = SimpleNamespace(
                status="fault",
                diagnostics={"message": "synthetic fault", "fault_kind": "synthetic_fault"},
            )
        return {branch_id: result for branch_id in branch_ids}

    def cancel(self, internal_ids) -> None:
        self.calls.append(("cancel", list(internal_ids)))

    def release(self, internal_ids) -> None:
        self.calls.append(("release", list(internal_ids)))

    def branch_status(self, internal_id):
        return "faulted"


def _instance(phase: _Phase, *, max_branches: int = 2) -> WholeRunInstance:
    instance = object.__new__(WholeRunInstance)
    instance.max_branches = max_branches
    instance._combat_phase = phase  # noqa: SLF001
    instance._combat_branch_reservations = set()  # noqa: SLF001
    instance._branch_ids = BranchIdRegistry()  # noqa: SLF001
    instance._decision_points = DecisionPointRegistry()  # noqa: SLF001
    instance._decision_points.issue(ROOT_BRANCH_ID)  # noqa: SLF001
    instance._bookkeeping = {}  # noqa: SLF001
    instance._root_history = HistoryBuilder()  # noqa: SLF001
    instance._root_branch_log = []  # noqa: SLF001
    instance._session = _Session()  # noqa: SLF001
    instance._faulted = False  # noqa: SLF001
    instance._map_snapshot = None  # noqa: SLF001
    instance._room_id = None  # noqa: SLF001
    # Any attempt to take the pre-S7 route makes the test fail immediately.
    instance._pool = SimpleNamespace(dispatch_choice_work_items=lambda *_: (_ for _ in ()).throw(AssertionError("whole-room prefix replay used")))  # noqa: SLF001
    return instance


def _item(instance: WholeRunInstance, branch_id: str) -> dict:
    return {
        "parent_branch_id": ROOT_BRANCH_ID,
        "branch_id": branch_id,
        "rng_id": 0,
        "decision_point_id": instance._decision_points.current(ROOT_BRANCH_ID),  # noqa: SLF001
        "action_id": "7",
    }


def test_combat_batch_reserves_global_capacity_before_phase_dispatch_and_releases_to_tombstone() -> None:
    phase = _Phase()
    instance = _instance(phase)

    response = instance.emulate_actions(items=[_item(instance, "a"), _item(instance, "b")], simulation_options=None)

    assert response["status"] == "completed"
    assert [name for name, _ in phase.calls] == ["build", "build", "submit", "poll"]
    assert instance._combat_branch_reservations == {"a", "b"}  # noqa: SLF001
    with pytest.raises(RequestRejected, match="would exceed max_branches"):
        instance.emulate_actions(items=[_item(instance, "c")], simulation_options=None)

    instance.release_branches(["a", "b"])
    assert instance._combat_branch_reservations == set()  # noqa: SLF001
    assert instance.get_branch_status(["a", "b"])["branch_statuses"] == {"a": "released", "b": "released"}
    assert instance.get_decision("a") == {"status": "released", "branch_id": "a"}
    assert instance._branch_ids.is_known("a")  # noqa: SLF001
    assert instance._branch_ids.is_known("b")  # noqa: SLF001


def test_mid_batch_registration_failure_releases_phase_records_and_every_reservation() -> None:
    phase = _Phase()
    instance = _instance(phase)
    original_register = instance._branch_ids.register  # noqa: SLF001

    def fail_second(branch_id: str) -> None:
        if branch_id == "b":
            raise RuntimeError("synthetic registration failure")
        original_register(branch_id)

    instance._branch_ids.register = fail_second  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(RuntimeError, match="registration failure"):
        instance.emulate_actions(items=[_item(instance, "a"), _item(instance, "b")], simulation_options=None)

    assert instance._combat_branch_reservations == set()  # noqa: SLF001
    assert instance._bookkeeping == {}  # noqa: SLF001
    assert not instance._branch_ids.is_known("a")  # noqa: SLF001
    assert not instance._branch_ids.is_known("b")  # noqa: SLF001
    assert [name for name, _ in phase.calls][-3:] == ["cancel", "release", "restore_rng"]


def test_combat_terminal_is_combat_completed_leaf_and_never_falls_back_to_prefix_replay() -> None:
    phase = _Phase(terminal=True)
    instance = _instance(phase, max_branches=1)

    branch = instance.emulate_action(**_item(instance, "leaf"), simulation_options=None)

    assert branch["status"] == "completed"
    dto = branch["masked_emulator_dto"]
    assert dto["transition"]["kind"] == "combat_completed"
    # The wire contract requires an outcome wherever `terminal` is set, and checks it
    # against the combat outcomes when `run_terminal` is absent (Training-side
    # src/sts2_training/api/contract.py). This assertion originally said the opposite,
    # and a live run rejected every response until it was corrected: combat_completed
    # says the combat ended, not how it ended. What must stay absent is run_terminal -
    # the run continues into reward and map after this leaf.
    assert dto["outcome"] == "victory"
    assert "run_terminal" not in dto
    with pytest.raises(RequestRejected, match="combat_completed branches are leaves"):
        instance.emulate_action(
            parent_branch_id="leaf",
            branch_id="impossible-child",
            rng_id=0,
            decision_point_id=branch["decision_point_id"],
            action_id="7",
            simulation_options=None,
        )
