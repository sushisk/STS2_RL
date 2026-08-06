import inspect
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import API.dto as dto
from API.identifiers import BranchIdRegistry, DecisionPointRegistry, RequestLedger
from API.validation import RequestRejected, validate_request


def _dto_value(*names, default):
    for name in names:
        if hasattr(dto, name):
            value = getattr(dto, name)
            if isinstance(value, str):
                return value
    return default


def _operation(name):
    operations = getattr(dto, "OPERATIONS", None)
    if isinstance(operations, dict):
        if name in operations:
            return operations[name]
        for key, value in operations.items():
            if key == name or value == name:
                return value
    elif isinstance(operations, (tuple, list, set)) and name in operations:
        return name

    candidates = [
        f"OP_{name.upper()}",
        name.upper(),
        name,
    ]
    for candidate in candidates:
        if hasattr(dto, candidate):
            value = getattr(dto, candidate)
            if isinstance(value, str):
                return value
    return name


def _field(name):
    # Wire field names are plain literal strings per the contract (schema_version,
    # request_id, branch_id, ...) - NOT resolved through API.dto constants
    # (those hold VALUES like dto.SCHEMA_VERSION == "0.5", not field NAMES; routing
    # this through `_dto_value` was a bug that silently produced payload dicts keyed
    # by "0.5" instead of "schema_version").
    return name


def _base_request(operation, *, include_instance_id=True, **overrides):
    payload = {
        _field("schema_version"): _dto_value("SCHEMA_VERSION", default="0.5"),
        _field("operation"): _operation(operation),
        _field("request_id"): "request-1",
    }
    if include_instance_id:
        payload[_field("instance_id")] = "instance-1"
    payload.update(overrides)
    return payload


