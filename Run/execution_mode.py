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
