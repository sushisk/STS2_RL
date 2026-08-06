"""RL-side request dispatcher: routes a validated Request dict to the right `Instance`
(Combat or Whole Run) and operation, wrapping everything in the common Response shape.

This is the ONLY place that talks to both `instance_combat.py` and
`instance_whole_run.py` - `api_runtime.py`'s server process owns exactly one
`RLApiServer`, which owns zero or more `Instance`s (one CLR `GameInstance` each, safe
because each `Instance` spawns its own separate Branch Worker OS processes and never
constructs a second `GameInstance` in ITS OWN process - see the module docstrings on
`instance_combat.py`/`instance_whole_run.py`).
"""

from __future__ import annotations

import itertools
from typing import Any

from API.dto import (
    INSTANCE_TYPE_COMBAT,
    INSTANCE_TYPE_WHOLE_RUN,
    OP_CANCEL_BRANCHES,
    OP_CLOSE_INSTANCE,
    OP_COMMIT_ACTION,
    OP_EMULATE_ACTION,
    OP_GET_BRANCH_STATUS,
    OP_GET_DECISION,
    OP_RELEASE_BRANCHES,
    OP_START_INSTANCE,
    SCHEMA_VERSION,
    STATUS_COMPLETED,
    STATUS_REJECTED,
)
from API.identifiers import RequestLedger
from API.validation import RequestRejected, validate_request


class RLApiServer:
    def __init__(self, *, instance_factory_kwargs: "dict | None" = None) -> None:
        self._instances: dict[str, Any] = {}
        self._ledgers: dict[str, RequestLedger] = {}
        self._pre_instance_ledger = RequestLedger()
        self._instance_serial = itertools.count(1)
        self._kwargs = instance_factory_kwargs or {}

    def instance_count(self) -> int:
        return len(self._instances)

    def close_all(self) -> None:
        for instance in list(self._instances.values()):
            instance.close()
        self._instances.clear()

    def handle_request(self, payload: dict) -> dict:
        try:
            validate_request(payload)
        except RequestRejected as exc:
            return self._rejected(payload, exc.error, fault_kind=exc.fault_kind)

        operation = payload["operation"]
        try:
            if operation == OP_START_INSTANCE:
                cached = self._pre_instance_ledger.begin(payload)
                if cached is not None:
                    return cached
                response = self._handle_start_instance(payload)
                response = self._wrap(payload, response)
                self._pre_instance_ledger.complete(payload, response)
                return response

            instance_id = payload["instance_id"]
            instance = self._instances.get(instance_id)
            if instance is None:
                raise RequestRejected(f"unknown instance_id {instance_id!r}")
            ledger = self._ledgers[instance_id]
            cached = ledger.begin(payload)
            if cached is not None:
                return cached

            response = self._dispatch(instance, operation, payload)
            response = self._wrap(payload, response)
            ledger.complete(payload, response)

            if operation == OP_CLOSE_INSTANCE:
                instance.close()
                del self._instances[instance_id]
                del self._ledgers[instance_id]
            return response
        except RequestRejected as exc:
            return self._rejected(payload, exc.error, fault_kind=exc.fault_kind)

    def _dispatch(self, instance: Any, operation: str, payload: dict) -> dict:
        if operation == OP_GET_DECISION:
            return instance.get_decision(payload["branch_id"])
        if operation == OP_COMMIT_ACTION:
            return instance.commit_action(payload["decision_point_id"], payload["action_id"])
        if operation == OP_EMULATE_ACTION:
            return instance.emulate_action(
                parent_branch_id=payload["parent_branch_id"],
                branch_id=payload["branch_id"],
                rng_id=payload["rng_id"],
                decision_point_id=payload["decision_point_id"],
                action_id=payload["action_id"],
                simulation_options=payload.get("simulation_options"),
            )
        if operation == OP_CANCEL_BRANCHES:
            return instance.cancel_branches(payload["branch_ids"])
        if operation == OP_RELEASE_BRANCHES:
            return instance.release_branches(payload["branch_ids"])
        if operation == OP_GET_BRANCH_STATUS:
            return instance.get_branch_status(payload["branch_ids"])
        if operation == OP_CLOSE_INSTANCE:
            return {"status": STATUS_COMPLETED}
        raise RequestRejected(f"unhandled operation {operation!r}")

    def _handle_start_instance(self, payload: dict) -> dict:
        instance_config = payload["instance_config"]
        instance_type = instance_config.get("instance_type")
        instance_id = f"inst-{next(self._instance_serial):06d}"
        if instance_type == INSTANCE_TYPE_COMBAT:
            from API.instance_combat import CombatInstance

            instance = CombatInstance(instance_id, instance_config, **self._kwargs.get(INSTANCE_TYPE_COMBAT, {}))
        elif instance_type == INSTANCE_TYPE_WHOLE_RUN:
            from API.instance_whole_run import WholeRunInstance

            instance = WholeRunInstance(instance_id, instance_config, **self._kwargs.get(INSTANCE_TYPE_WHOLE_RUN, {}))
        else:
            raise RequestRejected(f"unknown instance_config.instance_type {instance_type!r}")

        self._instances[instance_id] = instance
        self._ledgers[instance_id] = RequestLedger()
        return instance.start_instance_response()

    @staticmethod
    def _wrap(payload: dict, response: dict) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": payload["request_id"],
            "operation": payload["operation"],
            **response,
        }

    @staticmethod
    def _rejected(payload: dict, error: str, *, fault_kind: "str | None" = None) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": payload.get("request_id"),
            "operation": payload.get("operation"),
            "status": STATUS_REJECTED,
            "error": error,
            "fault_kind": fault_kind,
        }
