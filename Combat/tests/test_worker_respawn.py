"""Combat Worker OS-level Respawn coverage (RL担当指示：公開DTO監査・Combat Worker
Respawn・Branch Cancel, Part B).

Verifies: forced kill -> respawn changes PID and bumps worker_generation into the same
slot; a single worker's death/hang never tears down the rest of the Pool or requires a
full Pool restart; old-generation Leases are invalidated on respawn (old Holder Steps
must re-bootstrap, never route to the dead process's replacement); other workers keep
handling requests normally throughout; timing out inside an Emulator call converts to a
normal BRANCH_STATUS_FAULT/task_timeout result (never an uncaught TimeoutError) and
itself triggers a respawn of the hung worker only.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BranchWorkerPool,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    Lease,
    LeaseRegistry,
    WorkerExecutionRequest,
    _is_process_tainting_fault,
)
from search.decision_context import (  # noqa: E402
    DecisionContext,
    DecisionSignature,
    SemanticAction,
)
from search.main_loop import build_main_decision_context, initialize_main_loop_state  # noqa: E402


def _semantic_action_for(action: dict) -> SemanticAction:
    params = action.get("parameters") or {}
    return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))


def _simple_spec():
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _build_context():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    return build_main_decision_context(loop_state)


def _liquid_memories_spec():
    # Drinking LIQUID_MEMORIES mid-turn opens a "choose a card to return to hand from
    # discard" Pending sub-decision - the kind of Decision that establishes a real
    # Holder Lease. An ordinary mid-turn card play (e.g. DEFEND_IRONCLAD) instead
    # yields BOUNDARY_STABLE (snapshot-continuable, no Lease needed), so it cannot
    # exercise Lease invalidation.
    spec = _simple_spec()
    spec["hand"] = ["STRIKE_IRONCLAD"]
    spec["discard_pile"] = ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]
    spec["potions"] = [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}]
    return spec


def _build_pending_context_and_candidate_index():
    from verify_restore_bootstrap_phase3b import _make_eligible

    spec = _liquid_memories_spec()
    session = LiveCombatSession()
    state = session.start_combat(spec)
    legal_actions = state._cached_legal_actions  # noqa: SLF001
    index = next(i for i, a in enumerate(legal_actions) if a["action_type"] == "potion")
    representative = legal_actions[index]
    signature = DecisionSignature.from_battle_state(
        state, semantic_action=_semantic_action_for(representative), resolved_action=representative
    )
    eligible_snapshot = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    context = DecisionContext.from_main_stable_capture(eligible_snapshot, state, signature)
    return context, index


def test_single_forced_kill_respawns_with_new_pid_and_incremented_generation():
    pool = BranchWorkerPool(worker_count=2)
    try:
        old_handle = pool._workers[0]  # noqa: SLF001
        old_pid = old_handle.pid
        old_generation = old_handle.worker_generation
        assert pool.is_worker_alive(0)

        old_handle.process.terminate()
        old_handle.process.join(timeout=5)
        assert not old_handle.process.is_alive()

        pool.respawn_worker(0)

        new_handle = pool._workers[0]  # noqa: SLF001
        assert new_handle.pid != old_pid, "respawned worker must get a fresh OS PID"
        assert new_handle.worker_generation == old_generation + 1
        assert pool.is_worker_alive(0)
        # Sibling untouched.
        assert pool.is_worker_alive(1)
        assert pool._workers[1].worker_generation == 1  # noqa: SLF001
    finally:
        pool.close()


def test_respawn_invalidates_only_that_workers_leases():
    pool = BranchWorkerPool(worker_count=2)
    try:
        registry = LeaseRegistry()
        lease_w0 = Lease(
            worker_id=0, worker_generation=1, context_id="ctx-a", search_hypothesis_id=None,
            state_epoch=1, combat_session_id="sess-a", step_index=1, decision_result_digest="deadbeef",
        )
        lease_w1 = Lease(
            worker_id=1, worker_generation=1, context_id="ctx-b", search_hypothesis_id=None,
            state_epoch=1, combat_session_id="sess-b", step_index=1, decision_result_digest="cafebabe",
        )
        registry.set(lease_w0)
        registry.set(lease_w1)

        pool.respawn_worker(0, lease_registry=registry)

        assert registry.get("ctx-a", None) is None, "old worker-0 lease must be invalidated on respawn"
        assert registry.get("ctx-b", None) is lease_w1, "worker-1 lease must survive worker-0's respawn"
    finally:
        pool.close()


def test_100_forced_kills_across_both_workers_never_needs_full_pool_restart():
    pool = BranchWorkerPool(worker_count=2)
    try:
        registry = LeaseRegistry()
        seen_pids = {0: set(), 1: set()}
        for i in range(100):
            worker_id = i % 2
            handle = pool._workers[worker_id]  # noqa: SLF001
            seen_pids[worker_id].add(handle.pid)
            expected_generation = handle.worker_generation
            lease = Lease(
                worker_id=worker_id, worker_generation=expected_generation, context_id=f"ctx-{i}",
                search_hypothesis_id=None, state_epoch=1, combat_session_id=f"sess-{i}", step_index=1,
                decision_result_digest="feedface",
            )
            registry.set(lease)

            handle.process.terminate()
            handle.process.join(timeout=5)
            pool.respawn_worker(worker_id, lease_registry=registry)

            new_handle = pool._workers[worker_id]  # noqa: SLF001
            assert new_handle.worker_generation == expected_generation + 1
            assert new_handle.pid not in seen_pids[worker_id]
            assert pool.is_worker_alive(worker_id)
            other_id = 1 - worker_id
            assert pool.is_worker_alive(other_id), "the other worker must never be affected"
            assert registry.get(f"ctx-{i}", None) is None, "respawn must invalidate the dying worker's lease"

        assert len(pool._workers) == 2, "Pool must still contain exactly its original worker slots (no restart)"  # noqa: SLF001
    finally:
        pool.close()


def test_old_generation_holder_step_is_rejected_after_respawn():
    context, candidate_index = _build_pending_context_and_candidate_index()
    pool = BranchWorkerPool(worker_count=1)
    try:
        from search.candidate_pipeline import build_candidate_pipeline_result_for_explicit_candidates
        from search.search_coordinator import _candidate_batch, _plain_work_items  # noqa: SLF001

        pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [candidate_index])
        candidates = _candidate_batch(pipeline)
        work_items = _plain_work_items(context, candidates)

        registry = LeaseRegistry()
        results = pool.dispatch_work_items(work_items, registry)
        result = results[0]
        assert result.status == "success", result.diagnostics
        assert result.established_lease is not None
        stale_lease = result.established_lease

        # Force the worker that holds this Lease to die and respawn - the retained
        # in-process state is gone; the old Lease's worker_generation no longer matches.
        pool.respawn_worker(0)
        new_generation = pool._workers[0].worker_generation  # noqa: SLF001
        assert new_generation != stale_lease.worker_generation
        assert not stale_lease.is_valid_for(
            work_items[0], worker_generation=new_generation
        ), "a Lease minted under the old generation must not validate against the new one"
    finally:
        pool.close()


def test_execute_timeout_surfaces_as_fault_result_and_respawns_hung_worker():
    context = _build_context()
    # An unrealistically short timeout guarantees the real Bootstrap Step (IPC +
    # replay + emulator Step) cannot finish in time, deterministically exercising the
    # queue.Empty/respawn path without needing to fabricate a genuinely stuck worker.
    pool = BranchWorkerPool(worker_count=1, request_timeout_s=0.001)
    try:
        from search.candidate_pipeline import build_candidate_pipeline_result_for_explicit_candidates
        from search.search_coordinator import _candidate_batch, _plain_work_items  # noqa: SLF001

        pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [1])
        candidates = _candidate_batch(pipeline)
        work_items = _plain_work_items(context, candidates)

        old_pid = pool._workers[0].pid  # noqa: SLF001
        registry = LeaseRegistry()
        result = pool.execute(
            0, WorkerExecutionRequest(work_items[0], EXECUTION_MODE_BOOTSTRAP_STEP), lease_registry=registry
        )
        assert result.status == "fault"
        assert result.diagnostics.get("fault_kind") == "task_timeout"
        assert pool._workers[0].worker_generation == 2, "the hung worker must be respawned exactly once"  # noqa: SLF001
        assert pool._workers[0].pid != old_pid  # noqa: SLF001
        assert pool.is_worker_alive(0)
    finally:
        pool.close()


def test_process_tainting_fault_detection_is_limited_to_combat_lifecycle_nullref():
    diagnostics = {
        "fault_kind": "worker_exception",
        "exception_type": "ActionExecutionError",
        "message": (
            "Action execution faulted: original_exception_type=System.NullReferenceException "
            "original_exception_message=Object reference not set to an instance of an object."
        ),
        "traceback": (
            "System.NullReferenceException: Object reference not set to an instance of an object.\n"
            "   at MegaCrit.Sts2.Core.Combat.CombatManager.EndCombatInternal()\n"
            "   at MegaCrit.Sts2.Core.Combat.CombatManager.CheckWinCondition()"
        ),
    }
    assert _is_process_tainting_fault(diagnostics)

    ordinary_fault = {
        "fault_kind": "worker_exception",
        "exception_type": "ActionExecutionError",
        "message": "RewardsSet error while resolving an expected game-logic fault",
        "traceback": "live_combat_session.ActionExecutionError: RewardsSet is unavailable",
    }
    assert not _is_process_tainting_fault(ordinary_fault)


def test_sibling_can_still_bootstrap_independently_after_a_neighbor_respawn():
    context = _build_context()
    pool = BranchWorkerPool(worker_count=2)
    try:
        from search.candidate_pipeline import build_candidate_pipeline_result_for_explicit_candidates
        from search.search_coordinator import _candidate_batch, _plain_work_items  # noqa: SLF001

        pool._workers[0].process.terminate()  # noqa: SLF001
        pool._workers[0].process.join(timeout=5)  # noqa: SLF001
        pool.respawn_worker(0)

        pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [1])
        candidates = _candidate_batch(pipeline)
        work_items = _plain_work_items(context, candidates)
        result = pool.execute(1, WorkerExecutionRequest(work_items[0], EXECUTION_MODE_BOOTSTRAP_STEP))
        assert result.status == "success", "sibling worker-1 must still function normally after worker-0's respawn"
    finally:
        pool.close()


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
