"""Whole Run Worker Pool - OS-process-separated Choice branching for Run/.

Architecture (mirrors Combat/search/branch_worker_pool.py's persistent-process design,
reimplemented independently for Whole Run rather than imported/modified - see the
module docstring's "why a separate module" note below):

    Controller
    |- Main Run Worker   (continuous room progression)
    |- Branch Worker 1
    |- Branch Worker 2
    `- Branch Worker N

Every worker is an OS process spawned via ``multiprocessing.get_context("spawn")``
(Windows-safe). Each worker constructs exactly ONE ``WholeRunSession`` (=> exactly one
CLR ``GameInstance``) ONCE at process startup and keeps it for the worker's entire
lifetime - `load_state`/`start_run`/`choose_room`/`step` calls REINITIALIZE that same
object across many requests; a worker never constructs a second `GameInstance` and the
Controller never constructs one at all. This is the fix for the pre-existing bug this
task supersedes: a single Python process previously constructed many `GameInstance`
objects back-to-back (probe/holder/sibling/replay), which the Emulator's own
`EnsureNotSuperseded()` guard (commit `fca2f06`) now fails fast on with a
`"...superseded..."` `InvalidOperationException` the instant one is touched while
another has since taken ownership of the process-wide `RunManager.Instance`/
`CombatManager.Instance` singletons - only ONE `GameInstance` may ever be *live and
in-use* per OS process, full stop.

Why a separate module instead of extending Combat's `branch_worker_pool.py`: that
module's `WorkItem`/`DecisionContext`/`LiveCombatSession`/`PipelineCandidateRef` are
tightly coupled to Combat's own search layer (Combat-only Boundary vocabulary,
Combat-only session type). Reusing it directly would require changing its types to
also cover Whole Run's very different domain (Map/Event/Reward/Shop/Rest, not just
in-combat card/potion actions) - exactly the kind of change the task's own stop
condition warns against ("Combat側の既存Worker Pool再利用により既存探索へ回帰が発生").
Instead, this module reuses the same PATTERN (spawn context, one Queue in / one shared
Queue out per pool, persistent per-worker runtime object, `Lease`/`LeaseRegistry`,
Continuation-vs-Bootstrap routing, fault-driven lease invalidation) without importing
or modifying a single line of `Combat/search/branch_worker_pool.py`.

As of the request-id-safety/auto-respawn pass, `execute()`'s and
`dispatch_choice_work_items()`'s stray-result-discard, timeout handling, and
respawn-then-synthesize-fault behavior deliberately mirror
`Combat/search/branch_worker_pool.py`'s `execute()`/`dispatch_work_items()` line-for-line
in SHAPE, but remain independently implemented here, not imported or shared, for the same
reasons given above. This ~40-line duplication between the two modules is accepted
process-management duplication, not an oversight - do not "fix" it by extracting a shared
base module without revisiting this note first (see the domain-type-coupling risk
explained above; a shared drain/respawn helper would need to be generic over each pool's
own `WorkItem`/`Lease`/`BranchResult` shapes, which is exactly the coupling this module was
created to avoid).

IPC purity: every dataclass that crosses a `multiprocessing.Queue` here is built from
plain JSON-safe primitives only (str/int/float/bool/None/dict/list) - `WholeRunSession`
methods already return plain dicts (see `run_emulator_bridge.py`), so no CLR-object
marshalling step is needed the way Combat's `_snapshot_ipc_json` needs one for
`CombatStateSnapshot`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing
import os
import queue
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from legal_action_identity import legal_action_semantic_key_text
from reward_auto_progress import drain_trivial_reward_frontier

WORK_KIND_CONTINUATION = "continuation"  # route to the worker already holding the Lease (Holder)
WORK_KIND_SUB_BRANCH = "sub_branch"  # bootstrap fresh on a different, unleased worker (sibling)
WORK_KIND_VALUES = frozenset({WORK_KIND_CONTINUATION, WORK_KIND_SUB_BRANCH})

EXECUTION_MODE_BOOTSTRAP_STEP = "bootstrap_step"
EXECUTION_MODE_HOLDER_STEP = "holder_step"
EXECUTION_MODE_VALUES = frozenset({EXECUTION_MODE_BOOTSTRAP_STEP, EXECUTION_MODE_HOLDER_STEP})

BRANCH_STATUS_SUCCESS = "success"
BRANCH_STATUS_FAULT = "fault"

MAIN_WORKER_SLOT = "main"


def _json_digest(payload: Any, *, length: int = 16) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def derive_context_id(
    *,
    map_snapshot: str,
    room_id: "int | None",
    action_prefix: list[int],
    choice_type: str,
    relic_injection: "str | None",
) -> str:
    """Stable identity for "the same Choice context" - a Map Boundary Run Snapshot plus
    the Room chosen from it plus the replayed Action Prefix plus (for Combat Pending)
    the relic-injection edit applied before loading. Two independently bootstrapped
    workers given identical inputs must derive the identical context_id.
    """
    return _json_digest(
        {
            "map_snapshot_sha256": hashlib.sha256(map_snapshot.encode("utf-8")).hexdigest(),
            "room_id": room_id,
            "action_prefix": action_prefix,
            "choice_type": choice_type,
            "relic_injection": relic_injection,
        }
    )


@dataclass(frozen=True)
class EventRngReplayPlan:
    """Active Event RNG Hypothesis replay plan - the 3 values a Branch (or a whole chain
    of descendant Branches) needs to apply a previously-established Hypothesis at exactly
    the right point during replay, consolidated into one immutable object so the
    application site is a single value rather than 3 independently-passed-around fields.

    A child Branch inherits its parent's `EventRngReplayPlan` UNCHANGED (never
    re-derives/recomputes it) - see `API/instance_whole_run.py`'s
    `WholeRunInstance.emulate_action`, non-root parent branch.
    """

    hypothesis_key: tuple
    """The Hypothesis Key `(parent_branch_id, decision_point_id, rng_id)` this plan's
    `override_state` was ESTABLISHED under (may belong to an ancestor Branch, for a deep
    Branch that inherited rather than re-derived). Also the key
    `API.whole_run_event_rng.EventRngHypothesisRegistry` uses for its own,
    separate ref-counted lifecycle bookkeeping - this plan does not itself manage that
    lifecycle, only carries the key."""
    override_state: dict
    """Active Event RNG Hypothesis state (`API/whole_run_event_rng.py`), shaped
    exactly like `WholeRunSession.get_event_rng_state()`'s return value (a full
    replacement across all 4 streams, not a partial one). Applied via
    `WholeRunSession.set_event_rng_state()` exactly ONCE per replay, immediately before
    stepping the entry at `apply_before_action_index` (see below) - never reapplied at
    any other point."""
    apply_before_action_index: int
    """0-based index into the logical replay sequence `action_prefix +
    [resolve_action_id]` at which `override_state` must be applied, once, right before
    that entry is stepped. `index == len(action_prefix)` means "apply right before
    `resolve_action_id`" (the common case: the Branch that FIRST establishes a
    Hypothesis). `index < len(action_prefix)` means "apply mid-prefix-replay, before
    `action_prefix[index]`" - the case for a DEEPER Branch whose `action_prefix` has
    grown past the point the Hypothesis was originally established at: the override
    must stay pinned to that ORIGINAL point so replay continues NATURALLY (consuming/
    advancing the RNG via normal gameplay) from there, rather than being reset back to
    the same override state before every subsequent step (RL担当指示：Active Event RNG
    Hypothesis実装 §4 "親Branchが到達したHidden Stateから継続する" - continue from what
    the parent reached, don't regenerate)."""


@dataclass(frozen=True)
class ChoiceWorkItem:
    """One Choice-branch job: reach a Choice (bootstrap or continuation) and optionally
    resolve it with `resolve_action_id`. Every field is JSON/pickle-safe.
    """

    work_id: str
    context_id: str
    choice_type: str
    map_snapshot: str
    room_id: "int | None"
    action_prefix: list[int]
    relic_injection: "str | None"
    target_boundary: str
    work_kind: str
    resolve_action_id: "int | None" = None
    event_rng_plan: "EventRngReplayPlan | None" = None
    """`None` means no Active Event RNG Hypothesis override applies to this replay
    (unchanged behavior). See `EventRngReplayPlan` for field-level detail."""
    discover_prefix: bool = False
    """When True (only meaningful for the very first `establish` job of a branch
    attempt), the worker DRIVES FORWARD from ChooseRoom using the same policy-free
    default action picker `room_progression_driver.pick_default_action` until it
    reaches `target_boundary`, instead of replaying `action_prefix` literally -
    `action_prefix` is ignored on input and the ACTUALLY-taken sequence is returned via
    `BranchResult.discovered_action_prefix` so the sibling/replay jobs can replay the
    exact same prefix (never re-discover independently, which could pick a different
    path through combat/multi-page events)."""

    def __post_init__(self) -> None:
        if self.work_kind not in WORK_KIND_VALUES:
            raise ValueError(f"unknown work_kind {self.work_kind!r}")


@dataclass(frozen=True)
class Lease:
    """State-Holding Worker Lease - a reference to (worker_slot, worker_generation, pid),
    never to a GameInstance/session object itself (those never leave their OS process).
    """

    worker_slot: Any
    worker_generation: int
    pid: int
    context_id: str
    combat_session_id: "str | None"
    run_seed: Any
    step_signature: str  # digest of (boundary, choice_scope, legal action semantic key set)

    @property
    def key(self) -> str:
        return self.context_id

    def is_valid_for(self, work_item: ChoiceWorkItem, *, worker_generation: "int | None" = None) -> bool:
        if work_item.work_kind != WORK_KIND_CONTINUATION:
            return False
        if self.context_id != work_item.context_id:
            return False
        if worker_generation is not None and self.worker_generation != worker_generation:
            return False
        return True


@dataclass
class LeaseRegistry:
    _leases: dict[str, Lease] = field(default_factory=dict)

    def get(self, context_id: str) -> "Lease | None":
        return self._leases.get(context_id)

    def set(self, lease: Lease) -> None:
        self._leases[lease.key] = lease

    def invalidate(self, context_id: str) -> None:
        self._leases.pop(context_id, None)

    def invalidate_lease(self, lease: Lease) -> None:
        current = self._leases.get(lease.key)
        if current == lease:
            self._leases.pop(lease.key, None)

    def invalidate_worker_generation(self, worker_slot: Any, worker_generation: int) -> None:
        """Invalidates every Lease belonging to a specific (slot, generation) pair -
        used when a worker is respawned so the OLD generation's leases never route
        a continuation to the fresh replacement process, which holds no live state.
        """
        for ctx_id, lease in list(self._leases.items()):
            if lease.worker_slot == worker_slot and lease.worker_generation == worker_generation:
                self._leases.pop(ctx_id, None)

    def invalidate_worker_all_generations(self, worker_slot: Any) -> None:
        for ctx_id, lease in list(self._leases.items()):
            if lease.worker_slot == worker_slot:
                self._leases.pop(ctx_id, None)

    def worker_slots_holding_leases(self) -> set[Any]:
        return {lease.worker_slot for lease in self._leases.values()}


@dataclass(frozen=True)
class ChoiceReachResult:
    """What a worker reports after reaching (not yet resolving) a Choice boundary."""

    boundary: str
    choice_scope: str
    choice_kind: "str | None"
    legal_actions: list[dict]
    room_context: dict
    observation: dict
    run_state: dict
    event_rng_state: "dict | None" = None
    """`WholeRunSession.get_event_rng_state()`'s return value, captured only when
    `boundary == "event_choice"` (an Active Event is live) - the base state an Active
    Event RNG Hypothesis is derived FROM. `None` at every other boundary."""


@dataclass(frozen=True)
class ChoiceStepResult:
    """Visible-action result plus the settled frontier after RL-only auto transport."""

    # Raw primary result of the Training-visible action. Keep this for transition/info
    # semantics even when hidden auto actions are consumed afterward.
    step_result: dict
    run_state: dict
    settled_observation: dict
    settled_legal_actions: list[dict]
    settled_room_context: dict
    auto_action_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class BranchResult:
    status: str
    work_item: ChoiceWorkItem
    execution_mode: str
    worker_slot: Any
    worker_generation: "int | None"
    pid: "int | None"
    reach: "ChoiceReachResult | None" = None
    step: "ChoiceStepResult | None" = None
    established_lease: "Lease | None" = None
    discovered_action_prefix: "list[int] | None" = None
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in (BRANCH_STATUS_SUCCESS, BRANCH_STATUS_FAULT):
            raise ValueError(f"unknown BranchResult status {self.status!r}")
        if self.execution_mode not in EXECUTION_MODE_VALUES:
            raise ValueError(f"unknown execution_mode {self.execution_mode!r}")


@dataclass(frozen=True)
class WorkerExecutionRequest:
    work_item: ChoiceWorkItem
    execution_mode: str
    expected_lease: "Lease | None" = None


@dataclass(frozen=True)
class ExploreRequest:
    """Drives the Main Run Worker's OWN session forward (StartRun -> continuous room
    progression) and, along the way, records a Map Boundary Snapshot + room_id the first
    time a room of each requested RoomType becomes reachable - all internally, reusing
    this worker's single `GameInstance` sequentially (LoadState/ChooseRoom many times on
    the SAME object), never constructing a second one. This keeps room-type discovery
    entirely inside one OS process instead of the Controller ever touching a
    `GameInstance` itself.
    """

    seed: Any
    character_id: str
    ascension: int
    min_rooms: int
    max_steps: int
    target_room_types: list[str]


@dataclass(frozen=True)
class ExploreResult:
    pid: int
    progression: dict
    found_snapshots: dict  # room_type -> {"map_snapshot": str, "room_id": int}


def _map_rooms_as_legal_actions(session) -> list[dict]:
    """Map has no LegalActions surface (GetMapRooms()+ChooseRoom(roomId) instead) - this
    reshapes MapRoomOption entries into the same {action_id, action_type, label,
    is_available, parameters} shape as every other choice type's LegalAction, purely so
    downstream Semantic Key comparison can treat Map uniformly with the other 5 choice
    types instead of needing its own special case at every call site.
    """
    rooms = session.get_map_rooms()
    return [
        {
            "action_id": room["room_id"],
            "action_type": "map_room",
            "label": f"{room['point_type']}@{room['column']},{room['row']}",
            "is_available": True,
            "parameters": {"column": room["column"], "row": room["row"], "point_type": room["point_type"]},
        }
        for room in rooms
    ]


def _reach_result_from_session(session, choice_type: str) -> ChoiceReachResult:
    obs = session.get_observation()
    legal = _map_rooms_as_legal_actions(session) if choice_type == "map" else session.get_legal_actions()
    pending = (obs.get("state") or {}).get("pendingChoice") or {}
    event_rng_state = session.get_event_rng_state() if obs["boundary"] == "event_choice" else None
    return ChoiceReachResult(
        boundary=obs["boundary"],
        choice_scope=obs["choice_scope"],
        choice_kind=pending.get("choiceType"),
        legal_actions=legal,
        room_context=session.get_room_context(),
        observation=obs,
        run_state=session.get_run_state(),
        event_rng_state=event_rng_state,
    )


def _apply_event_rng_override(session, event_rng_override: dict) -> None:
    """`event_rng_override` is shaped like `get_event_rng_state()`'s full return value
    (includes "event_id"), but `set_event_rng_state()`'s `overrides` param expects only
    the 4 stream sub-dicts as top-level keys - strip "event_id" before passing through.
    """
    session.set_event_rng_state({k: v for k, v in event_rng_override.items() if k != "event_id"})


def _bootstrap_reach(session, work_item: ChoiceWorkItem) -> "list[int] | None":
    """Loads the Map Boundary Snapshot (+ relic injection for Combat Pending), enters
    the room, and either replays the given Action Prefix or (when
    `work_item.discover_prefix`) drives forward with the policy-free default action
    picker until `target_boundary` is reached, recording what it did. Never Saves/Loads
    the Choice-waiting state itself, only the Map Boundary root. Returns the discovered
    prefix (or None when replaying a given one).
    """
    from choice_branch_runner import inject_relic
    from whole_run_session import pick_default_action, RUN_TERMINAL

    snapshot = (
        inject_relic(work_item.map_snapshot, work_item.relic_injection)
        if work_item.relic_injection
        else work_item.map_snapshot
    )
    session.load_state(snapshot)
    if work_item.choice_type == "map":
        return None

    assert work_item.room_id is not None
    session.choose_room(work_item.room_id)

    if not work_item.discover_prefix:
        plan = work_item.event_rng_plan
        for index, action_id in enumerate(work_item.action_prefix):
            if plan is not None and plan.apply_before_action_index == index:
                _apply_event_rng_override(session, plan.override_state)
            session.step(action_id)
        return None

    discovered: list[int] = []
    initial_auto = drain_trivial_reward_frontier(session)
    discovered.extend(initial_auto.auto_action_ids)
    obs = session.get_observation()
    for _ in range(400):
        if obs["boundary"] in (work_item.target_boundary, RUN_TERMINAL):
            return discovered
        actions = session.get_legal_actions()
        if not actions:
            return discovered
        action = pick_default_action(actions)
        session.step(action["action_id"])
        discovered.append(action["action_id"])
        auto = drain_trivial_reward_frontier(session)
        discovered.extend(auto.auto_action_ids)
        obs = session.get_observation()
    return discovered


def _probe_room_type_reusing_session(session, map_snapshot: str, room_id: int) -> str:
    """Same probe idea as choice_branch_runner.probe_room_type, but reuses the CALLER's
    single session (re-init via load_state) instead of constructing a new GameInstance -
    the worker-internal, one-instance-per-process-safe version.
    """
    session.load_state(map_snapshot)
    entered = session.choose_room(room_id)
    return entered["room_type"]


def _explore_for_room_types(session, request: ExploreRequest) -> ExploreResult:
    from room_progression_driver import drive_rooms, pick_room

    progression = drive_rooms(
        session,
        min_rooms=request.min_rooms,
        max_steps=request.max_steps,
        seed=request.seed,
        character_id=request.character_id,
        ascension=request.ascension,
    )

    found: dict[str, dict] = {}
    remaining = set(request.target_room_types)

    # Re-drive from scratch (same seed => same deterministic map), this time pausing at
    # every map boundary to probe each candidate's eventual RoomType before advancing -
    # the progression run above already proved forward movement works, this second pass
    # is dedicated to discovery so it can look at MORE than the single default choice.
    # Always runs (even with no target_room_types) so `__first_map__` is captured for
    # plain Map-choice testing.
    session.start_run(request.seed, request.character_id, request.ascension)
    from choice_branch_runner import drive_to_map_or_terminal

    obs = drive_to_map_or_terminal(session)
    hops = 0
    first_map_captured = False
    while obs["boundary"] == "map_select" and hops < 20 and (remaining or not first_map_captured):
        hops += 1
        map_snapshot = session.save_state()
        if not first_map_captured:
            found["__first_map__"] = {"map_snapshot": map_snapshot, "room_id": None}
            first_map_captured = True
        if not remaining:
            break
        rooms = session.get_map_rooms()
        matched_room = None
        for room in rooms:
            # Probing a Treasure room auto-resolves it (choose_room's Treasure auto-claim), so
            # only pay that cost when TreasureRoom is actually one of the types being searched for.
            if room["point_type"] == "Treasure" and "TreasureRoom" not in remaining:
                continue
            room_type = _probe_room_type_reusing_session(session, map_snapshot, room["room_id"])
            if room_type in remaining and room_type not in found:
                found[room_type] = {"map_snapshot": map_snapshot, "room_id": room["room_id"]}
                remaining.discard(room_type)
            if matched_room is None:
                matched_room = room
        if not remaining:
            break
        # Advance one hop using the same snapshot (re-load once more, then commit to a
        # single candidate this time) so the next map boundary can be explored too.
        session.load_state(map_snapshot)
        advance_room = pick_room(rooms) or (matched_room or rooms[0])
        session.choose_room(advance_room["room_id"])
        obs = drive_to_map_or_terminal(session)

    return ExploreResult(pid=os.getpid(), progression=progression, found_snapshots=found)


class _WorkerRuntime:
    """Persistent per-process state - constructed ONCE per worker OS process."""

    def __init__(self, worker_slot: Any, worker_generation: int, repo_root: "str | None") -> None:
        import sys

        run_dir = str(Path(__file__).resolve().parent)
        if run_dir not in sys.path:
            sys.path.insert(0, run_dir)
        from whole_run_session import WholeRunSession

        self.worker_slot = worker_slot
        self.worker_generation = worker_generation
        self.pid = os.getpid()
        self.session = WholeRunSession(Path(repo_root) if repo_root is not None else None)
        # Does NOT enable god mode by default - a normal Whole Run Worker must not be
        # invincible (see Outputs/reports/god_mode_default_removal_20260811.md).
        self.current_context_id: "str | None" = None

    def execute(self, request: WorkerExecutionRequest) -> BranchResult:
        work_item = request.work_item
        discovered_prefix: "list[int] | None" = None
        try:
            if request.execution_mode == EXECUTION_MODE_BOOTSTRAP_STEP:
                discovered_prefix = _bootstrap_reach(self.session, work_item)
                self.current_context_id = work_item.context_id
            elif request.execution_mode == EXECUTION_MODE_HOLDER_STEP:
                if request.expected_lease is None:
                    raise RuntimeError("Holder Step request requires expected_lease")
                if self.current_context_id != work_item.context_id:
                    raise RuntimeError(
                        f"Holder Step context mismatch: worker={self.current_context_id!r} "
                        f"request={work_item.context_id!r}"
                    )
                if request.expected_lease.worker_generation != self.worker_generation:
                    raise RuntimeError("Holder Step generation mismatch")
            else:
                raise RuntimeError(f"unknown execution mode {request.execution_mode!r}")

            reach = _reach_result_from_session(self.session, work_item.choice_type)
            if reach.boundary != work_item.target_boundary:
                self.current_context_id = None
                return BranchResult(
                    status=BRANCH_STATUS_FAULT,
                    work_item=work_item,
                    execution_mode=request.execution_mode,
                    worker_slot=self.worker_slot,
                    worker_generation=self.worker_generation,
                    pid=self.pid,
                    diagnostics={
                        "fault_kind": "replay_mismatch",
                        "expected_boundary": work_item.target_boundary,
                        "actual_boundary": reach.boundary,
                    },
                )

            plan = work_item.event_rng_plan
            if plan is not None and plan.apply_before_action_index == len(work_item.action_prefix):
                # Active Event RNG Hypothesis application (RL担当指示：Active Event RNG
                # Hypothesis実装 §4 step 4) - the "apply right before resolve_action_id"
                # case (index == len(action_prefix)); the "apply mid-prefix-replay" case
                # is handled inside `_bootstrap_reach` instead, and must NOT be reapplied
                # here (see `EventRngReplayPlan.apply_before_action_index`'s docstring).
                # Applied AFTER reach/boundary confirmation, BEFORE the given Action is
                # resolved/stepped below. A mismatched EventId or absent event raises
                # inside set_event_rng_state()/SetEventRngState(), which the outer except
                # below normalizes into a fault result rather than crashing the worker.
                if reach.boundary != "event_choice":
                    raise RuntimeError(
                        f"event_rng_plan given but boundary is {reach.boundary!r}, not 'event_choice'"
                    )
                _apply_event_rng_override(self.session, plan.override_state)

            if work_item.resolve_action_id is None:
                lease = Lease(
                    worker_slot=self.worker_slot,
                    worker_generation=self.worker_generation,
                    pid=self.pid,
                    context_id=work_item.context_id,
                    combat_session_id=reach.observation.get("combat_session_id"),
                    run_seed=reach.observation.get("seed"),
                    step_signature=_json_digest(
                        {
                            "boundary": reach.boundary,
                            "choice_scope": reach.choice_scope,
                            "legal_keys": sorted(
                                legal_action_semantic_key_text(action) for action in reach.legal_actions
                            ),
                        }
                    ),
                )
                return BranchResult(
                    status=BRANCH_STATUS_SUCCESS,
                    work_item=work_item,
                    execution_mode=request.execution_mode,
                    worker_slot=self.worker_slot,
                    worker_generation=self.worker_generation,
                    pid=self.pid,
                    reach=reach,
                    established_lease=lease,
                    discovered_action_prefix=discovered_prefix,
                )

            if work_item.choice_type == "map":
                # Map's visible action is ChooseRoom(roomId). Preserve its raw result, then
                # treat the entered room as a NEW frontier where trivial reward transport
                # is allowed. The resulting hidden suffix belongs to the new room prefix.
                entered = self.session.choose_room(work_item.resolve_action_id)
                step_result = {
                    "action_id": work_item.resolve_action_id,
                    "observation": self.session.get_observation(),
                    "room_context": self.session.get_room_context(),
                    "transition": None,
                    "info": entered.get("info", {}),
                    "room_enter_result": entered,
                }
            else:
                step_result = self.session.step(work_item.resolve_action_id)

            auto = drain_trivial_reward_frontier(self.session)
            settled_observation = self.session.get_observation()
            settled_legal_actions = (
                _map_rooms_as_legal_actions(self.session)
                if settled_observation["boundary"] == "map_select"
                else self.session.get_legal_actions()
            )
            settled_room_context = self.session.get_room_context()

            self.current_context_id = None
            return BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=request.execution_mode,
                worker_slot=self.worker_slot,
                worker_generation=self.worker_generation,
                pid=self.pid,
                reach=reach,
                step=ChoiceStepResult(
                    step_result=step_result,
                    run_state=self.session.get_run_state(),
                    settled_observation=settled_observation,
                    settled_legal_actions=settled_legal_actions,
                    settled_room_context=settled_room_context,
                    auto_action_ids=auto.auto_action_ids,
                ),
            )
        except BaseException as exc:  # noqa: BLE001 - worker must normalize faults, never crash silently.
            self.current_context_id = None
            return BranchResult(
                status=BRANCH_STATUS_FAULT,
                work_item=work_item,
                execution_mode=request.execution_mode,
                worker_slot=self.worker_slot,
                worker_generation=self.worker_generation,
                pid=self.pid,
                diagnostics={
                    "fault_kind": "worker_exception",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                    "superseded": "superseded" in str(exc),
                },
            )


