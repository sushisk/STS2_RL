"""Explicit execution modes for Whole Run - `external_control` and `zero_index`.

Mirrors `Combat/execution_mode.py`'s division of responsibility (RL担当指示：推論処理撤去と
受動実行基盤への整理): Training is the decision-making authority. RL's `Run/` layer
(`WholeRunSession`, `room_progression_driver.py`, `worker_pool.py`,
`process_choice_branch_runner.py`) never decides an action on its own initiative in
production - it either executes an action Training explicitly specifies
(`external_control`), or - only in the `zero_index` connectivity-check mode - takes the
Emulator's own first-listed Legal Action/Map Room, verbatim, with no reordering of any
kind.

`zero_index` is a connectivity-check fallback only, never a strength-oriented Policy -
see `whole_run_session.zero_index_action`'s own docstring.
"""

from __future__ import annotations

from typing import Callable

from whole_run_session import make_external_action_selector, zero_index_action

MODE_EXTERNAL_CONTROL = "external_control"
MODE_ZERO_INDEX = "zero_index"
EXECUTION_MODES = frozenset({MODE_EXTERNAL_CONTROL, MODE_ZERO_INDEX})


def zero_index_room_picker(rooms: list[dict], exclude_room_ids: "set[int] | None" = None) -> "dict | None":
    """The `zero_index` counterpart to `room_progression_driver.pick_room`: always the
    first NOT-YET-EXCLUDED Map room in the Emulator's own reported order - no Treasure
    avoidance, no reordering by PointType. `exclude_room_ids` still applies (it is not a
    decision - it is the caller's own bookkeeping of which candidates were already tried
    at this exact map fork after an unsupported-room reload, see
    `room_progression_driver.drive_rooms`); `None` when every candidate has already been
    excluded, exactly like `pick_room`'s own contract.
    """
    excluded = exclude_room_ids or set()
    pool = [r for r in rooms if r["room_id"] not in excluded]
    return pool[0] if pool else None


def make_external_room_selector(resolve_room_id: Callable[[list], int]):
    """`external_control` counterpart to `whole_run_session.make_external_action_selector`,
    for Map's `GetMapRooms()`/`ChooseRoom(roomId)` surface (which has no LegalActions of
    its own). `resolve_room_id(rooms) -> room_id` is Training's own decision function.
    """

    def _select(rooms: list[dict], exclude_room_ids: "set[int] | None" = None) -> "dict | None":
        excluded = exclude_room_ids or set()
        pool = [r for r in rooms if r["room_id"] not in excluded]
        if not pool:
            return None
        room_id = resolve_room_id(pool)
        matches = [r for r in pool if r["room_id"] == room_id]
        if len(matches) != 1:
            raise ValueError(
                f"external_control: resolve_room_id returned {room_id!r}, not a unique "
                f"room_id among {[r['room_id'] for r in pool]!r}"
            )
        return matches[0]

    return _select


ZERO_INDEX_ACTION_PICKER = zero_index_action
ZERO_INDEX_ROOM_PICKER = zero_index_room_picker


class StaleDecisionError(RuntimeError):
    """Raised when `external_control` is asked to apply an action against a Decision ID
    that no longer matches the session's current one - the Whole Run counterpart to
    Combat's `DecisionFrameMismatchError` (`decision_frame` there is
    `(combat_session_id, step_index)`; here it additionally carries `boundary` and
    `run_seed` since a single session moves across Map/Combat/Event/etc., not just one
    combat). Guards against a caller applying a decision computed against a since-changed
    or already-committed state, and against double-committing the same decision twice.
    """


def decision_id(session) -> tuple:
    """A `(seed, character_id, step_index, boundary, combat_session_id)` snapshot of the
    session's CURRENT decision identity - two calls only ever compare equal if nothing
    has advanced the session between them (a fresh `GetObservation()`/`GetRoomContext()`
    read never changes it). External Control callers should capture this once per
    decision (alongside `legal_actions`/`observation`), then pass it back to
    `apply_external_action`/`apply_external_room_choice` - if the session moved on for
    any reason in between, the mismatch raises `StaleDecisionError` instead of silently
    applying a decision made against stale state.
    """
    obs = session.get_observation()
    return (obs["seed"], obs["character_id"], obs["step_index"], obs["boundary"], obs.get("combat_session_id"))


def apply_external_action(session, expected_decision_id: tuple, action_id: int) -> dict:
    """Applies `action_id` only if `session`'s CURRENT decision_id still matches
    `expected_decision_id` (captured earlier by the same caller) - rejects stale
    decisions and double-commits (the second attempt against an already-advanced session
    will always see a different decision_id) without ever touching Main state first.
    """
    current = decision_id(session)
    if current != expected_decision_id:
        raise StaleDecisionError(
            f"external_control: expected_decision_id={expected_decision_id!r} but session's "
            f"current decision_id={current!r} - session has moved on (or this action was "
            f"already committed); resolve() must be called again against the CURRENT decision"
        )
    legal_actions = session.get_legal_actions()
    matches = [a for a in legal_actions if a["action_id"] == action_id]
    if len(matches) != 1:
        raise ValueError(
            f"external_control: action_id={action_id!r} not a unique id among "
            f"{[a['action_id'] for a in legal_actions]!r}"
        )
    return session.step(action_id)


def apply_external_room_choice(session, expected_decision_id: tuple, room_id: int) -> dict:
    """`apply_external_action`'s Map counterpart (`ChooseRoom(roomId)`, no LegalActions)."""
    current = decision_id(session)
    if current != expected_decision_id:
        raise StaleDecisionError(
            f"external_control: expected_decision_id={expected_decision_id!r} but session's "
            f"current decision_id={current!r} - session has moved on (or this room choice "
            f"was already committed); resolve() must be called again against the CURRENT decision"
        )
    rooms = session.get_map_rooms()
    matches = [r for r in rooms if r["room_id"] == room_id]
    if len(matches) != 1:
        raise ValueError(f"external_control: room_id={room_id!r} not a unique id among {[r['room_id'] for r in rooms]!r}")
    return session.choose_room(room_id)
