"""RL-side independent same-process repro driver for the Quiescent Boundary
nondeterminism fix acceptance check ("RL担当 最終統合指示" step 4).

Mirrors the Emulator-side `qb_repro_driver.py` methodology (declaration-order test
collection via `list(globals().items())`, matching each file's own `main()`) rather than
pytest, since these two files predate pytest adoption here and were the exact harness
the original nondeterminism was observed through. Runs the full test_scenario_v2.py +
test_choice_semantics.py sequence N times in ONE process (one shared GameInstance,
matching the one-GameInstance-per-process constraint), in a given order, and separately
tallies:

  * QuiescentBoundaryViolation-typed failures (the thing under test)
  * the known, accepted WRIGGLER quarantine-reason-change failure
  * any other, unexpected failure (would be a genuine new regression)

Diagnostic-only - does not modify test_scenario_v2.py/test_choice_semantics.py, does not
touch Policy/Heuristic/beam-search/RestoreSnapshot.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[2] / "tests"
_COMBAT_DIR = _TESTS_DIR.parent
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _collect_tests(module) -> list:
    return [obj for name, obj in list(vars(module).items()) if name.startswith("test_") and callable(obj)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--order", choices=["forward", "reverse"], required=True,
                         help="forward = scenario_v2 then choice_semantics; reverse = the opposite")
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    import test_scenario_v2  # noqa: E402
    import test_choice_semantics  # noqa: E402

    scenario_tests = _collect_tests(test_scenario_v2)
    choice_tests = _collect_tests(test_choice_semantics)
    assert len(scenario_tests) == 32, f"expected 32 test_scenario_v2 tests, got {len(scenario_tests)}"
    assert len(choice_tests) == 20, f"expected 20 test_choice_semantics tests, got {len(choice_tests)}"

    ordered = (scenario_tests + choice_tests) if args.order == "forward" else (choice_tests + scenario_tests)

    total = 0
    qb_violations = []  # (iteration, test_name, message)
    wriggler_failures = []  # (iteration, message) - known/accepted
    other_failures = []  # (iteration, test_name, exc_type, message) - would be new/unexpected

    QuiescentBoundaryViolationException = None
    try:
        from live_combat_session import _quiescent_exception_type
        QuiescentBoundaryViolationException = _quiescent_exception_type()
    except Exception:  # noqa: BLE001
        pass

    start = time.time()
    for iteration in range(1, args.iterations + 1):
        for test in ordered:
            total += 1
            try:
                test()
            except Exception as exc:  # noqa: BLE001
                type_name = type(exc).__name__
                is_qb = (QuiescentBoundaryViolationException is not None and isinstance(exc, QuiescentBoundaryViolationException)) \
                    or "QuiescentBoundaryViolation" in type_name
                if is_qb:
                    qb_violations.append((iteration, test.__name__, str(exc)[:200]))
                elif test.__name__ == "test_wriggler_missing_slot_without_encounter_is_detected":
                    wriggler_failures.append((iteration, str(exc)[:200]))
                else:
                    other_failures.append((iteration, test.__name__, type_name, str(exc)[:200]))
        if iteration % 10 == 0:
            elapsed = time.time() - start
            print(f"  ... iteration {iteration}/{args.iterations} done ({elapsed:.1f}s elapsed, "
                  f"{total} test executions, {len(qb_violations)} QB violations, "
                  f"{len(wriggler_failures)} wriggler, {len(other_failures)} other)")

    elapsed = time.time() - start
    print(f"\n=== order={args.order} iterations={args.iterations} ===")
    print(f"total test executions: {total}")
    print(f"elapsed: {elapsed:.1f}s")
    print(f"QuiescentBoundaryViolation count: {len(qb_violations)}")
    for it, name, msg in qb_violations[:10]:
        print(f"  iter={it} test={name}: {msg}")
    print(f"WRIGGLER (known/accepted) failure count: {len(wriggler_failures)}")
    for it, msg in wriggler_failures[:5]:
        print(f"  iter={it}: {msg}")
    print(f"OTHER (unexpected, new) failure count: {len(other_failures)}")
    for it, name, type_name, msg in other_failures[:20]:
        print(f"  iter={it} test={name} ({type_name}): {msg}")

    print(f"\nRESULT: qb_violations={len(qb_violations)} wriggler={len(wriggler_failures)} other={len(other_failures)}")
    return 0 if (len(qb_violations) == 0 and len(other_failures) == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
