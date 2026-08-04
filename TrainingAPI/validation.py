"""Request validation for the RL-Training Communication contract v0.5.

Per-operation required-field validation (contract §2.2) plus schema/type checks. This
module never touches Instance state - a request that fails validation here is rejected
("rejected" per contract §2.5/§3, never changes any state) before it ever reaches an
`Instance`.

Design choice (not explicit in v0.5, documented here for transparency): unknown
top-level fields on an otherwise-valid request are tolerated (ignored), not rejected -
this is the conservative forward-compatible reading of a versioned wire contract. An
unknown `operation` value, an unsupported `schema_version`, or a missing/mistyped
required field IS rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from TrainingAPI.dto import (
    COMMON_REQUEST_FIELDS,
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
)


class RequestRejected(Exception):
    """Raised for any request that must be answered with status="rejected".

    Carries a human-readable `error` string, and OPTIONALLY a machine-checkable
    `fault_kind` (contract §2.3 lists `fault_kind` under "失敗時に返す項目" generally,
    not only for `faulted` - e.g. `rng_hypothesis_unsupported_at_boundary` is a
    `rejected`-status response with a `fault_kind` set, per RL担当指示：Active Event
    RNG Hypothesis実装 §5). `fault_kind` is None for ordinary malformed/invalid
    requests where no machine classification is meaningful.
    """

    def __init__(self, error: str, *, fault_kind: "str | None" = None) -> None:
        super().__init__(error)
        self.fault_kind = fault_kind
        self.error = error


def _require(payload: dict, field: str, expected_type: type, *, allow_none: bool = False) -> Any:
    if field not in payload:
        raise RequestRejected(f"missing required field {field!r}")
    value = payload[field]
    if value is None:
        if allow_none:
            return value
        raise RequestRejected(f"field {field!r} must not be null")
    if expected_type is int and isinstance(value, bool):
        raise RequestRejected(f"field {field!r} must be an integer, got bool")
    if not isinstance(value, expected_type):
        raise RequestRejected(f"field {field!r} must be of type {expected_type.__name__}, got {type(value).__name__}")
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
        if expected is None:
            continue  # unknown simulation_options key: tolerated, ignored downstream.
        if value is not None and not isinstance(value, expected):
            raise RequestRejected(f"simulation_options.{key!r} must be of type {expected.__name__}")
    stop_condition = options.get("stop_condition")
    if stop_condition is not None and stop_condition not in _SUPPORTED_STOP_CONDITIONS:
        raise RequestRejected(
            f"simulation_options.stop_condition {stop_condition!r} is not supported "
            f"(known: {sorted(_SUPPORTED_STOP_CONDITIONS)})"
        )


def validate_request(payload: Any) -> dict:
    """Validate `payload` against contract §2.1/§2.2. Returns `payload` unchanged (a
    plain dict) on success; raises `RequestRejected` otherwise. Never mutates `payload`.
    """
    if not isinstance(payload, dict):
        raise RequestRejected("request must be a JSON object (dict)")

    schema_version = _require(payload, "schema_version", str)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RequestRejected(f"unsupported schema_version {schema_version!r}")
    _require(payload, "request_id", str)
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
        branch_id = _require(payload, "branch_id", str)
        if branch_id != ROOT_BRANCH_ID:
            raise RequestRejected(f"commit_action.branch_id must be {ROOT_BRANCH_ID!r}, got {branch_id!r}")
        rng_id = _require(payload, "rng_id", int)
        if rng_id != ROOT_RNG_ID:
            raise RequestRejected(f"commit_action.rng_id must be {ROOT_RNG_ID!r}, got {rng_id!r}")
        _require(payload, "decision_point_id", str)
        _require(payload, "action_id", str)

    elif operation == OP_EMULATE_ACTION:
        _require(payload, "parent_branch_id", str)
        _require(payload, "branch_id", str)
        rng_id = _require(payload, "rng_id", int)
        if rng_id <= 0:
            raise RequestRejected(f"emulate_action.rng_id must be a positive integer, got {rng_id!r}")
        _require(payload, "decision_point_id", str)
        _require(payload, "action_id", str)
        _validate_simulation_options(payload)

    elif operation in (OP_CANCEL_BRANCHES, OP_RELEASE_BRANCHES, OP_GET_BRANCH_STATUS):
        branch_ids = _require(payload, "branch_ids", list)
        if not branch_ids:
            raise RequestRejected("branch_ids must be a non-empty list")
        for branch_id in branch_ids:
            if not isinstance(branch_id, str):
                raise RequestRejected("every entry in branch_ids must be a string")

    elif operation == OP_CLOSE_INSTANCE:
        pass

    return payload
