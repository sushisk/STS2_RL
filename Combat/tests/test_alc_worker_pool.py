"""Tests for the ALC-backed Branch Worker Pool.

Run:
cd C:\\STS2_RL\\Combat\\tests && python test_alc_worker_pool.py
"""

from __future__ import annotations

import dataclasses
import queue
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _COMBAT_DIR.parent
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval", _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from API.instance_combat import DEFAULT_ALC_WORKER_COUNT, CombatInstance  # noqa: E402
import emulator_bridge  # noqa: E402
from search.alc_worker_pool import AlcBranchWorkerPool  # noqa: E402
from search.branch_manager import BranchManager  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_SUCCESS,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    BranchResult,
    Lease,
    LeaseRegistry,
    WorkerExecutionRequest,
    WorkItem,
    decision_result_digest,
    derive_context_id,
    dispatch_work_items,
)
from search.decision_context import BOUNDARY_PENDING, BOUNDARY_STABLE, BOUNDARY_TERMINAL  # noqa: E402
from test_branch_worker_pool import _context_and_pipeline, _simple_spec  # noqa: E402


class _ImmediateCapacityPool:
    def __init__(self, worker_count: int, pipeline) -> None:
        self.worker_count = worker_count
        self.worker_ids = list(range(worker_count))
        self.worker_generations = {worker_id: 1 for worker_id in self.worker_ids}
        self.request_timeout_s = 1.0
        self._next_request_id = 0
        self._result_queue = queue.Queue()
        self.modes: list[str] = []
        self.pipeline = pipeline

    def _submit(self, worker_id: int, request: WorkerExecutionRequest) -> int:
        self._next_request_id += 1
        request_id = self._next_request_id
        self.modes.append(request.execution_mode)
        work_item = request.work_item
        if work_item.work_kind == "sub_branch":
            pending_sig = dataclasses.replace(
                work_item.decision_context.current_context_signature,
                boundary=BOUNDARY_PENDING,
                choice_scope="TopLevel",
                choice_kind="CapacityMeasurement",
                candidate_semantic_keys=((("system", work_item.work_id, None), 1),),
            )
            pending_context = dataclasses.replace(
                work_item.decision_context,
                current_context_signature=pending_sig,
                search_hypothesis_id=work_item.search_hypothesis_id,
            )
            lease = Lease(
                worker_id=worker_id,
                worker_generation=1,
                context_id=f"pending-{work_item.work_id}",
                search_hypothesis_id=work_item.search_hypothesis_id,
                state_epoch=1,
                combat_session_id=pending_sig.combat_session_id,
                step_index=pending_sig.step_index,
                decision_result_digest=decision_result_digest(pending_sig),
            )
            result = BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=request.execution_mode,
                worker_id=worker_id,
                worker_generation=1,
                result_signature=pending_sig,
                pending_decision_context=pending_context,
                pending_pipeline_result=self.pipeline,
                established_lease=lease,
            )
        else:
            stable_sig = dataclasses.replace(
                work_item.decision_context.current_context_signature,
                boundary=BOUNDARY_STABLE,
                choice_scope=None,
                choice_kind=None,
                candidate_semantic_keys=None,
            )
            result = BranchResult(
                status=BRANCH_STATUS_SUCCESS,
                work_item=work_item,
                execution_mode=request.execution_mode,
                worker_id=worker_id,
                worker_generation=1,
                result_signature=stable_sig,
                child_snapshot=object(),
            )
        self._result_queue.put((request_id, result))
        return request_id

    def respawn_worker(self, worker_id: int, lease_registry=None) -> None:
        raise AssertionError(f"unexpected respawn of worker {worker_id}")


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


def test_alc_pool_parallel_execute_keeps_results_on_their_own_isolated_sessions():
    """Drives raw pool.execute() from concurrent Python threads.

    The shared/default-ALC GameInstance guard is a proxy: after the main-thread context
    is built, this test replaces emulator_bridge._shared_instance with a sentinel. ALC
    worker execution should use IsolatedGameSession objects only, so the sentinel must
    remain untouched while every result comes back with its own combat_session_id.
    """

    context, pipeline = _context_and_pipeline(
        enemy_hp=999,
        width=4,
        hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
    )
    context_id = derive_context_id(context)
    candidates = [pipeline.continuation_candidate, *pipeline.sub_branch_candidates[:3]]
    work_items = [
        WorkItem.from_candidate_ref(
            context,
            candidate,
            work_kind="continuation" if index == 0 else "sub_branch",
            context_id=context_id,
            work_id=f"alc-parallel-{index}",
        )
        for index, candidate in enumerate(candidates)
    ]

    sentinel = object()
    old_shared_instance = emulator_bridge._shared_instance  # noqa: SLF001
    emulator_bridge._shared_instance = sentinel  # noqa: SLF001
    try:
        with AlcBranchWorkerPool(worker_count=4, request_timeout_s=120.0) as pool:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        pool.execute,
                        worker_id,
                        WorkerExecutionRequest(work_item, EXECUTION_MODE_BOOTSTRAP_STEP),
                    )
                    for worker_id, work_item in enumerate(work_items)
                ]
                results = [future.result() for future in as_completed(futures)]
    finally:
        assert emulator_bridge._shared_instance is sentinel  # noqa: SLF001
        emulator_bridge._shared_instance = old_shared_instance  # noqa: SLF001

    assert len(results) == 4
    assert {r.status for r in results} == {BRANCH_STATUS_SUCCESS}, [r.diagnostics for r in results]
    assert {r.work_item.work_id for r in results} == {item.work_id for item in work_items}
    assert {r.worker_id for r in results} == {0, 1, 2, 3}
    assert len({r.result_signature.combat_session_id for r in results}) == 4