def _worker_main(worker_slot: Any, worker_generation: int, repo_root: "str | None", in_queue, out_queue) -> None:
    try:
        runtime = _WorkerRuntime(worker_slot, worker_generation, repo_root)
    except BaseException as exc:  # noqa: BLE001
        out_queue.put(("__startup_fault__", worker_slot, worker_generation, type(exc).__name__, str(exc)))
        return
    out_queue.put(("__ready__", worker_slot, worker_generation, os.getpid()))
    while True:
        message = in_queue.get()
        if message is None:
            return
        request_id, kind, payload = message
        if kind == "explore":
            try:
                result = _explore_for_room_types(runtime.session, payload)
            except BaseException as exc:  # noqa: BLE001
                result = ExploreResult(pid=os.getpid(), progression={}, found_snapshots={})
                out_queue.put((request_id, "explore_fault", (type(exc).__name__, str(exc))))
                continue
            out_queue.put((request_id, "explore_ok", result))
            continue

        request = payload
        try:
            result = runtime.execute(request)
        except BaseException as exc:  # noqa: BLE001 - defensive; execute() normally catches.
            result = BranchResult(
                status=BRANCH_STATUS_FAULT,
                work_item=request.work_item,
                execution_mode=request.execution_mode,
                worker_slot=worker_slot,
                worker_generation=worker_generation,
                pid=os.getpid(),
                diagnostics={"fault_kind": "worker_loop_exception", "exception_type": type(exc).__name__, "message": str(exc)},
            )
        out_queue.put((request_id, "choice", result))