def _assert_rejected(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except RequestRejected:
        return
    raise AssertionError("expected RequestRejected")


def _assert_valid(payload):
    result = validate_request(payload)
    assert result == payload


def _assert_invalid(payload):
    _assert_rejected(validate_request, payload)


def _make_simulation_options(**overrides):
    options = {
        _field("stop_condition"): "next_decision",
        _field("max_depth"): 3,
        _field("max_steps"): 10,
        _field("max_time_ms"): 1000,
        _field("max_hypotheses"): 4,
    }
    options.update(overrides)
    return options


def test_start_instance_required_fields():
    payload = _base_request(
        "start_instance",
        include_instance_id=False,
        **{
            _field("instance_config"): {
                _field("instance_type"): "demo",
            }
        },
    )
    _assert_valid(payload)

    missing_instance_type = _base_request(
        "start_instance",
        include_instance_id=False,
        **{
            _field("instance_config"): {},
        },
    )
    _assert_invalid(missing_instance_type)


def test_get_decision_requires_branch_id_and_instance_id():
    payload = _base_request(
        "get_decision",
        **{
            _field("branch_id"): "branch-1",
        },
    )
    _assert_valid(payload)

    missing_branch = dict(payload)
    del missing_branch[_field("branch_id")]
    _assert_invalid(missing_branch)

    missing_instance = _base_request(
        "get_decision",
        include_instance_id=False,
        **{
            _field("branch_id"): "branch-1",
        },
    )
    _assert_invalid(missing_instance)


def test_commit_action_required_fields_and_constraints():
    valid = _base_request(
        "commit_action",
        **{
            _field("branch_id"): "root",
            _field("rng_id"): 0,
            _field("decision_point_id"): "dp-1",
            _field("action_id"): "action-1",
        },
    )
    _assert_valid(valid)

    wrong_branch = dict(valid)
    wrong_branch[_field("branch_id")] = "child-1"
    _assert_invalid(wrong_branch)

    wrong_rng = dict(valid)
    wrong_rng[_field("rng_id")] = 1
    _assert_invalid(wrong_rng)

    missing_decision_point = dict(valid)
    del missing_decision_point[_field("decision_point_id")]
    _assert_invalid(missing_decision_point)

    missing_action = dict(valid)
    del missing_action[_field("action_id")]
    _assert_invalid(missing_action)


def test_emulate_action_required_fields_and_rng_constraint():
    valid = _base_request(
        "emulate_action",
        **{
            _field("parent_branch_id"): "root",
            _field("branch_id"): "branch-2",
            _field("rng_id"): 1,
            _field("decision_point_id"): "dp-2",
            _field("action_id"): "action-2",
        },
    )
    _assert_valid(valid)

    bad_rng = dict(valid)
    bad_rng[_field("rng_id")] = 0
    _assert_invalid(bad_rng)

    bad_rng2 = dict(valid)
    bad_rng2[_field("rng_id")] = -1
    _assert_invalid(bad_rng2)

    missing_parent = dict(valid)
    del missing_parent[_field("parent_branch_id")]
    _assert_invalid(missing_parent)


def test_branch_list_operations_require_non_empty_list_of_strings():
    for operation in ("cancel_branches", "release_branches", "get_branch_status"):
        payload = _base_request(
            operation,
            **{
                _field("branch_ids"): ["branch-1", "branch-2"],
            },
        )
        _assert_valid(payload)

        empty = dict(payload)
        empty[_field("branch_ids")] = []
        _assert_invalid(empty)

        wrong_type = dict(payload)
        wrong_type[_field("branch_ids")] = "branch-1"
        _assert_invalid(wrong_type)


def test_close_instance_requires_only_common_fields():
    payload = _base_request("close_instance")
    _assert_valid(payload)


def test_unknown_operation_value_is_rejected():
    payload = _base_request("totally_not_an_operation")
    _assert_invalid(payload)


def test_unsupported_schema_version_is_rejected():
    payload = _base_request("close_instance")
    payload[_field("schema_version")] = "9.9"
    _assert_invalid(payload)


def test_missing_instance_id_is_rejected_for_non_start_instance_operations():
    for operation in (
        "get_decision",
        "commit_action",
        "emulate_action",
        "cancel_branches",
        "release_branches",
        "get_branch_status",
        "close_instance",
    ):
        payload = _base_request(operation)
        payload.pop(_field("instance_id"), None)
        _assert_invalid(payload)


def test_wrong_field_types_are_rejected():
    rng_payload = _base_request(
        "commit_action",
        **{
            _field("branch_id"): "root",
            _field("rng_id"): "0",
            _field("decision_point_id"): "dp-1",
            _field("action_id"): "action-1",
        },
    )
    _assert_invalid(rng_payload)

    branch_ids_payload = _base_request(
        "cancel_branches",
        **{
            _field("branch_ids"): "branch-1",
        },
    )
    _assert_invalid(branch_ids_payload)

    request_id_payload = _base_request(
        "close_instance",
    )
    request_id_payload[_field("request_id")] = 123
    _assert_invalid(request_id_payload)


def test_unknown_extra_top_level_field_is_tolerated():
    payload = _base_request(
        "close_instance",
    )
    payload["unknown_extra_field"] = {"kept": True}
    _assert_valid(payload)


def _emulate_action_base(**simulation_options_overrides):
    # simulation_options is validated only for emulate_action (validation.py's
    # `_validate_simulation_options` is called exclusively from that branch) - a
    # close_instance payload never exercises this check at all.
    return _base_request(
        "emulate_action",
        **{
            _field("parent_branch_id"): "root",
            _field("branch_id"): "branch-3",
            _field("rng_id"): 1,
            _field("decision_point_id"): "dp-3",
            _field("action_id"): "action-3",
            _field("simulation_options"): _make_simulation_options(**simulation_options_overrides),
        },
    )


def test_simulation_options_validation():
    valid = _emulate_action_base()
    _assert_valid(valid)

    bad_stop_condition = _emulate_action_base(**{_field("stop_condition"): "not-supported"})
    _assert_invalid(bad_stop_condition)

    for key in ("max_depth", "max_steps", "max_time_ms", "max_hypotheses"):
        payload = _emulate_action_base(**{_field(key): "bad-type"})
        _assert_invalid(payload)


def _ledger_begin(ledger, request_id, payload):
    begin = getattr(ledger, "begin")
    sig = inspect.signature(begin)
    argc = len(sig.parameters)
    if argc == 2:
        return begin(request_id, payload)
    if argc == 1:
        return begin(payload)
    raise AssertionError(f"unsupported RequestLedger.begin signature: {sig}")


def _ledger_complete(ledger, begin_result, response, request_id, payload):
    if hasattr(begin_result, "complete"):
        complete = begin_result.complete
        sig = inspect.signature(complete)
        argc = len(sig.parameters)
        if argc == 1:
            return complete(response)
        if argc == 2:
            return complete(request_id, response)
        if argc == 3:
            return complete(request_id, payload, response)
        raise AssertionError(f"unsupported completion signature: {sig}")

    if hasattr(ledger, "complete"):
        complete = ledger.complete
        sig = inspect.signature(complete)
        argc = len(sig.parameters)
        if argc == 2:
            return complete(request_id, response)
        if argc == 3:
            return complete(request_id, payload, response)
        raise AssertionError(f"unsupported RequestLedger.complete signature: {sig}")

    raise AssertionError("RequestLedger exposes no complete method")


def _ledger_result_value(result):
    for attr in ("response", "cached_response", "value", "result", "output"):
        if hasattr(result, attr):
            return getattr(result, attr)
    return result


def test_request_ledger_caches_identical_request_content():
    ledger = RequestLedger()
    request_id = "request-1"
    payload = {"request_id": request_id, "alpha": 1, "beta": {"x": 2}}
    response = {"ok": True}

    begin_result = _ledger_begin(ledger, request_id, payload)
    assert begin_result is None, "a brand-new request_id must have no cached response yet"
    ledger.complete(payload, response)

    cached = _ledger_begin(ledger, request_id, payload)
    assert _ledger_result_value(cached) == response


def test_request_ledger_rejects_same_request_id_with_different_content():
    ledger = RequestLedger()
    request_id = "request-1"
    payload = {"request_id": request_id, "alpha": 1}
    other_payload = {"request_id": request_id, "alpha": 2}
    response = {"ok": True}

    begin_result = _ledger_begin(ledger, request_id, payload)
    ledger.complete(payload, response)

    _assert_rejected(_ledger_begin, ledger, request_id, other_payload)


def _registry_register(registry, branch_id):
    for method_name in ("register", "claim", "add", "reserve"):
        if hasattr(registry, method_name):
            return getattr(registry, method_name)(branch_id)
    raise AssertionError("BranchIdRegistry exposes no register-like method")


def test_branch_id_registry_rejects_duplicate_and_reserves_root():
    registry = BranchIdRegistry()
    _assert_rejected(_registry_register, registry, "root")

    registry = BranchIdRegistry()
    _registry_register(registry, "branch-1")
    _assert_rejected(_registry_register, registry, "branch-1")


def test_decision_point_registry_issue_and_validation():
    registry = DecisionPointRegistry()
    branch_id = "branch-1"
    decision_point_id = registry.issue(branch_id)
    registry.validate(branch_id, decision_point_id)

    _assert_rejected(registry.validate, branch_id, "some-other-id")
    _assert_rejected(registry.validate, "unknown-branch", decision_point_id)


def _run_all():
    tests = [
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and inspect.isfunction(fn)
    ]
    tests.sort(key=lambda item: item[0])

    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            print(f"PASS {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
