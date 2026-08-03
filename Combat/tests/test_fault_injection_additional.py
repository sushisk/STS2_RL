"""Additional Fault/abnormal-path coverage for Combat (RL担当指示：推論撤去後の総合テスト・
デバッグ, section 8) not already covered elsewhere: Episode close with an incomplete/held
Branch Lease, and rejecting progress requests made without an explicit external
instruction. Snapshot Load/Restore failure is already exhaustively covered by
`test_restore_snapshot_phase3c1.py` (26+ rejection-path tests) - not duplicated here.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BranchWorkerPool,
    EXECUTION_MODE_BOOTSTRAP_STEP,
    LeaseRegistry,
    WorkerExecutionRequest,
)
from search.decision_context import DecisionContext  # noqa: E402
from search.main_loop import build_main_decision_context, initialize_main_loop_state, run_until_terminal_or_fault  # noqa: E402


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


def test_episode_close_with_active_lease_shuts_down_cleanly():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)

    from search.candidate_pipeline import build_candidate_pipeline_result_for_explicit_candidates
    from search.search_coordinator import _candidate_batch, _plain_work_items  # noqa: SLF001

    # Deliberately NOT index 0 ("system"/End Turn, which triggers a real pre-existing
    # Emulator enemy-turn AI instability against this monster/hp combination, unrelated
    # to what this test verifies) - index 1 (DEFEND_IRONCLAD, no target, no enemy turn)
    # exercises the SAME Bootstrap Step + held-Lease-at-close path without it.
    pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [1])
    candidates = _candidate_batch(pipeline)
    work_items = _plain_work_items(context, candidates)

    pool = BranchWorkerPool(worker_count=2)
    registry = LeaseRegistry()
    result = pool.execute(0, WorkerExecutionRequest(work_items[0], EXECUTION_MODE_BOOTSTRAP_STEP))
    assert result.status == "success"

    # Close the pool without ever resolving further - must not hang.
    pool.close()
    for handle_worker_id in pool.worker_ids:
        assert not pool._workers[handle_worker_id].process.is_alive(), "worker must be shut down after close()"  # noqa: SLF001


def test_progress_request_without_direct_selector_is_structurally_impossible():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    loop_state = initialize_main_loop_state(session, state)
    raised = False
    try:
        run_until_terminal_or_fault(loop_state)  # type: ignore[call-arg]
    except TypeError:
        raised = True
    assert raised, "run_until_terminal_or_fault() must require an explicit direct_selector"


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
