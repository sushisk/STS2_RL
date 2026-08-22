"""Regression coverage for CombatInstance root handling at mid-combat Pending boundaries."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402
from API.combat_phase import (  # noqa: E402
    CombatPhase,
    ROOT_BRANCHING_UNAVAILABLE_NO_ROOM_ENTRY_ANCHOR,
    ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR,
)
from API.history_builder import HistoryBuilder  # noqa: E402
from API.identifiers import BranchIdRegistry, DecisionPointRegistry  # noqa: E402
from API.validation import RequestRejected  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.decision_context import BOUNDARY_PENDING, BOUNDARY_STABLE  # noqa: E402


def _liquid_memories_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _first_action_id(response: dict, action_type: str) -> str:
    return next(action["action_id"] for action in _legal_actions(response) if action["action_type"] == action_type)


def _gambling_chips_config() -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": ["GAMBLING_CHIP"],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _adopt_live_phase(spec: dict) -> CombatPhase:
    session = LiveCombatSession(whole_run_mode=True)
    root_state = session.start_combat(spec)
    return CombatPhase.adopt(
        session,
        root_state,
        worker_count=1,
        request_timeout_s=20.0,
        max_branches=4,
        worker_pool_backend="multiprocessing",
    )


class _NoReconstructionGameProxy:
    def __init__(self, game) -> None:
        self._game = game
        self.reset_calls = 0
        self.restore_calls = 0

    def ResetFromScenario(self, scenario):  # noqa: N802 - CLR member name
        self.reset_calls += 1
        raise AssertionError("adopting a live combat must not call ResetFromScenario")

    def RestoreSnapshot(self, snapshot):  # noqa: N802 - CLR member name
        self.restore_calls += 1
        raise AssertionError("adopting a live combat must not restore a snapshot")

    def RestoreSnapshotJson(self, json_text):  # noqa: N802 - CLR member name
        self.restore_calls += 1
        raise AssertionError("adopting a live combat must not restore a snapshot")

    def __getattr__(self, name):
        return getattr(self._game, name)


def _unanchored_facade(phase: CombatPhase) -> CombatInstance:
    instance = object.__new__(CombatInstance)
    instance._phase = phase
    instance._branch_ids = BranchIdRegistry()
    instance._decision_points = DecisionPointRegistry()
    instance._decision_points.issue("root")
    instance._root_branch_log = []
    instance._root_history = HistoryBuilder()
    instance._bookkeeping = {}
    instance._closed = False
    return instance


def test_adopt_stable_combat_captures_anchor_without_resetting_live_board() -> None:
    session = LiveCombatSession(whole_run_mode=True)
    root_state = session.start_combat(_liquid_memories_config())
    proxy = _NoReconstructionGameProxy(session._game)  # noqa: SLF001 - guards the adopted board
    session._game = proxy  # noqa: SLF001 - adopt must only observe/capture this game
    phase = CombatPhase.adopt(
        session,
        root_state,
        worker_count=1,
        request_timeout_s=20.0,
        max_branches=4,
        worker_pool_backend="multiprocessing",
    )
    try:
        assert phase._held_stable_snapshot is not None  # noqa: SLF001 - anchor invariant
        assert proxy.reset_calls == 0
        assert proxy.restore_calls == 0
    finally:
        phase.close()


def test_adopt_pending_root_leaves_anchor_unestablished_without_raising() -> None:
    phase = _adopt_live_phase(_gambling_chips_config())
    try:
        assert phase._held_stable_snapshot is None  # noqa: SLF001 - no restoreable pending anchor exists
    finally:
        phase.close()


def test_unanchored_root_decision_returns_legal_actions_and_pending_boundary() -> None:
    phase = _adopt_live_phase(_gambling_chips_config())
    try:
        legal, context, boundary = phase.root_decision()
        assert boundary == BOUNDARY_PENDING
        assert legal
        assert context is None
    finally:
        phase.close()


def test_standalone_combat_instance_still_rejects_start_pending_root_at_construction() -> None:
    try:
        CombatInstance("standalone-pending-root", {"instance_type": "combat", **_gambling_chips_config()}, worker_count=1)
    except RuntimeError as exc:
        assert "Start-of-Combat Pending root" in str(exc)
    else:
        raise AssertionError("standalone CombatInstance must retain its pending-root rejection")


def test_unanchored_root_branch_request_is_rejected_with_specific_reason() -> None:
    phase = _adopt_live_phase(_gambling_chips_config())
    try:
        instance = _unanchored_facade(phase)
        legal, _, _ = phase.root_decision()
        try:
            instance.emulate_action(
                parent_branch_id="root",
                branch_id="unanchored-branch",
                rng_id=0,
                decision_point_id=instance._decision_points.current("root"),
                action_id=str(legal[0]["action_id"]),
                simulation_options=None,
            )
        except RequestRejected as exc:
            # This phase is adopted (whole-run shaped) and was given no map snapshot,
            # so the refusal names that specific cause rather than the generic "no
            # anchor". The two are kept apart on purpose: with a map snapshot this
            # combat would now be branchable, and the counts must be separable.
            assert exc.error == ROOT_BRANCHING_UNAVAILABLE_NO_ROOM_ENTRY_ANCHOR
        else:
            raise AssertionError("unanchored root Branch request must be rejected")
        # The refused id is burned, exactly as every other refusal after registration
        # in emulate_action behaves (resolve_action_id, _view_for). Registering early
        # and rolling back only when a commit raises is the documented policy - see
        # BranchIdRegistry.rollback_registration, which this path deliberately does not
        # call. Branch ids are never reusable, so a refusal costs the caller nothing.
        assert instance._branch_ids.is_known("unanchored-branch")
    finally:
        phase.close()


def test_pending_root_commit_establishes_anchor_before_branch_work_is_built() -> None:
    phase = _adopt_live_phase(_gambling_chips_config())
    try:
        for _ in range(8):
            legal, context, boundary = phase.root_decision()
            if context is not None:
                break
            assert boundary == BOUNDARY_PENDING
            phase.commit_root_action(legal[0])
        else:
            raise AssertionError("Gambling Chips root did not reach a stable board")

        legal, context, boundary = phase.root_decision()
        assert boundary == BOUNDARY_STABLE
        assert context is not None
        assert phase._held_stable_snapshot is not None  # noqa: SLF001 - re-anchor invariant
        assert phase.root_branching_unavailable_reason is None
        work_item = phase.build_work_item(context, legal[0], "root", "d-root-000001", 0)
        assert work_item is not None
    finally:
        phase.close()


def test_root_emulate_action_from_mid_combat_pending_replays_from_held_stable_snapshot() -> None:
    inst = CombatInstance("pending-root-regression", _liquid_memories_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        potion_id = _first_action_id(start, "potion")

        pending = inst.commit_action(start["decision_point_id"], potion_id)
        assert pending["status"] == "completed", pending
        pending_actions = [action for action in _legal_actions(pending) if action["action_type"] == "choice_card"]
        assert len(pending_actions) >= 2, pending_actions

        first = inst.emulate_action(
            parent_branch_id="root",
            branch_id="pending-choice-0",
            rng_id=1,
            decision_point_id=pending["decision_point_id"],
            action_id=pending_actions[0]["action_id"],
            simulation_options=None,
        )
        assert first["status"] == "completed", first

        second = inst.emulate_action(
            parent_branch_id="root",
            branch_id="pending-choice-1",
            rng_id=2,
            decision_point_id=pending["decision_point_id"],
            action_id=pending_actions[1]["action_id"],
            simulation_options=None,
        )
        assert second["status"] == "completed", second
    finally:
        inst.close()


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
