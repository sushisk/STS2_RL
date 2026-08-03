"""Runtime audit: `external_control`/`zero_index` Whole Run paths never fall back to the
legacy filler policies (`pick_default_action`/`pick_room`) when explicit pickers are
supplied (RL担当指示：推論撤去後の総合テスト・デバッグ, section 1). Run/ never had a
Policy/Value/Heuristic model to begin with - the only "decision-adjacent" functions here
are the legacy filler pickers used internally by Branch prefix discovery.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import room_progression_driver  # noqa: E402
from execution_mode import zero_index_room_picker, make_external_action_selector, make_external_room_selector  # noqa: E402
from whole_run_session import WholeRunSession, zero_index_action  # noqa: E402


def test_zero_index_drive_rooms_never_calls_legacy_pick_default_action_or_pick_room():
    calls = {"pick_default_action": 0, "pick_room": 0}
    original_action = room_progression_driver.pick_default_action
    original_room = room_progression_driver.pick_room

    def _counting_action(*args, **kwargs):
        calls["pick_default_action"] += 1
        return original_action(*args, **kwargs)

    def _counting_room(*args, **kwargs):
        calls["pick_room"] += 1
        return original_room(*args, **kwargs)

    room_progression_driver.pick_default_action = _counting_action
    room_progression_driver.pick_room = _counting_room
    try:
        session = WholeRunSession()
        room_progression_driver.drive_rooms(
            session, min_rooms=1, max_steps=30, seed=18,
            action_picker=zero_index_action, room_picker=zero_index_room_picker,
        )
    finally:
        room_progression_driver.pick_default_action = original_action
        room_progression_driver.pick_room = original_room

    assert calls["pick_default_action"] == 0, "zero_index run must never call the legacy filler action policy"
    assert calls["pick_room"] == 0, "zero_index run must never call the legacy filler room policy"


def test_external_control_drive_rooms_never_calls_legacy_pick_default_action_or_pick_room():
    calls = {"pick_default_action": 0, "pick_room": 0}
    original_action = room_progression_driver.pick_default_action
    original_room = room_progression_driver.pick_room

    def _counting_action(*args, **kwargs):
        calls["pick_default_action"] += 1
        return original_action(*args, **kwargs)

    def _counting_room(*args, **kwargs):
        calls["pick_room"] += 1
        return original_room(*args, **kwargs)

    room_progression_driver.pick_default_action = _counting_action
    room_progression_driver.pick_room = _counting_room

    def _resolve_action(legal_actions):
        preferred = ["choice_shop_leave", "choice_confirm", "choice_skip", "system", "card"]
        for t in preferred:
            for a in legal_actions:
                if a["action_type"] == t:
                    return a["action_id"]
        return legal_actions[0]["action_id"]

    def _resolve_room(rooms):
        return rooms[0]["room_id"]

    try:
        session = WholeRunSession()
        action_selector = make_external_action_selector(_resolve_action)
        room_selector = make_external_room_selector(_resolve_room)
        room_progression_driver.drive_rooms(
            session, min_rooms=3, max_steps=200, seed=18,
            action_picker=action_selector, room_picker=room_selector,
        )
    finally:
        room_progression_driver.pick_default_action = original_action
        room_progression_driver.pick_room = original_room

    assert calls["pick_default_action"] == 0, "external_control run must never call the legacy filler action policy"
    assert calls["pick_room"] == 0, "external_control run must never call the legacy filler room policy"


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
