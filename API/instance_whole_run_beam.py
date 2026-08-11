"""Whole Run instance with Combat-scoped batch branching for Training Beam Search.

The base WholeRunInstance deliberately supports RNG hypotheses only at active Event
boundaries.  Combat Beam Search needs a different capability: replay the exact current
run state from the latest map snapshot/action prefix and branch on Combat actions without
inventing a new RNG hypothesis.  In that mode ``rng_id`` remains a lineage identifier;
no RNG override is applied.

Non-Combat Whole Run boundaries (map/reward/shop/rest/etc.) remain non-branchable here.
Active Event branching continues to use the base implementation unchanged.
"""

from __future__ import annotations

from typing import Optional

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
    """Whole Run API instance that exposes batched branching at Combat decisions."""

    def start_instance_response(self) -> dict:
        response = super().start_instance_response()
        return {**response, "max_emulate_actions_items": self.max_branches}

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
        parent_view = self._view_for(parent_branch_id)
        if parent_view.boundary == EVENT_CHOICE:
            return super().emulate_action(
                parent_branch_id=parent_branch_id,
                branch_id=branch_id,
                rng_id=rng_id,
                decision_point_id=decision_point_id,
                action_id=action_id,
                simulation_options=simulation_options,
            )
        if not _is_combat_view(parent_view):
            raise RequestRejected(
                "Branch simulation is unavailable at this Whole Run boundary.",
                fault_kind=FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY,
            )
        return self._emulate_combat_action(
            parent_view=parent_view,
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            action_id=action_id,
            simulation_options=simulation_options,
        )

    def emulate_actions(self, *, items: list, simulation_options: Optional[dict]) -> dict:
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(
                f"stop_condition {stop_condition!r} is not supported for whole_run instances"
            )
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
            self._preflight_emulate_item(item)

        if self.active_branch_count() + len(items) > self.max_branches:
            raise RequestRejected(
                f"submitting {len(items)} Branch(es) would exceed max_branches="
                f"{self.max_branches} (currently {self.active_branch_count()} active)"
            )

        created: list[str] = []
        branch_results: dict[str, dict] = {}
        try:
            for item in items:
                result = self.emulate_action(
                    parent_branch_id=item["parent_branch_id"],
                    branch_id=item["branch_id"],
                    rng_id=item["rng_id"],
                    decision_point_id=item["decision_point_id"],
                    action_id=item["action_id"],
                    simulation_options=simulation_options,
                )
                created.append(item["branch_id"])
                branch_results[item["branch_id"]] = result
        except Exception:
            if created:
                try:
                    self.release_branches(created)
                except Exception:
                    # Preserve the original batch failure. Root commit/instance close still
                    # invalidates all speculative Whole Run branches as a final safeguard.
                    pass
            raise
        return {"status": STATUS_COMPLETED, "branch_results": branch_results}

    def _preflight_emulate_item(self, item: dict) -> None:
        parent_branch_id = item["parent_branch_id"]
        branch_id = item["branch_id"]
        rng_id = item["rng_id"]
        decision_point_id = item["decision_point_id"]
        action_id = item["action_id"]

        if self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"branch_id {branch_id!r} already used (branch IDs are never reusable)")
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
        if parent_view.boundary != EVENT_CHOICE and not _is_combat_view(parent_view):
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
        parent_view.resolve_action_id(action_id)

    def _emulate_combat_action(
        self,
        *,
        parent_view,
        parent_branch_id: str,
        branch_id: str,
        rng_id: int,
        decision_point_id: str,
        action_id: str,
        simulation_options: Optional[dict],
    ) -> dict:
        stop_condition = (simulation_options or {}).get("stop_condition")
        if stop_condition not in (None, "next_decision"):
            raise RequestRejected(
                f"stop_condition {stop_condition!r} is not supported for whole_run instances"
            )
        if self.active_branch_count() >= self.max_branches:
            raise RequestRejected(f"active Branch count would exceed max_branches={self.max_branches}")
        if parent_view.map_snapshot is None:
            raise RequestRejected(
                f"parent_branch_id {parent_branch_id!r} has not reached a map_select boundary yet; "
                "emulate_action is unavailable until the first Map Decision is reached"
            )
        if parent_branch_id != ROOT_BRANCH_ID:
            parent_rng_id = self._bookkeeping[parent_branch_id].rng_id
            if rng_id != parent_rng_id:
                raise RequestRejected(
                    f"non-root parent_branch_id {parent_branch_id!r} requires rng_id="
                    f"{parent_rng_id!r} (its own lineage rng_id), got {rng_id!r}"
                )
        self._decision_points.validate(parent_branch_id, decision_point_id)
        if self._branch_ids.is_known(branch_id):
            raise RequestRejected(f"branch_id {branch_id!r} already used (branch IDs are never reusable)")
        self._branch_ids.register(branch_id)

        index = parent_view.resolve_action_id(action_id)
        chosen = parent_view.legal_actions_raw[index]
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
            event_rng_plan=None,
        )

        parent_history = (
            self._root_history
            if parent_branch_id == ROOT_BRANCH_ID
            else self._bookkeeping[parent_branch_id].history
        )
        parent_log = (
            list(self._root_branch_log)
            if parent_branch_id == ROOT_BRANCH_ID
            else list(self._bookkeeping[parent_branch_id].branch_log)
        )
        depth = len(parent_log)
        branch_log = parent_log + [
            {
                "depth": depth,
                "decision_point_id": decision_point_id,
                "action_id": action_id,
                "rng_id": rng_id,
            }
        ]
        book = _BranchBookkeeping(parent_branch_id, branch_log, parent_history.fork(), rng_id)
        self._bookkeeping[branch_id] = book

        try:
            results = self._pool.dispatch_choice_work_items([work_item], self._lease_registry)
        except TimeoutError as exc:
            book.status = STATUS_FAULTED
            return {
                "status": STATUS_FAULTED,
                "branch_id": branch_id,
                "parent_branch_id": parent_branch_id,
                "rng_id": rng_id,
                "error": str(exc),
                "fault_kind": FAULT_TASK_TIMEOUT,
            }
        result = results[0]
        if result.status != "success":
            book.status = STATUS_FAULTED
            diagnostics = result.diagnostics or {}
            return {
                "status": STATUS_FAULTED,
                "branch_id": branch_id,
                "parent_branch_id": parent_branch_id,
                "rng_id": rng_id,
                "error": diagnostics.get("message", "branch execution faulted"),
                "fault_kind": diagnostics.get("fault_kind", FAULT_EMULATOR_ERROR),
            }

        step = result.step
        new_observation = step.settled_observation
        new_boundary = new_observation["boundary"]
        new_room_context = step.settled_room_context
        book.history.observe_room_context(new_room_context)
        if new_boundary == RUN_TERMINAL:
            terminal_outcome = require_terminal_outcome(
                new_observation.get("outcome"),
                context=f"whole-run branch {branch_id!r}",
                valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES,
            )
            book.outcome = terminal_outcome
            book.terminal = True
            self._decision_points.issue(branch_id)
            return {
                "status": STATUS_COMPLETED,
                "branch_id": branch_id,
                "parent_branch_id": parent_branch_id,
                "rng_id": rng_id,
                "decision_point_id": self._decision_points.current(branch_id),
                "branch_log": branch_log,
                "masked_emulator_dto": build_masked_emulator_dto(
                    {"run_terminal": True, "outcome": book.outcome}
                ),
            }

        new_view = _build_child_view(parent_view, chosen["action_id"], result)
        book.view = new_view
        self._decision_points.issue(branch_id)
        return {
            "status": STATUS_COMPLETED,
            **self._decision_response_fields(
                branch_id,
                new_view,
                branch_log=branch_log,
                history=book.history,
            ),
            "parent_branch_id": parent_branch_id,
            "rng_id": rng_id,
        }
