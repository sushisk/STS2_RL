"""Compatibility runner for the split Snapshot Restore test modules.

The former Phase 3C.1 monolith mixed wire fixtures, serialization, restore behavior,
and rejection/failure tests. Those responsibilities now live in:

* ``test_snapshot_wire_contract.py``
* ``test_snapshot_restore.py``
* ``test_snapshot_restore_rejections.py``

This file intentionally contains no tests or schema knowledge. It only preserves the
old useful execution property: each test case runs in a fresh Python subprocess because
the Emulator runtime owns process-wide singleton state.
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


def _discover_tests() -> dict[str, tuple[str, str]]:
    tests: dict[str, tuple[str, str]] = {}
    for module_name in _TEST_MODULES:
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if name.startswith("test_") and callable(obj):
                qualified = f"{module_name}:{name}"
                tests[qualified] = (module_name, name)
    return tests


def _run_case(qualified: str) -> int:
    tests = _discover_tests()
    if qualified not in tests:
        raise ValueError(f"unknown snapshot test case: {qualified}")
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
