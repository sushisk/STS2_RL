"""RL-side dispatcher for session-sequenced API DTO v0.6."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

from API.dto import (
    FAULT_INVALID_REQUEST,
    FAULT_SESSION_CAPACITY,
    FAULT_SESSION_INSTANCE_CONFLICT,
    FAULT_SESSION_SEQUENCE_GAP,
    FAULT_UNKNOWN_INSTANCE,
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
    STATUSES,
    STATUS_COMPLETED,
    STATUS_FAULTED,
    STATUS_REJECTED,
)
from API.faults import fault_response
from API.identifiers import SessionLedger
from API.validation import RequestRejected, validate_request

DEFAULT_MAX_SESSIONS = 4096


class RLApiServer:
    """Execute one strictly ordered request stream per logical Training session.

    Each session retains only its last executable request and terminal response. A retry
    of the same sequence number is replayed verbatim; a different payload with the same
    sequence is rejected; the next new request must use exactly ``last_seq + 1``.

    The guarantee is scoped to ``server_epoch``. Restarting RL changes the epoch and
    intentionally invalidates all Training sessions instead of pretending an in-memory
    replay ledger survived a process crash.
    """

    def __init__(
        self,
        *,
        instance_factory_kwargs: dict | None = None,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        server_epoch: str | None = None,
    ) -> None:
        if max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self.server_epoch = server_epoch or str(uuid.uuid4())
        self._max_sessions = max_sessions
        self._sessions: dict[str, SessionLedger] = {}
        self._instances: dict[str, Any] = {}
        self._instance_serial = itertools.count(1)
        self._kwargs = instance_factory_kwargs or {}

    def instance_count(self) -> int:
        return len(self._instances)

    def session_count(self) -> int:
        return len(self._sessions)

    def close_all(self) -> None:
        for instance in list(self._instances.values()):
            try:
                instance.close()
            except Exception:
                pass
        self._instances.clear()
        self._sessions.clear()

    def handle_request(self, payload: dict) -> dict:
        try:
            validate_request(payload)
        except RequestRejected as exc:
            return self._rejected(
                payload,
                exc.error,
                fault_kind=exc.fault_kind or FAULT_INVALID_REQUEST,
            )

        session_id = payload["client_session_id"]
        try:
            ledger = self._get_or_create_session(session_id, payload["request_seq"])
            cached = ledger.begin(payload)
            if cached is not None:
                return cached

            try:
                response = self._execute_new_request(ledger, payload)
            except RequestRejected as exc:
                response = {
                    "status": STATUS_REJECTED,
                    "error": exc.error,
                    "fault_kind": exc.fault_kind,
                }
            except Exception as exc:
                response = fault_response(payload, exc)

            status = response.get("status") if isinstance(response, dict) else None
            if not isinstance(response, dict) or not isinstance(status, str) or status not in STATUSES:
                response = fault_response(
                    payload,
                    TypeError(f"RL handler returned invalid response status {status!r}"),
                )
            response = self._wrap(payload, response)
            ledger.complete(response)
            return response
        except RequestRejected as exc:
            # Session capacity/sequence errors happen before the new sequence becomes an
            # executable request and therefore are not written into the session ledger.
            return self._rejected(payload, exc.error, fault_kind=exc.fault_kind)

    def _get_or_create_session(self, session_id: str, request_seq: int) -> SessionLedger:
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing
        if request_seq != 1:
            raise RequestRejected(
                f"request_seq must be 1 for a new session, got {request_seq}",
                fault_kind=FAULT_SESSION_SEQUENCE_GAP,
            )
        if len(self._sessions) >= self._max_sessions:
            raise RequestRejected(
                "RL session capacity exhausted; restart RL or reuse an existing client session",
                fault_kind=FAULT_SESSION_CAPACITY,
            )
        ledger = SessionLedger()
        self._sessions[session_id] = ledger
        return ledger

    def _execute_new_request(self, ledger: SessionLedger, payload: dict) -> dict:
        operation = payload["operation"]

        if operation == OP_START_INSTANCE:
            if ledger.active_instance_id is not None:
                raise RequestRejected(
                    f"session already owns active instance {ledger.active_instance_id!r}",
                    fault_kind=FAULT_SESSION_INSTANCE_CONFLICT,
                )
            response = self._handle_start_instance(payload)
            instance_id = response.get("instance_id")
            if isinstance(instance_id, str) and instance_id:
                ledger.active_instance_id = instance_id
            return response

        instance_id = payload["instance_id"]
        if ledger.active_instance_id != instance_id:
            raise RequestRejected(
                f"instance_id {instance_id!r} is not owned by this client session",
                fault_kind=FAULT_SESSION_INSTANCE_CONFLICT,
            )
        instance = self._instances.get(instance_id)
        if instance is None:
            raise RequestRejected(
                f"unknown instance_id {instance_id!r}",
                fault_kind=FAULT_UNKNOWN_INSTANCE,
            )

        if operation == OP_CLOSE_INSTANCE:
            return self._handle_close_instance(ledger, instance_id, instance)
        return self._dispatch(instance, operation, payload)

    def _handle_close_instance(
        self,
        ledger: SessionLedger,
        instance_id: str,
        instance: Any,
    ) -> dict:
        try:
            instance.close()
            response: dict = {"status": STATUS_COMPLETED}
        except Exception as exc:
            fault = fault_response({"instance_id": instance_id}, exc)
            response = {
                "status": STATUS_FAULTED,
                "error": fault["error"],
                "fault_kind": fault["fault_kind"],
            }
        finally:
            self._instances.pop(instance_id, None)
            ledger.active_instance_id = None
        return response

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
        raise RequestRejected(f"unhandled operation {operation!r}")

    def _handle_start_instance(self, payload: dict) -> dict:
        instance_config = payload["instance_config"]
        instance_type = instance_config.get("instance_type")
        instance_id = f"inst-{next(self._instance_serial):06d}"
        if instance_type == INSTANCE_TYPE_COMBAT:
            from API.instance_combat import CombatInstance

            instance = CombatInstance(
                instance_id,
                instance_config,
                **self._kwargs.get(INSTANCE_TYPE_COMBAT, {}),
            )
        elif instance_type == INSTANCE_TYPE_WHOLE_RUN:
            from API.instance_whole_run import WholeRunInstance

            instance = WholeRunInstance(
                instance_id,
                instance_config,
                **self._kwargs.get(INSTANCE_TYPE_WHOLE_RUN, {}),
            )
        else:
            raise RequestRejected(f"unknown instance_config.instance_type {instance_type!r}")

        self._instances[instance_id] = instance
        try:
            response = instance.start_instance_response()
            if not isinstance(response, dict) or response.get("status") != STATUS_COMPLETED:
                raise TypeError("start_instance_response must return a completed response")
            return {**response, "instance_id": instance_id}
        except Exception:
            try:
                instance.close()
            except Exception:
                pass
            self._instances.pop(instance_id, None)
            raise

    def _wrap(self, payload: dict, response: dict) -> dict:
        # Instance implementations own operation-specific fields only. Correlation and
        # transport identity are written last so a buggy lower layer cannot spoof them.
        wrapped = dict(response)
        wrapped.update(
            {
                "schema_version": SCHEMA_VERSION,
                "server_epoch": self.server_epoch,
                "client_session_id": payload.get("client_session_id"),
                "request_seq": payload.get("request_seq"),
                "request_id": payload.get("request_id"),
                "operation": payload.get("operation"),
            }
        )
        if payload.get("instance_id") is not None:
            wrapped["instance_id"] = payload["instance_id"]
        return wrapped

    def _rejected(self, payload: dict, error: str, *, fault_kind: str | None = None) -> dict:
        return self._wrap(
            payload,
            {
                "status": STATUS_REJECTED,
                "error": error,
                "fault_kind": fault_kind,
            },
        )
