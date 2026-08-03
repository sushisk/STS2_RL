"""Tests for Run/execution_mode.py and the RL-owns-no-inference refactor.

Native assertion runner, no pytest dependency. Real GameInstance - no mocking of the
decision surface.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from execution_mode import (  # noqa: E402
    EXECUTION_MODES,
    MODE_EXTERNAL_CONTROL,
    MODE_ZERO_INDEX,
    make_external_room_selector,
    zero_index_room_picker,
)
from room_progression_driver import drive_rooms  # noqa: E402
from whole_run_session import (  # noqa: E402
    MAP_SELECT,
    WholeRunSession,
    make_external_action_selector,
    zero_index_action,
)


def test_execution_modes_are_exactly_zero_index_and_external_control():
    assert EXECUTION_MODES == {MODE_ZERO_INDEX, MODE_EXTERNAL_CONTROL}


def test_zero_index_action_never_reorders_never_scores():
    session = WholeRunSession()
    session.enable_god_mode_for_testing()
    session.start_run(18, "Ironclad", 0)
    obs = session.get_observation()
    while obs["boundary"] != MAP_SELECT:
        actions = session.get_legal_actions()
        chosen = zero_index_action(actions)
        assert chosen is actions[0], "zero_index must always be legal_actions[0], no reordering"
        obs = session.step(chosen["action_id"])["observation"]


def test_zero_index_room_picker_never_avoids_treasure():
    rooms = [
        {"room_id": 0, "column": 0, "row": 0, "point_type": "Treasure"},
        {"room_id": 1, "column": 1, "row": 0, "point_type": "Monster"},
    ]
    chosen = zero_index_room_picker(rooms)
    assert chosen["room_id"] == 0, "zero_index must not prefer non-Treasure - that is a decision"


def test_external_control_action_selector_resolves_exactly_given_id():
    session = WholeRunSession()
    session.enable_god_mode_for_testing()
    session.start_run(18, "Ironclad", 0)
    actions = session.get_legal_actions()
    target = actions[-1]
    selector = make_external_action_selector(lambda _legal: target["action_id"])
    resolved = selector(actions)
    assert resolved["action_id"] == target["action_id"]


def test_external_control_action_selector_rejects_unresolvable_id():
    session = WholeRunSession()
    session.enable_god_mode_for_testing()
    session.start_run(18, "Ironclad", 0)
    actions = session.get_legal_actions()
    selector = make_external_action_selector(lambda _legal: 999999)
    raised = False
    try:
        selector(actions)
    except ValueError:
        raised = True
    assert raised


def test_external_control_room_selector_resolves_exactly_given_room():
    rooms = [
        {"room_id": 5, "column": 0, "row": 0, "point_type": "Monster"},
        {"room_id": 7, "column": 1, "row": 0, "point_type": "Unknown"},
    ]
    selector = make_external_room_selector(lambda pool: pool[-1]["room_id"])
    chosen = selector(rooms)
    assert chosen["room_id"] == 7


def test_room_progression_driver_zero_index_picks_index_zero_at_every_decision():
    """Not a full 10-room run (Combat rooms cannot conclude under strict zero_index -
    see the report's documented finding: End Turn is always LegalActions[0] in this
    Emulator's fixed action-list order, so zero_index can never choose to attack). This
    verifies zero_index picks legal_actions[0]/rooms[0] consistently for however many
    decisions it does reach before the run legitimately stalls inside the first Combat.
    """
    session = WholeRunSession()
    summary = drive_rooms(
        session, min_rooms=1, max_steps=30, seed=18,
        action_picker=zero_index_action, room_picker=zero_index_room_picker,
    )
    assert summary["rooms_entered"] >= 1
    assert "CombatRoom" in summary["room_kinds_seen"]
    step_events = [e for e in summary["log"] if e.get("event") == "step"]
    assert len(step_events) > 0
    assert all(e["action"]["action_id"] == 0 for e in step_events), (
        "zero_index must always pick action_id 0, whatever it is at that decision - "
        "Map/Event/Pending choices resolve fine this way; Combat's own 'stable' steps "
        "always land on 'system'/End Turn since this Emulator always lists it first"
    )
    combat_steps = [e for e in step_events if e["pre_boundary"] == "stable"]
    assert combat_steps, "expected at least one in-Combat zero_index step"
    assert all(e["action"]["action_type"] == "system" for e in combat_steps), (
        "documented finding: End Turn is always LegalActions[0] in Combat for this "
        "Emulator, so strict zero_index can never choose to attack - see report"
    )


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
