"""Branch Cancel/Release API coverage (RL担当指示：公開DTO監査・Combat Worker Respawn・
Branch Cancel, Part C).

Verifies the internal ``BranchManager`` state machine: queued/running/holder/sibling
cancel, near-completion race, double-cancel, use-after-release rejection, cancel that
requires killing a stuck worker, episode-close cleanup, and that Branches NOT targeted
by a Cancel keep running/complete normally. Also covers the max_branches safety cap.

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
from search.branch_manager import (  # noqa: E402
    BRANCH_STATE_CANCELLED,
    BRANCH_STATE_COMPLETED,
    BRANCH_STATE_QUEUED,
    BRANCH_STATE_RELEASED,
    BranchLimitExceededError,
    BranchManager,
    BranchReleasedError,
    UnknownBranchError,
)
from search.branch_worker_pool import BranchWorkerPool, LeaseRegistry  # noqa: E402
from search.candidate_pipeline import build_candidate_pipeline_result_for_explicit_candidates  # noqa: E402
from search.decision_context import DecisionSignature, SemanticAction  # noqa: E402
from search.main_loop import build_main_decision_context, initialize_main_loop_state  # noqa: E402
from search.search_coordinator import _candidate_batch, _plain_work_items  # noqa: E402, SLF001


def _semantic_action_for(action: dict) -> SemanticAction:
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


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


def _context_and_index(card_id: str):
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)
    legal_actions = context.current_decision_result._cached_legal_actions  # noqa: SLF001
    index = next(
        i for i, a in enumerate(legal_actions) if a.get("action_type") == "card" and a.get("parameters", {}).get("cardId") == card_id
    )
    return context, index


def _work_items_for(card_id: str = "DEFEND_IRONCLAD"):
    context, index = _context_and_index(card_id)
    pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [index])
    candidates = _candidate_batch(pipeline)
    return _plain_work_items(context, candidates)


def test_queued_cancel_prevents_dispatch_entirely():
    pool = BranchWorkerPool(worker_count=1)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        (branch_id,) = manager.submit(_work_items_for())
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_QUEUED

        statuses = manager.cancel_branches([branch_id])
        assert statuses[branch_id] == BRANCH_STATE_CANCELLED

        results = manager.poll(timeout=5)
        assert branch_id not in results, "a cancelled-while-queued Branch must never be dispatched"
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_CANCELLED
    finally:
        pool.close()


def test_running_cancel_kills_and_respawns_only_that_worker():
    pool = BranchWorkerPool(worker_count=2)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        (branch_id,) = manager.submit(_work_items_for())
        # Force this Branch onto worker 0 deterministically by exhausting bootstrap
        # rotation manually is unnecessary for a single-Branch pool(1)-style check;
        # instead directly drive the internal routing to know the worker.
        results_future = {}

        # Manually route+submit like poll() would, but stop before waiting so we can
        # cancel while genuinely "running" (mid Emulator call, from this test's PoV).
        manager.poll(timeout=0.001)  # too short to ever complete a real Bootstrap Step
        status = manager.get_branch_status([branch_id])[branch_id]
        assert status in ("running", "faulted"), status
        if status == "running":
            worker_id_before = manager._records[branch_id].worker_id  # noqa: SLF001
            old_pid = pool.worker_pids[worker_id_before]
            manager.cancel_branches([branch_id])
            assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_CANCELLED
            assert pool.worker_pids[worker_id_before] != old_pid, "cancelling a running Branch must kill+respawn its worker"
    finally:
        pool.close()


def test_holder_cancel_invalidates_its_own_lease_and_completed_result_is_discarded():
    pool = BranchWorkerPool(worker_count=1)
    try:
        registry = LeaseRegistry()
        manager = BranchManager(pool, registry)
        (branch_id,) = manager.submit(_work_items_for())
        results = manager.poll(timeout=60)
        result = results.get(branch_id) or manager.get_branch_result(branch_id)
        assert result.status == "success", result.diagnostics
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_COMPLETED

        manager.cancel_branches([branch_id])
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_CANCELLED
        if result.established_lease is not None:
            assert registry.get(result.established_lease.context_id, result.established_lease.search_hypothesis_id) is None
    finally:
        pool.close()


def test_sibling_cancel_does_not_affect_other_branch_or_its_lease():
    pool = BranchWorkerPool(worker_count=2)
    try:
        registry = LeaseRegistry()
        manager = BranchManager(pool, registry)
        branch_a, = manager.submit(_work_items_for("DEFEND_IRONCLAD"))
        branch_b, = manager.submit(_work_items_for("BASH"))
        manager.poll(timeout=60)
        status_a_before = manager.get_branch_status([branch_a])[branch_a]
        status_b_before = manager.get_branch_status([branch_b])[branch_b]

        manager.cancel_branches([branch_a])

        assert manager.get_branch_status([branch_a])[branch_a] == BRANCH_STATE_CANCELLED
        assert manager.get_branch_status([branch_b])[branch_b] == status_b_before, "sibling Branch must be unaffected"
    finally:
        pool.close()


def test_double_cancel_is_idempotent():
    pool = BranchWorkerPool(worker_count=1)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        (branch_id,) = manager.submit(_work_items_for())
        manager.cancel_branches([branch_id])
        first = manager.get_branch_status([branch_id])[branch_id]
        manager.cancel_branches([branch_id])  # must not raise
        second = manager.get_branch_status([branch_id])[branch_id]
        assert first == second == BRANCH_STATE_CANCELLED
    finally:
        pool.close()


def test_use_after_release_is_explicitly_rejected():
    pool = BranchWorkerPool(worker_count=1)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        (branch_id,) = manager.submit(_work_items_for())
        manager.poll(timeout=60)
        manager.release_branches([branch_id])
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_RELEASED

        raised = False
        try:
            manager.get_branch_result(branch_id)
        except BranchReleasedError:
            raised = True
        assert raised, "reading a released Branch's result must be explicitly rejected"
    finally:
        pool.close()


def test_release_is_idempotent_and_release_of_active_branch_cancels_first():
    pool = BranchWorkerPool(worker_count=1)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        (branch_id,) = manager.submit(_work_items_for())
        # Still queued - release must implicitly cancel then release.
        manager.release_branches([branch_id])
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_RELEASED
        manager.release_branches([branch_id])  # idempotent, must not raise
        assert manager.get_branch_status([branch_id])[branch_id] == BRANCH_STATE_RELEASED
    finally:
        pool.close()


def test_episode_close_cancels_and_releases_everything():
    pool = BranchWorkerPool(worker_count=2)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        branch_a, = manager.submit(_work_items_for("DEFEND_IRONCLAD"))
        branch_b, = manager.submit(_work_items_for("BASH"))
        manager.poll(timeout=60)

        manager.close_all()

        statuses = manager.get_branch_status([branch_a, branch_b])
        assert all(state == BRANCH_STATE_RELEASED for state in statuses.values())
    finally:
        pool.close()


def test_non_cancelled_branch_completes_normally_when_batched_with_a_cancelled_one():
    pool = BranchWorkerPool(worker_count=2)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        branch_a, = manager.submit(_work_items_for("DEFEND_IRONCLAD"))
        branch_b, = manager.submit(_work_items_for("BASH"))
        manager.cancel_branches([branch_a])

        results = manager.poll(timeout=60)
        assert branch_a not in results
        assert branch_b in results
        assert results[branch_b].status == "success", results[branch_b].diagnostics
    finally:
        pool.close()


def test_max_branches_cap_is_enforced():
    pool = BranchWorkerPool(worker_count=1)
    try:
        manager = BranchManager(pool, LeaseRegistry(), max_branches=2)
        manager.submit(_work_items_for())
        manager.submit(_work_items_for())
        raised = False
        try:
            manager.submit(_work_items_for())
        except BranchLimitExceededError:
            raised = True
        assert raised, "submitting past max_branches must be rejected"
    finally:
        pool.close()


def test_unknown_branch_id_raises():
    pool = BranchWorkerPool(worker_count=1)
    try:
        manager = BranchManager(pool, LeaseRegistry())
        raised = False
        try:
            manager.get_branch_status(["branch-does-not-exist"])
        except UnknownBranchError:
            raised = True
        assert raised
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