def test_alc_pool_repeated_respawn_exceeding_threshold_raises():
    with AlcBranchWorkerPool(worker_count=1, request_timeout_s=120.0, max_respawns_per_worker=1) as pool:
        pool.respawn_worker(0)
        try:
            pool.respawn_worker(0)
        except RuntimeError as exc:
            assert "ALC worker 0 exceeded max respawns" in str(exc)
        else:
            raise AssertionError("second respawn should exceed max_respawns_per_worker=1")


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


def test_combat_instance_alc_default_worker_count_is_bounded():
    inst = CombatInstance("alc-capacity", {"instance_type": "combat", **_simple_spec(enemy_hp=20)}, worker_pool_backend="alc", max_branches=6)
    try:
        assert isinstance(inst._pool, AlcBranchWorkerPool)  # noqa: SLF001
        assert inst._pool.worker_count == DEFAULT_ALC_WORKER_COUNT  # noqa: SLF001
    finally:
        inst.close()


def test_combat_instance_alc_explicit_worker_count_can_match_max_branches():
    inst = CombatInstance(
        "alc-explicit-capacity",
        {"instance_type": "combat", **_simple_spec(enemy_hp=20)},
        worker_count=6,
        worker_pool_backend="alc",
        max_branches=6,
    )
    try:
        assert isinstance(inst._pool, AlcBranchWorkerPool)  # noqa: SLF001
        assert inst._pool.worker_count == 6  # noqa: SLF001
    finally:
        inst.close()


def test_branch_holder_capacity_measurement_improves_with_wider_pool():
    def _measure(worker_count: int, width: int = 8) -> dict[str, int]:
        context, pipeline = _context_and_pipeline(enemy_hp=999, width=width, hand=["DEFEND_IRONCLAD"] * width)
        root_context_id = derive_context_id(context)
        sub_items = [
            WorkItem.from_candidate_ref(
                context,
                candidate,
                work_kind="sub_branch",
                context_id=root_context_id,
                work_id=f"sub-{index}",
            )
            for index, candidate in enumerate(pipeline.sub_branch_candidates[:width])
        ]
        pool = _ImmediateCapacityPool(worker_count, pipeline)
        registry = LeaseRegistry()
        manager = BranchManager(pool, registry, max_branches=width)
        branch_ids = manager.submit(sub_items)
        first_results = manager.poll(timeout=1.0, branch_ids=branch_ids)

        continuation_items = []
        for branch_id in branch_ids:
            result = first_results[branch_id]
            assert result.established_lease is not None
            continuation_candidate = dataclasses.replace(
                pipeline.continuation_candidate,
                current_context_signature=result.pending_decision_context.current_context_signature,
            )
            continuation_items.append(
                WorkItem.from_candidate_ref(
                    result.pending_decision_context,
                    continuation_candidate,
                    work_kind="continuation",
                    context_id=result.established_lease.context_id,
                    work_id=f"cont-{branch_id}",
                )
            )
        continuation_ids = manager.submit_many([(item, branch_id) for item, branch_id in zip(continuation_items, branch_ids, strict=True)])
        manager.poll(timeout=1.0, branch_ids=continuation_ids)
        return {
            EXECUTION_MODE_BOOTSTRAP_STEP: pool.modes.count(EXECUTION_MODE_BOOTSTRAP_STEP),
            EXECUTION_MODE_HOLDER_STEP: pool.modes.count(EXECUTION_MODE_HOLDER_STEP),
        }

    narrow = _measure(worker_count=2)
    wide = _measure(worker_count=8)
    print(f"capacity measurement narrow={narrow} wide={wide}")

    assert narrow == {EXECUTION_MODE_BOOTSTRAP_STEP: 14, EXECUTION_MODE_HOLDER_STEP: 0}
    assert wide == {EXECUTION_MODE_BOOTSTRAP_STEP: 7, EXECUTION_MODE_HOLDER_STEP: 7}
    assert wide[EXECUTION_MODE_HOLDER_STEP] > narrow[EXECUTION_MODE_HOLDER_STEP]
    assert wide[EXECUTION_MODE_BOOTSTRAP_STEP] < narrow[EXECUTION_MODE_BOOTSTRAP_STEP]


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
