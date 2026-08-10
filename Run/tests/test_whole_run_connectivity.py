"""Real-Emulator connectivity tests for the Whole Run bridge/session/driver/branch runner.

Native assertion runner, no pytest dependency (matches Combat/tests' convention).
Run: `python test_whole_run_connectivity.py`.

These are deliberately real (no mocking) - the whole point of this package is
connectivity to the real Emulator, so a mocked test would not actually verify
anything the instruction asked for.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from choice_branch_runner import (  # noqa: E402
    CHOICE_MAP,
    attempt_branch,
    new_session,
    search_for_room_type,
)
from room_progression_driver import drive_rooms  # noqa: E402
from whole_run_session import (  # noqa: E402
    MAP_SELECT,
    RUN_TERMINAL,
    WholeRunSession,
    combat_just_concluded,
    pick_default_action,
)


def test_step_result_carries_room_context_and_transition_fields():
    session = new_session()
    session.start_run(1, "Ironclad", 0)
    obs = session.get_observation()
    saw_transition = False
    for _ in range(60):
        if obs["boundary"] == MAP_SELECT:
            rooms = session.get_map_rooms()
            entered = session.choose_room(rooms[0]["room_id"])
            obs = session.get_observation()
            if entered["is_combat"]:
                continue
            continue
        actions = session.get_legal_actions()
        if not actions:
            break
        result = session.step(pick_default_action(actions)["action_id"])
        assert "room_context" in result and "boundary" in result["room_context"]
        assert "transition" in result  # present (possibly None) on every StepResult dict
        if combat_just_concluded(result):
            saw_transition = True
            assert result["transition"]["kind"] == "combat_completed"
            assert isinstance(result["transition"]["victory"], bool)
            break
        obs = result["observation"]
        if obs["boundary"] == RUN_TERMINAL:
            break
    assert saw_transition, "expected at least one combat conclusion within 60 steps at seed=1"


def test_room_context_event_field_converts_to_plain_dict_not_clr_object():
    """Regression test for the event_room_context_to_dict bug found live: `to_plain()`
    alone left `RoomContext.Event` as a raw pythonnet-wrapped `EventRoomContext` object
    (no Keys/Values/__iter__), which compared unequal to itself across two independent
    fetches and would break any caller that persists/diffs a RoomContext dict (e.g. this
    package's own progression/branch logs).
    """
    from choice_branch_runner import search_for_room_type

    snapshot, room_id = search_for_room_type("EventRoom", seed=18, max_hops=15)
    assert snapshot is not None, "expected an EventRoom to be reachable at seed=18"
    session = new_session()
    session.load_state(snapshot)
    session.choose_room(room_id)
    ctx_a = session.get_room_context()
    ctx_b = session.get_room_context()
    assert ctx_a == ctx_b
    assert isinstance(ctx_a["event"], dict)
    assert set(ctx_a["event"].keys()) == {"event_id", "is_finished", "current_option_text_keys"}


def test_room_progression_driver_reaches_at_least_ten_rooms_seed18():
    # This is a connectivity/coverage check (can we mechanically reach 10 rooms via the
    # naive legacy filler policy), not a combat-difficulty test - god mode must be
    # requested explicitly (new_session()/drive_rooms() no longer enable it by default,
    # see Outputs/reports/god_mode_default_removal_20260811.md).
    session = new_session()
    session.enable_god_mode_for_testing()
    summary = drive_rooms(session, min_rooms=10, max_steps=1500, seed=18)
    assert summary["rooms_entered"] >= 10
    assert "CombatRoom" in summary["room_kinds_seen"]
    # Treasure now auto-resolves inside choose_room() (see room_progression_driver's module
    # docstring), so it must never land in unsupported_rooms again. NOT asserting
    # `unsupported_rooms == []` outright: driving seed=18 far enough now (Treasure no longer
    # dead-ends it early) surfaces a SEPARATE, pre-existing gap - an Act transition leaves
    # CurrentRoom as a "MapRoom" stuck at boundary=="stable" with no legal actions, the same
    # *class* of bug Treasure had (no IsCurrentRoomResolved() case for it) but for a different
    # room type and out of scope for this fix. `all(...)` alone would be vacuously True for an
    # empty list and silently stop catching a Treasure regression, so assert the negative
    # instead - this is the real regression guard for what THIS fix changed.
    assert not any(r["room_type"] == "TreasureRoom" for r in summary["unsupported_rooms"]), summary["unsupported_rooms"]


def test_choice_branch_map_holder_sibling_determinism_and_isolation():
    session = new_session()
    session.start_run(1, "Ironclad", 0)
    obs = session.get_observation()
    while obs["boundary"] != MAP_SELECT:
        actions = session.get_legal_actions()
        obs = session.step(pick_default_action(actions)["action_id"])["observation"]
    snapshot = session.save_state()
    del session

    result = attempt_branch(CHOICE_MAP, snapshot, None)
    assert result.ok, result.checks
    assert result.checks["same_choice_same_result"]
    assert result.checks["different_choices_diverge"]
    assert result.checks["holder_sibling_isolated"]


def test_choice_branch_shop_holder_sibling_reproduction():
    snapshot, room_id = search_for_room_type("MerchantRoom", seed=18, max_hops=15)
    assert snapshot is not None, "expected a MerchantRoom to be reachable at seed=18"
    result = attempt_branch("shop", snapshot, room_id)
    assert result.checks["boundary_matches"]
    assert result.checks["room_context_matches"]
    assert result.checks["legal_action_semantic_set_matches"]
    assert result.checks["prefix_replay_reproduces_boundary_again"]
    assert result.checks["same_choice_same_result"]


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