class WorkerDiedError(RuntimeError):
    """A worker's OS process is no longer alive - used internally by
    `WholeRunWorkerPool.is_worker_alive`/respawn bookkeeping. Not raised across an
    `execute`/`dispatch_choice_work_items` call boundary - a dead/hung worker surfaces to
    callers as a normal `BRANCH_STATUS_FAULT` result (`fault_kind="task_timeout"` or
    `"worker_process_crash"`), matching Combat's `search/branch_worker_pool.py` contract,
    never as an uncaught exception.
    """


@dataclass
class _WorkerHandle:
    worker_slot: Any
    worker_generation: int
    process: Any
    in_queue: Any
    pid: "int | None" = None


def _fault_result(
    work_item: ChoiceWorkItem,
    execution_mode: str,
    slot: Any,
    worker_generation: "int | None",
    exc: BaseException,
    *,
    fault_kind: str,
) -> BranchResult:
    return BranchResult(
        status=BRANCH_STATUS_FAULT,
        work_item=work_item,
        execution_mode=execution_mode,
        worker_slot=slot,
        worker_generation=worker_generation,
        pid=None,
        diagnostics={
            "fault_kind": fault_kind,
            "exception_type": type(exc).__name__,
            "message": str(exc),
        },
    )


class WholeRunWorkerPool:
    """Persistent, OS-process-separated Worker Pool: 1 Main Run Worker + N Branch Workers.

    Every worker slot maps to exactly one OS process at a time, holding exactly one live
    `GameInstance`. On fault or unexpected death, the slot's process is terminated and a
    FRESH process is spawned with an incremented `worker_generation`; every Lease that
    belonged to the old generation is invalidated so no future request is ever routed to
    a dead worker's stale state.
    """

    def __init__(
        self,
        *,
        branch_worker_count: int = 2,
        repo_root: "Path | str | None" = None,
        request_timeout_s: float = 120.0,
        include_main_worker: bool = True,
    ) -> None:
        if branch_worker_count <= 0:
            raise ValueError("branch_worker_count must be positive")
        self.branch_worker_count = branch_worker_count
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else None
        self.request_timeout_s = request_timeout_s
        self._ctx = multiprocessing.get_context("spawn")
        self._result_queue = self._ctx.Queue()
        self._workers: dict[Any, _WorkerHandle] = {}
        self._next_bootstrap_index = 0
        self._next_request_id = 0
        self._closed = False

        slots: list[Any] = ([MAIN_WORKER_SLOT] if include_main_worker else []) + list(range(branch_worker_count))
        for slot in slots:
            self._spawn_worker(slot, generation=1)
        self._await_ready(slots)

    def _spawn_worker(self, slot: Any, *, generation: int) -> None:
        in_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_main,
            args=(slot, generation, str(self.repo_root) if self.repo_root is not None else None, in_queue, self._result_queue),
            daemon=True,
        )
        process.start()
        self._workers[slot] = _WorkerHandle(slot, generation, process, in_queue)

    def _await_ready(self, slots: list[Any]) -> None:
        pending = set(slots)
        while pending:
            message = self._result_queue.get(timeout=self.request_timeout_s)
            if message[0] == "__startup_fault__":
                _, slot, generation, exc_type, msg = message
                raise RuntimeError(f"worker {slot!r} gen {generation} failed to start: {exc_type}: {msg}")
            _, slot, generation, pid = message
            self._workers[slot].pid = pid
            pending.discard(slot)

    @property
    def worker_generations(self) -> dict[Any, int]:
        return {slot: handle.worker_generation for slot, handle in self._workers.items()}

    @property
    def worker_pids(self) -> dict[Any, "int | None"]:
        return {slot: handle.pid for slot, handle in self._workers.items()}

    @property
    def worker_slots(self) -> list[Any]:
        return list(self._workers)

    @property
    def branch_worker_slots(self) -> list[Any]:
        return [slot for slot in self._workers if slot != MAIN_WORKER_SLOT]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in self._workers.values():
            try:
                handle.in_queue.put(None)
            except Exception:  # noqa: BLE001
                pass
        for handle in self._workers.values():
            handle.process.join(timeout=10)
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=5)

    def __enter__(self) -> "WholeRunWorkerPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def is_worker_alive(self, slot: Any) -> bool:
        handle = self._workers.get(slot)
        return handle is not None and handle.process.is_alive()

    def worker_pid(self, slot: Any) -> "int | None":
        handle = self._workers.get(slot)
        return handle.pid if handle is not None else None

    def respawn_worker(self, slot: Any, lease_registry: "LeaseRegistry | None" = None) -> None:
        """Terminates the process at `slot` (if alive) and starts a fresh one with an
        incremented generation, invalidating every Lease the old generation held.
        """
        old_handle = self._workers.get(slot)
        old_generation = old_handle.worker_generation if old_handle is not None else 0
        if old_handle is not None and old_handle.process.is_alive():
            old_handle.process.terminate()
            old_handle.process.join(timeout=5)
        if old_handle is not None:
            # The old in_queue is now permanently abandoned (its reader process is dead) -
            # close it and cancel its background feeder thread's join so interpreter exit
            # never blocks trying to flush a queue nobody will ever read again.
            old_handle.in_queue.close()
            old_handle.in_queue.cancel_join_thread()
        self._spawn_worker(slot, generation=old_generation + 1)
        self._await_ready([slot])
        if lease_registry is not None:
            lease_registry.invalidate_worker_generation(slot, old_generation)

    def _submit(self, slot: Any, request: WorkerExecutionRequest) -> int:
        if self._closed:
            raise RuntimeError("WholeRunWorkerPool is closed")
        handle = self._workers[slot]
        self._next_request_id += 1
        request_id = self._next_request_id
        handle.in_queue.put((request_id, "choice", request))
        return request_id

    def execute(
        self, slot: Any, request: WorkerExecutionRequest, *, lease_registry: "LeaseRegistry | None" = None
    ) -> BranchResult:
        stale_generation = self._workers[slot].worker_generation
        request_id = self._submit(slot, request)
        poll_s = min(1.0, self.request_timeout_s)
        waited = 0.0
        while waited < self.request_timeout_s:
            try:
                received_id, _kind, result = self._result_queue.get(timeout=poll_s)
            except queue.Empty:
                waited += poll_s
                if not self.is_worker_alive(slot):
                    self.respawn_worker(slot, lease_registry=lease_registry)
                    return _fault_result(
                        request.work_item,
                        request.execution_mode,
                        slot,
                        stale_generation,
                        WorkerDiedError(f"worker slot {slot!r} is no longer alive"),
                        fault_kind="worker_process_crash",
                    )
                continue
            if received_id == request_id:
                return result
            # Stray/late result from an old (respawned-away or previously timed-out)
            # request - discard rather than raise, matching Combat's contract.
            continue
        self.respawn_worker(slot, lease_registry=lease_registry)
        return _fault_result(
            request.work_item,
            request.execution_mode,
            slot,
            stale_generation,
            TimeoutError(f"timed out waiting for worker request {request_id}"),
            fault_kind="task_timeout",
        )

    def dispatch_choice_work_items(
        self, work_items: list[ChoiceWorkItem], lease_registry: LeaseRegistry, *, holder_slots: "dict[str, Any] | None" = None
    ) -> list[BranchResult]:
        return dispatch_choice_work_items(work_items, lease_registry, worker_pool=self, holder_slots=holder_slots)

    def explore(self, request: ExploreRequest, *, slot: Any = MAIN_WORKER_SLOT) -> ExploreResult:
        """Dispatches an ExploreRequest to the Main Run Worker (or another slot) and
        waits for its result - the only sanctioned way for the Controller to obtain a
        Map Boundary Snapshot reaching a given RoomType, since the Controller itself
        never touches a GameInstance.
        """
        if self._closed:
            raise RuntimeError("WholeRunWorkerPool is closed")
        handle = self._workers[slot]
        self._next_request_id += 1
        request_id = self._next_request_id
        handle.in_queue.put((request_id, "explore", request))
        while True:
            received_id, kind, payload = self._result_queue.get(timeout=self.request_timeout_s)
            if received_id == request_id:
                break
            # Stray/late result targeting a stale request_id - discard and keep waiting
            # for the one this call actually issued.
            continue
        if kind == "explore_fault":
            exc_type, msg = payload
            raise RuntimeError(f"explore() failed on worker {slot!r}: {exc_type}: {msg}")
        return payload


