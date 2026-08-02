"""Small-N regression for the shared BranchWorkerPool endurance runner."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from search.endurance_runner import run_endurance  # noqa: E402


def test_endurance_runner_small_n_exercises_real_fault_injection_and_preserves_main():
    report = run_endurance(
        6,
        worker_count=1,
        width=1,
        hypothesis_count=1,
        max_retries=1,
        fault_fraction=0.5,
        checkpoint_interval=3,
        use_beam_every=0,
        max_iterations_per_combat=40,
    )

    summary = report["summary"]
    assert summary["combat_count"] == 6
    assert summary["decision_count"] >= 6
    assert summary["injected_decisions"] >= 1
    assert sum(summary["fault_counts"].values()) >= 1
    assert summary["retry_rounds_observed"] >= 1
    assert summary["combat_session_id_unique"]
    assert summary["lease_invalid_consumptions"] == 0
    assert summary["worker_generation_non_decreasing"]
    assert summary["main_session_unchanged"]
    assert len(report["checkpoints"]) == 2
    assert all(check["eligible"] for check in report["snapshot_checks"])


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(_run_all())
