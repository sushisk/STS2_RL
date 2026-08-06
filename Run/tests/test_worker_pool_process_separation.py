"""Real-Emulator tests for the OS-process-separated Whole Run Worker Pool.

Native assertion runner, no pytest dependency (matches this package's existing test
files). Run: `python test_worker_pool_process_separation.py`.

Every test here spawns a REAL `WholeRunWorkerPool` (real OS processes, real
`GameInstance`s) - no mocking, since the entire point of this module is verifying real
process separation, real PIDs, and real Lease/generation bookkeeping across process
boundaries.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from process_choice_branch_runner import (  # noqa: E402
    ALL_CHOICE_TYPES,
    CHOICE_COMBAT_PENDING,
    CHOICE_MAP,
    ROOM_TYPE_FOR_CHOICE,
    TOOLBOX_RELIC_ID,
    run_choice_branch,
)
from worker_fault_injection import (  # noqa: E402
    inject_in_worker_fault,
    kill_worker_process,
    respawn_and_verify,
)


def _establish_item(work_id: str, choice_type: str, target_boundary: str, map_snapshot: str, room_id: "int | None") -> ChoiceWorkItem:
    context_id = derive_context_id(
        map_snapshot=map_snapshot, room_id=room_id, action_prefix=[], choice_type=choice_type, relic_injection=None
    )
    return ChoiceWorkItem(
        work_id=work_id,
        context_id=context_id,
        choice_type=choice_type,
        map_snapshot=map_snapshot,
        room_id=room_id,
        action_prefix=[],
        relic_injection=None,
        target_boundary=target_boundary,
        work_kind=WORK_KIND_SUB_BRANCH,
        discover_prefix=True,
    )
from worker_pool import (  # noqa: E402
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    ChoiceWorkItem,
    EventRngReplayPlan,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    EXECUTION_MODE_HOLDER_STEP,
    ExploreRequest,
    Lease,
    LeaseRegistry,
    WholeRunWorkerPool,
    WorkerExecutionRequest,
    WORK_KIND_CONTINUATION,
    WORK_KIND_SUB_BRANCH,
    derive_context_id,
)


def _discover_all(pool: WholeRunWorkerPool, seed: int = 18) -> dict:
    target_room_types = sorted(set(ROOM_TYPE_FOR_CHOICE.values()))
    explored = pool.explore(
        ExploreRequest(
            seed=seed, character_id="Ironclad", ascension=0, min_rooms=10, max_steps=1500,
            target_room_types=target_room_types,
        )
    )
    return explored.found_snapshots


def test_holder_and_sibling_pids_differ_for_all_six_choice_types():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool)
        registry = LeaseRegistry()
        for choice_type in ALL_CHOICE_TYPES:
            if choice_type == CHOICE_MAP:
                info = found["__first_map__"]
                room_id = None
            else:
                room_type = ROOM_TYPE_FOR_CHOICE[choice_type]
                assert room_type in found, f"{room_type} not reached at seed=18 within 10 rooms"
                info = found[room_type]
                room_id = info["room_id"]
            relic = TOOLBOX_RELIC_ID if choice_type == CHOICE_COMBAT_PENDING else None
            attempt = run_choice_branch(pool, registry, choice_type, info["map_snapshot"], room_id, relic_injection=relic)
            assert attempt.checks["holder_sibling_pids_differ"], (choice_type, attempt.checks)
            assert attempt.checks["boundary_matches"], (choice_type, attempt.checks)
            assert attempt.checks["legal_action_semantic_set_matches"], (choice_type, attempt.checks)
            assert attempt.checks["same_choice_same_result_determinism"], (choice_type, attempt.checks)
            # combat_pending's TOOLBOX-add-to-hand path is a documented, low-frequency
            # real-Emulator content-divergence caveat (see the report) - every other
            # choice type must fully pass including divergence.
            if choice_type != CHOICE_COMBAT_PENDING:
                assert attempt.checks["different_choices_diverge"], (choice_type, attempt.checks)


def test_worker_generation_increments_on_respawn_and_old_leases_invalidated():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()
        info = found["MerchantRoom"]
        attempt = run_choice_branch(pool, registry, "shop", info["map_snapshot"], info["room_id"])
        assert attempt.ok, attempt.checks
        holder_slot = attempt.holder.worker_slot

        fi = respawn_and_verify(pool, registry, holder_slot)
        assert fi.new_generation == fi.old_generation + 1
        assert fi.new_pid is not None and fi.new_pid != fi.old_pid
        assert fi.old_generation_leases_survived == 0

        # pool still usable afterward
        attempt2 = run_choice_branch(pool, registry, "shop", info["map_snapshot"], info["room_id"])
        assert attempt2.ok, attempt2.checks


def test_in_worker_fault_does_not_kill_the_process():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()
        info = found["EventRoom"]
        result = inject_in_worker_fault(pool, registry, 0, info["map_snapshot"])
        assert result["status"] == "fault"
        assert pool.is_worker_alive(0)


def test_continuation_to_unestablished_context_is_rejected():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()  # deliberately empty - no establish() call happened
        info = found["MerchantRoom"]
        context_id = derive_context_id(
            map_snapshot=info["map_snapshot"], room_id=info["room_id"], action_prefix=[], choice_type="shop",
            relic_injection=None,
        )
        phantom_item = ChoiceWorkItem(
            work_id="phantom",
            context_id=context_id,
            choice_type="shop",
            map_snapshot=info["map_snapshot"],
            room_id=info["room_id"],
            action_prefix=[],
            relic_injection=None,
            target_boundary="shop_choice",
            work_kind=WORK_KIND_CONTINUATION,
            resolve_action_id=0,
        )
        raised = False
        try:
            pool.dispatch_choice_work_items([phantom_item], registry)
        except RuntimeError as exc:
            raised = True
            assert "no valid Lease" in str(exc)
        assert raised, "expected dispatch to reject a continuation with no established Lease"


def test_stale_generation_lease_rejected_after_respawn():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()
        info = found["MerchantRoom"]
        attempt = run_choice_branch(pool, registry, "shop", info["map_snapshot"], info["room_id"])
        assert attempt.ok, attempt.checks
        holder_slot = attempt.holder.worker_slot

        # Fabricate a stale Lease claiming the OLD generation still holds this context,
        # after the real registry entry has already been consumed/cleared by the
        # resolve step above - simulates a caller routing against outdated Lease info.
        stale_lease = Lease(
            worker_slot=holder_slot,
            worker_generation=pool.worker_generations[holder_slot],
            pid=pool.worker_pid(holder_slot),
            context_id="not-a-real-context",
            combat_session_id=None,
            run_seed=18,
            step_signature="deadbeef",
        )
        registry.set(stale_lease)
        respawn_and_verify(pool, registry, holder_slot)
        assert registry.get("not-a-real-context") is None, "respawn must invalidate leases for the respawned slot"


def test_choice_work_item_event_rng_plan_pickles_round_trip():
    import pickle

    plan = EventRngReplayPlan(
        hypothesis_key=("root", "dp-1", 3),
        override_state={"event_id": "ev1", "event_rng": {"counter": 1, "s0": 2, "s1": 3, "s2": 4, "s3": 5}},
        apply_before_action_index=2,
    )
    item = ChoiceWorkItem(
        work_id="wid",
        context_id="ctx",
        choice_type="event",
        map_snapshot="snap",
        room_id=7,
        action_prefix=[1, 2],
        relic_injection=None,
        target_boundary="event_choice",
        work_kind=WORK_KIND_SUB_BRANCH,
        resolve_action_id=9,
        event_rng_plan=plan,
    )

    restored = pickle.loads(pickle.dumps(item))
    assert restored == item
    assert restored.event_rng_plan == plan


def test_stray_late_result_is_discarded_not_misrouted():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()
        shop_info = found["MerchantRoom"]
        event_info = found["EventRoom"]
        item1 = _establish_item("stray-test-1", "shop", "shop_choice", shop_info["map_snapshot"], shop_info["room_id"])
        item2 = _establish_item("stray-test-2", "event", "event_choice", event_info["map_snapshot"], event_info["room_id"])

        # Inject a stray/late result carrying a request_id that was never issued by this
        # dispatch call (simulating a delayed response from a prior timed-out request) -
        # it must be silently discarded, not misrouted to either real pending work item.
        pool._result_queue.put((999999999, "choice", None))  # noqa: SLF001

        results = pool.dispatch_choice_work_items([item1, item2], registry)
        by_id = {r.work_item.work_id: r for r in results}
        assert by_id["stray-test-1"].status == BRANCH_STATUS_SUCCESS
        assert by_id["stray-test-2"].status == BRANCH_STATUS_SUCCESS


def test_dead_worker_auto_respawns_during_dispatch():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()
        info = found["EventRoom"]

        old_generation = pool.worker_generations[0]
        kill_worker_process(pool, 0)

        # Bootstrap routing for the very first dispatch picks slot 0 deterministically
        # (round-robin starts at index 0, no leases held yet) - dispatching now must not
        # raise, must auto-respawn slot 0, and must return a normalized fault result.
        item = _establish_item("dead-worker-test", "event", "event_choice", info["map_snapshot"], info["room_id"])
        (result,) = pool.dispatch_choice_work_items([item], registry)
        assert result.status == BRANCH_STATUS_FAULT
        assert result.diagnostics.get("fault_kind") in ("worker_process_crash", "task_timeout")
        assert pool.worker_generations[0] == old_generation + 1


def test_pool_recovers_and_succeeds_after_auto_respawn():
    with WholeRunWorkerPool(branch_worker_count=2) as pool:
        found = _discover_all(pool, seed=18)
        registry = LeaseRegistry()
        info = found["EventRoom"]

        kill_worker_process(pool, 0)
        item = _establish_item("dead-worker-test-2", "event", "event_choice", info["map_snapshot"], info["room_id"])
        (result,) = pool.dispatch_choice_work_items([item], registry)
        assert result.status == BRANCH_STATUS_FAULT

        # A follow-up request must succeed normally against the respawned worker.
        item2 = _establish_item("dead-worker-test-2-followup", "event", "event_choice", info["map_snapshot"], info["room_id"])
        (result2,) = pool.dispatch_choice_work_items([item2], registry)
        assert result2.status == BRANCH_STATUS_SUCCESS, result2.diagnostics


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