def _choose_bootstrap_slot(branch_slots: list[Any], lease_registry: LeaseRegistry, next_index: int) -> tuple[Any, int]:
    leased = lease_registry.worker_slots_holding_leases()
    unleased = [slot for slot in branch_slots if slot not in leased]
    candidates = unleased or branch_slots
    chosen = candidates[next_index % len(candidates)]
    return chosen, next_index + 1


def _route_choice_work_item(
    work_item: ChoiceWorkItem,
    lease_registry: LeaseRegistry,
    *,
    branch_slots: list[Any],
    worker_generations: dict[Any, int],
    next_bootstrap_index: int,
    holder_slot: "Any | None" = None,
) -> tuple[WorkerExecutionRequest, Any, int]:
    if work_item.work_kind == WORK_KIND_CONTINUATION:
        slot = holder_slot
        lease = lease_registry.get(work_item.context_id)
        if slot is None and lease is not None:
            slot = lease.worker_slot
        if slot is not None and lease is not None:
            generation = worker_generations.get(slot)
            if generation is not None and lease.is_valid_for(work_item, worker_generation=generation):
                return (
                    WorkerExecutionRequest(work_item, EXECUTION_MODE_HOLDER_STEP, expected_lease=lease),
                    slot,
                    next_bootstrap_index,
                )
        if lease is not None:
            lease_registry.invalidate_lease(lease)
        raise RuntimeError(f"no valid Lease/Holder for continuation context_id={work_item.context_id!r}")

    slot, next_bootstrap_index = _choose_bootstrap_slot(branch_slots, lease_registry, next_bootstrap_index)
    if slot in lease_registry.worker_slots_holding_leases():
        lease_registry.invalidate_worker_all_generations(slot)
    return (WorkerExecutionRequest(work_item, EXECUTION_MODE_BOOTSTRAP_STEP), slot, next_bootstrap_index)


