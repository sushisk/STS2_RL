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
from collections import OrderedDict
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

DEFAULT_REPLAY_CACHE_ENTRIES = 1024


class RLApiServer:
    def __init__(
        self,
        *,
        instance_factory_kwargs: "dict | None" = None,
        replay_cache_entries: int = DEFAULT_REPLAY_CACHE_ENTRIES,
    ) -> None:
        if replay_cache_entries <= 0:
            raise ValueError("replay_cache_entries must be positive")

        self._instances: dict[str, Any] = {}
        self._ledgers: dict[str, RequestLedger] = {}
        # start_instance has no instance-scoped ledger yet. Keep a bounded server-wide
        # replay window so a lost start response can be retried with the same request id
        # without retaining every initial decision DTO for the lifetime of the process.
        self._pre_instance_ledger = RequestLedger(
            max_completed_entries=replay_cache_entries
        )
        # After close_instance succeeds, retain only that close request/response as a
        # bounded tombstone. The instance's full RequestLedger may contain large
        # emulator responses and must be released with the closed instance.
        self._closed_ledgers: OrderedDict[str, RequestLedger] = OrderedDict()
        self._replay_cache_entries = replay_cache_entries
        self._instance_serial = itertools.count(1)
        self._kwargs = instance_factory_kwargs or {}

    def instance_count(self) -> int:
        return len(self._instances)

    def close_all(self) -> None:
        for instance in list(self._instances.values()):
            instance.close()
        self._instances.clear()
        self._ledgers.clear()
        self._closed_ledgers.clear()

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
                if operation == OP_CLOSE_INSTANCE:
                    closed_ledger = self._closed_ledgers.get(instance_id)
                    if closed_ledger is not None:
                        cached = closed_ledger.replay(payload)
                        if cached is not None:
                            self._closed_ledgers.move_to_end(instance_id)
                            return cached
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
                self._remember_close_tombstone(instance_id, payload, response)
            return response
        except RequestRejected as exc:
            return self._rejected(payload, exc.error, fault_kind=exc.fault_kind)

    def _remember_close_tombstone(
        self,
        instance_id: str,
        payload: dict,
        response: dict,
    ) -> None:
        tombstone = RequestLedger(max_completed_entries=1)
        tombstone.complete(payload, response)
        self._closed_ledgers[instance_id] = tombstone
        self._closed_ledgers.move_to_end(instance_id)
        while len(self._closed_ledgers) > self._replay_cache_entries:
            self._closed_ledgers.popitem(last=False)

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
        wrapped = {
            "schema_version": SCHEMA_VERSION,
            "request_id": payload["request_id"],
            "operation": payload["operation"],
            **response,
        }
        # Contract §2.3: an Instance対象Request's Response必ずRequestと同じinstance_idを含む。
        # `start_instance`はresponse側(RL発行のinstance_id)がそのまま優先される - この分岐は
        # 個々のInstanceメソッドがinstance_idを積み忘れても壊れない安全網。
        if "instance_id" not in wrapped and payload.get("instance_id") is not None:
            wrapped["instance_id"] = payload["instance_id"]
        return wrapped

    @staticmethod
    def _rejected(payload: dict, error: str, *, fault_kind: "str | None" = None) -> dict:
        response = {
            "schema_version": SCHEMA_VERSION,
            "request_id": payload.get("request_id"),
            "operation": payload.get("operation"),
            "status": STATUS_REJECTED,
            "error": error,
            "fault_kind": fault_kind,
        }
        if payload.get("instance_id") is not None:
            response["instance_id"] = payload["instance_id"]
        return response
