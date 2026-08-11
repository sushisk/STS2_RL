"""`Instance` implementation for `instance_type="whole_run"`.

root lives directly as a plain `WholeRunSession` owned by this object - the ONLY
`GameInstance` construction in this process (same "coordinator holds root directly,
Branch Workers are separate spawned processes" pattern as `instance_combat.py`).
Branches are dispatched through the existing, already-tested `Run/worker_pool.
WholeRunWorkerPool` (Phase K/L) with `include_main_worker=False` (this Instance IS the
Main Run Worker, in-process, so the Pool's own optional Main slot would be redundant).

Deliberate scope reductions versus `instance_combat.py`, documented here and in the
final report rather than silently:

1. **`rng_id` is a real RNG Hypothesis ONLY at an Active Event boundary** (RL担当指示：
   Active Event RNG Hypothesis実装). Whole Run's Map/Encounter/Boss/Ancient/Act-generation
   content is generated upfront in one draw at Act-generation time (Emulator's `UpFront`
   RNG stream) with no safely isolable "unconsumed future" boundary and no purpose-built
   partial-override API - see `Outputs/reports/rl_whole_run_rng_hypothesis_STOP_20260804.md`.
   The ONLY Emulator API that safely exposes a partial, purpose-built RNG override is
   `GetEventRngState`/`SetEventRngState`, scoped to whatever Event is currently active.
   Consequently `emulate_action` with a positive `rng_id` (the only kind v0.5 allows -
   `rng_id=0` is reserved for root) is `rejected` with
   `fault_kind="rng_hypothesis_unsupported_at_boundary"` at every OTHER boundary (Map,
   pre-Combat, Reward, Shop, Rest, Treasure, post-Event, and anything Map/Encounter/Boss/
   Ancient-generation-related) - see `API.dto.RNG_HYPOTHESIS_CAPABILITIES` for
   the formal capability declaration. `whole_run_event_rng.py` implements the actual
   Hypothesis derivation (deterministic, process/PID-independent) and lifecycle registry.
2. **Branch dispatch is synchronous** (`WholeRunWorkerPool.dispatch_choice_work_items`
   blocks for the whole request, matching this contract's own example where
   `emulate_action`'s response already carries `status: "completed"`), so a Whole Run
   Branch is never observably `queued`/`running` from a separate `get_branch_status`
   call, and Cancel never needs to kill a worker mid-flight for a WHOLE RUN branch
   specifically (unlike Combat's, which is async - see `search/branch_manager.py`).
   `cancel_branches`/`release_branches` here are therefore pure state-transitions on
   already-resolved results, not a live-kill path. If Training's real usage needs
   whole-run Branch simulation to be interruptible mid-flight (e.g. a very long Room
   auto-play), that would need an async submit/poll layer mirroring
   `search/branch_manager.py`, built for `WholeRunWorkerPool` - out of scope for this
   pass, flagged rather than silently done differently. See `API/dto.py`'s
   "-- status --" section for the formal statement of which statuses each instance_type
   can reach - `STATUS_QUEUED`/`STATUS_RUNNING` are declared there for shared vocabulary
   but are never produced by this module.
3. Chaining `emulate_action` PAST a newly-reached `map_select` boundary is only
   supported from **root** (which can cheaply `save_state()` in-process); from a Branch,
   landing on `map_select` marks that Branch `chain_blocked` and a further
   `emulate_action` with it as `parent_branch_id` is `rejected` - capturing a
   Branch-side Map Snapshot would require an extra dedicated round-trip not built in
   this pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import run_emulator_bridge as bridge
from whole_run_session import EVENT_CHOICE, MAP_SELECT, RUN_TERMINAL, WholeRunSession
from worker_pool import (
    BranchResult,
    ChoiceWorkItem,
    EventRngReplayPlan,
    LeaseRegistry as WRLeaseRegistry,
    WORK_KIND_SUB_BRANCH,
    WholeRunWorkerPool,
    derive_context_id as wr_derive_context_id,
)

from API.dto import (
    FAULT_EMULATOR_ERROR,
    FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY,
    FAULT_TASK_TIMEOUT,
    ROOT_BRANCH_ID,
    ROOT_RNG_ID,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAULTED,
    STATUS_RELEASED,
)
from API.history_builder import HistoryBuilder
from API.identifiers import BranchIdRegistry, DecisionPointRegistry, RngHypothesisTable
from API.masking import build_masked_emulator_dto, mask_legal_actions
from API.terminal_outcome import VALID_WHOLE_RUN_TERMINAL_OUTCOMES, require_terminal_outcome
from API.validation import RequestRejected
from API.whole_run_event_rng import EventRngHypothesisRegistry


def _map_rooms_as_legal_actions(session: WholeRunSession) -> list:
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


def _choice_type_from_boundary(boundary: str) -> str:
    """The one place Observation's `"map_select"` boundary is translated into the
    internal choice_type `"map"` - every other boundary passes through unchanged. Does
    not touch the external DTO's own boundary string, only this module's internal
    routing vocabulary."""
    return "map" if boundary == MAP_SELECT else boundary


@dataclass(frozen=True)
class _View:
    legal_actions_raw: list
    boundary: str
    observation: dict
    room_context: dict
    map_snapshot: "str | None"
    room_id: "int | None"
    action_prefix: tuple
    choice_type: str
    chain_blocked: bool
    event_rng_state: "dict | None"

    def resolve_action_id(self, public_action_id: str) -> int:
        """Resolve the opaque public ActionId back to this Decision's raw action slot.

        ``mask_legal_actions`` publishes the Emulator's ActionId unchanged except for
        string conversion, so the numeric spelling of the token is not a positional
        index. Reward/Shop/Map decisions may legitimately use sparse IDs such as 3, 10,
        or arbitrary room IDs.
        """
        matches = [
            index
            for index, action in enumerate(self.legal_actions_raw)
            if "action_id" in action and str(action["action_id"]) == public_action_id
        ]
        if not matches:
            raise RequestRejected(f"action_id {public_action_id!r} is not among current legal actions")
        if len(matches) != 1:
            raise RequestRejected(f"action_id {public_action_id!r} is ambiguous among current legal actions")
        return matches[0]


def _build_child_view(parent_view: _View, chosen_action_id: int, branch_result: BranchResult) -> _View:
    """Builds the `_View` a Branch reaches after `branch_result` resolves one Action from
    `parent_view` - the single place Map-transition vs normal-Action-transition shape
    differences (`legal_actions` source, `room_id`/`action_prefix` reset-vs-append) are
    decided, done exactly once each (see `worker_pool.py`'s `_WorkerRuntime.execute`, map
    branch, for why Map's `step_result` shape differs from every other choice type's).
    """
    step = branch_result.step
    new_boundary = step.step_result["observation"]["boundary"]
    if parent_view.choice_type == "map":
        new_legal = step.step_result["room_enter_result"]["legal_actions"]
        new_room_id = chosen_action_id
        new_action_prefix: tuple = ()
    else:
        new_legal = step.step_result["legal_actions"]
        new_room_id = parent_view.room_id
        new_action_prefix = parent_view.action_prefix + (chosen_action_id,)
    return _View(
        legal_actions_raw=new_legal,
        boundary=new_boundary,
        observation=step.step_result["observation"],
        room_context=step.step_result["room_context"],
        map_snapshot=parent_view.map_snapshot,
        room_id=new_room_id,
        action_prefix=new_action_prefix,
        choice_type=_choice_type_from_boundary(new_boundary),
        chain_blocked=(new_boundary == MAP_SELECT),
        event_rng_state=None,
    )


class _BranchBookkeeping:
    __slots__ = (
        "parent_public_id",
        "branch_log",
        "history",
        "view",
        "status",
        "terminal",
        "outcome",
        "rng_id",
        "event_rng_plan",
    )

    def __init__(self, parent_public_id: str, branch_log: list, history: HistoryBuilder, rng_id: int) -> None:
        self.parent_public_id = parent_public_id
        self.branch_log = branch_log
        self.history = history
        self.rng_id = rng_id
        self.view: Optional[_View] = None
        self.status = STATUS_COMPLETED
        self.terminal = False
        # Set alongside `terminal` when the run concludes (see `emulate_action`'s
        # RUN_TERMINAL handling below) - the run's win/loss signal, captured from the
        # Observation at the moment of transition since it is not retrievable from this
        # Branch afterward (`view` becomes None once terminal).
        self.outcome: "str | None" = None
        # Set only for a Branch created (or inherited) under an Active Event RNG
        # Hypothesis - may belong to an ancestor's plan unchanged, for a deep Branch
        # that inherited rather than re-derived (see `WholeRunInstance.emulate_action`).
        self.event_rng_plan: "EventRngReplayPlan | None" = None


class WholeRunInstance:
    instance_type = "whole_run"

    def __init__(
        self,
        instance_id: str,
        instance_config: dict,
        *,
        branch_worker_count: int = 2,
        request_timeout_s: float = 60.0,
        max_branches: int = 64,
    ) -> None:
        self.instance_id = instance_id
        self._session = WholeRunSession()
        self._session.start_run(
            instance_config.get("seed", 1), instance_config.get("character_id", "IRONCLAD"), instance_config.get("ascension", 0)
        )

        self._pool = WholeRunWorkerPool(
            branch_worker_count=branch_worker_count, request_timeout_s=request_timeout_s, include_main_worker=False
        )
        self._lease_registry = WRLeaseRegistry()

        self._branch_ids = BranchIdRegistry()
        self._decision_points = DecisionPointRegistry()
        self._rng_table = RngHypothesisTable()
        self._event_rng_registry = EventRngHypothesisRegistry()
        self.max_branches = max_branches

        self._map_snapshot: Optional[str] = None
        self._room_id: Optional[int] = None
        self._action_prefix: list = []
        self._root_branch_log: list = []
        self._root_history = HistoryBuilder()
        self._bookkeeping: dict[str, _BranchBookkeeping] = {}
        self._closed = False

        self._maybe_capture_map_snapshot()
        self._decision_points.issue(ROOT_BRANCH_ID)

    # -- views ------------------------------------------------------------------------

    def _maybe_capture_map_snapshot(self) -> None:
        obs = self._session.get_observation()
        if obs["boundary"] == MAP_SELECT:
            self._map_snapshot = self._session.save_state()
            self._room_id = None
            self._action_prefix = []

    def _root_view(self) -> _View:
        obs = self._session.get_observation()
        boundary = obs["boundary"]
        legal = _map_rooms_as_legal_actions(self._session) if boundary == MAP_SELECT else self._session.get_legal_actions()
        room_context = self._session.get_room_context()
        self._root_history.observe_room_context(room_context)
        event_rng_state = self._session.get_event_rng_state() if boundary == EVENT_CHOICE else None
        return _View(
            legal_actions_raw=legal,
            boundary=boundary,
            observation=obs,
            room_context=room_context,
            map_snapshot=self._map_snapshot,
            room_id=self._room_id,
            action_prefix=tuple(self._action_prefix),
            choice_type=_choice_type_from_boundary(boundary),
            chain_blocked=False,
            event_rng_state=event_rng_state,
        )

    def _view_for(self, public_branch_id: str) -> _View:
        if public_branch_id == ROOT_BRANCH_ID:
            return self._root_view()
        book = self._bookkeeping.get(public_branch_id)
        if book is None:
            raise RequestRejected(f"unknown branch_id {public_branch_id!r}")
        if book.status in (STATUS_CANCELLED, STATUS_RELEASED, STATUS_FAULTED):
            raise RequestRejected(f"branch_id {public_branch_id!r} is {book.status} and cannot be used")
        if book.terminal or book.view is None:
            raise RequestRejected(f"branch_id {public_branch_id!r} has no current Decision to branch from")
        if book.view.chain_blocked:
            raise RequestRejected(
                f"branch_id {public_branch_id!r} reached a new map boundary; further emulate_action from a "
                "non-root Branch across a map boundary is not supported in this pass"
            )
        return book.view

    # -- response builders --------------------------------------------------------------

    def _decision_response_fields(self, public_branch_id: str, view: _View, *, branch_log: list, history: HistoryBuilder) -> dict:
        extra: dict = {
            "boundary": view.boundary,
            "legal_actions": mask_legal_actions(view.legal_actions_raw),
            "room_context": view.room_context,
            "history": history.to_public_list(),
        }
        # Root never gets the Branch-side {"run_terminal": True} shortcut (it always
        # flows through this method, live off `self._session`), so this is root's only
        # place to surface the win/loss signal `view.observation` already carries
        # (`observation_to_dict()`'s `outcome` field) - mirrors the Branch RUN_TERMINAL
        # payload in `emulate_action()` below for a consistent shape either way.
        if view.boundary == RUN_TERMINAL:
            extra["run_terminal"] = True
            extra["outcome"] = require_terminal_outcome(
                view.observation.get("outcome"),
                context="whole-run root decision",
                valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
            )
        masked = build_masked_emulator_dto(view.observation.get("state") or {}, extra=extra)
        return {
            "branch_id": public_branch_id,
            "decision_point_id": self._decision_points.current(public_branch_id),
            "branch_log": branch_log,
            "masked_emulator_dto": masked,
        }

    # -- operations ---------------------------------------------------------------------

    def start_instance_response(self) -> dict:
        view = self._root_view()
        return {
            "status": STATUS_COMPLETED,
            "instance_id": self.instance_id,
            **self._decision_response_fields(ROOT_BRANCH_ID, view, branch_log=list(self._root_branch_log), history=self._root_history),
        }

    def get_decision(self, branch_id: str) -> dict:
        if branch_id != ROOT_BRANCH_ID and not self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"unknown branch_id {branch_id!r}")
        if branch_id != ROOT_BRANCH_ID:
            book = self._bookkeeping[branch_id]
            if book.status != STATUS_COMPLETED:
                return {"status": book.status, "branch_id": branch_id}
            if book.terminal:
                return {
                    "status": STATUS_COMPLETED,
                    "branch_id": branch_id,
                    "decision_point_id": self._decision_points.current(branch_id),
                    "branch_log": book.branch_log,
                    "masked_emulator_dto": build_masked_emulator_dto(
                        {
                            "run_terminal": True,
                            "outcome": require_terminal_outcome(
                                book.outcome,
                                context=f"whole-run branch {branch_id!r}",
                                valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
                            ),
                        }
                    ),
                }
            return {
                "status": STATUS_COMPLETED,
                **self._decision_response_fields(branch_id, book.view, branch_log=book.branch_log, history=book.history),
            }
        view = self._root_view()
        return {"status": STATUS_COMPLETED, **self._decision_response_fields(ROOT_BRANCH_ID, view, branch_log=list(self._root_branch_log), history=self._root_history)}

    def commit_action(self, decision_point_id: str, action_id: str) -> dict:
        self._decision_points.validate(ROOT_BRANCH_ID, decision_point_id)
        view = self._root_view()
        if view.boundary == RUN_TERMINAL:
            raise RequestRejected("root has already reached run_terminal; no further commit_action possible")
        index = view.resolve_action_id(action_id)
        chosen = view.legal_actions_raw[index]
        try:
            if view.boundary == MAP_SELECT:
                self._session.choose_room(chosen["action_id"])
                self._room_id = chosen["action_id"]
                self._action_prefix = []
            else:
                self._session.step(chosen["action_id"])
                self._action_prefix.append(chosen["action_id"])
        except Exception as exc:  # noqa: BLE001
            return {"status": STATUS_FAULTED, "error": str(exc), "fault_kind": FAULT_EMULATOR_ERROR}

        depth = len(self._root_branch_log)
        self._root_branch_log.append({"depth": depth, "decision_point_id": decision_point_id, "action_id": action_id, "rng_id": ROOT_RNG_ID})

        self._cancel_and_release_all_branches()
        self._maybe_capture_map_snapshot()
        self._decision_points.issue(ROOT_BRANCH_ID)
        view = self._root_view()
        return {"status": STATUS_COMPLETED, **self._decision_response_fields(ROOT_BRANCH_ID, view, branch_log=list(self._root_branch_log), history=self._root_history)}

    def emulate_action(
        self,
        *,
        parent_branch_id: str,
        branch_id: str,
        rng_id: int,
        decision_point_id: str,
        action_id: str,
        simulation_options: Optional[dict],
    ) -> dict:
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(f"stop_condition {stop_condition!r} is not supported for whole_run instances")
        if self.active_branch_count() >= self.max_branches:
            raise RequestRejected(f"active Branch count would exceed max_branches={self.max_branches}")

        parent_view = self._view_for(parent_branch_id)
        if parent_view.boundary == RUN_TERMINAL:
            raise RequestRejected(f"parent_branch_id {parent_branch_id!r} has reached run_terminal; cannot branch further")
        if parent_view.map_snapshot is None:
            # No map_select boundary has been reached yet on this progression line (can
            # happen for the very first room of a run, auto-entered before any explicit
            # Map Decision) - the Branch Worker bootstrap path requires a Map Snapshot to
            # load from (see module docstring), so there is nothing to branch from yet.
            raise RequestRejected(
                f"parent_branch_id {parent_branch_id!r} has not reached a map_select boundary yet; "
                "emulate_action is unavailable until the first Map Decision is reached"
            )
        if parent_view.boundary != EVENT_CHOICE:
            # RL担当指示：Active Event RNG Hypothesis実装 §1/§5 - a positive rng_id is
            # only meaningful (and only accepted) at an Active Event boundary; every
            # other boundary is rejected outright, never silently accepted as
            # bookkeeping-only.
            raise RequestRejected(
                "Active Event RNG hypothesis is not available at this boundary.",
                fault_kind=FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY,
            )
        if parent_branch_id != ROOT_BRANCH_ID:
            parent_rng_id = self._bookkeeping[parent_branch_id].rng_id
            if rng_id != parent_rng_id:
                raise RequestRejected(
                    f"non-root parent_branch_id {parent_branch_id!r} requires rng_id={parent_rng_id!r} "
                    f"(its own lineage rng_id), got {rng_id!r}"
                )
        self._decision_points.validate(parent_branch_id, decision_point_id)
        self._branch_ids.register(branch_id)

        index = parent_view.resolve_action_id(action_id)
        chosen = parent_view.legal_actions_raw[index]

        # Hypothesis Key per contract §3: (parent_branch_id, decision_point_id, rng_id).
        # root parents ESTABLISH (or, for a repeat call with the same Key, re-obtain via
        # memoization) a fresh Hypothesis derived from the CURRENT Active Event's own
        # RNG state. Non-root parents INHERIT their own already-established plan
        # unchanged (§4: "新しいHypothesisを再生成しない...親Branchが到達したHidden
        # Stateから継続する") - the boundary check above guarantees the parent Branch
        # itself was only ever created under an Active Event Hypothesis, so
        # `parent_book.event_rng_plan` is always set here.
        if parent_branch_id == ROOT_BRANCH_ID:
            event_rng_key = (parent_branch_id, decision_point_id, rng_id)
            assert parent_view.event_rng_state is not None
            override_state = self._event_rng_registry.get_or_create(event_rng_key, parent_view.event_rng_state, rng_id)
            plan = EventRngReplayPlan(
                hypothesis_key=event_rng_key,
                override_state=override_state,
                apply_before_action_index=len(parent_view.action_prefix),
            )
        else:
            parent_book = self._bookkeeping[parent_branch_id]
            assert parent_book.event_rng_plan is not None, "non-root parent must have its own established Hypothesis plan"
            plan = parent_book.event_rng_plan
        self._event_rng_registry.register_branch(plan.hypothesis_key, branch_id)

        context_id = wr_derive_context_id(
            map_snapshot=parent_view.map_snapshot,
            room_id=parent_view.room_id,
            action_prefix=parent_view.action_prefix,
            choice_type=parent_view.choice_type,
            relic_injection=None,
        )
        work_item = ChoiceWorkItem(
            work_id=f"{branch_id}-{rng_id}",
            context_id=context_id,
            choice_type=parent_view.choice_type,
            map_snapshot=parent_view.map_snapshot,
            room_id=parent_view.room_id,
            action_prefix=list(parent_view.action_prefix),
            relic_injection=None,
            target_boundary=parent_view.boundary,
            work_kind=WORK_KIND_SUB_BRANCH,
            resolve_action_id=chosen["action_id"],
            event_rng_plan=plan,
        )

        parent_history = self._root_history if parent_branch_id == ROOT_BRANCH_ID else self._bookkeeping[parent_branch_id].history
        parent_log = list(self._root_branch_log) if parent_branch_id == ROOT_BRANCH_ID else list(self._bookkeeping[parent_branch_id].branch_log)
        depth = len(parent_log)
        branch_log = parent_log + [{"depth": depth, "decision_point_id": decision_point_id, "action_id": action_id, "rng_id": rng_id}]
        book = _BranchBookkeeping(parent_branch_id, branch_log, parent_history.fork(), rng_id)
        book.event_rng_plan = plan
        self._bookkeeping[branch_id] = book

        try:
            results = self._pool.dispatch_choice_work_items([work_item], self._lease_registry)
        except TimeoutError as exc:
            book.status = STATUS_FAULTED
            self._event_rng_registry.release_branch(plan.hypothesis_key, branch_id)
            return {
                "status": STATUS_FAULTED, "branch_id": branch_id, "parent_branch_id": parent_branch_id, "rng_id": rng_id,
                "error": str(exc), "fault_kind": FAULT_TASK_TIMEOUT,
            }
        result = results[0]
        if result.status != "success":
            book.status = STATUS_FAULTED
            self._event_rng_registry.release_branch(plan.hypothesis_key, branch_id)
            diagnostics = result.diagnostics or {}
            return {
                "status": STATUS_FAULTED, "branch_id": branch_id, "parent_branch_id": parent_branch_id, "rng_id": rng_id,
                "error": diagnostics.get("message", "branch execution faulted"), "fault_kind": diagnostics.get("fault_kind", FAULT_EMULATOR_ERROR),
            }

        step = result.step
        new_observation = step.step_result["observation"]
        new_boundary = new_observation["boundary"]
        new_room_context = step.step_result["room_context"]
        book.history.observe_room_context(new_room_context)
        if new_boundary == RUN_TERMINAL:
            # observation_to_dict() (run_emulator_bridge.py) already carries the
            # authoritative win/loss signal (obs.Outcome) - captured here since `view`
            # becomes None below and this Branch has no other way to recover it later.
            try:
                terminal_outcome = require_terminal_outcome(
                    new_observation.get("outcome"),
                    context=f"whole-run branch {branch_id!r}",
                    valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
                )
            except RuntimeError:
                # The worker completed, but its terminal payload violated the producer
                # invariant. Keep the public Branch tombstone, but never leave it looking
                # completed/non-terminal (which would consume capacity and later try to
                # build a response from book.view=None). Release this Branch's RNG-plan
                # reference just like other fault paths before surfacing the invariant.
                book.status = STATUS_FAULTED
                self._release_event_rng_reference(branch_id, book)
                raise
            book.outcome = terminal_outcome
            book.terminal = True
            # NOTE: deliberately does NOT release_branch(event_rng_key, branch_id) here.
            # The same Hypothesis Key may still be referenced by SIBLING Branches from
            # the same parent Decision (contract fairness: "同一親Decision＋同一rng_id
            # の複数Actionは同一Hypothesisを共有する") - releasing on this one Branch's
            # own outcome would drop the SHARED registry entry out from under them.
            # Further use of THIS branch_id as a parent is already correctly rejected by
            # the boundary check above regardless (a terminal Branch has no Decision to
            # branch from at all) - actual release only happens via explicit Cancel/
            # Release, root Commit, or instance Close (contract §6).
            self._decision_points.issue(branch_id)
            return {
                "status": STATUS_COMPLETED, "branch_id": branch_id, "parent_branch_id": parent_branch_id, "rng_id": rng_id,
                "decision_point_id": self._decision_points.current(branch_id), "branch_log": branch_log,
                "masked_emulator_dto": build_masked_emulator_dto({"run_terminal": True, "outcome": book.outcome}),
            }

        new_view = _build_child_view(parent_view, chosen["action_id"], result)
        # NOTE: deliberately does NOT release_branch()/clear book.event_rng_plan here
        # even when new_boundary != EVENT_CHOICE (event concluded, moved to Map/Reward/
        # Shop/Rest/whatever) - see the RUN_TERMINAL branch's comment above for why
        # (shared-Hypothesis fairness with sibling Branches). This Branch's OWN resulting
        # boundary being non-event_choice already, by itself, correctly blocks any
        # further emulate_action FROM it (the general boundary check at the top of this
        # method applies uniformly to root and non-root parents alike) - the contract's
        # "Event終了後のMap、Encounter等へ同じHypothesisの意味を引き継がないでください"
        # is satisfied structurally, without needing an extra release here.
        book.view = new_view
        self._decision_points.issue(branch_id)
        return {
            "status": STATUS_COMPLETED,
            **self._decision_response_fields(branch_id, new_view, branch_log=branch_log, history=book.history),
            "parent_branch_id": parent_branch_id,
            "rng_id": rng_id,
        }

    def cancel_branches(self, branch_ids: list) -> dict:
        for bid in branch_ids:
            self._reject_if_root(bid)
            book = self._bookkeeping.get(bid)
            if book is None:
                raise RequestRejected(f"unknown branch_id {bid!r}")
            if book.status not in (STATUS_CANCELLED, STATUS_RELEASED):
                book.status = STATUS_CANCELLED
                book.view = None
                self._release_event_rng_reference(bid, book)
        return {"status": STATUS_COMPLETED, "branch_statuses": {bid: STATUS_CANCELLED for bid in branch_ids}}

    def release_branches(self, branch_ids: list) -> dict:
        for bid in branch_ids:
            self._reject_if_root(bid)
            book = self._bookkeeping.get(bid)
            if book is None:
                raise RequestRejected(f"unknown branch_id {bid!r}")
            if book.status != STATUS_RELEASED:
                book.status = STATUS_RELEASED
                book.view = None
                self._release_event_rng_reference(bid, book)
        return {"status": STATUS_COMPLETED, "branch_statuses": {bid: STATUS_RELEASED for bid in branch_ids}}

    def get_branch_status(self, branch_ids: list) -> dict:
        statuses = {}
        for bid in branch_ids:
            self._reject_if_root(bid)
            book = self._bookkeeping.get(bid)
            if book is None:
                raise RequestRejected(f"unknown branch_id {bid!r}")
            statuses[bid] = book.status
        return {"status": STATUS_COMPLETED, "branch_statuses": statuses}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Contract §6: instance Close -> every live Hypothesis reference is released.
        self._event_rng_registry.release_all()
        self._pool.close()

    # -- helpers ----------------------------------------------------------------------

    def active_branch_count(self) -> int:
        return sum(1 for book in self._bookkeeping.values() if book.status == STATUS_COMPLETED and not book.terminal)

    def _reject_if_root(self, branch_id: str) -> None:
        if branch_id == ROOT_BRANCH_ID:
            raise RequestRejected("root cannot be Cancelled/Released/status-queried as a Branch")

    def _release_event_rng_reference(self, branch_id: str, book: "_BranchBookkeeping") -> None:
        if book.event_rng_plan is not None:
            self._event_rng_registry.release_branch(book.event_rng_plan.hypothesis_key, branch_id)
            book.event_rng_plan = None

    def _cancel_and_release_all_branches(self) -> None:
        # Keep each bookkeeping ENTRY (status flipped to released) rather than deleting
        # it - branch_id must stay permanently non-reusable and `get_decision`/
        # `get_branch_status` must keep answering "released" for it, never "unknown".
        for bid, book in self._bookkeeping.items():
            if book.status not in (STATUS_CANCELLED, STATUS_RELEASED):
                book.status = STATUS_RELEASED
            book.view = None
            self._decision_points.clear(bid)
        # Contract §6: root Commit -> every Hypothesis derived from the just-committed
        # (now stale) root Decision is released, regardless of which Branch referenced
        # it - a blanket release_all() rather than per-Branch bookkeeping is correct
        # here because a root commit_action always invalidates the ENTIRE current
        # Decision's derived tree at once (see `_cancel_and_release_all_branches`'s own
        # caller, `commit_action`).
        self._event_rng_registry.release_all()