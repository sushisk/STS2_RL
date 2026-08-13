"""Tests for the ALC-backed Branch Worker Pool.

Run:
cd C:\\STS2_RL\\Combat\\tests && python test_alc_worker_pool.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _COMBAT_DIR.parent
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval", _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from API.instance_combat import CombatInstance  # noqa: E402
from search.alc_worker_pool import AlcBranchWorkerPool  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    LeaseRegistry,
    WorkItem,
    derive_context_id,
    dispatch_work_items,
)
from search.decision_context import BOUNDARY_STABLE, BOUNDARY_TERMINAL  # noqa: E402
from test_branch_worker_pool import _context_and_pipeline, _simple_spec  # noqa: E402


def test_alc_pool_creation_creates_one_live_thread_per_session():
    with AlcBranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
        assert pool.worker_ids == [0, 1]
        assert pool.worker_generations == {0: 1, 1: 1}
        assert pool.worker_pids == {0: None, 1: None}
        assert all(pool.is_worker_alive(worker_id) for worker_id in pool.worker_ids)


def test_alc_pool_bootstrap_steps_sibling_branches_on_independent_sessions():
    context, pipeline = _context_and_pipeline(enemy_hp=999, width=2, hand=["DEFEND_IRONCLAD", "DEFEND_IRONCLAD"])
    context_id = derive_context_id(context)
    work_items = [
        WorkItem.from_candidate_ref(
            context, pipeline.continuation_candidate, work_kind="continuation", context_id=context_id, work_id="alc-cont"
        ),
        WorkItem.from_candidate_ref(
            context, pipeline.sub_branch_candidates[0], work_kind="sub_branch", context_id=context_id, work_id="alc-sub"
        ),
    ]

    registry = LeaseRegistry()
    with AlcBranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
        results = dispatch_work_items(work_items, registry, worker_pool=pool)

    assert len(results) == 2
    assert {r.status for r in results} == {BRANCH_STATUS_SUCCESS}, [r.diagnostics for r in results]
    assert {r.execution_mode for r in results} == {EXECUTION_MODE_BOOTSTRAP_STEP}
    assert {r.worker_id for r in results} == {0, 1}
    assert all(r.result_signature.boundary in {BOUNDARY_STABLE, BOUNDARY_TERMINAL} for r in results)
    assert len({r.result_signature.combat_session_id for r in results}) == 2


def test_combat_instance_can_opt_into_alc_pool():
    inst = CombatInstance("alc-instance", {"instance_type": "combat", **_simple_spec(enemy_hp=20)}, worker_count=1, worker_pool_backend="alc")
    try:
        assert isinstance(inst._pool, AlcBranchWorkerPool)  # noqa: SLF001
        response = inst.start_instance_response()
        assert response["status"] == "completed"
        decision_point_id = response["decision_point_id"]
        action_id = next(
            str(index)
            for index, action in enumerate(response["masked_emulator_dto"]["legal_actions"])
            if action["action_type"] == "card"
        )
        branch = inst.emulate_action(
            parent_branch_id="root",
            branch_id="b-alc",
            rng_id=0,
            decision_point_id=decision_point_id,
            action_id=action_id,
            simulation_options={"max_time_ms": 120000},
        )
        assert branch["status"] == "completed", branch
    finally:
        inst.close()


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
    sys.exit(_run_all())
