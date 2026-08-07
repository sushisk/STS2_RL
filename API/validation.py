"""Request validation for the RL/Training Communication contract v0.6."""

from __future__ import annotations

from typing import Any

from API.dto import (
    OP_CANCEL_BRANCHES,
    OP_CLOSE_INSTANCE,
    OP_COMMIT_ACTION,
    OP_EMULATE_ACTION,
    OP_GET_BRANCH_STATUS,
    OP_GET_DECISION,
    OP_RELEASE_BRANCHES,
    OP_START_INSTANCE,
    OPERATIONS,
    ROOT_BRANCH_ID,
    ROOT_RNG_ID,
    SUPPORTED_SCHEMA_VERSIONS,
    request_id_for,
)


class RequestRejected(Exception):
    def __init__(self, error: str, *, fault_kind: str | None = None) -> None:
        super().__init__(error)
        self.fault_kind = fault_kind
        self.error = error


def _require(payload: dict, field: str, expected_type: type) -> Any:
    if field not in payload:
        raise RequestRejected(f"missing required field {field!r}")
    value = payload[field]
    if expected_type is int and isinstance(value, bool):
        raise RequestRejected(f"field {field!r} must be an integer, got bool")
    if not isinstance(value, expected_type):
        raise RequestRejected(
            f"field {field!r} must be of type {expected_type.__name__}, got {type(value).__name__}"
        )
    if expected_type is str and not value:
        raise RequestRejected(f"field {field!r} must not be empty")
    return value


_OPTIONAL_SIMULATION_OPTION_TYPES = {
    "stop_condition": str,
    "max_depth": int,
    "max_steps": int,
    "max_time_ms": int,
    "max_hypotheses": int,
}
_SUPPORTED_STOP_CONDITIONS = frozenset({"next_decision", "combat_end", "room_end", "run_end"})


def _validate_simulation_options(payload: dict) -> None:
    options = payload.get("simulation_options")
    if options is None:
        return
    if not isinstance(options, dict):
        raise RequestRejected("field 'simulation_options' must be an object")
    for key, value in options.items():
        expected = _OPTIONAL_SIMULATION_OPTION_TYPES.get(key)
        if expected is None or value is None:
            continue
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RequestRejected(
                    f"simulation_options.{key!r} must be a positive integer"
                )
            continue
        if not isinstance(value, expected):
            raise RequestRejected(
                f"simulation_options.{key!r} must be of type {expected.__name__}"
            )
    stop_condition = options.get("stop_condition")
    if stop_condition is not None and stop_condition not in _SUPPORTED_STOP_CONDITIONS:
        raise RequestRejected(
            f"simulation_options.stop_condition {stop_condition!r} is not supported"
        )


def validate_request(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise RequestRejected("request must be a JSON object (dict)")

    schema_version = _require(payload, "schema_version", str)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RequestRejected(f"unsupported schema_version {schema_version!r}")

    session_id = _require(payload, "client_session_id", str)
    request_seq = _require(payload, "request_seq", int)
    if request_seq <= 0:
        raise RequestRejected("request_seq must be a positive integer")
    request_id = _require(payload, "request_id", str)
    expected_request_id = request_id_for(session_id, request_seq)
    if request_id != expected_request_id:
        raise RequestRejected(
            f"request_id must equal {expected_request_id!r} for this session/sequence"
        )

    operation = _require(payload, "operation", str)
    if operation not in OPERATIONS:
        raise RequestRejected(f"unknown operation {operation!r}")

    if operation != OP_START_INSTANCE:
        _require(payload, "instance_id", str)

    if operation == OP_START_INSTANCE:
        instance_config = _require(payload, "instance_config", dict)
        if "instance_type" not in instance_config:
            raise RequestRejected("instance_config.instance_type is required")
    elif operation == OP_GET_DECISION:
        _require(payload, "branch_id", str)
    elif operation == OP_COMMIT_ACTION:
        if _require(payload, "branch_id", str) != ROOT_BRANCH_ID:
            raise RequestRejected(f"commit_action.branch_id must be {ROOT_BRANCH_ID!r}")
        if _require(payload, "rng_id", int) != ROOT_RNG_ID:
            raise RequestRejected(f"commit_action.rng_id must be {ROOT_RNG_ID!r}")
        _require(payload, "decision_point_id", str)
        _require(payload, "action_id", str)
    elif operation == OP_EMULATE_ACTION:
        _require(payload, "parent_branch_id", str)
        _require(payload, "branch_id", str)
        if _require(payload, "rng_id", int) <= 0:
            raise RequestRejected("emulate_action.rng_id must be positive")
        _require(payload, "decision_point_id", str)
        _require(payload, "action_id", str)
        _validate_simulation_options(payload)
    elif operation in (OP_CANCEL_BRANCHES, OP_RELEASE_BRANCHES, OP_GET_BRANCH_STATUS):
        branch_ids = _require(payload, "branch_ids", list)
        if not branch_ids:
            raise RequestRejected("branch_ids must be a non-empty list")
        if any(not isinstance(branch_id, str) or not branch_id for branch_id in branch_ids):
            raise RequestRejected("every entry in branch_ids must be a non-empty string")
    elif operation == OP_CLOSE_INSTANCE:
        pass

    return payload
