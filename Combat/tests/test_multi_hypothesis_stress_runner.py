"""Small-N regression for the multi-candidate x multi-hypothesis stress runner."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from search.multi_hypothesis_stress_runner import run_multi_hypothesis_stress  # noqa: E402


def test_multi_hypothesis_stress_runner_small_n_checks_grid_faults_and_commit_first_only():
    report = run_multi_hypothesis_stress(
        3,
        worker_count=1,
        width=3,
        hypothesis_count=3,
        max_retries=1,
        fault_fraction=1.0,
        checkpoint_interval=3,
        max_iterations_per_combat=10,
    )

    summary = report["summary"]
    assert summary["combat_count"] == 3
    assert summary["decision_count"] == 3
    assert summary["hypothesis_decision_count"] == 3
    assert summary["grid_checks"] == 3
    assert summary["grid_fairness_passed"]
    assert summary["injected_decisions"] == 3
    assert summary["partial_missing_injections"] == 2
    assert summary["root_exclusion_injections"] == 1
    assert summary["pessimistic_fill_actions"] >= 1
    assert summary["excluded_root_actions"] >= 1
    assert sum(summary["fault_counts"].values()) >= 1
    assert summary["retry_count"]["max"] >= 1.0
    assert summary["plan_path_length"]["min"] == 1.0
    assert summary["plan_path_length"]["max"] == 1.0
    assert summary["main_session_unchanged"]


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