ExecuteRequest = Callable[[Any, WorkerExecutionRequest], BranchResult]


def dispatch_choice_work_items(
    work_items: list[ChoiceWorkItem],
    lease_registry: LeaseRegistry,
    *,
    worker_pool: "WholeRunWorkerPool | None" = None,
    holder_slots: "dict[str, Any] | None" = None,
) -> list[BranchResult]:
    """Dispatches a batch and updates Lease lifecycle from the results.

    `holder_slots` (by work_id) lets a caller pin a WORK_KIND_CONTINUATION item to a
    specific worker slot it already knows is the Holder (used right after that same
    slot's own establishing bootstrap, before any Lease round-trip is strictly needed).
    """
    if worker_pool is None:
        raise ValueError("dispatch_choice_work_items() requires worker_pool")
    branch_slots = worker_pool.branch_worker_slots
    worker_generations = worker_pool.worker_generations
    next_bootstrap_index = worker_pool._next_bootstrap_index  # noqa: SLF001

    pending: dict[int, ChoiceWorkItem] = {}
    pending_slots: dict[int, Any] = {}
    pending_execution_modes: dict[int, str] = {}
    for work_item in work_items:
        holder_slot = (holder_slots or {}).get(work_item.work_id)
        request, slot, next_bootstrap_index = _route_choice_work_item(
            work_item,
            lease_registry,
            branch_slots=branch_slots,
            worker_generations=worker_generations,
            next_bootstrap_index=next_bootstrap_index,
            holder_slot=holder_slot,
        )
        request_id = worker_pool._submit(slot, request)  # noqa: SLF001
        pending[request_id] = work_item
        pending_slots[request_id] = slot
        pending_execution_modes[request_id] = request.execution_mode

    results_by_work_id: dict[str, BranchResult] = {}
    remaining_request_ids: set[int] = set(pending)
    while remaining_request_ids:
        try:
            request_id, _kind, result = worker_pool._result_queue.get(  # noqa: SLF001
                timeout=worker_pool.request_timeout_s
            )
        except queue.Empty:
            # Every request still outstanding at this point is hung - respawn each
            # distinct slot exactly once and synthesize a fault result for each of its
            # still-pending requests, rather than waiting indefinitely.
            hung_slots = {pending_slots[rid] for rid in remaining_request_ids}
            for hung_slot in hung_slots:
                stale_generation = worker_pool.worker_generations.get(hung_slot)
                worker_pool.respawn_worker(hung_slot, lease_registry=lease_registry)
                for rid in list(remaining_request_ids):
                    if pending_slots[rid] != hung_slot:
                        continue
                    work_item = pending[rid]
                    results_by_work_id[work_item.work_id] = _fault_result(
                        work_item,
                        pending_execution_modes[rid],
                        hung_slot,
                        stale_generation,
                        TimeoutError(f"timed out waiting for worker request {rid}"),
                        fault_kind="task_timeout",
                    )
                    remaining_request_ids.discard(rid)
            continue
        if request_id not in remaining_request_ids:
            # Stray/late result: either a genuine duplicate, or a response from a worker
            # generation we already gave up on and respawned away. Discard.
            continue
        work_item = pending[request_id]
        results_by_work_id[work_item.work_id] = result
        remaining_request_ids.discard(request_id)

    results: list[BranchResult] = []
    for work_item in work_items:
        result = results_by_work_id[work_item.work_id]
        lease_registry.invalidate(work_item.context_id)
        if result.status == BRANCH_STATUS_SUCCESS and result.established_lease is not None:
            lease_registry.set(result.established_lease)
        if result.status == BRANCH_STATUS_FAULT and result.worker_slot is not None:
            lease_registry.invalidate_worker_all_generations(result.worker_slot)
        results.append(result)

    worker_pool._next_bootstrap_index = next_bootstrap_index  # noqa: SLF001
    return results
