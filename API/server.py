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
    STATUS_FAULTED,
    STATUS_REJECTED,
)
from API.faults import fault_response
from API.identifiers import RequestLedger
from API.validation import RequestRejected, validate_request

DEFAULT_REPLAY_CACHE_ENTRIES = 1024
UNKNOWN_INSTANCE_FAULT_KIND = "unknown_instance"


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
        self._pre_instance_ledger = RequestLedger()
        self._start_request_ids_by_instance: dict[str, str] = {}
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
        self._pre_instance_ledger.clear()
        self._start_request_ids_by_instance.clear()
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

                try:
                    response = self._handle_start_instance(payload)
                    response = self._wrap(payload, response)
                except RequestRejected as exc:
                    response = self._rejected(
                        payload,
                        exc.error,
                        fault_kind=exc.fault_kind,
                    )
                except Exception as exc:
                    response = fault_response(payload, exc)

                self._pre_instance_ledger.complete(payload, response)
                if response.get("status") == STATUS_COMPLETED:
                    instance_id = response.get("instance_id")
                    if isinstance(instance_id, str) and instance_id:
                        self._start_request_ids_by_instance[instance_id] = payload[
                            "request_id"
                        ]
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
                raise RequestRejected(
                    f"unknown instance_id {instance_id!r}",
                    fault_kind=UNKNOWN_INSTANCE_FAULT_KIND,
                )

            ledger = self._ledgers[instance_id]
            cached = ledger.begin(payload)
            if cached is not None:
                return cached

            try:
                response = self._dispatch(instance, operation, payload)
                response = self._wrap(payload, response)
                if (
                    operation == OP_CLOSE_INSTANCE
                    and response.get("status") == STATUS_COMPLETED
                ):
                    instance.close()
            except RequestRejected as exc:
                response = self._rejected(
                    payload,
                    exc.error,
                    fault_kind=exc.fault_kind,
                )
            except Exception as exc:
                response = fault_response(payload, exc)

            ledger.complete(payload, response)

            if operation == OP_CLOSE_INSTANCE and response.get("status") in {
                STATUS_COMPLETED,
                STATUS_FAULTED,
            }:
                # close() may partially release resources before raising. A terminal
                # close fault therefore quarantines the instance instead of returning
                # it to normal service. Keep only the close replay record so same-ID
                # retries remain deterministic without retaining the full history.
                self._instances.pop(instance_id, None)
                self._ledgers.pop(instance_id, None)
                start_request_id = self._start_request_ids_by_instance.pop(
                    instance_id,
                    None,
                )
                if start_request_id is not None:
                    self._pre_instance_ledger.discard(start_request_id)
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
        try:
            return instance.start_instance_response()
        except Exception:
            try:
                instance.close()
            except Exception:
                pass
            self._instances.pop(instance_id, None)
            self._ledgers.pop(instance_id, None)
            raise

    @staticmethod
    def _wrap(payload: dict, response: dict) -> dict:
        wrapped = {
            "schema_version": SCHEMA_VERSION,
            "request_id": payload["request_id"],
            "operation": payload["operation"],
            **response,
        }
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
