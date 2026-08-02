"""Small-N regression for the reusable shadow evaluation batch runner."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from search.shadow_evaluation_runner import run_batch  # noqa: E402


def test_shadow_evaluation_batch_small_n_preserves_main_session():
    report = run_batch(3, worker_count=1)

    summary = report["summary"]
    assert summary["combat_count"] == 3
    assert 0.0 <= summary["agreement_rate"] <= 1.0
    assert summary["main_session_unchanged"]
    assert len(report["rows"]) == 3
    assert all(row["old_outcome"] is not None for row in report["rows"])
    assert all(row["new_metrics"]["worker_count"] == 1 for row in report["rows"])


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
