"""Wire-level DTO constants for the RL/Training TCP contract v0.7.

v0.7 retains v0.6's session-sequenced, single in-flight protocol and adds the plural
``emulate_actions`` batch operation. Every API request belongs to one
``client_session_id`` and carries a positive, strictly increasing ``request_seq``.
``request_id`` remains on the wire for tracing and exact replay, and is deterministically
derived from those two fields.
"""

from __future__ import annotations

SCHEMA_VERSION = "0.7"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

DTO_VERSION = "emulator-fca2f06"
MASK_VERSION = "1.0"

OP_START_INSTANCE = "start_instance"
OP_GET_DECISION = "get_decision"
OP_COMMIT_ACTION = "commit_action"
OP_EMULATE_ACTION = "emulate_action"
OP_EMULATE_ACTIONS = "emulate_actions"
OP_CANCEL_BRANCHES = "cancel_branches"
OP_RELEASE_BRANCHES = "release_branches"
OP_GET_BRANCH_STATUS = "get_branch_status"
OP_CLOSE_INSTANCE = "close_instance"

OPERATIONS = frozenset(
    {
        OP_START_INSTANCE,
        OP_GET_DECISION,
        OP_COMMIT_ACTION,
        OP_EMULATE_ACTION,
        OP_EMULATE_ACTIONS,
        OP_CANCEL_BRANCHES,
        OP_RELEASE_BRANCHES,
        OP_GET_BRANCH_STATUS,
        OP_CLOSE_INSTANCE,
    }
)

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_CANCELLED = "cancelled"
STATUS_REJECTED = "rejected"
STATUS_FAULTED = "faulted"
STATUS_RELEASED = "released"

STATUSES = frozenset(
    {
        STATUS_COMPLETED,
        STATUS_PARTIAL,
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_CANCELLED,
        STATUS_REJECTED,
        STATUS_FAULTED,
        STATUS_RELEASED,
    }
)
FAILURE_STATUSES = frozenset({STATUS_REJECTED, STATUS_FAULTED})

FAULT_TASK_TIMEOUT = "task_timeout"
FAULT_WORKER_PROCESS_CRASH = "worker_process_crash"
FAULT_REPLAY_MISMATCH = "replay_mismatch"
FAULT_EMULATOR_ERROR = "emulator_error"
FAULT_SNAPSHOT_RESTORE_FAILED = "snapshot_restore_failed"
FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY = "rng_hypothesis_unsupported_at_boundary"
FAULT_INVALID_REQUEST = "invalid_request"
FAULT_UNKNOWN_INSTANCE = "unknown_instance"
FAULT_SESSION_SEQUENCE_CONFLICT = "session_sequence_conflict"
FAULT_SESSION_SEQUENCE_GAP = "session_sequence_gap"
FAULT_SESSION_CAPACITY = "session_capacity_exceeded"
FAULT_SESSION_INSTANCE_CONFLICT = "session_instance_conflict"

RNG_HYPOTHESIS_CAPABILITY_EVENT = "event"
RNG_HYPOTHESIS_CAPABILITY_MAP = "map"
RNG_HYPOTHESIS_CAPABILITY_ENCOUNTER = "encounter"
RNG_HYPOTHESIS_CAPABILITY_BOSS_ANCIENT = "boss_ancient"
RNG_HYPOTHESIS_CAPABILITIES: dict[str, bool] = {
    RNG_HYPOTHESIS_CAPABILITY_EVENT: True,
    RNG_HYPOTHESIS_CAPABILITY_MAP: False,
    RNG_HYPOTHESIS_CAPABILITY_ENCOUNTER: False,
    RNG_HYPOTHESIS_CAPABILITY_BOSS_ANCIENT: False,
}

INSTANCE_TYPE_WHOLE_RUN = "whole_run"
INSTANCE_TYPE_COMBAT = "combat"
INSTANCE_TYPES = frozenset({INSTANCE_TYPE_WHOLE_RUN, INSTANCE_TYPE_COMBAT})

ROOT_BRANCH_ID = "root"
ROOT_RNG_ID = 0

COMMON_REQUEST_FIELDS = (
    "schema_version",
    "client_session_id",
    "request_seq",
    "request_id",
    "operation",
    "instance_id",
)
COMMON_RESPONSE_ALWAYS_FIELDS = (
    "schema_version",
    "server_epoch",
    "client_session_id",
    "request_seq",
    "request_id",
    "operation",
    "status",
)


def request_id_for(client_session_id: str, request_seq: int) -> str:
    return f"{client_session_id}:{request_seq}"
