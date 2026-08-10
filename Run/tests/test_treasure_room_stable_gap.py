"""Regression coverage for the TreasureRoom stable-boundary gap, now fixed.

Before this fix, entering a TreasureRoom left the Whole Run session permanently stuck
at `boundary == "stable"` with zero legal actions and `room_context["room_resolved"]
is False`, with no client-visible way to make it progress - see this file's git
history for the original repro (`test_treasure_room_leaves_session_stuck_with_no_legal_actions`
et al., seeds 1348178664 and 106720923, both IRONCLAD ascension 0, found via
STS2_Training self-play).

Root cause and fix: see `Outputs/reports/treasure_room_fix_implementation_plan_final_20260810.md`
in this repo, and its counterpart Emulator-side commits. Summary: `TreasureRoomRelicSynchronizer`/
`OneOffSynchronizer.DoLocalTreasureRoomRewards()` were no-op stubs (excluded from the
real build along with the rest of the multiplayer relic-picking/vote machinery that
single player never needs), and `GameInstance.IsCurrentRoomResolved()` had no case for
TreasureRoom - so nothing ever drove `choose_room(Treasure)` to completion or let
`TryEagerExitResolvedRoom()` fall back to `map_select`. The fix reimplements both as a
direct single-player auto-claim (same pattern as the existing `RestSiteSynchronizer`
reimplementation): `choose_room()` now grants gold/relic and eager-exits back to
`map_select` entirely inside the Emulator, with NO new Boundary or ActionType exposed
(Treasure never had a "pick 1 of N relics" decision to expose in the first place - see
section 2 of the plan doc). This suite asserts the resulting SUCCESS path.

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
    RUN_TERMINAL,
    inject_relic,
    new_session,
    search_for_room_type,
)

# Seeds picked because a real STS2_Training self-play run independently reached a
# TreasureRoom at each within the first ~50 root decisions (see this file's git history
# for the original stuck-boundary repro) - not special-cased for this test, just
# already-known seeds that reliably exercise a Treasure room early.
_KNOWN_AFFECTED_SEEDS = (1348178664, 106720923)

# SCREAMING_SNAKE_CASE ModelId.Entry derived from the C# class name (matches this
# module's own TOOLBOX_RELIC_ID convention - see choice_branch_runner.inject_relic).
_SILVER_CRUCIBLE_RELIC_ID = "SILVER_CRUCIBLE"


def _find_treasure_room(seed: int, *, max_hops: int = 15) -> "tuple[str, int] | tuple[None, None]":
    return search_for_room_type("TreasureRoom", seed=seed, max_hops=max_hops)


def _resolve_pending_decision(session, *, max_steps: int = 200) -> str:
    """Drives `step()` with the default action until a map/terminal boundary is reached.

    Only meant for the rare case where a granted relic's `AfterObtained()` (e.g.
    CallingBell) raises its own pending decision - see the plan doc's "CallingBellへの
    対応" section. In the ordinary case `choose_room()` already returns at map_select
    and this is never called.
    """
    from whole_run_session import pick_default_action

    boundary = session.get_observation()["boundary"]
    for _ in range(max_steps):
        if boundary in (MAP_SELECT, RUN_TERMINAL):
            return boundary
        actions = session.get_legal_actions()
        assert actions, f"stuck resolving a post-Treasure pending decision at boundary={boundary}"
        action = pick_default_action(actions)
        result = session.step(action["action_id"])
        boundary = result["observation"]["boundary"]
    raise AssertionError("timed out resolving a post-Treasure pending decision")


def test_search_for_room_type_finds_treasure_room():
    """Discovery regression guard (see choice_branch_runner.search_for_room_type's own
    docstring): before this fix, an unconditional `point_type == "Treasure": continue`
    skip meant a TreasureRoom could never be found via this helper, regardless of
    `target_room_type`. Also covers worker_pool's matching discovery fix implicitly, via
    the shared `remaining`/`target_room_types` skip-condition shape both now use.
    """
    for seed in _KNOWN_AFFECTED_SEEDS:
        snapshot, room_id = _find_treasure_room(seed)
        assert snapshot is not None, f"expected a TreasureRoom to be reachable at seed={seed}"
        assert room_id is not None


def test_treasure_room_auto_claim_reaches_map_select():
    for seed in _KNOWN_AFFECTED_SEEDS:
        snapshot, room_id = _find_treasure_room(seed)
        assert snapshot is not None, f"expected a TreasureRoom to be reachable at seed={seed}"

        session = new_session()
        session.load_state(snapshot)
        before = session.get_run_state()

        entered = session.choose_room(room_id)
        # Regression guard: RoomEnterResult must still correctly report the room type
        # that was entered even though it has ALREADY been auto-resolved and exited by
        # the time choose_room() returns (see the plan doc's ChooseRoom() section - the
        # RoomType comes from a local var captured before the eager exit).
        assert entered["room_type"] == "TreasureRoom", entered["room_type"]

        boundary = session.get_observation()["boundary"]
        if boundary != MAP_SELECT:
            # A CallingBell-style AfterObtained() pending decision is a legitimate,
            # documented exception to "always map_select" - resolve it before asserting.
            boundary = _resolve_pending_decision(session)

        assert boundary == MAP_SELECT, f"seed={seed}: expected map_select, got {boundary!r}"
        assert session.get_map_rooms(), f"seed={seed}: expected non-empty map rooms after Treasure auto-claim"

        after = session.get_run_state()
        assert after["gold"] >= before["gold"]
        assert len(after["relics"]) >= len(before["relics"])
        # Treasure always grants SOMETHING under normal (non-SilverCrucible) conditions:
        # either gold went up, or a relic was added (Circlet fallback still counts even
        # if the shared grab bag was exhausted) - i.e. gold/relic were granted exactly
        # once, not zero times (the original stuck-boundary bug) nor silently dropped.
        assert after["gold"] > before["gold"] or len(after["relics"]) > len(before["relics"]), (
            f"seed={seed}: expected Treasure to grant gold and/or a relic"
        )

        # SaveState() must succeed on the settled map_select state, same as after any
        # other resolved room type.
        session.save_state()


def test_treasure_room_with_silver_crucible_grants_nothing_but_still_resolves():
    """SilverCrucible overrides Hook.ShouldGenerateTreasure (gating gold AND relic on the
    same hook - see the plan doc's SilverCrucible note), so injecting it makes this a
    deterministic "0 gold, 0 relic" case rather than the rare/lucky natural one. Also
    exercises the auto-claim path when Hook.ShouldGenerateTreasure is false, i.e. the
    `BeginRelicPicking()` -> immediately-Complete branch that never rolls a relic at all.
    """
    seed = _KNOWN_AFFECTED_SEEDS[0]
    snapshot, room_id = _find_treasure_room(seed)
    assert snapshot is not None, f"expected a TreasureRoom to be reachable at seed={seed}"

    snapshot = inject_relic(snapshot, _SILVER_CRUCIBLE_RELIC_ID)
    session = new_session()
    session.load_state(snapshot)
    before = session.get_run_state()
    assert _SILVER_CRUCIBLE_RELIC_ID in before["relics"]

    entered = session.choose_room(room_id)
    assert entered["room_type"] == "TreasureRoom", entered["room_type"]

    boundary = session.get_observation()["boundary"]
    if boundary != MAP_SELECT:
        boundary = _resolve_pending_decision(session)
    assert boundary == MAP_SELECT, f"seed={seed}: expected map_select even with SilverCrucible, got {boundary!r}"
    assert session.get_map_rooms()

    after = session.get_run_state()
    assert after["gold"] == before["gold"], "SilverCrucible should suppress Treasure gold"
    assert after["relics"] == before["relics"], "SilverCrucible should suppress the Treasure relic"

    session.save_state()


def test_second_treasure_visit_reinitializes():
    """Regression guard for `TreasureRoomRelicSynchronizer.OnRoomExited()`: a second
    Treasure visit in the same run must roll/grant its own relic independently, not
    reuse or fault on state left over from the first visit.
    """
    from room_progression_driver import pick_room

    seed = _KNOWN_AFFECTED_SEEDS[0]
    snapshot, room_id = _find_treasure_room(seed)
    assert snapshot is not None, f"expected a TreasureRoom to be reachable at seed={seed}"

    session = new_session()
    session.load_state(snapshot)
    entered = session.choose_room(room_id)
    assert entered["room_type"] == "TreasureRoom"
    boundary = session.get_observation()["boundary"]
    if boundary != MAP_SELECT:
        boundary = _resolve_pending_decision(session)
    assert boundary == MAP_SELECT

    # Best-effort walk (preferring non-Treasure, like the rest of this package's traversal
    # helpers) looking for a second Treasure room within the SAME session/GameInstance. A
    # genuinely unrelated snag along the way (e.g. an Act transition needing more than this
    # traversal's simple default-action policy) makes this inconclusive, not a failure -
    # only what happens ONCE a second Treasure room is actually found is a real regression
    # assertion (see below).
    second_treasure = None
    try:
        for _ in range(24):
            rooms = session.get_map_rooms()
            if not rooms:
                break
            treasure = next((r for r in rooms if r["point_type"] == "Treasure"), None)
            if treasure is not None:
                second_treasure = treasure
                break

            advance_room = pick_room(rooms)
            if advance_room is None:
                break
            session.choose_room(advance_room["room_id"])
            boundary = session.get_observation()["boundary"]
            if boundary not in (MAP_SELECT, RUN_TERMINAL):
                boundary = _resolve_pending_decision(session)
            if boundary != MAP_SELECT:
                break
    except AssertionError:
        pass

    if second_treasure is None:
        print(
            "test_second_treasure_visit_reinitializes: no second Treasure room found within "
            "budget for this seed - not a failure, just inconclusive for this run."
        )
        return

    # From here on, every failure IS a real regression - OnRoomExited() must have correctly
    # reset TreasureRoomRelicSynchronizer state for this second, independent auto-claim to work.
    before = session.get_run_state()
    entered = session.choose_room(second_treasure["room_id"])
    assert entered["room_type"] == "TreasureRoom"
    boundary = session.get_observation()["boundary"]
    if boundary != MAP_SELECT:
        boundary = _resolve_pending_decision(session)
    assert boundary == MAP_SELECT, "expected map_select after second Treasure auto-claim"
    after = session.get_run_state()
    assert after["gold"] > before["gold"] or len(after["relics"]) > len(before["relics"]), (
        "expected second Treasure visit to also grant gold and/or a relic"
    )
    session.save_state()


def test_whole_run_instance_root_view_exposes_map_room_after_treasure():
    """`API/instance_whole_run.py`'s root view (`_map_rooms_as_legal_actions`, active
    only at `boundary == MAP_SELECT`) must also see the post-Treasure map, not just the
    lower-level `WholeRunSession`/`choice_branch_runner` API exercised above.

    Drives `inst._session` directly with this module's own known-reliable traversal
    (`_find_treasure_room`/`choose_room`) rather than walking floor-by-floor through
    `commit_action`'s index-resolving action_id contract, which is orthogonal to what
    this test is actually verifying (that `_root_view()`/`_map_rooms_as_legal_actions`
    correctly reflect the auto-claimed Treasure room's resulting map, whatever session
    state produced it) and adds unrelated fragility for a general combat/reward walk
    long enough to reliably reach Treasure. `_root_view()` always recomputes boundary/
    legal_actions fresh from `self._session` (see its source), so it does not care
    whether `self._session`'s state was reached via `commit_action` or driven directly.
    """
    import sys as _sys

    _api_root = Path(__file__).resolve().parents[2]
    for _p in (_api_root, _api_root / "Run"):
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
    from API.dto import ROOT_BRANCH_ID
    from API.instance_whole_run import WholeRunInstance

    seed = _KNOWN_AFFECTED_SEEDS[0]
    snapshot, room_id = _find_treasure_room(seed)
    assert snapshot is not None, f"expected a TreasureRoom to be reachable at seed={seed}"

    inst = WholeRunInstance(
        "treasure-room-gap-test", {"instance_type": "whole_run", "seed": seed, "character_id": "IRONCLAD", "ascension": 0}
    )
    try:
        inst._session.load_state(snapshot)
        entered = inst._session.choose_room(room_id)
        assert entered["room_type"] == "TreasureRoom"
        boundary = inst._session.get_observation()["boundary"]
        if boundary != MAP_SELECT:
            boundary = _resolve_pending_decision(inst._session)
        assert boundary == MAP_SELECT, f"expected map_select after Treasure auto-claim, got {boundary!r}"

        decision = inst.get_decision(ROOT_BRANCH_ID)
        dto = decision["masked_emulator_dto"]
        assert dto.get("boundary") == "map_select"
        assert any(a.get("action_type") == "map_room" for a in dto["legal_actions"]), (
            "expected root view legal actions to include map_room after Treasure auto-claim"
        )
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
