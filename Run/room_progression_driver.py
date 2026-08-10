"""Drives a Whole Run across many rooms end to end, for Whole Run connectivity testing.

Not a gameplay policy - `whole_run_session.pick_default_action` is a deterministic,
policy-free action picker, and `pick_room` here prefers non-Treasure map nodes so this
driver spends its `min_rooms` budget exercising a wider mix of room kinds rather than
repeatedly walking into Treasure. Treasure itself DOES have a resolve path now:
`choose_room()` auto-claims gold/relic and eager-exits back to `map_select` inside the
same call (see `Outputs/reports/treasure_room_fix_implementation_plan_final_20260810.md`
in this repo) - it no longer has "no public resolve API" as older revisions of this
comment claimed. This module exists to exercise Map/Combat/Combat Reward/Event/Shop/Rest
connectivity and produce a full per-step log, not to play well.
"""

from __future__ import annotations

import hashlib
from typing import Any

from whole_run_session import (
    MAP_SELECT,
    RUN_TERMINAL,
    WholeRunSession,
    pick_default_action,
)

DEFAULT_MAX_STEPS = 4000


def snapshot_digest(snapshot_json: str) -> str:
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()[:16]


def pick_room(rooms: list[dict], exclude_room_ids: "set[int] | None" = None) -> "dict | None":
    """Prefers a non-Treasure candidate not already excluded; None if nothing usable is left.

    This is a coverage preference, not a workaround - Treasure auto-resolves cleanly via
    `choose_room()` (see the module docstring), it's just a single-outcome room type that
    would otherwise dominate a driver looking for varied connectivity coverage.
    """
    excluded = exclude_room_ids or set()
    pool = [r for r in rooms if r["room_id"] not in excluded]
    non_treasure = [r for r in pool if r["point_type"] != "Treasure"]
    candidates = non_treasure or pool
    return candidates[0] if candidates else None


def run_state_light(session: WholeRunSession) -> dict:
    rs = session.get_run_state()
    return {k: rs[k] for k in ("gold", "hp", "max_hp", "deck_size", "relics", "current_room_type")}


def full_state_fields(observation: dict) -> dict:
    """HP/Deck/Relic/Potion/Gold straight from `GameObservation.State` - `RunStateSummary`
    only carries deck SIZE and no potions at all, so this reads the raw state dict
    (`GameInstance.BuildFullStateDict`) instead for the fields the instruction asks to log.
    """
    state = observation.get("state") or {}
    return {
        "deck": state.get("deck"),
        "potions": state.get("potions"),
        "relics": state.get("relics"),
        "gold": state.get("gold"),
        "hp": state.get("hp"),
        "max_hp": state.get("maxHp"),
    }


