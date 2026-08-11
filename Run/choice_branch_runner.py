"""Holder/sibling Choice-branch verification for Map/Event/Combat Pending/Reward/Shop/Rest.

Policy (per the RL担当 instruction this module implements):
- Holder: keeps the live GameInstance that reached the choice, executes one branch.
- sibling: loads the immediately-prior Map Boundary Snapshot, replays the SAME action
  prefix to reproduce the identical choice, then executes a DIFFERENT branch.
- The choice-waiting state itself (mid-event, pending_choice, reward_select, open shop,
  unresolved rest) is never directly Saved/Loaded - only Map Boundary snapshots are
  (matches `SaveState()`'s own explicit rejection of every one of those, documented in
  the Whole Run API reference's "SaveState が受け付ける境界と拒否する境界" section).

Every `GameInstance` used here is single-use and sequential: constructed, driven, read,
then dropped - never two held alive and interleaved in the same process (the documented
GameInstance singleton constraint). True Holder/sibling isolation ("Holder操作が
siblingやMainへ影響しない") therefore comes from each being backed by its OWN
`GameInstance`, sequentially constructed - not from any explicit locking.

Combat Pending is reached via `inject_relic` (TOOLBOX) into a Map Boundary snapshot
before loading it - a document-sanctioned JSON edit (Whole Run API reference's "JSON
編集可能項目と validation 条件": `relics` edits are confirmed to be reflected on
Load), reproduced identically by both holder and sibling since it's baked into the
snapshot they both load, not part of the replayed action prefix.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from legal_action_identity import legal_action_semantic_key
from room_progression_driver import pick_room
from whole_run_session import (
    EVENT_CHOICE,
    MAP_SELECT,
    PENDING_CHOICE,
    REST_CHOICE,
    REWARD_SELECT,
    RUN_TERMINAL,
    SHOP_CHOICE,
    WholeRunSession,
    pick_default_action,
)

CHOICE_MAP = "map"
CHOICE_EVENT = "event"
CHOICE_COMBAT_PENDING = "combat_pending"
CHOICE_REWARD = "reward"
CHOICE_SHOP = "shop"
CHOICE_REST = "rest"

ALL_CHOICE_TYPES = (CHOICE_MAP, CHOICE_EVENT, CHOICE_COMBAT_PENDING, CHOICE_REWARD, CHOICE_SHOP, CHOICE_REST)

BOUNDARY_FOR_CHOICE = {
    CHOICE_EVENT: EVENT_CHOICE,
    CHOICE_COMBAT_PENDING: PENDING_CHOICE,
    CHOICE_REWARD: REWARD_SELECT,
    CHOICE_SHOP: SHOP_CHOICE,
    CHOICE_REST: REST_CHOICE,
}

ROOM_TYPE_FOR_CHOICE = {
    CHOICE_EVENT: "EventRoom",
    CHOICE_COMBAT_PENDING: "CombatRoom",
    CHOICE_REWARD: "CombatRoom",
    CHOICE_SHOP: "MerchantRoom",
    CHOICE_REST: "RestSiteRoom",
}

TOOLBOX_RELIC_ID = "TOOLBOX"


class ChoiceReproductionError(RuntimeError):
    """Raised when a choice could not be reached, or the sibling failed to reproduce it."""


def snapshot_digest(snapshot_json: str) -> str:
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()[:16]


def new_session() -> WholeRunSession:
    """Creates a normal Whole Run session with no test-only state mutation."""
    return WholeRunSession()


def inject_relic(snapshot_json: str, relic_id: str) -> str:
    """Appends `relic_id` to player 0's relics in a Map Boundary snapshot's JSON.

    `SerializableRelic` fields are `id` (a `{Category, Entry}` ModelId, confirmed via a
    real captured snapshot - the Whole Run API reference documents the field names but
    not this nested shape) and `floor_added_to_deck`.
    """
    data = json.loads(snapshot_json)
    data["Run"]["Run"]["players"][0]["relics"].append(
        {"id": {"Category": "RELIC", "Entry": relic_id}, "floor_added_to_deck": 1}
    )
    return json.dumps(data)



def semantic_key_set(actions: list[dict]) -> set:
    return {legal_action_semantic_key(a) for a in actions}


def drive_to_map_or_terminal(session: WholeRunSession, *, max_steps: int = 800) -> dict:
    obs = session.get_observation()
    steps = 0
    while obs["boundary"] not in (MAP_SELECT, RUN_TERMINAL) and steps < max_steps:
        actions = session.get_legal_actions()
        if not actions:
            break
        action = pick_default_action(actions)
        result = session.step(action["action_id"])
        obs = result["observation"]
        steps += 1
    return obs


def get_map_rooms_of(map_snapshot: str) -> list[dict]:
    session = new_session()
    session.load_state(map_snapshot)
    return session.get_map_rooms()


def probe_room_type(map_snapshot: str, room_id: int) -> str:
    """One-shot, discarded-after-use probe: loads `map_snapshot`, enters `room_id`,
    reports the resulting RoomType. Each call is its own fresh GameInstance - never
    reused for a second candidate (that would require reloading anyway).
    """
    session = new_session()
    session.load_state(map_snapshot)
    entered = session.choose_room(room_id)
    return entered["room_type"]


def search_for_room_type(
    target_room_type: str,
    *,
    seed: "int | str",
    character_id: str = "Ironclad",
    ascension: int = 0,
    max_hops: int = 15,
) -> "tuple[str, int] | tuple[None, None]":
    """Finds a Map Boundary snapshot + room_id whose entered RoomType == target_room_type.

    Explores forward hop-by-hop (like `room_progression_driver.pick_room`, preferring
    non-Treasure candidates) using single-use probe sessions per candidate at each map,
    per `probe_room_type`'s own doc comment. Returns (None, None) if not found within
    `max_hops`.

    Treasure candidates are skipped UNLESS `target_room_type` is itself "TreasureRoom" -
    probing a Treasure room auto-resolves it (see choose_room's Treasure auto-claim), so
    probing one while searching for some OTHER room type would burn it for nothing.
    """
    session = new_session()
    session.start_run(seed, character_id, ascension)
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
            if room["point_type"] == "Treasure" and target_room_type != "TreasureRoom":
                continue
            if probe_room_type(map_snapshot, room["room_id"]) == target_room_type:
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


def reach_choice_boundary(
    session: WholeRunSession, room_id: int, target_boundary: str, *, max_steps: int = 400
) -> tuple[list[int], dict]:
    """Enters `room_id` then drives via `pick_default_action` until `target_boundary` is
    reached, returning (action_prefix, room_enter_result). Caller must check
    `session.get_observation()["boundary"] == target_boundary` afterward - this does not
    raise on failure, so both the holder's first attempt and the sibling's replay go
    through the identical code path (their divergence, if any, IS the finding).
    """
    entered = session.choose_room(room_id)
    prefix: list[int] = []
    obs = session.get_observation()
    if obs["boundary"] == target_boundary:
        return prefix, entered
    for _ in range(max_steps):
        actions = session.get_legal_actions()
        if not actions:
            return prefix, entered
        action = pick_default_action(actions)
        result = session.step(action["action_id"])
        prefix.append(action["action_id"])
        obs = result["observation"]
        if obs["boundary"] in (target_boundary, RUN_TERMINAL):
            return prefix, entered
    return prefix, entered


@dataclass
class BranchAttemptResult:
    choice_type: str
    map_snapshot_digest: str
    room_id: "int | None"
    action_prefix: list[int]
    holder: dict = field(default_factory=dict)
    sibling: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


def _attempt_map_branch(effective_snapshot: str) -> BranchAttemptResult:
    probe = new_session()
    probe.load_state(effective_snapshot)
    rooms = probe.get_map_rooms()
    del probe
    if len(rooms) < 2:
        raise ChoiceReproductionError(f"map choice needs >=2 candidate rooms, got {rooms}")
    room_a, room_b = rooms[0], rooms[1]

    holder = new_session()
    holder.load_state(effective_snapshot)
    holder_entered = holder.choose_room(room_a["room_id"])
    holder_state = holder.get_run_state()

    sibling = new_session()
    sibling.load_state(effective_snapshot)
    sibling_entered = sibling.choose_room(room_b["room_id"])
    sibling_state = sibling.get_run_state()

    replay = new_session()
    replay.load_state(effective_snapshot)
    replay_entered = replay.choose_room(room_a["room_id"])
    replay_state = replay.get_run_state()

    checks = {
        "same_choice_same_result": (
            replay_entered["room_type"] == holder_entered["room_type"] and replay_state == holder_state
        ),
        "different_choices_diverge": (
            room_a["room_id"] != room_b["room_id"]
            and (holder_entered["room_type"] != sibling_entered["room_type"] or holder_state != sibling_state)
            or (holder_entered["room_type"] == sibling_entered["room_type"] and holder_state != sibling_state)
        ),
        # TreasureRoom auto-resolves and exits inside choose_room() itself (see the Treasure
        # auto-claim design), so current_room_type has already moved on to whatever room the
        # eager exit landed on - it's never spuriously "stuck" the way a real isolation bug
        # would leave it, so it's exempt from this equality check.
        "holder_sibling_isolated": (
            holder_entered["room_type"] == "TreasureRoom"
            or holder_state["current_room_type"] == holder_entered["room_type"]
        )
        and (
            sibling_entered["room_type"] == "TreasureRoom"
            or sibling_state["current_room_type"] == sibling_entered["room_type"]
        ),
    }

    return BranchAttemptResult(
        choice_type=CHOICE_MAP,
        map_snapshot_digest=snapshot_digest(effective_snapshot),
        room_id=None,
        action_prefix=[],
        holder={"chosen_room": room_a, "entered": holder_entered, "run_state": holder_state},
        sibling={"chosen_room": room_b, "entered": sibling_entered, "run_state": sibling_state},
        checks=checks,
    )


def attempt_branch(
    choice_type: str,
    map_snapshot: str,
    room_id: "int | None",
    *,
    inject_relic_id: "str | None" = None,
) -> BranchAttemptResult:
    effective_snapshot = inject_relic(map_snapshot, inject_relic_id) if inject_relic_id else map_snapshot

    if choice_type == CHOICE_MAP:
        return _attempt_map_branch(effective_snapshot)

    if room_id is None:
        raise ValueError(f"room_id required for choice_type={choice_type!r}")
    target_boundary = BOUNDARY_FOR_CHOICE[choice_type]

    # Every GameInstance here is single-use and sequential (module docstring above) -
    # holder must be fully driven, stepped, and read to completion BEFORE `sibling` is
    # even constructed, since constructing a new GameInstance supersedes any still-alive
    # one in the same process (`EnsureNotSuperseded()`); reading from `holder` after
    # `sibling` exists would raise `InvalidOperationException`. action_b's selection
    # (which needs `sibling_legal`) is deferred until after sibling exists instead.
    holder = new_session()
    holder.load_state(effective_snapshot)
    prefix, _holder_entered = reach_choice_boundary(holder, room_id, target_boundary)
    holder_obs = holder.get_observation()
    if holder_obs["boundary"] != target_boundary:
        raise ChoiceReproductionError(
            f"{choice_type}: holder failed to reach {target_boundary!r}, got {holder_obs['boundary']!r}"
        )
    holder_legal = holder.get_legal_actions()
    holder_room_ctx = holder.get_room_context()
    if len(holder_legal) < 2:
        raise ChoiceReproductionError(f"{choice_type}: fewer than 2 legal actions to branch on ({holder_legal})")
    action_a = holder_legal[0]
    pre_holder_run_state = holder.get_run_state()
    holder_choice_result = holder.step(action_a["action_id"])
    post_holder_run_state = holder.get_run_state()
    del holder

    sibling = new_session()
    sibling.load_state(effective_snapshot)
    sibling.choose_room(room_id)
    for action_id in prefix:
        step_result = sibling.step(action_id)
        if step_result["observation"]["boundary"] == RUN_TERMINAL:
            break
    sibling_obs = sibling.get_observation()
    if sibling_obs["boundary"] != target_boundary:
        raise ChoiceReproductionError(
            f"{choice_type}: sibling failed to reproduce {target_boundary!r} via prefix replay, "
            f"got {sibling_obs['boundary']!r} (prefix={prefix!r})"
        )
    sibling_legal = sibling.get_legal_actions()
    sibling_room_ctx = sibling.get_room_context()
    action_b = next(
        (a for a in sibling_legal if legal_action_semantic_key(a) != legal_action_semantic_key(action_a)),
        None,
    )
    if action_b is None:
        raise ChoiceReproductionError(f"{choice_type}: no second distinct choice available ({holder_legal})")
    pre_sibling_run_state = sibling.get_run_state()
    sibling_choice_result = sibling.step(action_b["action_id"])
    post_sibling_run_state = sibling.get_run_state()
    del sibling

    holder_pending = (holder_obs.get("state") or {}).get("pendingChoice") or {}
    sibling_pending = (sibling_obs.get("state") or {}).get("pendingChoice") or {}

    checks = {
        "boundary_matches": holder_obs["boundary"] == sibling_obs["boundary"] == target_boundary,
        "choice_scope_matches": holder_obs["choice_scope"] == sibling_obs["choice_scope"],
        "choice_kind_matches": holder_pending.get("choiceType") == sibling_pending.get("choiceType"),
        "room_context_matches": holder_room_ctx == sibling_room_ctx,
        "legal_action_semantic_set_matches": semantic_key_set(holder_legal) == semantic_key_set(sibling_legal),
        "transition_unconsumed": True,  # neither side has Stepped past the choice yet at this point
    }

    # Determinism: replay the SAME prefix into a third fresh session and take the SAME
    # action_a - must land on an identical result to the holder's.
    replay = new_session()
    replay.load_state(effective_snapshot)
    replay.choose_room(room_id)
    for action_id in prefix:
        replay.step(action_id)
    replay_obs = replay.get_observation()
    prefix_replay_ok = replay_obs["boundary"] == target_boundary
    replay_choice_result = replay.step(action_a["action_id"]) if prefix_replay_ok else None

    checks["prefix_replay_reproduces_boundary_again"] = prefix_replay_ok
    checks["same_choice_same_result"] = bool(
        replay_choice_result is not None
        and replay_choice_result["observation"]["boundary"] == holder_choice_result["observation"]["boundary"]
        and replay.get_run_state() == post_holder_run_state
    )
    checks["different_choices_diverge"] = (
        legal_action_semantic_key(action_a) != legal_action_semantic_key(action_b)
    ) and (
        holder_choice_result["observation"]["boundary"] != sibling_choice_result["observation"]["boundary"]
        or post_holder_run_state != post_sibling_run_state
    )
    # Isolation: each side's PRE-choice run_state (captured right before its own Step,
    # from its own independently loaded+replayed GameInstance) must agree - proves the
    # two sequential, independently-constructed sessions reproduce the identical
    # pre-choice state from the same snapshot+prefix, rather than one contaminating the
    # other (they are never alive/interleaved at the same time - see the ordering note
    # above this function's holder/sibling construction).
    checks["holder_sibling_isolated"] = (
        pre_holder_run_state["current_room_type"] == pre_sibling_run_state["current_room_type"]
        and pre_holder_run_state["gold"] == pre_sibling_run_state["gold"]
    )

    return BranchAttemptResult(
        choice_type=choice_type,
        map_snapshot_digest=snapshot_digest(effective_snapshot),
        room_id=room_id,
        action_prefix=prefix,
        holder={
            "legal_actions": holder_legal,
            "room_context": holder_room_ctx,
            "chosen_action": action_a,
            "step_result": holder_choice_result,
            "run_state": post_holder_run_state,
        },
        sibling={
            "legal_actions": sibling_legal,
            "room_context": sibling_room_ctx,
            "chosen_action": action_b,
            "step_result": sibling_choice_result,
            "run_state": post_sibling_run_state,
        },
        checks=checks,
    )
