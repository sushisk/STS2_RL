"""`Instance` implementation for `instance_type="whole_run"`.

The root run is owned in-process while speculative Branches are reconstructed by
``WholeRunWorkerPool`` from the latest map snapshot. Active Event branching is the one
Whole Run path where positive ``rng_id`` values denote real RNG hypotheses; other
boundaries either use deterministic replay in the Beam subclass or reject branching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import run_emulator_bridge as bridge
from reward_auto_progress import drain_trivial_reward_frontier
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
            "parameters": {
                "column": room["column"],
                "row": room["row"],
                "point_type": room["point_type"],
            },
        }
        for room in rooms
    ]


def _choice_type_from_boundary(boundary: str) -> str:
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
        matches = [
            index
            for index, action in enumerate(self.legal_actions_raw)
            if "action_id" in action and str(action["action_id"]) == public_action_id
        ]
        if not matches:
            raise RequestRejected(
                f"action_id {public_action_id!r} is not among current legal actions"
            )
        if len(matches) != 1:
            raise RequestRejected(
                f"action_id {public_action_id!r} is ambiguous among current legal actions"
            )
        return matches[0]


def _build_child_view(
    parent_view: _View,
    chosen_action_id: int,
    branch_result: BranchResult,
) -> _View:
    step = branch_result.step
    if step is None:
        raise RuntimeError("successful branch result is missing ChoiceStepResult")

    new_observation = step.settled_observation
    new_boundary = new_observation["boundary"]
    new_legal = step.settled_legal_actions
    if parent_view.choice_type == "map":
        new_room_id = chosen_action_id
        new_action_prefix = tuple(step.auto_action_ids)
    else:
        new_room_id = parent_view.room_id
        new_action_prefix = (
            parent_view.action_prefix
            + (chosen_action_id,)
            + tuple(step.auto_action_ids)
        )

    return _View(
        legal_actions_raw=new_legal,
        boundary=new_boundary,
        observation=new_observation,
        room_context=step.settled_room_context,
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

    def __init__(
        self,
        parent_public_id: str,
        branch_log: list,
        history: HistoryBuilder,
        rng_id: int,
    ) -> None:
        self.parent_public_id = parent_public_id
        self.branch_log = branch_log
        self.history = history
        self.rng_id = rng_id
        self.view: Optional[_View] = None
        self.status = STATUS_COMPLETED
        self.terminal = False
        self.outcome: "str | None" = None
        self.event_rng_plan: "EventRngReplayPlan | None" = None


@dataclass(frozen=True)
class _BranchSpec:
    """Validated Branch request detached from coordinator mutation."""

    parent_view: Any
    parent_branch_id: str
    branch_id: str
    rng_id: int
    decision_point_id: str
    action_id: str
    chosen_action_id: Any


def _faulted_branch_result(
    spec: _BranchSpec,
    *,
    error: str,
    fault_kind: str,
) -> dict:
    return {
        "status": STATUS_FAULTED,
        "branch_id": spec.branch_id,
        "parent_branch_id": spec.parent_branch_id,
        "rng_id": spec.rng_id,
        "error": error or "branch execution faulted",
        "fault_kind": fault_kind or FAULT_EMULATOR_ERROR,
    }


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
        self._god_mode = bool(instance_config.get("god_mode", False))
        self._session = WholeRunSession()
        if self._god_mode:
            # Explicit, per-instance opt-in only - see Outputs/reports/
            # god_mode_data_collection_proposal_20260812.md. GodModeEnabled round-trips
            # through save_state()/load_state(), so every Branch Worker spawned from a
            # Map snapshot captured after this call inherits it automatically; no other
            # call site is needed.
            self._session.enable_god_mode_for_testing()
        self._session.start_run(
            instance_config.get("seed", 1),
            instance_config.get("character_id", "IRONCLAD"),
            instance_config.get("ascension", 0),
        )

        self._pool = WholeRunWorkerPool(
            branch_worker_count=branch_worker_count,
            request_timeout_s=request_timeout_s,
            include_main_worker=False,
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

        initial_auto = drain_trivial_reward_frontier(self._session)
        self._action_prefix.extend(initial_auto.auto_action_ids)
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
        legal = (
            _map_rooms_as_legal_actions(self._session)
            if boundary == MAP_SELECT
            else self._session.get_legal_actions()
        )
        room_context = self._session.get_room_context()
        self._root_history.observe_room_context(room_context)
        event_rng_state = (
            self._session.get_event_rng_state()
            if boundary == EVENT_CHOICE
            else None
        )
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
            raise RequestRejected(
                f"branch_id {public_branch_id!r} is {book.status} and cannot be used"
            )
        if book.terminal or book.view is None:
            raise RequestRejected(
                f"branch_id {public_branch_id!r} has no current Decision to branch from"
            )
        if book.view.chain_blocked:
            raise RequestRejected(
                f"branch_id {public_branch_id!r} reached a new map boundary; further "
                "emulate_action from a non-root Branch across a map boundary is not "
                "supported in this pass"
            )
        return book.view

    # -- response builders ------------------------------------------------------------

    def _decision_response_fields(
        self,
        public_branch_id: str,
        view: _View,
        *,
        branch_log: list,
        history: HistoryBuilder,
    ) -> dict:
        extra: dict = {
            "boundary": view.boundary,
            "legal_actions": mask_legal_actions(view.legal_actions_raw),
            "room_context": view.room_context,
            "history": history.to_public_list(),
        }
        if view.boundary == RUN_TERMINAL:
            extra["run_terminal"] = True
            extra["outcome"] = require_terminal_outcome(
                view.observation.get("outcome"),
                context="whole-run root decision",
                valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
            )
        masked = build_masked_emulator_dto(
            view.observation.get("state") or {},
            extra=extra,
        )
        return {
            "branch_id": public_branch_id,
            "decision_point_id": self._decision_points.current(public_branch_id),
            "branch_log": branch_log,
            "masked_emulator_dto": masked,
        }

    # -- operations -------------------------------------------------------------------

    def start_instance_response(self) -> dict:
        view = self._root_view()
        return {
            "status": STATUS_COMPLETED,
            "instance_id": self.instance_id,
            **self._decision_response_fields(
                ROOT_BRANCH_ID,
                view,
                branch_log=list(self._root_branch_log),
                history=self._root_history,
            ),
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
                **self._decision_response_fields(
                    branch_id,
                    book.view,
                    branch_log=book.branch_log,
                    history=book.history,
                ),
            }
        view = self._root_view()
        return {
            "status": STATUS_COMPLETED,
            **self._decision_response_fields(
                ROOT_BRANCH_ID,
                view,
                branch_log=list(self._root_branch_log),
                history=self._root_history,
            ),
        }

    def commit_action(self, decision_point_id: str, action_id: str) -> dict:
        self._decision_points.validate(ROOT_BRANCH_ID, decision_point_id)
        view = self._root_view()
        if view.boundary == RUN_TERMINAL:
            raise RequestRejected(
                "root has already reached run_terminal; no further commit_action possible"
            )
        index = view.resolve_action_id(action_id)
        chosen = view.legal_actions_raw[index]
        try:
            if view.boundary == MAP_SELECT:
                self._session.choose_room(chosen["action_id"])
                self._room_id = chosen["action_id"]
                auto = drain_trivial_reward_frontier(self._session)
                self._action_prefix = list(auto.auto_action_ids)
            else:
                self._session.step(chosen["action_id"])
                self._action_prefix.append(chosen["action_id"])
                auto = drain_trivial_reward_frontier(self._session)
                self._action_prefix.extend(auto.auto_action_ids)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": STATUS_FAULTED,
                "error": str(exc),
                "fault_kind": FAULT_EMULATOR_ERROR,
            }

        depth = len(self._root_branch_log)
        self._root_branch_log.append(
            {
                "depth": depth,
                "decision_point_id": decision_point_id,
                "action_id": action_id,
                "rng_id": ROOT_RNG_ID,
            }
        )

        self._cancel_and_release_all_branches()
        self._maybe_capture_map_snapshot()
        self._decision_points.issue(ROOT_BRANCH_ID)
        view = self._root_view()
        return {
            "status": STATUS_COMPLETED,
            **self._decision_response_fields(
                ROOT_BRANCH_ID,
                view,
                branch_log=list(self._root_branch_log),
                history=self._root_history,
            ),
        }

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
        """Execute one Active Event Branch through shared validate/prepare/finalize logic."""
        self._validate_stop_condition(simulation_options)
        spec = self._validate_event_branch(
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            action_id=action_id,
        )
        if self.active_branch_count() >= self.max_branches:
            raise RequestRejected(
                f"active Branch count would exceed max_branches={self.max_branches}"
            )

        work_item, book = self._prepare_event_branch(spec)
        self._commit_prepared_branch(spec, book)
        try:
            result = self._pool.dispatch_choice_work_items(
                [work_item], self._lease_registry
            )[0]
        except TimeoutError as exc:
            book.status = STATUS_FAULTED
            self._release_event_rng_reference(spec.branch_id, book)
            return _faulted_branch_result(
                spec,
                error=str(exc) or "Whole Run branch execution timed out",
                fault_kind=FAULT_TASK_TIMEOUT,
            )
        except Exception:
            book.status = STATUS_FAULTED
            self._release_event_rng_reference(spec.branch_id, book)
            raise
        return self._finalize_event_branch(spec, book, result)

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
        return {
            "status": STATUS_COMPLETED,
            "branch_statuses": {bid: STATUS_CANCELLED for bid in branch_ids},
        }

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
        return {
            "status": STATUS_COMPLETED,
            "branch_statuses": {bid: STATUS_RELEASED for bid in branch_ids},
        }

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
        self._event_rng_registry.release_all()
        self._pool.close()

    # -- shared Branch lifecycle primitives ------------------------------------------

    @staticmethod
    def _validate_stop_condition(simulation_options: Optional[dict]) -> None:
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(
                f"stop_condition {stop_condition!r} is not supported for whole_run instances"
            )

    def _validate_common_branch(
        self,
        *,
        parent_branch_id: str,
        branch_id: str,
        rng_id: int,
        decision_point_id: str,
        parent_view: Any | None = None,
    ) -> Any:
        if self._branch_ids.is_known(branch_id):
            raise RequestRejected(
                f"branch_id {branch_id!r} already used (branch IDs are never reusable)"
            )
        if parent_view is None:
            parent_view = self._view_for(parent_branch_id)
        if parent_view.boundary == RUN_TERMINAL:
            raise RequestRejected(
                f"parent_branch_id {parent_branch_id!r} has reached run_terminal; "
                "cannot branch further"
            )
        if parent_view.map_snapshot is None:
            raise RequestRejected(
                f"parent_branch_id {parent_branch_id!r} has not reached a map_select "
                "boundary yet; emulate_action is unavailable until the first Map "
                "Decision is reached"
            )
        if parent_branch_id != ROOT_BRANCH_ID:
            parent_rng_id = self._bookkeeping[parent_branch_id].rng_id
            if rng_id != parent_rng_id:
                raise RequestRejected(
                    f"non-root parent_branch_id {parent_branch_id!r} requires rng_id="
                    f"{parent_rng_id!r} (its own lineage rng_id), got {rng_id!r}"
                )
        self._decision_points.validate(parent_branch_id, decision_point_id)
        return parent_view

    @staticmethod
    def _resolve_chosen_action(parent_view: Any, action_id: str) -> Any:
        index = parent_view.resolve_action_id(action_id)
        chosen_action = parent_view.legal_actions_raw[index]
        if chosen_action.get("is_available") is False:
            raise RequestRejected(f"action_id {action_id!r} is not currently available")
        return chosen_action

    def _validate_event_branch(
        self,
        *,
        parent_branch_id: str,
        branch_id: str,
        rng_id: int,
        decision_point_id: str,
        action_id: str,
        parent_view: Any | None = None,
    ) -> _BranchSpec:
        parent_view = self._validate_common_branch(
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            parent_view=parent_view,
        )
        if parent_view.boundary != EVENT_CHOICE:
            raise RequestRejected(
                "Active Event RNG hypothesis is not available at this boundary.",
                fault_kind=FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY,
            )
        chosen_action = self._resolve_chosen_action(parent_view, action_id)
        return _BranchSpec(
            parent_view=parent_view,
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            action_id=action_id,
            chosen_action_id=chosen_action["action_id"],
        )

    def _branch_history_and_log(
        self,
        spec: _BranchSpec,
    ) -> tuple[HistoryBuilder, list]:
        parent_history = (
            self._root_history
            if spec.parent_branch_id == ROOT_BRANCH_ID
            else self._bookkeeping[spec.parent_branch_id].history
        )
        parent_log = (
            list(self._root_branch_log)
            if spec.parent_branch_id == ROOT_BRANCH_ID
            else list(self._bookkeeping[spec.parent_branch_id].branch_log)
        )
        branch_log = parent_log + [
            {
                "depth": len(parent_log),
                "decision_point_id": spec.decision_point_id,
                "action_id": spec.action_id,
                "rng_id": spec.rng_id,
            }
        ]
        return parent_history, branch_log

    def _prepare_event_branch(
        self,
        spec: _BranchSpec,
    ) -> tuple[ChoiceWorkItem, _BranchBookkeeping]:
        """Build Event work/bookkeeping without publishing Branch or RNG ownership."""
        parent_view = spec.parent_view
        if spec.parent_branch_id == ROOT_BRANCH_ID:
            event_rng_key = (
                spec.parent_branch_id,
                spec.decision_point_id,
                spec.rng_id,
            )
            assert parent_view.event_rng_state is not None
            override_state = self._event_rng_registry.prepare_state(
                event_rng_key,
                parent_view.event_rng_state,
                spec.rng_id,
            )
            plan = EventRngReplayPlan(
                hypothesis_key=event_rng_key,
                override_state=override_state,
                apply_before_action_index=len(parent_view.action_prefix),
            )
        else:
            parent_book = self._bookkeeping[spec.parent_branch_id]
            assert parent_book.event_rng_plan is not None
            plan = parent_book.event_rng_plan

        context_id = wr_derive_context_id(
            map_snapshot=parent_view.map_snapshot,
            room_id=parent_view.room_id,
            action_prefix=parent_view.action_prefix,
            choice_type=parent_view.choice_type,
            relic_injection=None,
        )
        work_item = ChoiceWorkItem(
            work_id=f"{spec.branch_id}-{spec.rng_id}",
            context_id=context_id,
            choice_type=parent_view.choice_type,
            map_snapshot=parent_view.map_snapshot,
            room_id=parent_view.room_id,
            action_prefix=list(parent_view.action_prefix),
            relic_injection=None,
            target_boundary=parent_view.boundary,
            work_kind=WORK_KIND_SUB_BRANCH,
            resolve_action_id=spec.chosen_action_id,
            event_rng_plan=plan,
        )
        parent_history, branch_log = self._branch_history_and_log(spec)
        book = _BranchBookkeeping(
            spec.parent_branch_id,
            branch_log,
            parent_history.fork(),
            spec.rng_id,
        )
        book.event_rng_plan = plan
        return work_item, book

    def _commit_prepared_branch(
        self,
        spec: _BranchSpec,
        book: _BranchBookkeeping,
    ) -> None:
        """Publish one prepared Branch atomically, rolling back partial commit failure."""
        branch_registered = False
        rng_registered = False
        bookkeeping_registered = False
        try:
            self._branch_ids.register(spec.branch_id)
            branch_registered = True
            if book.event_rng_plan is not None:
                plan = book.event_rng_plan
                self._event_rng_registry.commit_branch(
                    plan.hypothesis_key,
                    plan.override_state,
                    spec.branch_id,
                )
                rng_registered = True
            self._bookkeeping[spec.branch_id] = book
            bookkeeping_registered = True
        except Exception:
            if bookkeeping_registered:
                self._bookkeeping.pop(spec.branch_id, None)
            if rng_registered and book.event_rng_plan is not None:
                self._event_rng_registry.release_branch(
                    book.event_rng_plan.hypothesis_key,
                    spec.branch_id,
                )
            if branch_registered:
                self._branch_ids.rollback_registration(spec.branch_id)
            raise

    def _rollback_prepared_branch(
        self,
        spec: _BranchSpec,
        book: _BranchBookkeeping,
    ) -> None:
        """Rollback a committed Branch before it has become request-visible."""
        self._bookkeeping.pop(spec.branch_id, None)
        self._decision_points.clear(spec.branch_id)
        if book.event_rng_plan is not None:
            self._event_rng_registry.release_branch(
                book.event_rng_plan.hypothesis_key,
                spec.branch_id,
            )
            book.event_rng_plan = None
        self._branch_ids.rollback_registration(spec.branch_id)

    def _finalize_event_branch(
        self,
        spec: _BranchSpec,
        book: _BranchBookkeeping,
        result: Any,
    ) -> dict:
        """Finalize Event worker output; shared by singleton and Beam batch paths."""
        if result.status != "success":
            book.status = STATUS_FAULTED
            self._release_event_rng_reference(spec.branch_id, book)
            diagnostics = result.diagnostics or {}
            return _faulted_branch_result(
                spec,
                error=diagnostics.get("message") or "branch execution faulted",
                fault_kind=diagnostics.get("fault_kind") or FAULT_EMULATOR_ERROR,
            )

        step = result.step
        if step is None:
            book.status = STATUS_FAULTED
            self._release_event_rng_reference(spec.branch_id, book)
            raise RuntimeError("successful Whole Run branch result is missing a step")

        new_observation = step.settled_observation
        book.history.observe_room_context(step.settled_room_context)
        if new_observation["boundary"] == RUN_TERMINAL:
            try:
                book.outcome = require_terminal_outcome(
                    new_observation.get("outcome"),
                    context=f"whole-run branch {spec.branch_id!r}",
                    valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
                )
            except RuntimeError:
                book.status = STATUS_FAULTED
                self._release_event_rng_reference(spec.branch_id, book)
                raise
            book.terminal = True
            self._decision_points.issue(spec.branch_id)
            return {
                "status": STATUS_COMPLETED,
                "branch_id": spec.branch_id,
                "parent_branch_id": spec.parent_branch_id,
                "rng_id": spec.rng_id,
                "decision_point_id": self._decision_points.current(spec.branch_id),
                "branch_log": book.branch_log,
                "masked_emulator_dto": build_masked_emulator_dto(
                    {"run_terminal": True, "outcome": book.outcome}
                ),
            }

        try:
            new_view = _build_child_view(
                spec.parent_view,
                spec.chosen_action_id,
                result,
            )
        except Exception:
            book.status = STATUS_FAULTED
            self._release_event_rng_reference(spec.branch_id, book)
            raise
        book.view = new_view
        self._decision_points.issue(spec.branch_id)
        return {
            "status": STATUS_COMPLETED,
            **self._decision_response_fields(
                spec.branch_id,
                new_view,
                branch_log=book.branch_log,
                history=book.history,
            ),
            "parent_branch_id": spec.parent_branch_id,
            "rng_id": spec.rng_id,
        }

    # -- helpers ----------------------------------------------------------------------

    def active_branch_count(self) -> int:
        return sum(
            1
            for book in self._bookkeeping.values()
            if book.status == STATUS_COMPLETED and not book.terminal
        )

    def _reject_if_root(self, branch_id: str) -> None:
        if branch_id == ROOT_BRANCH_ID:
            raise RequestRejected(
                "root cannot be Cancelled/Released/status-queried as a Branch"
            )

    def _release_event_rng_reference(
        self,
        branch_id: str,
        book: "_BranchBookkeeping",
    ) -> None:
        if book.event_rng_plan is not None:
            self._event_rng_registry.release_branch(
                book.event_rng_plan.hypothesis_key,
                branch_id,
            )
            book.event_rng_plan = None

    def _cancel_and_release_all_branches(self) -> None:
        for bid, book in self._bookkeeping.items():
            if book.status not in (STATUS_CANCELLED, STATUS_RELEASED):
                book.status = STATUS_RELEASED
            book.view = None
            self._decision_points.clear(bid)
        self._event_rng_registry.release_all()
