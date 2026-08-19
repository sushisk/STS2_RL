"""Compatibility runner for the split Snapshot Restore test modules.

The former Phase 3C.1 monolith mixed wire fixtures, serialization, restore behavior,
and rejection/failure tests. Those responsibilities now live in:

* ``test_snapshot_wire_contract.py``
* ``test_snapshot_restore.py``
* ``test_snapshot_restore_rejections.py``

This file intentionally contains no tests or schema knowledge. It preserves the old
useful execution property (one fresh Python subprocess per test) and accepts both the
new qualified case names and the former monolith's bare ``--case test_name`` form.
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_TEST_MODULES = (
    "test_snapshot_wire_contract",
    "test_snapshot_restore",
    "test_snapshot_restore_rejections",
)

# Old monolith case names that were intentionally renamed while splitting by concern.
# Most aliases resolve to one coverage-equivalent split test. The old consolidated
# rejection-category case expands to the individual rejection tests that replaced it,
# preserving the old one-process invocation semantics for that manual --case.
_LEGACY_CASE_ALIASES: dict[str, str | tuple[str, ...]] = {
    "test_get_restore_capabilities_hashes":
        "test_snapshot_wire_contract:test_restore_capabilities_match_snapshot_wire_contract",
    "test_object_restore_round_trip":
        "test_snapshot_restore:test_object_restore_round_trip_uses_typed_snapshot",
    "test_pet_object_restore_round_trip":
        "test_snapshot_restore:test_restore_accessors_cover_player_enemies_and_pet",
    "test_json_restore_round_trip":
        "test_snapshot_restore:test_json_restore_round_trip_uses_shared_testkit_serializer",
    "test_legacy_json_example_is_rejected_by_canonical_contract":
        "test_snapshot_wire_contract:test_legacy_json_example_is_rejected_by_current_wire_contract",
    "test_validate_restore_snapshot_json_is_pure":
        "test_snapshot_wire_contract:test_validate_restore_snapshot_json_is_pure_for_valid_and_malformed_wire",
    "test_invalid_json_restore_preserves_session_and_step_still_works":
        "test_snapshot_wire_contract:test_invalid_json_restore_preserves_live_session_and_step_still_works",
    "test_restore_snapshot_json_rejects_invalid_without_prior_validate":
        "test_snapshot_wire_contract:test_restore_snapshot_json_rejects_malformed_wire_without_prior_validate",
    "test_python_snapshot_object_restore_round_trip_uses_existing_dto":
        "test_snapshot_restore:test_object_restore_round_trip_uses_typed_snapshot",
    "test_object_vs_json_restore_equivalent":
        "test_snapshot_restore:test_object_and_json_restore_are_equivalent",
    "test_pet_json_restore_round_trip_matches_object_restore":
        "test_snapshot_restore:test_restore_accessors_cover_player_enemies_and_pet",
    "test_canonical_json_round_trip":
        "test_snapshot_restore:test_canonical_typed_payload_round_trip",
    "test_pet_canonical_json_round_trip":
        "test_snapshot_restore:test_restore_accessors_cover_player_enemies_and_pet",
    "test_combat_history_all_17_entry_types_round_trip_via_json_fixture":
        "test_snapshot_wire_contract:test_combat_history_all_17_entry_types_round_trip_via_wire_fixture",
    "test_validate_restore_snapshot_is_pure":
        "test_snapshot_restore:test_validate_restore_snapshot_is_pure_for_typed_dto",
    "test_no_power_capture_round_trip":
        "test_snapshot_restore:test_object_restore_round_trip_uses_typed_snapshot",
    "test_with_power_capture_round_trip":
        "test_snapshot_restore:test_live_power_capture_round_trip_uses_typed_snapshot",
    "test_power_internal_data_classifications_round_trip_and_reject_via_json_api":
        "test_snapshot_wire_contract:test_power_internal_data_classifications_round_trip_via_wire",
    "test_full_rng_stream_equality_across_round_trip":
        "test_snapshot_restore:test_full_rng_streams_survive_round_trip",
    "test_rejection_categories_via_public_python_api": (
        "test_snapshot_restore_rejections:test_unknown_combat_history_entry_rejected_from_typed_dto",
        "test_snapshot_restore_rejections:test_dangling_combat_history_reference_rejected_from_clr_object_contract",
        "test_snapshot_restore_rejections:test_pending_choice_capture_is_rejected_without_raw_wire_fixture",
        "test_snapshot_restore_rejections:test_unsupported_capture_boundary_rejected_from_typed_dto",
        "test_snapshot_restore_rejections:test_action_continuation_rejected_from_typed_dto",
        "test_snapshot_restore_rejections:test_reference_integrity_rejected_from_typed_dto",
        "test_snapshot_restore_rejections:test_rng_owner_reference_integrity_rejected_from_typed_dto",
        "test_snapshot_restore_rejections:test_unsupported_internal_data_rejected_from_typed_dto",
        "test_snapshot_restore_rejections:test_unknown_schema_version_rejected_from_typed_dto",
    ),
    "test_rejected_restore_preserves_session_and_step_still_works":
        "test_snapshot_restore_rejections:test_rejected_restore_preserves_live_session_and_step_still_works",
    "test_post_teardown_failure_faults_and_all_recovery_paths_clear":
        "test_snapshot_restore_rejections:test_post_teardown_failure_faults_session_and_recovery_paths_clear_it",
}


def _discover_tests() -> dict[str, tuple[str, str]]:
    tests: dict[str, tuple[str, str]] = {}
    for module_name in _TEST_MODULES:
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if name.startswith("test_") and callable(obj):
                qualified = f"{module_name}:{name}"
                tests[qualified] = (module_name, name)
    return tests


def _resolve_case(case_name: str, tests: dict[str, tuple[str, str]]) -> tuple[str, ...]:
    if case_name in tests:
        return (case_name,)

    legacy_target = _LEGACY_CASE_ALIASES.get(case_name)
    if legacy_target is not None:
        targets = (legacy_target,) if isinstance(legacy_target, str) else legacy_target
        missing = [target for target in targets if target not in tests]
        if missing:
            raise ValueError(
                f"legacy snapshot test alias {case_name!r} points at missing {missing!r}"
            )
        return targets

    bare_matches = [
        qualified
        for qualified, (_, test_name) in tests.items()
        if test_name == case_name
    ]
    if len(bare_matches) == 1:
        return (bare_matches[0],)
    if len(bare_matches) > 1:
        raise ValueError(
            f"ambiguous snapshot test case {case_name!r}; use one of {sorted(bare_matches)!r}"
        )
    raise ValueError(f"unknown snapshot test case: {case_name}")


def _run_case(case_name: str) -> int:
    tests = _discover_tests()
    for qualified in _resolve_case(case_name, tests):
        module_name, test_name = tests[qualified]
        getattr(importlib.import_module(module_name), test_name)()
    return 0


def _run_all() -> int:
    tests = sorted(_discover_tests())
    passed: list[str] = []
    failed: list[str] = []

    for qualified in tests:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--case", qualified],
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            passed.append(qualified)
            print(f"PASS {qualified}")
            continue

        failed.append(qualified)
        print(f"FAIL {qualified}")
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="")

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    args = parser.parse_args()

    if args.case:
        try:
            return _run_case(args.case)
        except Exception:  # noqa: BLE001 - test runner must render the full failure
            traceback.print_exc()
            return 1
    return _run_all()


if __name__ == "__main__":
    sys.exit(main())
