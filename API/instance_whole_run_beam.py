"""Whole Run instance with Combat-scoped branch replay for Training Beam Search.

Active Event branches keep the base instance's RNG-hypothesis semantics. Combat branches
replay the latest map snapshot and room action prefix deterministically; ``rng_id`` is
lineage metadata only. Other Whole Run boundaries remain non-branchable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from whole_run_session import PENDING_CHOICE, STABLE

from API.instance_whole_run import (
    EVENT_CHOICE,
    FAULT_EMULATOR_ERROR,
    FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY,
    FAULT_TASK_TIMEOUT,
    ROOT_BRANCH_ID,
    RUN_TERMINAL,
    STATUS_COMPLETED,
    STATUS_FAULTED,
    VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
    WORK_KIND_SUB_BRANCH,
    ChoiceWorkItem,
    WholeRunInstance as _BaseWholeRunInstance,
    _BranchBookkeeping,
    _build_child_view,
    build_masked_emulator_dto,
    require_terminal_outcome,
    wr_derive_context_id,
)
from API.validation import RequestRejected

_COMBAT_BOUNDARIES = frozenset({STABLE, PENDING_CHOICE})
_COMBAT_ACTION_TYPES = frozenset(
    {
        "system",
        "card",
        "potion",
        "choice_target",
        "choice_card",
        "choice_confirm",
        "choice_skip",
    }
)


def _is_combat_view(view: object) -> bool:
    if getattr(view, "boundary", None) not in _COMBAT_BOUNDARIES:
        return False

    legal_actions = getattr(view, "legal_actions_raw", None)
    if not isinstance(legal_actions, list):
        return False

    action_types: set[str] = set()
    for action in legal_actions:
        if not isinstance(action, dict):
            return False
        if action.get("is_available") is False:
            continue
        action_type = action.get("action_type")
        if not isinstance(action_type, str):
            return False
        action_types.add(action_type)
    return bool(action_types) and action_types <= _COMBAT_ACTION_TYPES


@dataclass(frozen=True)
class _CombatBranchSpec:
    parent_view: Any
    parent_branch_id: str
    branch_id: str
    rng_id: int
    decision_point_id: str
    action_id: str
    chosen_action_id: Any


def _faulted_branch_result(
    spec: _CombatBranchSpec,
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


class WholeRunInstance(_BaseWholeRunInstance):
    """Whole Run API instance with batched branching at Combat decisions."""

    def start_instance_response(self) -> dict:
        response = super().start_instance_response()
        response["max_emulate_actions_items"] = self.max_branches
        return response

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
        if self._parent_boundary(parent_branch_id) == EVENT_CHOICE:
            return super().emulate_action(
                parent_branch_id=parent_branch_id,
                branch_id=branch_id,
                rng_id=rng_id,
                decision_point_id=decision_point_id,
                action_id=action_id,
                simulation_options=simulation_options,
            )

        self._validate_stop_condition(simulation_options)
        spec = self._validate_combat_branch(
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            action_id=action_id,
        )
        if self.active_branch_count() >= self.max_branches:
            raise RequestRejected(f"active Branch count would exceed max_branches={self.max_branches}")

        work_item, book = self._register_combat_branch(spec)
        try:
            result = self._pool.dispatch_choice_work_items([work_item], self._lease_registry)[0]
        except TimeoutError as exc:
            book.status = STATUS_FAULTED
            return _faulted_branch_result(
                spec,
                error=str(exc) or "Whole Run branch execution timed out",
                fault_kind=FAULT_TASK_TIMEOUT,
            )
        except Exception:
            book.status = STATUS_FAULTED
            raise
        return self._finalize_combat_branch(spec, book, result)

    def emulate_actions(self, *, items: list, simulation_options: Optional[dict]) -> dict:
        """Execute one Combat frontier in a single WorkerPool dispatch."""
        self._validate_stop_condition(simulation_options)
        if not isinstance(items, list) or not items:
            raise RequestRejected("emulate_actions.items must be a non-empty list")
        if len(items) > self.max_branches:
            raise RequestRejected(
                f"emulate_actions batch size {len(items)} exceeds max batch size "
                f"{self.max_branches}; chunk the frontier into multiple requests"
            )

        batch_branch_ids: set[str] = set()
        for item in items:
            branch_id = item["branch_id"]
            if branch_id in batch_branch_ids:
                raise RequestRejected(f"branch_id {branch_id!r} is duplicated within this batch")
            batch_branch_ids.add(branch_id)
        for item in items:
            if item["parent_branch_id"] in batch_branch_ids:
                raise RequestRejected(
                    "emulate_actions items may only use parents that existed before the batch"
                )

        parent_views: dict[str, Any] = {}
        specs: list[_CombatBranchSpec] = []
        for item in items:
            parent_branch_id = item["parent_branch_id"]
            parent_view = parent_views.get(parent_branch_id)
            if parent_view is None:
                parent_view = self._view_for(parent_branch_id)
                parent_views[parent_branch_id] = parent_view
            specs.append(
                self._validate_combat_branch(
                    parent_branch_id=parent_branch_id,
                    branch_id=item["branch_id"],
                    rng_id=item["rng_id"],
                    decision_point_id=item["decision_point_id"],
                    action_id=item["action_id"],
                    parent_view=parent_view,
                )
            )

        active_count = self.active_branch_count()
        if active_count + len(specs) > self.max_branches:
            raise RequestRejected(
                f"submitting {len(specs)} Branch(es) would exceed max_branches="
                f"{self.max_branches} (currently {active_count} active)"
            )

        prepared: list[tuple[_CombatBranchSpec, Any, _BranchBookkeeping]] = []
        try:
            for spec in specs:
                work_item, book = self._register_combat_branch(spec)
                prepared.append((spec, work_item, book))
        except Exception:
            self._best_effort_release_prepared(prepared)
            raise

        try:
            results = self._pool.dispatch_choice_work_items(
                [work_item for _, work_item, _ in prepared],
                self._lease_registry,
            )
        except TimeoutError as exc:
            error = str(exc) or "Whole Run branch batch timed out"
            branch_results: dict[str, dict] = {}
            for spec, _, book in prepared:
                book.status = STATUS_FAULTED
                branch_results[spec.branch_id] = _faulted_branch_result(
                    spec,
                    error=error,
                    fault_kind=FAULT_TASK_TIMEOUT,
                )
            return {"status": STATUS_COMPLETED, "branch_results": branch_results}
        except Exception:
            self._best_effort_release_prepared(prepared)
            raise

        if len(results) != len(prepared):
            self._best_effort_release_prepared(prepared)
            raise RuntimeError("WholeRunWorkerPool returned an incomplete batch")

        branch_results: dict[str, dict] = {}
        try:
            for (spec, _, book), result in zip(prepared, results):
                branch_results[spec.branch_id] = self._finalize_combat_branch(
                    spec, book, result
                )
        except Exception:
            self._best_effort_release_prepared(prepared)
            raise

        return {"status": STATUS_COMPLETED, "branch_results": branch_results}

    def _best_effort_release_prepared(
        self,
        prepared: list[tuple[_CombatBranchSpec, Any, _BranchBookkeeping]],
    ) -> None:
        branch_ids = [spec.branch_id for spec, _, _ in prepared]
        if not branch_ids:
            return
        try:
            self.release_branches(branch_ids)
        except Exception:
            pass

    def _parent_boundary(self, parent_branch_id: str) -> str | None:
        if parent_branch_id == ROOT_BRANCH_ID:
            boundary = self._session.get_observation().get("boundary")
            return boundary if isinstance(boundary, str) else None
        book = self._bookkeeping.get(parent_branch_id)
        if book is None or book.view is None:
            return None
        return book.view.boundary

    @staticmethod
    def _validate_stop_condition(simulation_options: Optional[dict]) -> None:
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(
                f"stop_condition {stop_condition!r} is not supported for whole_run instances"
            )

    def _validate_combat_branch(
        self,
        *,
        parent_branch_id: str,
        branch_id: str,
        rng_id: int,
        decision_point_id: str,
        action_id: str,
        parent_view: Any | None = None,
    ) -> _CombatBranchSpec:
        if self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"branch_id {branch_id!r} already used (branch IDs are never reusable)")

        if parent_view is None:
            parent_view = self._view_for(parent_branch_id)
        if parent_view.boundary == RUN_TERMINAL:
            raise RequestRejected(
                f"parent_branch_id {parent_branch_id!r} has reached run_terminal; cannot branch further"
            )
        if parent_view.map_snapshot is None:
            raise RequestRejected(
                f"parent_branch_id {parent_branch_id!r} has not reached a map_select boundary yet; "
                "emulate_action is unavailable until the first Map Decision is reached"
            )
        if not _is_combat_view(parent_view):
            raise RequestRejected(
                "Branch simulation is unavailable at this Whole Run boundary.",
                fault_kind=FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY,
            )
        if parent_branch_id != ROOT_BRANCH_ID:
            parent_rng_id = self._bookkeeping[parent_branch_id].rng_id
            if rng_id != parent_rng_id:
                raise RequestRejected(
                    f"non-root parent_branch_id {parent_branch_id!r} requires rng_id="
                    f"{parent_rng_id!r} (its own lineage rng_id), got {rng_id!r}"
                )

        self._decision_points.validate(parent_branch_id, decision_point_id)
        index = parent_view.resolve_action_id(action_id)
        chosen_action = parent_view.legal_actions_raw[index]
        if chosen_action.get("is_available") is False:
            raise RequestRejected(f"action_id {action_id!r} is not currently available")
        chosen_action_id = chosen_action["action_id"]
        return _CombatBranchSpec(
            parent_view=parent_view,
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            action_id=action_id,
            chosen_action_id=chosen_action_id,
        )

    def _register_combat_branch(
        self, spec: _CombatBranchSpec
    ) -> tuple[ChoiceWorkItem, _BranchBookkeeping]:
        self._branch_ids.register(spec.branch_id)
        parent_view = spec.parent_view
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
            event_rng_plan=None,
        )

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
        book = _BranchBookkeeping(
            spec.parent_branch_id,
            branch_log,
            parent_history.fork(),
            spec.rng_id,
        )
        self._bookkeeping[spec.branch_id] = book
        return work_item, book

    def _finalize_combat_branch(
        self,
        spec: _CombatBranchSpec,
        book: _BranchBookkeeping,
        result: Any,
    ) -> dict:
        if result.status != "success":
            book.status = STATUS_FAULTED
            diagnostics = result.diagnostics or {}
            return _faulted_branch_result(
                spec,
                error=diagnostics.get("message") or "branch execution faulted",
                fault_kind=diagnostics.get("fault_kind") or FAULT_EMULATOR_ERROR,
            )

        step = result.step
        if step is None:
            book.status = STATUS_FAULTED
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
            new_view = _build_child_view(spec.parent_view, spec.chosen_action_id, result)
        except Exception:
            book.status = STATUS_FAULTED
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
