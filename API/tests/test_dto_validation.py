from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.dto import SCHEMA_VERSION
from API.identifiers import BranchIdRegistry, DecisionPointRegistry, SessionLedger
from API.validation import RequestRejected, validate_request


def _request(operation: str, *, seq: int = 1, include_instance_id: bool = True, **fields) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "client_session_id": "session-a",
        "request_seq": seq,
        "request_id": f"session-a:{seq}",
        "operation": operation,
        **fields,
    }
    if include_instance_id:
        payload["instance_id"] = "inst-001"
    return payload


def _assert_rejected(payload: dict) -> None:
    try:
        validate_request(payload)
    except RequestRejected:
        return
    raise AssertionError("expected RequestRejected")


def test_wire_schema_is_v08_hard_cutover() -> None:
    assert SCHEMA_VERSION == "0.8"
    legacy = _request("close_instance")
    legacy["schema_version"] = "0.7"
    _assert_rejected(legacy)


def test_start_instance_required_fields() -> None:
    valid = _request(
        "start_instance",
        include_instance_id=False,
        instance_config={"instance_type": "combat"},
    )
    assert validate_request(valid) is valid

    missing_type = _request(
        "start_instance",
        include_instance_id=False,
        instance_config={},
    )
    _assert_rejected(missing_type)

    invalid_god_mode = _request(
        "start_instance",
        include_instance_id=False,
        instance_config={"instance_type": "whole_run", "god_mode": "false"},
    )
    _assert_rejected(invalid_god_mode)


def test_session_identity_fields_are_required_and_correlated() -> None:
    valid = _request("close_instance")
    assert validate_request(valid) is valid

    for field in ("schema_version", "client_session_id", "request_seq", "request_id"):
        broken = dict(valid)
        broken.pop(field)
        _assert_rejected(broken)

    bad_request_id = dict(valid)
    bad_request_id["request_id"] = "arbitrary-id"
    _assert_rejected(bad_request_id)

    for bad_seq in (0, -1, True):
        broken = dict(valid)
        broken["request_seq"] = bad_seq
        broken["request_id"] = f"session-a:{bad_seq}"
        _assert_rejected(broken)


def test_operation_specific_validation() -> None:
    get_decision = _request("get_decision", branch_id="root")
    assert validate_request(get_decision) is get_decision

    commit = _request(
        "commit_action",
        branch_id="root",
        rng_id=0,
        decision_point_id="d-root-1",
        action_id="0",
    )
    assert validate_request(commit) is commit

    emulate = _request(
        "emulate_action",
        parent_branch_id="root",
        branch_id="branch-1",
        rng_id=1,
        decision_point_id="d-root-1",
        action_id="0",
    )
    assert validate_request(emulate) is emulate

    bad_commit = dict(commit)
    bad_commit["branch_id"] = "branch-1"
    _assert_rejected(bad_commit)

    bad_emulate = dict(emulate)
    bad_emulate["rng_id"] = 0
    _assert_rejected(bad_emulate)


def test_branch_batch_operations_require_non_empty_string_ids() -> None:
    for operation in ("cancel_branches", "release_branches", "get_branch_status"):
        valid = _request(operation, branch_ids=["branch-1", "branch-2"])
        assert validate_request(valid) is valid

        empty = dict(valid)
        empty["branch_ids"] = []
        _assert_rejected(empty)

        bad_entry = dict(valid)
        bad_entry["branch_ids"] = ["branch-1", ""]
        _assert_rejected(bad_entry)


def test_simulation_options_validate_known_limits() -> None:
    base = _request(
        "emulate_action",
        parent_branch_id="root",
        branch_id="branch-1",
        rng_id=1,
        decision_point_id="d-root-1",
        action_id="0",
    )
    valid = dict(base)
    valid["simulation_options"] = {
        "stop_condition": "next_decision",
        "max_depth": 1,
        "max_steps": None,
        "future_extension": True,
    }
    assert validate_request(valid) is valid

    for field in ("max_depth", "max_steps", "max_time_ms", "max_hypotheses"):
        for value in (0, -1, True, 1.5):
            broken = dict(base)
            broken["simulation_options"] = {field: value}
            _assert_rejected(broken)

    bad_stop = dict(base)
    bad_stop["simulation_options"] = {"stop_condition": "never"}
    _assert_rejected(bad_stop)


def test_session_ledger_replays_only_exact_latest_request() -> None:
    ledger = SessionLedger()
    request = _request(
        "start_instance",
        include_instance_id=False,
        instance_config={"instance_type": "combat"},
    )
    response = {"status": "completed", "instance_id": "inst-001"}

    assert ledger.begin(request) is None
    ledger.complete(response)
    assert ledger.begin(request) == response

    conflict = dict(request)
    conflict["instance_config"] = {"instance_type": "whole_run"}
    try:
        ledger.begin(conflict)
    except RequestRejected as exc:
        assert exc.fault_kind == "session_sequence_conflict"
    else:
        raise AssertionError("same sequence with different content must be rejected")


def test_session_ledger_rejects_gap_and_inflight_duplicate() -> None:
    ledger = SessionLedger()
    request = _request(
        "start_instance",
        include_instance_id=False,
        instance_config={"instance_type": "combat"},
    )
    assert ledger.begin(request) is None

    try:
        ledger.begin(request)
    except RequestRejected as exc:
        assert exc.fault_kind == "session_sequence_conflict"
    else:
        raise AssertionError("in-flight duplicate must be rejected")

    ledger.complete({"status": "completed"})
    gap = _request("close_instance", seq=3)
    try:
        ledger.begin(gap)
    except RequestRejected as exc:
        assert exc.fault_kind == "session_sequence_gap"
    else:
        raise AssertionError("sequence gap must be rejected")


def test_branch_and_decision_registries_preserve_lifecycle_rules() -> None:
    branches = BranchIdRegistry()
    for branch_id in ("root",):
        try:
            branches.register(branch_id)
        except RequestRejected:
            pass
        else:
            raise AssertionError("root must be reserved")

    branches.register("branch-1")
    try:
        branches.register("branch-1")
    except RequestRejected:
        pass
    else:
        raise AssertionError("branch IDs must never be reusable")

    decisions = DecisionPointRegistry()
    current = decisions.issue("branch-1")
    decisions.validate("branch-1", current)
    try:
        decisions.validate("branch-1", "stale")
    except RequestRejected:
        pass
    else:
        raise AssertionError("stale decision_point_id must be rejected")


def _run_all() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    failures = 0
    for test in sorted(tests, key=lambda fn: fn.__name__):
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
