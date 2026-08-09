"""Repro/characterization test for a real gap found via STS2_Training self-play
(2026-08-09): entering a TreasureRoom leaves the Whole Run session permanently stuck at
`boundary == "stable"` with zero legal actions and `room_context["room_resolved"] is
False`, with no client-visible way to make it progress.

This is NOT a bug in the sense of contradicting documented behavior - per
`STS2_Emulator/docs/api/whole_run_api_reference_20260803.md` ("### Treasure"), Treasure
deliberately has no public Boundary/ActionType and is meant to be resolved by "エンジン側の
room flow と reward flow" (the engine's own room/reward flow), the same way relic/potion
rewards are auto-claimed. `choice_branch_runner.search_for_room_type()` already avoids
Treasure by default when searching for other room types ("preferring non-Treasure
candidates" - see its own docstring), which suggests this gap was already informally
known before this test made it explicit.

The gap: nothing in `WholeRunSession`/`instance_whole_run.py` currently drives that
auto-resolution. A caller that only has the public `WholeRunSession` API (as
STS2_Training's Whole Run driver does, via the TCP/DTO boundary) has no action to take
and no other call that advances the room, so it is stuck. Two independent real self-play
runs (seed=1348178664 and seed=106720923, both IRONCLAD ascension 0) hit this at
Act 0/Floor ~10 - see the paired STS2_Training PR for the client-side symptom
(`commit_action`/`get_decision` loop raising because `legal_actions` never becomes
non-empty and no terminal marker is ever set).

Native assertion runner, no pytest dependency (matches this package's convention).
Run: `python test_treasure_room_stable_gap.py`.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from choice_branch_runner import (
    MAP_SELECT,
    drive_to_map_or_terminal,
    get_map_rooms_of,
    new_session,
)
from room_progression_driver import pick_room

# Seeds picked because a real STS2_Training self-play run independently reached a
# TreasureRoom at each within the first ~50 root decisions - not special-cased for this
# test, just already-known-affected seeds.
_KNOWN_AFFECTED_SEEDS = (1348178664, 106720923)


def _find_treasure_room(seed: int, *, max_hops: int = 15) -> tuple[str, int]:
    """Same shape/traversal as `choice_branch_runner.search_for_room_type()`, but
    without that helper's unconditional `if room["point_type"] == "Treasure": continue`
    (present regardless of `target_room_type`) - which means `search_for_room_type`
    itself can never actually find a Treasure room. That skip is presumably why this
    room type had no direct repro test before this one; reproducing it here needs its
    own traversal rather than a `target_room_type="TreasureRoom"` call.
    """
    session = new_session()
    session.start_run(seed, "Ironclad", 0)
    obs = drive_to_map_or_terminal(session)
    if obs["boundary"] != MAP_SELECT:
        return None, None
    map_snapshot = session.save_state()
    del session

    for _ in range(max_hops):
        rooms = get_map_rooms_of(map_snapshot)
        if not rooms:
            return None, None
        for room in rooms:
            if room["point_type"] == "Treasure":
                return map_snapshot, room["room_id"]

        advance_room = pick_room(rooms)
        if advance_room is None:
            return None, None
        nxt = new_session()
        nxt.load_state(map_snapshot)
        nxt.choose_room(advance_room["room_id"])
        obs = drive_to_map_or_terminal(nxt)
        if obs["boundary"] != MAP_SELECT:
            return None, None
        map_snapshot = nxt.save_state()
        del nxt

    return None, None


def _enter_treasure_room(seed: int):
    snapshot, room_id = _find_treasure_room(seed)
    assert snapshot is not None, f"expected a TreasureRoom to be reachable at seed={seed}"
    session = new_session()
    session.load_state(snapshot)
    entered = session.choose_room(room_id)
    assert entered["room_type"] == "TreasureRoom", entered["room_type"]
    return session


def test_treasure_room_leaves_session_stuck_with_no_legal_actions():
    session = _enter_treasure_room(_KNOWN_AFFECTED_SEEDS[0])

    obs = session.get_observation()
    actions = session.get_legal_actions()
    room_context = session.get_room_context()

    assert room_context["room_type"] == "TreasureRoom"
    assert room_context["room_resolved"] is False
    assert obs["boundary"] == "stable"
    # This is the actual gap: no legal action is ever offered for this state, so a
    # caller limited to the public WholeRunSession API (as Training's Whole Run driver
    # is) has nothing it can do to make the room resolve and the run progress.
    assert actions == []


def test_treasure_room_does_not_self_resolve_on_repeated_reads():
    """Rules out "just needs another read" - repeatedly re-fetching observation/legal
    actions/room context (no `step()`, since there is no action to step with) never
    changes the outcome. If this test starts failing, the gap may already be partially
    fixed upstream (Emulator or WholeRunSession) - re-check before assuming a regression.
    """
    session = _enter_treasure_room(_KNOWN_AFFECTED_SEEDS[1])

    for _ in range(5):
        assert session.get_legal_actions() == []
        assert session.get_observation()["boundary"] == "stable"
        assert session.get_room_context()["room_resolved"] is False


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
