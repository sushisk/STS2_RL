"""Regression coverage for terminal win/loss exposure in Combat and Whole Run."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import API.instance_combat as combat_module  # noqa: E402
from API.history_builder import HistoryBuilder  # noqa: E402
from API.identifiers import BranchIdRegistry, DecisionPointRegistry  # noqa: E402
from API.instance_combat import (  # noqa: E402
    CombatInstance,
    _BranchBookkeeping as _CombatBranchBookkeeping,
)
from API.instance_whole_run import WholeRunInstance, _View  # noqa: E402
from API.terminal_outcome import require_terminal_outcome  # noqa: E402


def _victory_combat_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["WHIRLWIND"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
    }


def _defeat_combat_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": 1,
        "player_max_hp": 80,
        "hand": ["DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999}],
    }


def test_combat_victory_exposes_terminal_and_outcome():
    inst = CombatInstance("c-victory", _victory_combat_config(), worker_count=1)
    try:
        decision = inst.start_instance_response()
        legal = decision["masked_emulator_dto"]["legal_actions"]
        whirlwind = next(a for a in legal if a.get("parameters", {}).get("cardId") == "WHIRLWIND")
        result = inst.commit_action(decision["decision_point_id"], whirlwind["action_id"])
        dto = result["masked_emulator_dto"]
        assert dto["legal_actions"] == []
        assert dto.get("terminal") is True
        assert dto.get("outcome") == "victory"
    finally:
        inst.close()


def test_combat_defeat_exposes_terminal_and_outcome():
    inst = CombatInstance("c-defeat", _defeat_combat_config(), worker_count=1)
    try:
        decision = inst.start_instance_response()
        for _ in range(5):
            dto = decision["masked_emulator_dto"]
            if dto.get("legal_actions") == []:
                break
            legal = dto["legal_actions"]
            end_turn = next(a for a in legal if a["action_type"] == "system")
            decision = inst.commit_action(decision["decision_point_id"], end_turn["action_id"])
        dto = decision["masked_emulator_dto"]
        assert dto["legal_actions"] == []
        assert dto.get("terminal") is True
        assert dto.get("outcome") == "defeat"
    finally:
        inst.close()


def test_combat_non_terminal_decision_has_no_terminal_key():
    inst = CombatInstance("c-nonterm", _victory_combat_config(), worker_count=1)
    try:
        decision = inst.start_instance_response()
        dto = decision["masked_emulator_dto"]
        assert dto["legal_actions"] != []
        assert "terminal" not in dto
        assert "outcome" not in dto
    finally:
        inst.close()


def test_combat_empty_legal_actions_non_terminal_is_invariant_failure():
    inst = CombatInstance("c-empty-legal", _victory_combat_config(), worker_count=1)
    try:
        assert not inst._root_state.is_terminal  # noqa: SLF001
        inst._root_state._cached_legal_actions = []  # noqa: SLF001
        try:
            inst._root_view()  # noqa: SLF001
        except RuntimeError as exc:
            assert "non-terminal combat state" in str(exc)
        else:
            raise AssertionError("non-terminal empty legal-actions cache must fail")
    finally:
        inst.close()


def test_combat_terminal_root_discards_stale_cached_legal_actions():
    inst = CombatInstance("c-terminal-stale-legal", _victory_combat_config(), worker_count=1)
    try:
        assert inst._root_state._cached_legal_actions  # noqa: SLF001
        inst._root_state.is_terminal = True  # noqa: SLF001
        inst._root_state.outcome = "victory"  # noqa: SLF001
        view = inst._root_view()  # noqa: SLF001
        assert view.legal_actions_raw == []
    finally:
        inst.close()


def test_combat_terminal_branch_get_decision_preserves_outcome():
    inst = CombatInstance.__new__(CombatInstance)
    inst._closed = False  # noqa: SLF001
    inst._branch_ids = BranchIdRegistry()  # noqa: SLF001
    inst._decision_points = DecisionPointRegistry()  # noqa: SLF001
    inst._bookkeeping = {}  # noqa: SLF001
    inst._branch_manager = SimpleNamespace(  # noqa: SLF001
        get_branch_status=lambda ids: {ids[0]: "completed"}
    )
    book = _CombatBranchBookkeeping("internal-b1", "root", [], HistoryBuilder(), 1)
    book.terminal = True
    book.outcome = "victory"
    inst._bookkeeping["b1"] = book  # noqa: SLF001
    inst._branch_ids.register("b1")  # noqa: SLF001
    inst._decision_points.issue("b1")  # noqa: SLF001

    response = inst.get_decision("b1")
    dto = response["masked_emulator_dto"]
    assert dto.get("terminal") is True
    assert dto.get("outcome") == "victory"


def test_combat_invalid_terminal_outcome_does_not_half_mark_book_terminal():
    inst = CombatInstance.__new__(CombatInstance)
    inst._decision_points = DecisionPointRegistry()  # noqa: SLF001
    book = _CombatBranchBookkeeping("internal-b1", "root", [], HistoryBuilder(), 1)
    result = SimpleNamespace(
        status="success",
        result_signature=SimpleNamespace(boundary="terminal"),
        terminal_result=SimpleNamespace(outcome=None),
    )

    try:
        inst._finalize_branch_result(  # noqa: SLF001
            branch_id="b1",
            parent_branch_id="root",
            rng_id=1,
            book=book,
            branch_log=[],
            result=result,
        )
    except RuntimeError as exc:
        assert "without a valid outcome" in str(exc)
    else:
        raise AssertionError("invalid terminal outcome must fail")

    assert book.terminal is False
    assert book.outcome is None
    assert book.view is None


class _FakeCombatRngTable:
    def __init__(self) -> None:
        self.restored = None

    def snapshot(self):
        return {"rng": "before"}

    def hypothesis_index_for(self, parent_branch_id: str, decision_point_id: str, rng_id: int) -> int:
        return 0

    def restore(self, snapshot) -> None:
        self.restored = snapshot


class _FakeCombatBranchManager:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.released: list[str] = []

    def submit(self, work_items: list, parent_branch_id=None) -> list[str]:
        return ["internal-b1"]

    def poll(self, *, timeout: float, branch_ids: list[str]) -> dict:
        return {"internal-b1": SimpleNamespace(status="success")}

    def cancel_branches(self, branch_ids: list[str]) -> None:
        self.cancelled.extend(branch_ids)

    def release_branches(self, branch_ids: list[str]) -> None:
        self.released.extend(branch_ids)


def test_combat_single_emulate_action_cleans_up_when_finalization_raises():
    inst = CombatInstance.__new__(CombatInstance)
    inst._closed = False  # noqa: SLF001
    inst._branch_ids = BranchIdRegistry()  # noqa: SLF001
    inst._decision_points = DecisionPointRegistry()  # noqa: SLF001
    inst._decision_points.issue("root")  # noqa: SLF001
    inst._bookkeeping = {}  # noqa: SLF001
    inst._root_history = HistoryBuilder()  # noqa: SLF001
    inst._root_branch_log = []  # noqa: SLF001
    inst._combat_start_deck_multiset = {}  # noqa: SLF001
    inst._rng_table = _FakeCombatRngTable()  # noqa: SLF001
    inst._branch_manager = _FakeCombatBranchManager()  # noqa: SLF001
    parent_view = SimpleNamespace(
        legal_actions_raw=[{"action_id": 7, "action_type": "system", "parameters": {}}],
        resolve_action_id=lambda action_id: 0,
        decision_context=SimpleNamespace(current_context_signature=SimpleNamespace()),
    )
    inst._view_for = lambda branch_id: parent_view  # type: ignore[method-assign]  # noqa: SLF001

    def _raise_finalize(**kwargs):
        raise RuntimeError("finalization failed")

    inst._finalize_branch_result = _raise_finalize  # type: ignore[method-assign]  # noqa: SLF001
    original_builder = combat_module.build_single_hypothesis_work_item
    combat_module.build_single_hypothesis_work_item = lambda *args, **kwargs: object()
    try:
        try:
            inst.emulate_action(
                parent_branch_id="root",
                branch_id="b1",
                rng_id=1,
                decision_point_id=inst._decision_points.current("root"),  # noqa: SLF001
                action_id="0",
                simulation_options=None,
            )
        except RuntimeError as exc:
            assert "finalization failed" in str(exc)
        else:
            raise AssertionError("finalization failure must propagate")
    finally:
        combat_module.build_single_hypothesis_work_item = original_builder

    assert "b1" not in inst._bookkeeping  # noqa: SLF001
    assert inst._branch_manager.cancelled == ["internal-b1"]  # noqa: SLF001
    assert inst._branch_manager.released == ["internal-b1"]  # noqa: SLF001
    assert inst._rng_table.restored == {"rng": "before"}  # noqa: SLF001


def _whole_run_config() -> dict:
    return {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0}


def test_whole_run_root_run_terminal_view_exposes_outcome():
    inst = WholeRunInstance("wr-root-terminal", _whole_run_config(), branch_worker_count=1)
    try:
        terminal_view = _View(
            legal_actions_raw=[],
            boundary="run_terminal",
            observation={"boundary": "run_terminal", "outcome": "victory", "state": {}},
            room_context={},
            map_snapshot=None,
            room_id=None,
            action_prefix=(),
            choice_type="run_terminal",
            chain_blocked=False,
            event_rng_state=None,
        )
        response = inst._decision_response_fields(  # noqa: SLF001
            "root", terminal_view, branch_log=[], history=HistoryBuilder()
        )
        dto = response["masked_emulator_dto"]
        assert dto.get("run_terminal") is True
        assert dto.get("outcome") == "victory"
    finally:
        inst.close()


def test_whole_run_non_terminal_view_has_no_run_terminal_key():
    inst = WholeRunInstance("wr-root-nonterm", _whole_run_config(), branch_worker_count=1)
    try:
        decision = inst.start_instance_response()
        dto = decision["masked_emulator_dto"]
        assert dto.get("boundary") != "run_terminal"
        assert "run_terminal" not in dto
        assert "outcome" not in dto
    finally:
        inst.close()


class _FakeEventRngRegistry:
    def __init__(self) -> None:
        self.registered: list[tuple[tuple, str]] = []
        self.released: list[tuple[tuple, str]] = []

    def get_or_create(self, key: tuple, state: dict, rng_id: int) -> dict:
        return dict(state)

    def register_branch(self, key: tuple, branch_id: str) -> None:
        self.registered.append((key, branch_id))

    def release_branch(self, key: tuple, branch_id: str) -> None:
        self.released.append((key, branch_id))


class _FakeWholeRunPool:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    def dispatch_choice_work_items(self, work_items: list, lease_registry: object) -> list:
        return [
            SimpleNamespace(
                status="success",
                step=SimpleNamespace(
                    step_result={
                        "observation": {
                            "boundary": "run_terminal",
                            "outcome": self.outcome,
                        },
                        "room_context": {},
                    }
                ),
            )
        ]


def _whole_run_stub_instance(outcome: object) -> WholeRunInstance:
    inst = WholeRunInstance.__new__(WholeRunInstance)
    inst.max_branches = 64
    inst._bookkeeping = {}  # noqa: SLF001
    inst._branch_ids = BranchIdRegistry()  # noqa: SLF001
    inst._decision_points = DecisionPointRegistry()  # noqa: SLF001
    inst._decision_points.issue("root")  # noqa: SLF001
    inst._event_rng_registry = _FakeEventRngRegistry()  # noqa: SLF001
    inst._root_history = HistoryBuilder()  # noqa: SLF001
    inst._root_branch_log = []  # noqa: SLF001
    inst._pool = _FakeWholeRunPool(outcome)  # noqa: SLF001
    inst._lease_registry = object()  # noqa: SLF001
    parent_view = _View(
        legal_actions_raw=[{"action_id": 7}],
        boundary="event_choice",
        observation={"boundary": "event_choice", "state": {}},
        room_context={},
        map_snapshot="stub-map-snapshot",
        room_id=1,
        action_prefix=(),
        choice_type="event_choice",
        chain_blocked=False,
        event_rng_state={"stub": 1},
    )
    inst._view_for = lambda branch_id: parent_view  # type: ignore[method-assign]  # noqa: SLF001
    return inst


def test_whole_run_emulate_action_wires_terminal_outcome_into_response_and_get_decision():
    inst = _whole_run_stub_instance("defeat")
    decision_point_id = inst._decision_points.current("root")  # noqa: SLF001

    response = inst.emulate_action(
        parent_branch_id="root",
        branch_id="b1",
        rng_id=1,
        decision_point_id=decision_point_id,
        action_id="0",
        simulation_options=None,
    )

    assert inst._bookkeeping["b1"].outcome == "defeat"  # noqa: SLF001
    assert response["masked_emulator_dto"]["outcome"] == "defeat"
    replay = inst.get_decision("b1")
    assert replay["masked_emulator_dto"]["run_terminal"] is True
    assert replay["masked_emulator_dto"]["outcome"] == "defeat"


def test_whole_run_emulate_action_rejects_missing_terminal_outcome_and_faults_branch():
    inst = _whole_run_stub_instance(None)
    decision_point_id = inst._decision_points.current("root")  # noqa: SLF001

    try:
        inst.emulate_action(
            parent_branch_id="root",
            branch_id="b1",
            rng_id=1,
            decision_point_id=decision_point_id,
            action_id="0",
            simulation_options=None,
        )
    except RuntimeError as exc:
        assert "without a valid outcome" in str(exc)
    else:
        raise AssertionError("terminal Whole Run result without outcome must fail")

    book = inst._bookkeeping["b1"]  # noqa: SLF001
    assert book.status == "faulted"
    assert book.terminal is False
    assert book.view is None
    assert book.event_rng_plan is None
    assert inst.active_branch_count() == 0
    assert inst._event_rng_registry.registered  # noqa: SLF001
    assert inst._event_rng_registry.released == inst._event_rng_registry.registered  # noqa: SLF001
    assert inst.get_decision("b1") == {"status": "faulted", "branch_id": "b1"}


def test_terminal_outcome_helper_rejects_unknown_value():
    try:
        require_terminal_outcome("draw", context="test")
    except RuntimeError as exc:
        assert "without a valid outcome" in str(exc)
    else:
        raise AssertionError("unknown terminal outcome must fail")


def _run_all() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    failures = 0
    for test in sorted(tests, key=lambda fn: fn.__name__):
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
