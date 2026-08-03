"""Additional Fault/abnormal-path coverage for Whole Run (RL担当指示：推論撤去後の総合テスト・
デバッグ, section 8) not already covered by `test_worker_pool_fault_and_respawn`-style tests
in `test_worker_pool_process_separation.py`: Snapshot Load failure, Episode close with an
incomplete/held Branch, and rejecting progress requests made without an explicit external
instruction.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from process_choice_branch_runner import CHOICE_SHOP, run_choice_branch  # noqa: E402
from worker_pool import ExploreRequest, LeaseRegistry, WholeRunWorkerPool  # noqa: E402
from whole_run_session import WholeRunSession  # noqa: E402


def test_snapshot_load_failure_raises_cleanly_and_session_remains_usable_after_reload():
    session = WholeRunSession()
    session.enable_god_mode_for_testing()
    session.start_run(18, "Ironclad", 0)
    good_snapshot = None
    obs = session.get_observation()
    while obs["boundary"] != "map_select":
        obs = session.step(session.get_legal_actions()[0]["action_id"])["observation"]
    good_snapshot = session.save_state()

    raised = False
    try:
        session.load_state("{not valid json at all")
    except Exception:  # noqa: BLE001 - the Emulator raises a JSON/InvalidOperation-family exception, not ours to name
        raised = True
    assert raised, "loading a corrupted snapshot string must raise, not silently succeed"

    # Session must still be usable after reloading a KNOWN GOOD snapshot on the SAME
    # object (the sanctioned recovery per the Whole Run API reference: call
    # LoadState again on the same instance to reclaim ownership/a valid state).
    session.load_state(good_snapshot)
    assert session.get_observation()["boundary"] == "map_select"


def test_episode_close_with_holder_lease_still_active_shuts_down_cleanly():
    """A Branch attempt establishes a Holder (a Lease is held on a Branch Worker) and the
    pool is closed WITHOUT resolving it - `close()` must not hang or leave a zombie
    process; verified by successfully completing `close()` within its own join timeouts.
    """
    pool = WholeRunWorkerPool(branch_worker_count=2)
    explored = pool.explore(
        ExploreRequest(seed=18, character_id="Ironclad", ascension=0, min_rooms=10, max_steps=1500,
                       target_room_types=["MerchantRoom"])
    )
    registry = LeaseRegistry()
    info = explored.found_snapshots["MerchantRoom"]

    from worker_pool import ChoiceWorkItem, WORK_KIND_SUB_BRANCH
    from process_choice_branch_runner import BOUNDARY_FOR_CHOICE, CHOICE_SHOP

    context_id = "episode-close-test"
    establish_item = ChoiceWorkItem(
        work_id="establish", context_id=context_id, choice_type=CHOICE_SHOP,
        map_snapshot=info["map_snapshot"], room_id=info["room_id"], action_prefix=[],
        relic_injection=None, target_boundary=BOUNDARY_FOR_CHOICE[CHOICE_SHOP], work_kind=WORK_KIND_SUB_BRANCH,
    )
    (result,) = pool.dispatch_choice_work_items([establish_item], registry)
    assert result.status == "success"
    assert registry.get(context_id) is not None, "a Lease must be held (Holder established, not yet resolved)"

    # Episode close while the Branch/Lease is still incomplete - must not hang.
    pool.close()
    for slot in pool.worker_slots:
        assert not pool.is_worker_alive(slot), f"worker {slot!r} should be shut down after close()"


def test_progress_request_without_external_instruction_is_structurally_impossible():
    """`run_choice_branch`/`WholeRunSession.step`/`choose_room` all require an explicit
    action_id/room_id argument - there is no "just continue" call that advances Main
    without one. This is a static/structural check (calling with a missing required
    argument is a TypeError), confirming the interface itself cannot express "progress
    without an external instruction".
    """
    session = WholeRunSession()
    session.enable_god_mode_for_testing()
    session.start_run(18, "Ironclad", 0)
    raised = False
    try:
        session.step()  # type: ignore[call-arg]
    except TypeError:
        raised = True
    assert raised, "WholeRunSession.step() must require an explicit action_id"

    raised_room = False
    try:
        session.choose_room()  # type: ignore[call-arg]
    except TypeError:
        raised_room = True
    assert raised_room, "WholeRunSession.choose_room() must require an explicit room_id"


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