def drive_rooms(
    session: WholeRunSession,
    *,
    min_rooms: int = 10,
    max_steps: int = DEFAULT_MAX_STEPS,
    seed: "int | str" = 1,
    character_id: str = "Ironclad",
    ascension: int = 0,
    already_started: bool = False,
    action_picker=pick_default_action,
    room_picker=pick_room,
) -> dict:
    """Drives `session` through at least `min_rooms` map rooms (or until `run_terminal`/
    `max_steps`), returning a full per-step log plus a room-type-kind coverage summary.

    Each log entry records boundary (pre/post), the Transition (if any), RoomContext,
    the chosen action, run/combat-session identifiers, and HP/Deck/Relic/Potion/Gold -
    per the instruction's per-Step logging requirement. A `map_snapshot` is captured
    (as a JSON string plus its digest) every time the map boundary is reached, both to
    log a Snapshot identifier and to make routing around an unsupported room (Treasure)
    possible by reloading it and picking a different candidate.

    `action_picker`/`room_picker` default to the existing LEGACY filler policies
    (`pick_default_action`/`pick_room`) for full backward compatibility. Pass
    `action_picker=whole_run_session.zero_index_action` and
    `room_picker=lambda rooms, exclude_room_ids=None: next((r for r in rooms if r["room_id"] not in (exclude_room_ids or set())), None)`
    for the strict `zero_index` mode (legal_actions[0]/rooms[0], no reordering) - see
    `Run/execution_mode.py`.
    """
    log: list[dict] = []
    unsupported_rooms: list[dict] = []
    room_kinds_seen: set[str] = set()

    if not already_started:
        session.enable_god_mode_for_testing()
        session.start_run(seed, character_id, ascension)

    obs = session.get_observation()
    rooms_entered = 0
    step_count = 0
    last_map_snapshot: "str | None" = None
    tried_room_ids_at_current_map: set[int] = set()

    while step_count < max_steps:
        if obs["boundary"] == RUN_TERMINAL:
            log.append({"event": "run_terminal", "observation": obs})
            break

        if obs["boundary"] == MAP_SELECT:
            rooms = session.get_map_rooms()
            if not rooms:
                log.append({"event": "no_map_rooms"})
                break
            if last_map_snapshot is None or not tried_room_ids_at_current_map:
                last_map_snapshot = session.save_state()
                tried_room_ids_at_current_map = set()
            chosen = room_picker(rooms, tried_room_ids_at_current_map)
            if chosen is None:
                log.append({"event": "map_exhausted_all_candidates_unsupported", "available_rooms": rooms})
                break
            tried_room_ids_at_current_map.add(chosen["room_id"])
            try:
                entered = session.choose_room(chosen["room_id"])
            except Exception as exc:  # noqa: BLE001 - real Emulator-side action faults (e.g.
                # a NullReferenceException inside a specific event's own effect code, seen live
                # against DenseVegetation) surface here as CLR exceptions; not a contract
                # violation of this bridge, so record and stop this attempt rather than crashing.
                log.append(
                    {
                        "event": "action_fault_on_choose_room",
                        "room_id": chosen["room_id"],
                        "point_type": chosen["point_type"],
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    }
                )
                break
            rooms_entered += 1
            step_count += 1
            room_kinds_seen.add(entered["room_type"])
            log.append(
                {
                    "event": "choose_room",
                    "room_index": rooms_entered,
                    "chosen_room": chosen,
                    "available_rooms": rooms,
                    "map_snapshot_digest": snapshot_digest(last_map_snapshot),
                    "room_type": entered["room_type"],
                    "is_combat": entered["is_combat"],
                }
            )
            obs = session.get_observation()
            if obs["boundary"] == MAP_SELECT:
                # choose_room() auto-resolved this room (Treasure's auto-claim + eager
                # exit) and we're already at the NEXT map fork - last_map_snapshot/
                # tried_room_ids_at_current_map describe the OLD fork, so keeping them
                # would roll a later unsupported room back past this resolution. Reset
                # now, same as a real Step success does below.
                last_map_snapshot = None
                tried_room_ids_at_current_map = set()
                continue
            # Otherwise, deliberately do NOT clear last_map_snapshot/tried_room_ids_at_current_map
            # here: the just-entered room might turn out unsupported with zero legal actions,
            # in which case the "unsupported_room_no_actions" branch below needs
            # last_map_snapshot to route around it. They are only cleared once a real Step
            # actually succeeds (below) - proof this map fork is no longer "in progress".
            continue

        actions = session.get_legal_actions()
        if not actions:
            ctx = session.get_room_context()
            unsupported_rooms.append(
                {"room_type": ctx["room_type"], "boundary": obs["boundary"], "room_context": ctx}
            )
            log.append({"event": "unsupported_room_no_actions", "room_context": ctx, "observation": obs})
            if last_map_snapshot is not None:
                # Route around it: reload the map boundary this room was entered from and
                # try a different candidate next iteration (tried_room_ids_at_current_map
                # already excludes the one that led here).
                session.load_state(last_map_snapshot)
                obs = session.get_observation()
                log.append({"event": "reloaded_prior_map_snapshot_to_route_around_unsupported_room"})
                continue
            log.append({"event": "no_prior_map_snapshot_to_route_around_unsupported_room_stopping"})
            break

        action = action_picker(actions)
        try:
            result = session.step(action["action_id"])
        except Exception as exc:  # noqa: BLE001 - see the matching comment on choose_room above.
            log.append(
                {
                    "event": "action_fault_on_step",
                    "pre_boundary": obs["boundary"],
                    "action": {
                        "action_id": action["action_id"],
                        "action_type": action["action_type"],
                        "label": action["label"],
                    },
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                }
            )
            break
        step_count += 1
        # A real Step succeeded - this map fork is resolved; any future map_select is a new
        # fork position and needs its own fresh snapshot/exclusion set.
        last_map_snapshot = None
        tried_room_ids_at_current_map = set()
        log.append(
            {
                "event": "step",
                "step_index": step_count,
                "pre_boundary": obs["boundary"],
                "action": {
                    "action_id": action["action_id"],
                    "action_type": action["action_type"],
                    "label": action["label"],
                },
                "post_boundary": result["observation"]["boundary"],
                "room_context": result["room_context"],
                "transition": result["transition"],
                "done": result["done"],
                "combat_session_id": result["observation"]["combat_session_id"],
                "run_id_fields": {
                    "seed": result["observation"]["seed"],
                    "seed_text": result["observation"]["seed_text"],
                    "character_id": result["observation"]["character_id"],
                },
                "state_fields": full_state_fields(result["observation"]),
                "run_state": run_state_light(session),
            }
        )
        obs = result["observation"]

    return {
        "rooms_entered": rooms_entered,
        "steps": step_count,
        "room_kinds_seen": sorted(room_kinds_seen),
        "unsupported_rooms": unsupported_rooms,
        "final_boundary": obs["boundary"],
        "log": log,
    }


if __name__ == "__main__":
    import json
    import sys

    session = WholeRunSession()
    summary = drive_rooms(session, min_rooms=10)
    print(
        json.dumps(
            {
                "rooms_entered": summary["rooms_entered"],
                "steps": summary["steps"],
                "room_kinds_seen": summary["room_kinds_seen"],
                "unsupported_rooms": summary["unsupported_rooms"],
                "final_boundary": summary["final_boundary"],
            },
            indent=2,
        ),
        file=sys.stderr,
    )
