"""OS-process-separated Holder/sibling Choice branching, built on `worker_pool.py`.

Replaces `choice_branch_runner.attempt_branch`'s single-process implementation (which
constructed many `GameInstance` objects in the Controller's own process, interleaving a
holder and a sibling - exactly the misuse the Emulator's `fca2f06` "superseded" guard now
fails fast on). Every `GameInstance` touch here happens inside a `WholeRunWorkerPool`
worker OS process; the Controller (this module, run in the main/orchestrating process)
never constructs one.

Policy per choice type, all six unified on the same flow:

1. `establish` (WORK_KIND_SUB_BRANCH, no resolve_action_id): a Branch Worker loads the
   Map Boundary Run Snapshot (+ relic injection for Combat Pending), enters the Room,
   replays the Action Prefix, and reports the reached Choice without resolving it. This
   worker becomes the Holder; its Lease is registered against `context_id`.
2. `holder_resolve` (WORK_KIND_CONTINUATION) + `sibling_resolve` (WORK_KIND_SUB_BRANCH)
   dispatched together in one batch: the former routes via the Lease back to the SAME
   worker process that just established the choice (the live Holder, no reload); the
   latter bootstraps FRESH on a different, unleased worker (loads the same Map Boundary
   Snapshot, replays the same prefix independently) and resolves a DIFFERENT action.
3. A `determinism_replay` (WORK_KIND_SUB_BRANCH) bootstraps yet another fresh worker with
   the SAME action as the Holder's, to confirm identical Snapshot + identical Prefix +
   identical Choice reproduces an identical result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from worker_pool import (
    BRANCH_STATUS_SUCCESS,
    WORK_KIND_CONTINUATION,
    WORK_KIND_SUB_BRANCH,
    ChoiceWorkItem,
    LeaseRegistry,
    WholeRunWorkerPool,
    derive_context_id,
)

CHOICE_MAP = "map"
CHOICE_EVENT = "event"
CHOICE_COMBAT_PENDING = "combat_pending"
CHOICE_REWARD = "reward"
CHOICE_SHOP = "shop"
CHOICE_REST = "rest"

ALL_CHOICE_TYPES = (CHOICE_MAP, CHOICE_EVENT, CHOICE_COMBAT_PENDING, CHOICE_REWARD, CHOICE_SHOP, CHOICE_REST)

BOUNDARY_FOR_CHOICE = {
    CHOICE_MAP: "map_select",
    CHOICE_EVENT: "event_choice",
    CHOICE_COMBAT_PENDING: "pending_choice",
    CHOICE_REWARD: "reward_select",
    CHOICE_SHOP: "shop_choice",
    CHOICE_REST: "rest_choice",
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
    pass


def legal_action_semantic_key(action: dict) -> tuple:
    params = action.get("parameters") or {}
    key_param_names = ("cardId", "potionId", "eventId", "choiceId", "enemyIndex", "cost", "optionId")
    key_params = tuple(sorted((k, params[k]) for k in key_param_names if k in params))
    return (action["action_type"], action.get("label"), key_params)


def semantic_key_set(actions: list[dict]) -> set:
    return {legal_action_semantic_key(a) for a in actions}


def _content_fingerprint(step_result: dict, choice_type: str = "") -> tuple:
    """A comparison key sensitive to the CONTENT of a resolved Choice's effect, not just
    its resulting Boundary/run_state summary. `WholeRunSession.get_run_state()`'s
    `RunStateSummary` only carries deck SIZE (no card ids) and no hand/potions at all -
    coarse enough that two DIFFERENT reward cards (or two different Toolbox-selected
    hand cards) with the same resulting deck/hand COUNT would look identical to it. This
    reads the full `Observation.State` dict instead, where deck/hand/potions/relics card
    ids are visible.
    """
    if choice_type == "map":
        # No deck/hand/gold/relic change is expected immediately from choosing a room -
        # the divergent content is WHICH room got entered (RoomType + map coordinates).
        entered = step_result.get("room_enter_result") or {}
        room_context = step_result.get("room_context") or {}
        return (entered.get("room_type"), room_context.get("column"), room_context.get("row"))

    state = (step_result.get("observation") or {}).get("state") or {}

    def _ids(field_name: str, key: str = "id") -> tuple:
        items = state.get(field_name) or []
        return tuple(sorted(str(item.get(key)) for item in items if isinstance(item, dict)))

    return (
        state.get("boundary"),
        _ids("deck"),
        _ids("hand"),
        _ids("potions", key="potion_id"),
        tuple(sorted(str(r) for r in (state.get("relics") or []))),
        state.get("gold"),
        state.get("hp"),
    )


@dataclass
class BranchAttempt:
    choice_type: str
    context_id: str
    room_id: "int | None"
    action_prefix: list[int]
    establish: Any = None
    holder: Any = None
    sibling: Any = None
    determinism_replay: Any = None
    checks: dict = field(default_factory=dict)
    process_table: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


def run_choice_branch(
    pool: WholeRunWorkerPool,
    registry: LeaseRegistry,
    choice_type: str,
    map_snapshot: str,
    room_id: "int | None",
    *,
    action_prefix: "list[int] | None" = None,
    relic_injection: "str | None" = None,
) -> BranchAttempt:
    action_prefix = action_prefix or []
    target_boundary = BOUNDARY_FOR_CHOICE[choice_type]
    context_id = derive_context_id(
        map_snapshot=map_snapshot,
        room_id=room_id,
        action_prefix=action_prefix,
        choice_type=choice_type,
        relic_injection=relic_injection,
    )

    establish_item = ChoiceWorkItem(
        work_id=f"{context_id}:establish",
        context_id=context_id,
        choice_type=choice_type,
        map_snapshot=map_snapshot,
        room_id=room_id,
        action_prefix=action_prefix,
        relic_injection=relic_injection,
        target_boundary=target_boundary,
        work_kind=WORK_KIND_SUB_BRANCH,
        discover_prefix=not action_prefix,
    )
    (establish_result,) = pool.dispatch_choice_work_items([establish_item], registry)
    if establish_result.status == BRANCH_STATUS_SUCCESS and establish_result.discovered_action_prefix is not None:
        # The Holder discovered the real path from ChooseRoom to the Choice (e.g. an
        # entire combat's worth of actions for Reward) - the sibling/replay jobs below
        # MUST replay this EXACT sequence, never re-discover independently (which could
        # take a different path through combat/multi-page events and reach a
        # different Choice instance).
        action_prefix = establish_result.discovered_action_prefix
    attempt = BranchAttempt(choice_type=choice_type, context_id=context_id, room_id=room_id, action_prefix=action_prefix)
    attempt.establish = establish_result
    attempt.process_table.append(
        {"role": "establish/holder", "worker_slot": establish_result.worker_slot, "worker_generation": establish_result.worker_generation, "pid": establish_result.pid}
    )
    if establish_result.status != BRANCH_STATUS_SUCCESS:
        raise ChoiceReproductionError(f"{choice_type}: establish failed: {establish_result.diagnostics}")

    holder_slot = establish_result.worker_slot
    reach = establish_result.reach
    legal = reach.legal_actions
    if len(legal) < 2:
        raise ChoiceReproductionError(f"{choice_type}: fewer than 2 legal actions to branch on ({legal})")
    action_a = legal[0]
    action_b = next((a for a in legal if legal_action_semantic_key(a) != legal_action_semantic_key(action_a)), None)
    if action_b is None:
        raise ChoiceReproductionError(f"{choice_type}: no second distinct choice available ({legal})")

    holder_item = ChoiceWorkItem(
        work_id=f"{context_id}:holder",
        context_id=context_id,
        choice_type=choice_type,
        map_snapshot=map_snapshot,
        room_id=room_id,
        action_prefix=action_prefix,
        relic_injection=relic_injection,
        target_boundary=target_boundary,
        work_kind=WORK_KIND_CONTINUATION,
        resolve_action_id=action_a["action_id"],
    )
    sibling_item = ChoiceWorkItem(
        work_id=f"{context_id}:sibling",
        context_id=context_id,
        choice_type=choice_type,
        map_snapshot=map_snapshot,
        room_id=room_id,
        action_prefix=action_prefix,
        relic_injection=relic_injection,
        target_boundary=target_boundary,
        work_kind=WORK_KIND_SUB_BRANCH,
        resolve_action_id=action_b["action_id"],
    )
    results = pool.dispatch_choice_work_items(
        [holder_item, sibling_item], registry, holder_slots={holder_item.work_id: holder_slot}
    )
    holder_result = next(r for r in results if r.work_item.work_id == holder_item.work_id)
    sibling_result = next(r for r in results if r.work_item.work_id == sibling_item.work_id)
    attempt.holder = holder_result
    attempt.sibling = sibling_result
    attempt.process_table.append({"role": "holder_resolve", "worker_slot": holder_result.worker_slot, "worker_generation": holder_result.worker_generation, "pid": holder_result.pid})
    attempt.process_table.append({"role": "sibling_resolve", "worker_slot": sibling_result.worker_slot, "worker_generation": sibling_result.worker_generation, "pid": sibling_result.pid})

    if holder_result.status != BRANCH_STATUS_SUCCESS:
        raise ChoiceReproductionError(f"{choice_type}: holder resolve failed: {holder_result.diagnostics}")
    if sibling_result.status != BRANCH_STATUS_SUCCESS:
        raise ChoiceReproductionError(f"{choice_type}: sibling resolve failed: {sibling_result.diagnostics}")

    replay_item = ChoiceWorkItem(
        work_id=f"{context_id}:replay",
        context_id=context_id,
        choice_type=choice_type,
        map_snapshot=map_snapshot,
        room_id=room_id,
        action_prefix=action_prefix,
        relic_injection=relic_injection,
        target_boundary=target_boundary,
        work_kind=WORK_KIND_SUB_BRANCH,
        resolve_action_id=action_a["action_id"],
    )
    (replay_result,) = pool.dispatch_choice_work_items([replay_item], registry)
    attempt.determinism_replay = replay_result
    attempt.process_table.append({"role": "determinism_replay", "worker_slot": replay_result.worker_slot, "worker_generation": replay_result.worker_generation, "pid": replay_result.pid})
    if replay_result.status != BRANCH_STATUS_SUCCESS:
        raise ChoiceReproductionError(f"{choice_type}: determinism replay failed: {replay_result.diagnostics}")

    holder_reach = holder_result.reach
    sibling_reach = sibling_result.reach
    holder_step = holder_result.step.step_result
    sibling_step = sibling_result.step.step_result
    replay_step = replay_result.step.step_result

    pids = {holder_result.pid, sibling_result.pid}
    checks = {
        "holder_sibling_pids_differ": len(pids) == 2 and None not in pids,
        "boundary_matches": holder_reach.boundary == sibling_reach.boundary == target_boundary,
        "choice_scope_matches": holder_reach.choice_scope == sibling_reach.choice_scope,
        "choice_kind_matches": holder_reach.choice_kind == sibling_reach.choice_kind,
        "room_context_matches": holder_reach.room_context == sibling_reach.room_context,
        "legal_action_semantic_set_matches": semantic_key_set(holder_reach.legal_actions) == semantic_key_set(sibling_reach.legal_actions),
        "run_identifiers_match": (
            holder_reach.observation.get("seed") == sibling_reach.observation.get("seed")
            and holder_reach.observation.get("character_id") == sibling_reach.observation.get("character_id")
        ),
        "different_choices_diverge": (
            legal_action_semantic_key(action_a) != legal_action_semantic_key(action_b)
        )
        and (_content_fingerprint(holder_step, choice_type) != _content_fingerprint(sibling_step, choice_type)),
        "same_choice_same_result_determinism": (
            _content_fingerprint(replay_step, choice_type) == _content_fingerprint(holder_step, choice_type)
            and replay_result.step.run_state == holder_result.step.run_state
        ),
        "holder_sibling_isolated": (
            holder_reach.run_state.get("current_room_type") == sibling_reach.run_state.get("current_room_type")
            and holder_reach.run_state.get("gold") == sibling_reach.run_state.get("gold")
        ),
    }
    attempt.checks = checks
    return attempt
