"""Whole Run instance with batched Branch replay for Training Beam Search.

Active Event and Combat frontiers share the ``emulate_actions`` -> ``WholeRunWorkerPool``
batch path. Event validation, preparation, ownership commit, and finalization are inherited
from the base Whole Run instance so singleton and batch execution share one lifecycle.
Combat adds deterministic replay preparation/finalization on top of the same transaction.
"""

from __future__ import annotations

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
    _BranchSpec,
    _build_child_view,
    _faulted_branch_result,
    build_masked_emulator_dto,
    require_terminal_outcome,
    wr_derive_context_id,
)
from API.masking import mask_public_fragment
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


class WholeRunInstance(_BaseWholeRunInstance):
    """Whole Run API instance with batched branching at Event and Combat decisions."""

    def start_instance_response(self) -> dict:
        response = super().start_instance_response()
        response["max_emulate_actions_items"] = self.max_branches
        # Numeric batch size is not a semantic capability. Training can safely roll out
        # Event batching independently only when this explicit boundary list advertises
        # it; older servers omit the field and therefore trigger singleton fallback.
        response["emulate_actions_boundaries"] = [
            EVENT_CHOICE,
            PENDING_CHOICE,
            STABLE,
        ]
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
            raise RequestRejected(
                f"active Branch count would exceed max_branches={self.max_branches}"
            )

        work_item, book = self._prepare_combat_branch(spec)
        self._commit_prepared_branch(spec, book)
        try:
            result = self._pool.dispatch_choice_work_items(
                [work_item], self._lease_registry
            )[0]
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

    def emulate_actions(
        self,
        *,
        items: list,
        simulation_options: Optional[dict],
    ) -> dict:
        """Execute one homogeneous Event or Combat frontier in one WorkerPool dispatch.

        Validation and preparation are mutation-free. Branch IDs, Event RNG references,
        and bookkeeping are committed only after every item has prepared successfully;
        a commit failure rolls back all earlier commits before the request can become
        observable. Worker dispatch happens only after the complete batch is committed.
        """
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
                raise RequestRejected(
                    f"branch_id {branch_id!r} is duplicated within this batch"
                )
            batch_branch_ids.add(branch_id)
        for item in items:
            if item["parent_branch_id"] in batch_branch_ids:
                raise RequestRejected(
                    "emulate_actions items may only use parents that existed before the batch"
                )

        parent_views: dict[str, Any] = {}
        for item in items:
            parent_branch_id = item["parent_branch_id"]
            if parent_branch_id not in parent_views:
                parent_views[parent_branch_id] = self._view_for(parent_branch_id)

        event_batch = self._is_event_batch(parent_views)
        validator = (
            self._validate_event_branch
            if event_batch
            else self._validate_combat_branch
        )
        preparer = (
            self._prepare_event_branch
            if event_batch
            else self._prepare_combat_branch
        )
        finalizer = (
            self._finalize_event_branch
            if event_batch
            else self._finalize_combat_branch
        )

        specs: list[_BranchSpec] = []
        for item in items:
            parent_branch_id = item["parent_branch_id"]
            specs.append(
                validator(
                    parent_branch_id=parent_branch_id,
                    branch_id=item["branch_id"],
                    rng_id=item["rng_id"],
                    decision_point_id=item["decision_point_id"],
                    action_id=item["action_id"],
                    parent_view=parent_views[parent_branch_id],
                )
            )

        active_count = self.active_branch_count()
        if active_count + len(specs) > self.max_branches:
            raise RequestRejected(
                f"submitting {len(specs)} Branch(es) would exceed max_branches="
                f"{self.max_branches} (currently {active_count} active)"
            )

        # Preparation must be pure: exceptions here leave Branch IDs, RNG refs, and
        # bookkeeping exactly as they were before the request.
        prepared: list[tuple[_BranchSpec, Any, _BranchBookkeeping]] = []
        for spec in specs:
            work_item, book = preparer(spec)
            prepared.append((spec, work_item, book))

        committed: list[tuple[_BranchSpec, Any, _BranchBookkeeping]] = []
        try:
            for spec, work_item, book in prepared:
                self._commit_prepared_branch(spec, book)
                committed.append((spec, work_item, book))
        except Exception:
            for spec, _, book in reversed(committed):
                self._rollback_prepared_branch(spec, book)
            raise

        return self._dispatch_prepared_batch(prepared, finalizer)

    @staticmethod
    def _is_event_batch(parent_views: dict[str, Any]) -> bool:
        boundaries = {view.boundary for view in parent_views.values()}
        if EVENT_CHOICE not in boundaries:
            return False
        if boundaries != {EVENT_CHOICE}:
            raise RequestRejected(
                "emulate_actions cannot mix Active Event and non-Event parent boundaries"
            )
        return True

    def _dispatch_prepared_batch(
        self,
        prepared: list[tuple[_BranchSpec, Any, _BranchBookkeeping]],
        finalizer: Any,
    ) -> dict:
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
                self._release_event_rng_reference(spec.branch_id, book)
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
                branch_results[spec.branch_id] = finalizer(spec, book, result)
        except Exception:
            self._best_effort_release_prepared(prepared)
            raise

        return {"status": STATUS_COMPLETED, "branch_results": branch_results}

    def _best_effort_release_prepared(
        self,
        prepared: list[tuple[_BranchSpec, Any, _BranchBookkeeping]],
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

    def _validate_combat_branch(
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
        if not _is_combat_view(parent_view):
            raise RequestRejected(
                "Branch simulation is unavailable at this Whole Run boundary.",
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

    def _prepare_combat_branch(
        self,
        spec: _BranchSpec,
    ) -> tuple[ChoiceWorkItem, _BranchBookkeeping]:
        """Build Combat replay work without publishing Branch coordinator state."""
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

        parent_history, branch_log = self._branch_history_and_log(spec)
        book = _BranchBookkeeping(
            spec.parent_branch_id,
            branch_log,
            parent_history.fork(),
            spec.rng_id,
        )
        return work_item, book

    def _finalize_combat_branch(
        self,
        spec: _BranchSpec,
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

        transition = step.step_result.get("transition")
        if transition is not None and not isinstance(transition, dict):
            book.status = STATUS_FAULTED
            raise RuntimeError(
                "Whole Run branch transition must be a dictionary when present"
            )

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
            terminal_payload: dict[str, Any] = {
                "run_terminal": True,
                "outcome": book.outcome,
            }
            if transition is not None:
                terminal_payload["transition"] = transition
            return {
                "status": STATUS_COMPLETED,
                "branch_id": spec.branch_id,
                "parent_branch_id": spec.parent_branch_id,
                "rng_id": spec.rng_id,
                "decision_point_id": self._decision_points.current(spec.branch_id),
                "branch_log": book.branch_log,
                "masked_emulator_dto": build_masked_emulator_dto(terminal_payload),
            }

        try:
            new_view = _build_child_view(
                spec.parent_view,
                spec.chosen_action_id,
                result,
            )
        except Exception:
            book.status = STATUS_FAULTED
            raise
        book.view = new_view
        self._decision_points.issue(spec.branch_id)
        decision_fields = self._decision_response_fields(
            spec.branch_id,
            new_view,
            branch_log=book.branch_log,
            history=book.history,
        )
        if transition is not None:
            decision_fields["masked_emulator_dto"]["transition"] = mask_public_fragment(
                transition
            )
        return {
            "status": STATUS_COMPLETED,
            **decision_fields,
            "parent_branch_id": spec.parent_branch_id,
            "rng_id": spec.rng_id,
        }
