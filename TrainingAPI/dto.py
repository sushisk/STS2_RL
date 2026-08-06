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
#
# Lifecycle reachability differs by instance_type, even though the vocabulary below is
# shared across both:
#   - Combat (`instance_combat.py`, async via `search/branch_manager.py`) can reach every
#     status here, including STATUS_QUEUED/STATUS_RUNNING while a Branch is still being
#     worked by the Branch Worker Pool (`get_decision`/`emulate_action` poll for it).
#   - Whole Run (`instance_whole_run.py`) dispatches synchronously
#     (`WholeRunWorkerPool.dispatch_choice_work_items` blocks until the whole batch is
#     done - see that module's own docstring, "Deliberate scope reductions" item 2), so a
#     Whole Run Branch is never observably STATUS_QUEUED/STATUS_RUNNING from the outside -
#     only STATUS_COMPLETED/STATUS_FAULTED/STATUS_CANCELLED/STATUS_RELEASED are ever
#     produced there.
# Do not change this shared vocabulary (e.g. split it into implementation-specific Enums)
# until Whole Run's own async BranchManager parity question is revisited - see the
# completion note in the report this refactor pass came from.

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
FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY = "rng_hypothesis_unsupported_at_boundary"
"""`rejected`-status fault_kind (not `faulted`) - a positive rng_id was requested at a
Whole Run boundary where RNG Hypothesis generation is not supported (RL担当指示：Active
Event RNG Hypothesis実装 §5). See `RNG_HYPOTHESIS_CAPABILITIES` below for exactly which
boundaries/domains are supported."""

# -- RNG Hypothesis capability declaration (§8: no new public Operation, just a formally
# defined constant/contract + tests) ----------------------------------------------------

RNG_HYPOTHESIS_CAPABILITY_EVENT = "event"
RNG_HYPOTHESIS_CAPABILITY_MAP = "map"
RNG_HYPOTHESIS_CAPABILITY_ENCOUNTER = "encounter"
RNG_HYPOTHESIS_CAPABILITY_BOSS_ANCIENT = "boss_ancient"

RNG_HYPOTHESIS_CAPABILITIES: "dict[str, bool]" = {
    RNG_HYPOTHESIS_CAPABILITY_EVENT: True,
    RNG_HYPOTHESIS_CAPABILITY_MAP: False,
    RNG_HYPOTHESIS_CAPABILITY_ENCOUNTER: False,
    RNG_HYPOTHESIS_CAPABILITY_BOSS_ANCIENT: False,
}
"""Whole Run RNG Hypothesis support by domain. Only `event` (Active Event's own RNG plus
its Reward/Shop/Transformation streams, via the Emulator's `GetEventRngState`/
`SetEventRngState`) is implemented. Map/Encounter/Boss/Ancient/Act-generation Hypothesis
generation is explicitly unsupported - see
`Outputs/reports/rl_whole_run_rng_hypothesis_STOP_20260804.md` for why (the `UpFront`
RNG stream generates an entire Act's rooms/encounters/events/relic-offers in one
upfront draw at Act-generation time with no safely isolable "unconsumed future" boundary
and no purpose-built partial-override Emulator API, unlike the Event case). Combat's own
RNG Hypothesis mechanism (`search.rng_hypothesis`, DrawPile-order belief) is unrelated
and unaffected by this dict."""

# -- instance types --------------------------------------------------------------------

INSTANCE_TYPE_WHOLE_RUN = "whole_run"
INSTANCE_TYPE_COMBAT = "combat"
INSTANCE_TYPES = frozenset({INSTANCE_TYPE_WHOLE_RUN, INSTANCE_TYPE_COMBAT})

ROOT_BRANCH_ID = "root"
ROOT_RNG_ID = 0

# -- common request/response field names ------------------------------------------------

COMMON_REQUEST_FIELDS = ("schema_version", "request_id", "operation", "instance_id")
COMMON_RESPONSE_ALWAYS_FIELDS = ("schema_version", "request_id", "operation", "status")
