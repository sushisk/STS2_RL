"""DTO constants for the RL-Training Communication contract v0.5.

Mirrors `docs/contracts/rl_training_dto_documentation_v0_5.md` section 2. This module
holds only plain constants/shape references - no CLR, no I/O. Every Request/Response
that crosses the wire is a plain JSON-safe dict (never a dataclass instance), matching
the contract's "plain dict" transport requirement; the dataclasses below exist only to
give the allowed field/value sets a single documented source of truth for
`validation.py` and the instance implementations to import.
"""

from __future__ import annotations

SCHEMA_VERSION = "0.5"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

DTO_VERSION = "emulator-fca2f06"
MASK_VERSION = "1.0"

# -- operations ----------------------------------------------------------------------

OP_START_INSTANCE = "start_instance"
OP_GET_DECISION = "get_decision"
OP_COMMIT_ACTION = "commit_action"
OP_EMULATE_ACTION = "emulate_action"
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
        OP_CANCEL_BRANCHES,
        OP_RELEASE_BRANCHES,
        OP_GET_BRANCH_STATUS,
        OP_CLOSE_INSTANCE,
    }
)

# -- status ----------------------------------------------------------------------------

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

# -- fault_kind (§3 "想定値" - not an exhaustive closed set, but these are the ones this
# implementation ever produces) --------------------------------------------------------

FAULT_TASK_TIMEOUT = "task_timeout"
FAULT_WORKER_PROCESS_CRASH = "worker_process_crash"
FAULT_REPLAY_MISMATCH = "replay_mismatch"
FAULT_EMULATOR_ERROR = "emulator_error"
FAULT_SNAPSHOT_RESTORE_FAILED = "snapshot_restore_failed"

# -- instance types --------------------------------------------------------------------

INSTANCE_TYPE_WHOLE_RUN = "whole_run"
INSTANCE_TYPE_COMBAT = "combat"
INSTANCE_TYPES = frozenset({INSTANCE_TYPE_WHOLE_RUN, INSTANCE_TYPE_COMBAT})

ROOT_BRANCH_ID = "root"
ROOT_RNG_ID = 0

# -- common request/response field names ------------------------------------------------

COMMON_REQUEST_FIELDS = ("schema_version", "request_id", "operation", "instance_id")
COMMON_RESPONSE_ALWAYS_FIELDS = ("schema_version", "request_id", "operation", "status")
