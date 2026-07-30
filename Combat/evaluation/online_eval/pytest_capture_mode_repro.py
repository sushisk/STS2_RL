"""Phase 3A.4 RL-side independent re-verification: runs
`pytest tests/test_scenario_v2.py tests/test_choice_semantics.py` N times under a given
capture mode (each iteration a fresh subprocess - genuinely independent runs, not a
single looped process), tallying:

  * QuiescentBoundaryViolation mentions
  * ActionFaultedException mentions, split into "rooted in System.IO.IOException/
    ObjectDisposedException/UnauthorizedAccessException" (the 3 types Phase 3A.4's
    SafeConsoleTextWriter is supposed to have neutralized - should be 0 after the fix)
    vs. any other originalExceptionType (would indicate a genuine game-logic fault
    leaking through, which must NOT be suppressed)
  * other/unexpected failures (excluding the known WRIGGLER quarantine-reason test)

Independent of the Emulator's own `test_console_io_isolation.py`/§7-A pytest matrix -
re-derives the same numbers from the RL side rather than trusting them secondhand.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[2]

_CAPTURE_FLAGS = {
    "default": [],
    "fd": ["--capture=fd"],
    "sys": ["--capture=sys"],
    "-s": ["-s"],
}

_IO_EXCEPTION_TYPES = ("System.IO.IOException", "System.ObjectDisposedException", "System.UnauthorizedAccessException")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=list(_CAPTURE_FLAGS), required=True)
    parser.add_argument("--iterations", type=int, required=True)
    args = parser.parse_args()

    flags = _CAPTURE_FLAGS[args.mode]
    qb_count = 0
    action_fault_io_count = 0
    action_fault_other_count = 0
    action_fault_other_samples = []
    other_failure_count = 0
    other_failure_samples = []

    start = time.time()
    for i in range(1, args.iterations + 1):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *flags, "-q", "tests/test_scenario_v2.py", "tests/test_choice_semantics.py"],
            cwd=str(_COMBAT_DIR), capture_output=True, text=True,
        )
        out = proc.stdout + proc.stderr

        qb_hits = out.count("QuiescentBoundaryViolationException")
        if qb_hits:
            qb_count += 1

        for m in re.finditer(r"ActionFaultedException:.*?originalExceptionType=(\S+)", out, re.DOTALL):
            orig_type = m.group(1)
            if any(orig_type.endswith(t.split(".")[-1]) or orig_type == t for t in _IO_EXCEPTION_TYPES) or orig_type.startswith("System.IO.") :
                action_fault_io_count += 1
            else:
                action_fault_other_count += 1
                if len(action_fault_other_samples) < 5:
                    action_fault_other_samples.append((i, orig_type))

        summary_line = next((line for line in out.splitlines() if re.match(r"^\d+ (failed|passed)", line.strip()) or "passed" in line and "failed" in line), "")
        failed_lines = [line for line in out.splitlines() if line.startswith("FAILED ") and "test_wriggler_missing_slot_without_encounter_is_detected" not in line]
        # An "other" failure is any FAILED line not attributable to WRIGGLER and not already counted as a QB/action-fault hit this run.
        if failed_lines and not qb_hits and "ActionFaultedException" not in out:
            other_failure_count += 1
            if len(other_failure_samples) < 5:
                other_failure_samples.append((i, failed_lines[:3]))

        if i % 10 == 0:
            elapsed = time.time() - start
            print(f"  ... {args.mode} {i}/{args.iterations} ({elapsed:.0f}s) qb={qb_count} "
                  f"action_fault_io={action_fault_io_count} action_fault_other={action_fault_other_count} other={other_failure_count}")

    elapsed = time.time() - start
    print(f"\n=== mode={args.mode} iterations={args.iterations} elapsed={elapsed:.0f}s ===")
    print(f"QuiescentBoundaryViolation (run-level count): {qb_count}/{args.iterations}")
    print(f"ActionFaultedException rooted in IOException/ObjectDisposedException/UnauthorizedAccessException: {action_fault_io_count}")
    print(f"ActionFaultedException rooted in OTHER exception types (would need investigation): {action_fault_other_count}")
    for it, t in action_fault_other_samples:
        print(f"    iter={it} originalExceptionType={t}")
    print(f"Other unexpected failures (excluding WRIGGLER): {other_failure_count}")
    for it, lines in other_failure_samples:
        print(f"    iter={it}: {lines}")
    print(f"\nRESULT: mode={args.mode} qb={qb_count} action_fault_io={action_fault_io_count} "
          f"action_fault_other={action_fault_other_count} other={other_failure_count}")
    return 0 if (qb_count == 0 and action_fault_io_count == 0 and action_fault_other_count == 0 and other_failure_count == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
