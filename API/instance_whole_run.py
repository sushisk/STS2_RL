"""`Instance` implementation for `instance_type="whole_run"`.

The root run is owned in-process while speculative Branches are reconstructed by
``WholeRunWorkerPool`` from the latest map snapshot. Active Event branching is the one
Whole Run path where positive ``rng_id`` values denote real RNG hypotheses; other
boundaries either use deterministic replay in the Beam subclass or reject branching.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional

import run_emulator_bridge as bridge
from battle_emulator import CombatCompletion
from game_access import LeaseState
from live_combat_session import LiveCombatSession
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
from API.combat_phase import CombatPhase
from API.combat_phase import DEFAULT_MAX_TIME_MS, ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR
from API.history_builder import HistoryBuilder
from API.identifiers import BranchIdRegistry, DecisionPointRegistry, RngHypothesisTable
from API.masking import build_masked_emulator_dto, mask_legal_actions
from API.terminal_outcome import VALID_WHOLE_RUN_TERMINAL_OUTCOMES, require_terminal_outcome
from API.validation import RequestRejected
from API.whole_run_event_rng import EventRngHypothesisRegistry


logger = logging.getLogger(__name__)


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
        "internal_id",
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
        # Whole Run normally owns worker Branches directly.  During an adopted Combat
        # phase this is the phase-local BranchManager id; retaining it is what lets the
        # public tombstone release the worker before the phase is folded.
        self.internal_id: "str | None" = None


class _CombatPhaseView:
    """Combat decision state held by a Whole Run speculative Branch.

    This deliberately is not a ``_View``: it contains a combat snapshot and replay
    context, not a map snapshot/run position.  Treating it as a normal Whole Run view
    was the old prefix-replay bug in disguise.
    """

    __slots__ = ("legal_actions_raw", "decision_context", "battle_state", "boundary")

    def __init__(self, legal_actions_raw: list, decision_context: Any, boundary: str, battle_state: Any) -> None:
        self.legal_actions_raw = legal_actions_raw
        self.decision_context = decision_context
        self.battle_state = battle_state
        self.boundary = boundary

    def resolve_action_id(self, public_action_id: str) -> int:
        matches = [
            index
            for index, action in enumerate(self.legal_actions_raw)
            if str(action.get("action_id", index)) == public_action_id
        ]
        if not matches:
            raise RequestRejected(f"action_id {public_action_id!r} is not among current legal actions")
        if len(matches) != 1:
            raise RequestRejected(f"action_id {public_action_id!r} is ambiguous among current legal actions")
        return matches[0]


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
    diagnostics: Optional[dict] = None,
) -> dict:
    result = {
        "status": STATUS_FAULTED,
        "branch_id": spec.branch_id,
        "parent_branch_id": spec.parent_branch_id,
        "rng_id": spec.rng_id,
        "error": error or "branch execution faulted",
        "fault_kind": fault_kind or FAULT_EMULATOR_ERROR,
    }
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


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
        god_mode = instance_config.get("god_mode", False)
        if not isinstance(god_mode, bool):
            raise RequestRejected("instance_config.god_mode must be of type bool")
        self._god_mode = god_mode
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
        self._combat_worker_count = branch_worker_count
        self._request_timeout_s = request_timeout_s

        self._map_snapshot: Optional[str] = None
        self._room_id: Optional[int] = None
        self._action_prefix: list = []
        self._root_branch_log: list = []
        self._root_history = HistoryBuilder()
        self._bookkeeping: dict[str, _BranchBookkeeping] = {}
        self._combat_phase: CombatPhase | None = None
        # A combat public Branch owns both this reservation and a phase-local worker
        # record until release.  This is the whole-run-wide admission limit; do not
        # infer capacity from BranchManager's queued/running count after work finishes.
        self._combat_branch_reservations: set[str] = set()
        self._last_combat_completion: CombatCompletion | None = None
        self._faulted = False
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

    def enter_combat_phase(self) -> CombatPhase:
        """Atomically hand the live board from Whole Run to an adopted CombatPhase."""
        self._ensure_mutable()
        if self._combat_phase is not None:
            raise RequestRejected("a combat phase is already active for this whole-run instance")

        transfer = self._session.begin_lease_transfer()
        prior_decision = self._decision_points.current(ROOT_BRANCH_ID)
        phase: CombatPhase | None = None
        committed = False
        try:
            live_session = LiveCombatSession(whole_run_mode=True)
            root_state = live_session.adopt_current_combat()
            phase = CombatPhase.adopt(
                live_session,
                root_state,
                worker_count=self._combat_worker_count,
                request_timeout_s=self._request_timeout_s,
                max_branches=self.max_branches,
                worker_pool_backend=None,
            )
            # This is deliberately before the lease commit: adoption and the first
            # published root decision become visible together only after commit succeeds.
            self._decision_points.issue(ROOT_BRANCH_ID)
            lease = self._session.commit_lease_transfer(
                transfer, LeaseState.COMBAT, live_session.lease_holder
            )
            committed = True
            live_session.accept_transferred_lease(lease)
            self._combat_phase = phase
            return phase
        except Exception:
            if committed:
                self._poison_combat_transaction(phase)
            else:
                if phase is not None:
                    phase.close()
                self._decision_points.restore_current(ROOT_BRANCH_ID, prior_decision)
                self._session.rollback_lease_transfer(transfer)
            raise

    def leave_combat_phase(self, completion: CombatCompletion) -> None:
        """Atomically retire an adopted phase and return its settled run to Whole Run."""
        self._ensure_mutable()
        phase = self._combat_phase
        if phase is None:
            raise RequestRejected("no active combat phase to leave")

        transfer = phase.begin_lease_transfer()
        phase_folded = False
        try:
            # Preserve the hand-off fact before retiring the component that produced it.
            self._last_combat_completion = completion
            # Public branch tombstones must survive phase teardown.  S7 will add the
            # phase-branch mapping here; these are the currently public Whole Run branches.
            self._cancel_and_release_all_branches()
            phase.close()
            phase_folded = True
            self._combat_phase = None
            # The lease returns BEFORE the run is normalized, not after.  Draining the
            # reward frontier steps the board, and nothing may hold a mutating
            # capability while the access state is TRANSFERRING - that exclusion is the
            # whole point of the transfer.  Both still sit inside this try, so a failure
            # while normalizing poisons exactly as a failure before the commit would.
            lease = self._session.commit_lease_transfer(
                transfer, LeaseState.RUN, self._session.lease_holder
            )
            self._session.accept_transferred_lease(lease)
            auto = drain_trivial_reward_frontier(self._session)
            self._action_prefix = list(auto.auto_action_ids)
            self._maybe_capture_map_snapshot()
        except Exception:
            # A combat-completion Step has already changed the physical game.  Once any
            # following record/publication/normalization work fails, RUN is no longer a
            # truthful recovery point; poison instead of inventing one from a snapshot.
            self._poison_combat_transaction(None if phase_folded else phase)
            raise

    def _poison_combat_transaction(self, phase: CombatPhase | None) -> None:
        self._faulted = True
        # The public owners and their global reservations must be retired while the
        # adopted phase can still release its internal worker records.
        if self._combat_phase is not None:
            try:
                self._cancel_and_release_all_branches()
            except Exception:
                logger.exception("failed to release combat Branches while poisoning whole-run transaction")
        self._combat_phase = None
        self._session.poison_mutations()
        if phase is not None:
            try:
                phase.close()
            except Exception:
                logger.exception("failed to close combat phase while poisoning whole-run transaction")
        pool = self._pool
        if pool is not None:
            try:
                pool.close()
            except Exception:
                logger.exception("failed to close whole-run worker pool while poisoning combat transaction")

    def _ensure_mutable(self) -> None:
        if self._faulted:
            raise RequestRejected("whole-run instance is faulted; close it explicitly before reuse")

    def start_instance_response(self) -> dict:
        if self._faulted:
            return {"status": STATUS_FAULTED, "instance_id": self.instance_id}
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
        if self._faulted:
            return {"status": STATUS_FAULTED, "branch_id": branch_id}
        if branch_id != ROOT_BRANCH_ID and not self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"unknown branch_id {branch_id!r}")
        if branch_id != ROOT_BRANCH_ID:
            book = self._bookkeeping[branch_id]
            if book.status != STATUS_COMPLETED:
                return {"status": book.status, "branch_id": branch_id}
            if book.terminal:
                if book.internal_id is not None:
                    return {
                        "status": STATUS_COMPLETED,
                        "branch_id": branch_id,
                        "decision_point_id": self._decision_points.current(branch_id),
                        "branch_log": list(book.branch_log),
                        "masked_emulator_dto": build_masked_emulator_dto(
                            {},
                            extra={
                                "terminal": True,
                                "transition": {"kind": "combat_completed"},
                            },
                        ),
                    }
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
            if isinstance(book.view, _CombatPhaseView):
                return {
                    "status": STATUS_COMPLETED,
                    **self._phase_decision_response_fields(
                        branch_id,
                        book.view,
                        branch_log=list(book.branch_log),
                        history=book.history,
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

    def _record_root_commit(
        self,
        decision_point_id: str,
        action_id: str,
        *,
        release_branches: bool,
        issue_decision: bool,
    ) -> dict:
        """Book a committed root action and publish the decision that follows it.

        Shared by the combat and non-combat commit paths, which differ only in who
        already did these two things: a concluded combat released its branches inside
        leave_combat_phase, and an entering combat had its decision issued by adoption.
        """
        self._root_branch_log.append(
            {
                "depth": len(self._root_branch_log),
                "decision_point_id": decision_point_id,
                "action_id": action_id,
                "rng_id": ROOT_RNG_ID,
            }
        )
        if release_branches:
            self._cancel_and_release_all_branches()
        self._maybe_capture_map_snapshot()
        if issue_decision:
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

    def commit_action(self, decision_point_id: str, action_id: str) -> dict:
        self._ensure_mutable()
        self._decision_points.validate(ROOT_BRANCH_ID, decision_point_id)
        view = self._root_view()
        if view.boundary == RUN_TERMINAL:
            raise RequestRejected(
                "root has already reached run_terminal; no further commit_action possible"
            )
        index = view.resolve_action_id(action_id)
        chosen = view.legal_actions_raw[index]
        phase = self._combat_phase
        entered_combat = False
        if phase is not None:
            # A combat Step advances an adopted live frame.  Everything that follows
            # it is part of that transaction: a failure must fault the run rather than
            # leave the retained frame out of sync with the physical game.
            try:
                # The adopted phase retains the combat decision frame, so it is the
                # only safe executor while combat is live.  WholeRunSession remains
                # the source of the root observation below.
                phase.commit_root_action(chosen)
                completion = phase.root_state.combat_completion
                left_combat = completion is not None
                if completion is not None:
                    self.leave_combat_phase(completion)

                # leave_combat_phase owns the branch release when it folds a
                # completed combat phase back into Whole Run.
                return self._record_root_commit(
                    decision_point_id,
                    action_id,
                    release_branches=not left_combat,
                    issue_decision=True,
                )
            except Exception as exc:  # noqa: BLE001
                # CombatPhase marks the point at which its live Step has irreversibly
                # advanced.  After that point, returning an ordinary emulator fault
                # would leave its retained frame stale; poison the shared run instead.
                if phase.root_commit_advanced() and not self._faulted:
                    self._poison_combat_transaction(
                        phase if self._combat_phase is phase else None
                    )
                return {
                    "status": STATUS_FAULTED,
                    "error": str(exc),
                    "fault_kind": FAULT_EMULATOR_ERROR,
                }

        try:
            if view.boundary == MAP_SELECT:
                self._session.choose_room(chosen["action_id"])
                self._room_id = chosen["action_id"]
                # ChooseRoom supplies the live room context.  Adopt before any
                # WholeRunSession mutation such as reward draining: a combat phase
                # owns the newly entered combat board.
                entered_combat = (
                    self._session.get_room_context().get("room_type") == "CombatRoom"
                )
                if entered_combat:
                    self._action_prefix = []
                    self.enter_combat_phase()
                else:
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

        # Adoption already issued the first combat decision.  Every other
        # successful root commit advances exactly one root decision.
        return self._record_root_commit(
            decision_point_id,
            action_id,
            release_branches=True,
            issue_decision=not entered_combat,
        )

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
        if self._combat_phase is not None:
            response = self._emulate_combat_phase_actions(
                items=[
                    {
                        "parent_branch_id": parent_branch_id,
                        "branch_id": branch_id,
                        "rng_id": rng_id,
                        "decision_point_id": decision_point_id,
                        "action_id": action_id,
                    }
                ],
                simulation_options=simulation_options,
            )
            return response["branch_results"][branch_id]
        self._ensure_mutable()
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

    def emulate_actions(self, *, items: list, simulation_options: Optional[dict]) -> dict:
        """Batch combat delegation for the non-Beam facade.

        Event batching remains the Beam-specific optimization; an inactive phase has
        no safe combat replay fallback in either facade.
        """
        if self._combat_phase is None:
            raise RequestRejected(
                "combat branching requires an active CombatPhase; Whole Run will not replay a room prefix"
            )
        return self._emulate_combat_phase_actions(
            items=items, simulation_options=simulation_options
        )

    def cancel_branches(self, branch_ids: list) -> dict:
        self._ensure_mutable()
        for bid in branch_ids:
            self._reject_if_root(bid)
            book = self._bookkeeping.get(bid)
            if book is None:
                raise RequestRejected(f"unknown branch_id {bid!r}")
            if book.internal_id is not None and book.status not in (STATUS_CANCELLED, STATUS_RELEASED):
                phase = self._combat_phase
                if phase is None:
                    raise RuntimeError("combat Branch outlived its active CombatPhase")
                phase.cancel([book.internal_id])
            if book.status not in (STATUS_CANCELLED, STATUS_RELEASED):
                book.status = STATUS_CANCELLED
                book.view = None
                self._release_event_rng_reference(bid, book)
        return {
            "status": STATUS_COMPLETED,
            "branch_statuses": {bid: STATUS_CANCELLED for bid in branch_ids},
        }

    def release_branches(self, branch_ids: list) -> dict:
        self._ensure_mutable()
        for bid in branch_ids:
            self._reject_if_root(bid)
            book = self._bookkeeping.get(bid)
            if book is None:
                raise RequestRejected(f"unknown branch_id {bid!r}")
            if book.internal_id is not None and book.status != STATUS_RELEASED:
                phase = self._combat_phase
                if phase is None:
                    raise RuntimeError("combat Branch outlived its active CombatPhase")
                phase.release([book.internal_id])
                self._combat_branch_reservations.discard(bid)
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
            if book.internal_id is not None and book.status != STATUS_RELEASED:
                phase = self._combat_phase
                if phase is None:
                    raise RuntimeError("combat Branch outlived its active CombatPhase")
                translated = phase.branch_status(book.internal_id)
                book.status = {
                    "queued": "queued",
                    "running": "running",
                    "completed": STATUS_COMPLETED,
                    "cancelled": STATUS_CANCELLED,
                    "faulted": STATUS_FAULTED,
                    "released": STATUS_RELEASED,
                }.get(translated, translated)
            statuses[bid] = book.status
        return {"status": STATUS_COMPLETED, "branch_statuses": statuses}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            phase = self._combat_phase
            if phase is not None:
                # ``phase.close`` only knows its internal ids.  Preserve the public
                # release tombstones and free global capacity first.
                try:
                    self._cancel_and_release_all_branches()
                finally:
                    phase.close()
                    self._combat_phase = None
        finally:
            try:
                self._event_rng_registry.release_all()
            finally:
                try:
                    self._pool.close()
                finally:
                    self._session.close()

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
        # Phase branches have a second owner (CombatPhase) and therefore must be
        # released while it is still live.  Tombstone public records before callers
        # fold the phase, so in-flight status queries never observe an unknown id.
        phase = self._combat_phase
        phase_internal_ids = [
            book.internal_id
            for book in self._bookkeeping.values()
            if book.internal_id is not None and book.status != STATUS_RELEASED
        ]
        if phase_internal_ids:
            if phase is None:
                raise RuntimeError("combat Branch outlived its active CombatPhase")
            phase.release(phase_internal_ids)
        for bid, book in self._bookkeeping.items():
            if book.status not in (STATUS_CANCELLED, STATUS_RELEASED):
                book.status = STATUS_RELEASED
            book.view = None
            self._decision_points.clear(bid)
            self._combat_branch_reservations.discard(bid)
        self._event_rng_registry.release_all()

    # -- adopted CombatPhase branch transaction ---------------------------------------

    def _combat_phase_view_for(self, public_branch_id: str) -> _CombatPhaseView:
        phase = self._combat_phase
        if phase is None:
            raise RequestRejected(
                "combat branching requires an active CombatPhase; Whole Run will not replay a room prefix"
            )
        if public_branch_id == ROOT_BRANCH_ID:
            legal, context, boundary = phase.root_decision()
            return _CombatPhaseView(legal, context, boundary, phase.root_state)
        book = self._bookkeeping.get(public_branch_id)
        if book is None:
            raise RequestRejected(f"parent_branch_id {public_branch_id!r} does not exist")
        if book.status in (STATUS_CANCELLED, STATUS_RELEASED, STATUS_FAULTED):
            raise RequestRejected(
                f"parent_branch_id {public_branch_id!r} is {book.status} and cannot be extended"
            )
        if book.terminal:
            raise RequestRejected(
                f"parent_branch_id {public_branch_id!r} ended at the combat boundary; "
                "combat_completed branches are leaves because combat snapshots carry no run position"
            )
        if not isinstance(book.view, _CombatPhaseView):
            raise RequestRejected(
                f"parent_branch_id {public_branch_id!r} is not owned by the active CombatPhase"
            )
        return book.view

    def _reserve_combat_branch_capacity(self, branch_ids: list[str]) -> None:
        if len(self._combat_branch_reservations) + len(branch_ids) > self.max_branches:
            raise RequestRejected(
                f"submitting {len(branch_ids)} Branch(es) would exceed max_branches="
                f"{self.max_branches} (currently {len(self._combat_branch_reservations)} reserved)"
            )
        self._combat_branch_reservations.update(branch_ids)

    def _release_combat_branch_capacity(self, branch_ids: list[str]) -> None:
        self._combat_branch_reservations.difference_update(branch_ids)

    def _phase_decision_response_fields(
        self, public_branch_id: str, view: _CombatPhaseView, *, branch_log: list, history: HistoryBuilder
    ) -> dict:
        # Normalize combat output into the Whole Run public envelope.  In particular,
        # boundary/legal/context/history live inside the masked DTO, never beside it.
        room_context = self._session.get_room_context()
        history.observe_room_context(room_context)
        engine_state = dict(getattr(view.battle_state, "engine_state", {}) or {})
        return {
            "branch_id": public_branch_id,
            "decision_point_id": self._decision_points.current(public_branch_id),
            "branch_log": branch_log,
            "masked_emulator_dto": build_masked_emulator_dto(
                engine_state,
                extra={
                    "boundary": view.boundary,
                    "legal_actions": mask_legal_actions(view.legal_actions_raw),
                    "room_context": room_context,
                    "history": history.to_public_list(),
                },
            ),
        }

    def _phase_view_from_result(self, result: Any) -> _CombatPhaseView | None:
        # Keep Combat replay semantics inside CombatPhase: the context below is built
        # from its anchor snapshot/replay prefix result, never from a Whole Run prefix.
        from search.decision_context import (
            BOUNDARY_PENDING,
            BOUNDARY_STABLE,
            BOUNDARY_TERMINAL,
            DecisionContext,
        )

        boundary = result.result_signature.boundary
        if boundary == BOUNDARY_PENDING:
            context = result.pending_decision_context
            battle_state = context.current_decision_result
            return _CombatPhaseView(
                list(battle_state._cached_legal_actions or []), context, boundary, battle_state
            )
        if boundary == BOUNDARY_STABLE:
            context = DecisionContext.from_main_stable_capture(
                result.child_snapshot, result.next_decision_result, result.result_signature
            )
            return _CombatPhaseView(
                list(result.next_legal_actions or []), context, boundary, result.next_decision_result
            )
        if boundary == BOUNDARY_TERMINAL:
            return None
        raise RuntimeError(f"unexpected combat branch boundary: {boundary!r}")

    def _emulate_combat_phase_actions(self, *, items: list, simulation_options: Optional[dict]) -> dict:
        """Admit, register, dispatch, and retire adopted-combat Branches as one unit."""
        self._ensure_mutable()
        self._validate_stop_condition(simulation_options)
        if not isinstance(items, list) or not items:
            raise RequestRejected("emulate_actions.items must be a non-empty list")
        if len(items) > self.max_branches:
            raise RequestRejected(
                f"emulate_actions batch size {len(items)} exceeds max batch size {self.max_branches}; chunk the frontier into multiple requests"
            )
        phase = self._combat_phase
        if phase is None:
            raise RequestRejected(
                "combat branching requires an active CombatPhase; Whole Run will not replay a room prefix"
            )
        seen: set[str] = set()
        admitted: list[tuple[dict, _CombatPhaseView, dict]] = []
        for item in items:
            branch_id = item["branch_id"]
            if branch_id in seen:
                raise RequestRejected(f"branch_id {branch_id!r} is duplicated within this batch")
            seen.add(branch_id)
            if self._branch_ids.is_known(branch_id):
                raise RequestRejected(f"branch_id {branch_id!r} already used (branch IDs are never reusable)")
            if item["parent_branch_id"] in seen:
                raise RequestRejected("emulate_actions items may only use parents that existed before the batch")
            parent_id = item["parent_branch_id"]
            view = self._combat_phase_view_for(parent_id)
            if view.decision_context is None:
                raise RequestRejected(ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR)
            if parent_id != ROOT_BRANCH_ID:
                parent_book = self._bookkeeping[parent_id]
                if item["rng_id"] != parent_book.rng_id:
                    raise RequestRejected(
                        f"non-root parent_branch_id {parent_id!r} requires rng_id={parent_book.rng_id!r} "
                        f"(its own lineage rng_id), got {item['rng_id']!r}"
                    )
            self._decision_points.validate(parent_id, item["decision_point_id"])
            chosen = self._resolve_chosen_action(view, item["action_id"])
            admitted.append((item, view, chosen))

        # Reserve before asking CombatPhase to allocate a work item/worker.  Any error
        # after this point unwinds both this reservation and every phase record it made.
        branch_ids = [item["branch_id"] for item, _, _ in admitted]
        self._reserve_combat_branch_capacity(branch_ids)
        rng_snapshot = phase.snapshot_rng_hypotheses()
        internal_ids: list[str] = []
        registered: list[str] = []
        try:
            prepared: list[tuple[dict, _CombatPhaseView, dict, Any, str | None, list, HistoryBuilder]] = []
            for item, view, chosen in admitted:
                parent_id = item["parent_branch_id"]
                parent_internal_id = None if parent_id == ROOT_BRANCH_ID else self._bookkeeping[parent_id].internal_id
                parent_history = self._root_history if parent_id == ROOT_BRANCH_ID else self._bookkeeping[parent_id].history
                parent_log = list(self._root_branch_log) if parent_id == ROOT_BRANCH_ID else list(self._bookkeeping[parent_id].branch_log)
                branch_log = parent_log + [{
                    "depth": len(parent_log), "decision_point_id": item["decision_point_id"],
                    "action_id": item["action_id"], "rng_id": item["rng_id"],
                }]
                work_item = phase.build_work_item(view.decision_context, chosen, parent_id, item["decision_point_id"], item["rng_id"])
                prepared.append((item, view, chosen, work_item, parent_internal_id, branch_log, parent_history.fork()))
            internal_ids = list(phase.submit_many([(work, parent) for _, _, _, work, parent, _, _ in prepared]))
            for entry, internal_id in zip(prepared, internal_ids, strict=True):
                item, _, _, _, _, branch_log, history = entry
                book = _BranchBookkeeping(item["parent_branch_id"], branch_log, history, item["rng_id"])
                book.internal_id = internal_id
                self._branch_ids.register(item["branch_id"])
                registered.append(item["branch_id"])
                self._bookkeeping[item["branch_id"]] = book
            timeout_s = (simulation_options or {}).get("max_time_ms", DEFAULT_MAX_TIME_MS) / 1000.0
            results = phase.poll(timeout=timeout_s, branch_ids=internal_ids)
            branch_results: dict[str, dict] = {}
            for entry, internal_id in zip(prepared, internal_ids, strict=True):
                item, _, _, _, _, branch_log, _ = entry
                book = self._bookkeeping[item["branch_id"]]
                result = results.get(internal_id)
                if result is None:
                    raise RuntimeError(f"CombatPhase.poll() returned no result for Branch {internal_id}")
                if result.status != "success":
                    book.status = STATUS_FAULTED
                    diagnostics = result.diagnostics or {}
                    branch_results[item["branch_id"]] = _faulted_branch_result(
                        _BranchSpec(None, item["parent_branch_id"], item["branch_id"], item["rng_id"], item["decision_point_id"], item["action_id"], None),
                        error=diagnostics.get("message", "branch execution faulted"),
                        fault_kind=diagnostics.get("fault_kind", FAULT_EMULATOR_ERROR),
                    )
                    continue
                next_view = self._phase_view_from_result(result)
                self._decision_points.issue(item["branch_id"])
                if next_view is None:
                    book.terminal = True
                    branch_results[item["branch_id"]] = {
                        "status": STATUS_COMPLETED, "branch_id": item["branch_id"],
                        "parent_branch_id": item["parent_branch_id"], "rng_id": item["rng_id"],
                        "decision_point_id": self._decision_points.current(item["branch_id"]),
                        "branch_log": branch_log,
                        "masked_emulator_dto": build_masked_emulator_dto(
                            {}, extra={"terminal": True, "transition": {"kind": "combat_completed"}}
                        ),
                    }
                    continue
                book.view = next_view
                branch_results[item["branch_id"]] = {
                    "status": STATUS_COMPLETED,
                    **self._phase_decision_response_fields(item["branch_id"], next_view, branch_log=branch_log, history=book.history),
                    "parent_branch_id": item["parent_branch_id"], "rng_id": item["rng_id"],
                }
            return {"status": STATUS_COMPLETED, "branch_results": branch_results}
        except Exception:
            if internal_ids:
                try:
                    phase.cancel(internal_ids)
                finally:
                    phase.release(internal_ids)
            for branch_id in registered:
                self._bookkeeping.pop(branch_id, None)
                self._decision_points.clear(branch_id)
                self._branch_ids.rollback_registration(branch_id)
            phase.restore_rng_hypotheses(rng_snapshot)
            self._release_combat_branch_capacity(branch_ids)
            raise
