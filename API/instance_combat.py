"""Public `Instance` implementation for `instance_type="combat"`.

Combat semantics are owned by ``CombatPhase``.  This facade retains the mapping between
v0.7's wire vocabulary (Training-assigned ``branch_id``, ``decision_point_id``,
``rng_id``) and Combat's internal branch records, plus the public response formatting.

Only `stop_condition="next_decision"` (or unset) is supported for
`emulate_action.simulation_options` - Combat's Bootstrap Step is inherently a single
Decision-to-Decision step; `combat_end` etc. would require RL to choose MULTIPLE
intermediate actions Training never specified, which is out of scope for a single
`action_id` request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from search.decision_context import (
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    BOUNDARY_TERMINAL,
    DecisionContext,
    DecisionSignature,
)

from API.combat_phase import (
    CombatPhase,
    DEFAULT_MAX_TIME_MS,
    ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR,
    START_PENDING_UNSUPPORTED,
)
from API.dto import (
    FAULT_EMULATOR_ERROR,
    ROOT_BRANCH_ID,
    ROOT_RNG_ID,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAULTED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_RELEASED,
    STATUS_RUNNING,
)
from API.history_builder import HistoryBuilder
from API.identifiers import BranchIdRegistry, DecisionPointRegistry
from API.masking import build_masked_emulator_dto, mask_legal_actions
from API.terminal_outcome import require_terminal_outcome
from API.validation import RequestRejected

_AMBIGUOUS_ACTION_INDEX = -1


class _DecisionView:
    __slots__ = ("legal_actions_raw", "decision_context", "battle_state", "boundary", "public_id_by_index")

    def __init__(
        self,
        legal_actions_raw: list,
        decision_context: DecisionContext | None,
        boundary: str,
        battle_state=None,
    ) -> None:
        self.legal_actions_raw = legal_actions_raw
        self.decision_context = decision_context
        self.battle_state = (
            battle_state if battle_state is not None
            else (decision_context.current_decision_result if decision_context is not None else None)
        )
        self.boundary = boundary
        public_id_by_index = {}
        for i, action in enumerate(legal_actions_raw):
            public_action_id = str(action.get("action_id", i))
            if public_action_id in public_id_by_index:
                public_id_by_index[public_action_id] = _AMBIGUOUS_ACTION_INDEX
            else:
                public_id_by_index[public_action_id] = i
        self.public_id_by_index = public_id_by_index

    def resolve_action_id(self, public_action_id: str) -> int:
        if public_action_id not in self.public_id_by_index:
            raise RequestRejected(f"action_id {public_action_id!r} is not among current legal actions")
        index = self.public_id_by_index[public_action_id]
        if index == _AMBIGUOUS_ACTION_INDEX:
            raise RequestRejected(f"action_id {public_action_id!r} is ambiguous among current legal actions")
        return index


class _BranchBookkeeping:
    __slots__ = ("internal_id", "parent_public_id", "branch_log", "history", "view", "terminal", "outcome", "rng_id")

    def __init__(self, internal_id: str, parent_public_id: str, branch_log: list, history: HistoryBuilder, rng_id: int) -> None:
        self.internal_id = internal_id
        self.parent_public_id = parent_public_id
        self.branch_log = branch_log
        self.history = history
        self.rng_id = rng_id
        self.view: Optional[_DecisionView] = None
        self.terminal = False
        self.outcome: "str | None" = None


@dataclass(frozen=True)
class _AdmittedItem:
    parent_branch_id: str
    branch_id: str
    rng_id: int
    decision_point_id: str
    action_id: str
    parent_view: "_DecisionView"
    candidate: dict


class CombatInstance:
    instance_type = "combat"

    def __init__(
        self,
        instance_id: str,
        instance_config: dict,
        *,
        worker_count: int | None = None,
        request_timeout_s: float = 60.0,
        max_branches: int = 64,
        worker_pool_backend: str | None = None,
    ) -> None:
        self.instance_id = instance_id
        scenario_spec = {k: v for k, v in instance_config.items() if k != "instance_type"}
        self._phase = CombatPhase(
            scenario_spec,
            worker_count=worker_count,
            request_timeout_s=request_timeout_s,
            worker_pool_backend=worker_pool_backend,
            max_branches=max_branches,
        )
        if self._phase.root_branching_unavailable_reason is not None:
            # The phase has already created its worker pool; close it on this rejected path.
            self._phase.close()
            raise RuntimeError(START_PENDING_UNSUPPORTED)
        self._branch_ids = BranchIdRegistry()
        self._decision_points = DecisionPointRegistry()
        self._root_branch_log: list = []
        self._root_history = HistoryBuilder()
        self._bookkeeping: dict[str, _BranchBookkeeping] = {}
        self._closed = False
        self._decision_points.issue(ROOT_BRANCH_ID)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RequestRejected("combat instance is closed after an unrecoverable cleanup failure")

    def _root_view(self) -> _DecisionView:
        legal, context, boundary = self._phase.root_decision()
        return _DecisionView(legal, context, boundary, self._phase.root_state)

    def _view_for(self, public_branch_id: str) -> _DecisionView:
        if public_branch_id == ROOT_BRANCH_ID:
            return self._root_view()
        book = self._bookkeeping.get(public_branch_id)
        if book is None or book.view is None:
            raise RequestRejected(f"branch_id {public_branch_id!r} has no current Decision")
        return book.view

    def _decision_response_fields(self, public_branch_id: str, view: _DecisionView, *, branch_log: list) -> dict:
        battle_state = view.battle_state
        engine_state = dict(battle_state.engine_state)
        extra: dict[str, Any] = {"legal_actions": mask_legal_actions(view.legal_actions_raw)}
        if battle_state.is_terminal:
            extra["terminal"] = True
            extra["outcome"] = require_terminal_outcome(
                battle_state.outcome, context="combat root decision"
            )
        masked = build_masked_emulator_dto(engine_state, extra=extra)
        return {"branch_id": public_branch_id, "decision_point_id": self._decision_points.current(public_branch_id), "branch_log": branch_log, "masked_emulator_dto": masked}

    def start_instance_response(self) -> dict:
        self._ensure_open()
        view = self._root_view()
        return {"status": STATUS_COMPLETED, "instance_id": self.instance_id, "max_emulate_actions_items": self._phase.max_branches, **self._decision_response_fields(ROOT_BRANCH_ID, view, branch_log=list(self._root_branch_log))}

    def get_decision(self, branch_id: str) -> dict:
        self._ensure_open()
        if branch_id != ROOT_BRANCH_ID and not self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"unknown branch_id {branch_id!r}")
        if branch_id != ROOT_BRANCH_ID and branch_id not in self._bookkeeping:
            raise RequestRejected(f"branch_id {branch_id!r} is unavailable after a failed branch operation")
        if branch_id != ROOT_BRANCH_ID:
            book = self._bookkeeping[branch_id]
            status = self._phase.branch_status(book.internal_id)
            translated = _translate_branch_status(status)
            if translated in (STATUS_QUEUED, STATUS_RUNNING, STATUS_CANCELLED, STATUS_FAULTED, STATUS_RELEASED):
                return {"status": translated, "branch_id": branch_id}
            if book.terminal:
                return {
                    "status": STATUS_COMPLETED,
                    "branch_id": branch_id,
                    "decision_point_id": self._decision_points.current(branch_id),
                    "branch_log": list(book.branch_log),
                    "masked_emulator_dto": build_masked_emulator_dto(
                        {
                            "terminal": True,
                            "outcome": require_terminal_outcome(
                                book.outcome, context=f"combat branch {branch_id!r}"
                            ),
                        }
                    ),
                }
        view = self._view_for(branch_id)
        branch_log = list(self._root_branch_log) if branch_id == ROOT_BRANCH_ID else list(self._bookkeeping[branch_id].branch_log)
        return {"status": STATUS_COMPLETED, **self._decision_response_fields(branch_id, view, branch_log=branch_log)}

    def commit_action(self, decision_point_id: str, action_id: str) -> dict:
        self._ensure_open()
        self._decision_points.validate(ROOT_BRANCH_ID, decision_point_id)
        view = self._root_view()
        index = view.resolve_action_id(action_id)
        chosen = view.legal_actions_raw[index]
        try:
            self._phase.commit_root_action(chosen)
        except Exception as exc:
            if self._phase.root_commit_advanced():
                self._closed = True
            return {"status": STATUS_FAULTED, "error": str(exc), "fault_kind": FAULT_EMULATOR_ERROR}
        depth = len(self._root_branch_log)
        self._root_branch_log.append({"depth": depth, "decision_point_id": decision_point_id, "action_id": action_id, "rng_id": ROOT_RNG_ID})
        self._cancel_and_release_all_branches()
        self._decision_points.issue(ROOT_BRANCH_ID)
        view = self._root_view()
        return {"status": STATUS_COMPLETED, **self._decision_response_fields(ROOT_BRANCH_ID, view, branch_log=list(self._root_branch_log))}

    def emulate_action(self, *, parent_branch_id: str, branch_id: str, rng_id: int, decision_point_id: str, action_id: str, simulation_options: Optional[dict]) -> dict:
        self._ensure_open()
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(f"stop_condition {stop_condition!r} is not supported for combat instances")
        if parent_branch_id != ROOT_BRANCH_ID:
            if not self._branch_ids.is_known(parent_branch_id) or parent_branch_id not in self._bookkeeping:
                raise RequestRejected(f"parent_branch_id {parent_branch_id!r} does not exist")
            parent_status = self._phase.branch_status(self._bookkeeping[parent_branch_id].internal_id)
            if parent_status in ("cancelled", "released", "faulted"):
                raise RequestRejected(f"parent_branch_id {parent_branch_id!r} is {parent_status} and cannot be extended")
            parent_rng_id = self._bookkeeping[parent_branch_id].rng_id
            if rng_id != parent_rng_id:
                raise RequestRejected(f"non-root parent_branch_id {parent_branch_id!r} requires rng_id={parent_rng_id!r} (its own lineage rng_id), got {rng_id!r}")
        self._decision_points.validate(parent_branch_id, decision_point_id)
        self._branch_ids.register(branch_id)
        parent_view = self._view_for(parent_branch_id)
        self._require_branchable(parent_view)
        index = parent_view.resolve_action_id(action_id)
        chosen = parent_view.legal_actions_raw[index]
        rng_snapshot = self._phase.snapshot_rng_hypotheses()
        internal_id: str | None = None
        try:
            work_item = self._phase.build_work_item(parent_view.decision_context, chosen, parent_branch_id, decision_point_id, rng_id)
            parent_internal_id = None if parent_branch_id == ROOT_BRANCH_ID else self._bookkeeping[parent_branch_id].internal_id
            (internal_id,) = self._phase.submit([work_item], parent_internal_id=parent_internal_id)
            parent_history = self._root_history if parent_branch_id == ROOT_BRANCH_ID else self._bookkeeping[parent_branch_id].history
            parent_log = list(self._root_branch_log) if parent_branch_id == ROOT_BRANCH_ID else list(self._bookkeeping[parent_branch_id].branch_log)
            depth = len(parent_log)
            branch_log = parent_log + [{"depth": depth, "decision_point_id": decision_point_id, "action_id": action_id, "rng_id": rng_id}]
            book = _BranchBookkeeping(internal_id, parent_branch_id, branch_log, parent_history.fork(), rng_id)
            self._bookkeeping[branch_id] = book
            results = self._phase.poll(timeout=(simulation_options or {}).get("max_time_ms", DEFAULT_MAX_TIME_MS) / 1000.0, branch_ids=[internal_id])
            result = results.get(internal_id)
            if result is None:
                return {"status": STATUS_RUNNING, "branch_id": branch_id, "parent_branch_id": parent_branch_id, "rng_id": rng_id}
            return self._finalize_branch_result(branch_id=branch_id, parent_branch_id=parent_branch_id, rng_id=rng_id, book=book, branch_log=branch_log, result=result)
        except Exception as original_exc:
            cleanup_errors: list[Exception] = []
            if internal_id is not None:
                try:
                    self._phase.cancel([internal_id])
                except Exception as exc:
                    cleanup_errors.append(exc)
                try:
                    self._phase.release([internal_id])
                except Exception as exc:
                    cleanup_errors.append(exc)
            self._bookkeeping.pop(branch_id, None)
            self._decision_points.clear(branch_id)
            self._phase.restore_rng_hypotheses(rng_snapshot)
            if cleanup_errors:
                try:
                    self.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
                raise RuntimeError("emulate_action cleanup failed; combat instance was closed to prevent ghost Branch execution") from cleanup_errors[0]
            raise original_exc

    def _validate_emulate_actions_item(self, item: dict) -> _AdmittedItem:
        parent_branch_id = item["parent_branch_id"]
        branch_id = item["branch_id"]
        rng_id = item["rng_id"]
        decision_point_id = item["decision_point_id"]
        action_id = item["action_id"]
        if self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"branch_id {branch_id!r} already used (branch IDs are never reusable)")
        if parent_branch_id != ROOT_BRANCH_ID:
            if not self._branch_ids.is_known(parent_branch_id) or parent_branch_id not in self._bookkeeping:
                raise RequestRejected(f"parent_branch_id {parent_branch_id!r} does not exist")
            parent_status = self._phase.branch_status(self._bookkeeping[parent_branch_id].internal_id)
            if parent_status in ("cancelled", "released", "faulted"):
                raise RequestRejected(f"parent_branch_id {parent_branch_id!r} is {parent_status} and cannot be extended")
            parent_rng_id = self._bookkeeping[parent_branch_id].rng_id
            if rng_id != parent_rng_id:
                raise RequestRejected(f"non-root parent_branch_id {parent_branch_id!r} requires rng_id={parent_rng_id!r} (its own lineage rng_id), got {rng_id!r}")
        self._decision_points.validate(parent_branch_id, decision_point_id)
        parent_view = self._view_for(parent_branch_id)
        self._require_branchable(parent_view)
        index = parent_view.resolve_action_id(action_id)
        chosen = parent_view.legal_actions_raw[index]
        return _AdmittedItem(parent_branch_id, branch_id, rng_id, decision_point_id, action_id, parent_view, chosen)

    def emulate_actions(self, *, items: list, simulation_options: Optional[dict]) -> dict:
        self._ensure_open()
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(f"stop_condition {stop_condition!r} is not supported for combat instances")
        if not isinstance(items, list) or not items:
            raise RequestRejected("emulate_actions.items must be a non-empty list")
        if len(items) > self._phase.max_branches:
            raise RequestRejected(f"emulate_actions batch size {len(items)} exceeds max batch size {self._phase.max_branches}; chunk the frontier into multiple requests")
        seen_branch_ids: set = set()
        admitted: list[_AdmittedItem] = []
        for item in items:
            branch_id = item["branch_id"]
            if branch_id in seen_branch_ids:
                raise RequestRejected(f"branch_id {branch_id!r} is duplicated within this batch")
            seen_branch_ids.add(branch_id)
            admitted.append(self._validate_emulate_actions_item(item))
        active_count = self._phase.active_branch_count()
        if active_count + len(admitted) > self._phase.max_branches:
            raise RequestRejected(f"submitting {len(admitted)} Branch(es) would exceed max_branches={self._phase.max_branches} (currently {active_count} active)")
        rng_snapshot = self._phase.snapshot_rng_hypotheses()
        internal_ids: list[str] = []
        pending: list[tuple] = []
        try:
            prepared: list[tuple] = []
            for admitted_item in admitted:
                work_item = self._phase.build_work_item(admitted_item.parent_view.decision_context, admitted_item.candidate, admitted_item.parent_branch_id, admitted_item.decision_point_id, admitted_item.rng_id)
                parent_internal_id = None if admitted_item.parent_branch_id == ROOT_BRANCH_ID else self._bookkeeping[admitted_item.parent_branch_id].internal_id
                parent_history = self._root_history if admitted_item.parent_branch_id == ROOT_BRANCH_ID else self._bookkeeping[admitted_item.parent_branch_id].history
                parent_log = list(self._root_branch_log) if admitted_item.parent_branch_id == ROOT_BRANCH_ID else list(self._bookkeeping[admitted_item.parent_branch_id].branch_log)
                depth = len(parent_log)
                branch_log = parent_log + [{"depth": depth, "decision_point_id": admitted_item.decision_point_id, "action_id": admitted_item.action_id, "rng_id": admitted_item.rng_id}]
                prepared.append((admitted_item, work_item, parent_internal_id, branch_log, parent_history.fork()))
            internal_ids = self._phase.submit_many([(work_item, parent_internal_id) for _, work_item, parent_internal_id, _, _ in prepared])
            local_books: list[tuple] = []
            for prepared_item, internal_id in zip(prepared, internal_ids, strict=True):
                admitted_item, _, _, branch_log, history = prepared_item
                book = _BranchBookkeeping(internal_id, admitted_item.parent_branch_id, branch_log, history, admitted_item.rng_id)
                local_books.append((admitted_item, internal_id, book, branch_log))
            for admitted_item in admitted:
                self._branch_ids.register(admitted_item.branch_id)
            for admitted_item, internal_id, book, branch_log in local_books:
                self._bookkeeping[admitted_item.branch_id] = book
                pending.append((admitted_item, internal_id, book, branch_log))
            branch_timeout_s = (simulation_options or {}).get("max_time_ms", DEFAULT_MAX_TIME_MS) / 1000.0
            results = self._phase.poll(timeout=branch_timeout_s, branch_ids=internal_ids)
            branch_results: dict = {}
            for admitted_item, internal_id, book, branch_log in pending:
                result = results.get(internal_id)
                if result is None:
                    raise RuntimeError(f"BranchManager.poll() returned no terminal result for dispatched Branch {internal_id}")
                branch_results[admitted_item.branch_id] = self._finalize_branch_result(branch_id=admitted_item.branch_id, parent_branch_id=admitted_item.parent_branch_id, rng_id=admitted_item.rng_id, book=book, branch_log=branch_log, result=result)
            return {"status": STATUS_COMPLETED, "branch_results": branch_results}
        except Exception as original_exc:
            cleanup_errors: list[Exception] = []
            if internal_ids:
                try:
                    self._phase.cancel(internal_ids)
                except Exception as exc:
                    cleanup_errors.append(exc)
                try:
                    self._phase.release(internal_ids)
                except Exception as exc:
                    cleanup_errors.append(exc)
            for admitted_item in admitted:
                self._bookkeeping.pop(admitted_item.branch_id, None)
                self._decision_points.clear(admitted_item.branch_id)
            self._phase.restore_rng_hypotheses(rng_snapshot)
            if cleanup_errors:
                try:
                    self.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
                raise RuntimeError("emulate_actions cleanup failed; combat instance was closed to prevent ghost Branch execution") from cleanup_errors[0]
            raise original_exc

    def _finalize_branch_result(self, *, branch_id: str, parent_branch_id: str, rng_id: int, book: _BranchBookkeeping, branch_log: list, result: Any) -> dict:
        if result.status != "success":
            diagnostics = result.diagnostics or {}
            return {"status": STATUS_FAULTED, "branch_id": branch_id, "parent_branch_id": parent_branch_id, "rng_id": rng_id, "error": diagnostics.get("message", "branch execution faulted"), "fault_kind": diagnostics.get("fault_kind", FAULT_EMULATOR_ERROR)}
        boundary = result.result_signature.boundary
        if boundary == BOUNDARY_PENDING:
            next_view = _DecisionView(
                list(result.pending_decision_context.current_decision_result._cached_legal_actions or []),
                result.pending_decision_context,
                boundary,
                result.pending_decision_context.current_decision_result,
            )
        elif boundary == BOUNDARY_STABLE:
            next_context = DecisionContext.from_main_stable_capture(result.child_snapshot, result.next_decision_result, result.result_signature)
            next_view = _DecisionView(list(result.next_legal_actions or []), next_context, boundary, result.next_decision_result)
        elif boundary == BOUNDARY_TERMINAL:
            terminal_outcome = require_terminal_outcome(
                result.terminal_result.outcome if result.terminal_result else None,
                context=f"combat branch {branch_id!r}",
            )
            book.terminal = True
            book.outcome = terminal_outcome
            next_view = None
        else:
            raise RuntimeError(f"unexpected combat branch boundary: {boundary!r}")
        book.view = next_view
        self._decision_points.issue(branch_id)
        if next_view is not None:
            return {"status": STATUS_COMPLETED, **self._decision_response_fields(branch_id, next_view, branch_log=branch_log), "parent_branch_id": parent_branch_id, "rng_id": rng_id}
        return {"status": STATUS_COMPLETED, "branch_id": branch_id, "parent_branch_id": parent_branch_id, "rng_id": rng_id, "decision_point_id": self._decision_points.current(branch_id), "branch_log": branch_log, "masked_emulator_dto": build_masked_emulator_dto({"terminal": True, "outcome": book.outcome})}

    def cancel_branches(self, branch_ids: list) -> dict:
        self._ensure_open()
        internal_ids = [self._internal_id_or_reject(bid) for bid in branch_ids]
        self._phase.cancel(internal_ids)
        return {"status": STATUS_COMPLETED, "branch_statuses": {bid: STATUS_CANCELLED for bid in branch_ids}}

    def release_branches(self, branch_ids: list) -> dict:
        self._ensure_open()
        internal_ids = [self._internal_id_or_reject(bid) for bid in branch_ids]
        self._phase.release(internal_ids)
        for bid in branch_ids:
            self._compact_bookkeeping_for_release(bid)
        return {"status": STATUS_COMPLETED, "branch_statuses": {bid: STATUS_RELEASED for bid in branch_ids}}

    def get_branch_status(self, branch_ids: list) -> dict:
        self._ensure_open()
        statuses = {}
        for bid in branch_ids:
            if bid == ROOT_BRANCH_ID:
                raise RequestRejected("root has no Branch status")
            internal_id = self._internal_id_or_reject(bid)
            statuses[bid] = _translate_branch_status(self._phase.branch_status(internal_id))
        return {"status": STATUS_COMPLETED, "branch_statuses": statuses}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._phase.close()

    def _internal_id_or_reject(self, public_branch_id: str) -> str:
        if public_branch_id == ROOT_BRANCH_ID:
            raise RequestRejected("root cannot be Cancelled/Released")
        book = self._bookkeeping.get(public_branch_id)
        if book is None:
            raise RequestRejected(f"unknown branch_id {public_branch_id!r}")
        return book.internal_id

    @staticmethod
    def _require_branchable(view: _DecisionView) -> None:
        if view.decision_context is None:
            raise RequestRejected(ROOT_BRANCHING_UNAVAILABLE_NO_STABLE_ANCHOR)

    def _compact_bookkeeping_for_release(self, public_branch_id: str) -> None:
        """Retain only the lightweight public-to-internal tombstone for a released Branch."""
        book = self._bookkeeping.get(public_branch_id)
        if book is None:
            return
        book.branch_log.clear()
        book.history = None
        book.view = None
        book.terminal = True
        self._decision_points.clear(public_branch_id)

    def _cancel_and_release_all_branches(self) -> None:
        # Release every tracked Branch, including terminal speculative Branches whose
        # Decision view has already been compacted. BranchManager.release_branches()
        # safely cancels active Branches first and drops execution-heavy state for
        # terminal Branches, so filtering here would leave completed records resident.
        internal_ids = [book.internal_id for book in self._bookkeeping.values()]
        if internal_ids:
            self._phase.release(internal_ids)
        for bid in list(self._bookkeeping):
            self._compact_bookkeeping_for_release(bid)


def _translate_branch_status(internal_status: str) -> str:
    return {"queued": STATUS_QUEUED, "running": STATUS_RUNNING, "completed": STATUS_COMPLETED, "cancelled": STATUS_CANCELLED, "faulted": STATUS_FAULTED, "released": STATUS_RELEASED}.get(internal_status, internal_status)
