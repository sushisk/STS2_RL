"""External Control mode: Decision ID/stale/duplicate/invalid-action safety properties,
across representative Whole Run Decision types (RL担当指示：推論撤去後の総合テスト・デバッグ,
section 2). The 6 Choice-branch types (Map/Event/Combat Pending=Start-of-Combat
Pending/Reward/Shop/Rest) already have Holder/sibling coverage in
`test_worker_pool_process_separation.py`; this file adds the specific
stale/duplicate/invalid-action/no-side-effect-on-rejection guarantees using
`execution_mode.apply_external_action`/`apply_external_room_choice`, on Map, ordinary
Combat ("stable"), and Start-of-Combat Pending (TOOLBOX).

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from choice_branch_runner import inject_relic  # noqa: E402
from execution_mode import StaleDecisionError, apply_external_action, apply_external_room_choice, decision_id  # noqa: E402
from whole_run_session import MAP_SELECT, PENDING_CHOICE, WholeRunSession, pick_default_action  # noqa: E402


def _fresh_session_at_map(seed=18) -> WholeRunSession:
    session = WholeRunSession()
    session.enable_god_mode_for_testing()
    session.start_run(seed, "Ironclad", 0)
    obs = session.get_observation()
    while obs["boundary"] != MAP_SELECT:
        obs = session.step(pick_default_action(session.get_legal_actions())["action_id"])["observation"]
    return session


def test_map_stale_decision_id_rejected_and_no_state_change():
    session = _fresh_session_at_map()
    did = decision_id(session)
    rooms = session.get_map_rooms()
    # advance the session's own decision id by taking a throwaway read... actually need a
    # real state change: capture snapshot, reload it (also a state change from decision_id's
    # perspective only if step_index/boundary move) - simplest reliable staleness: choose a
    # room (a REAL decision), which changes step_index and boundary, THEN try to apply the
    # ORIGINAL decision_id's room choice again.
    apply_external_room_choice(session, did, rooms[0]["room_id"])
    run_state_after_first = session.get_run_state()

    raised = False
    try:
        apply_external_room_choice(session, did, rooms[0]["room_id"])
    except StaleDecisionError:
        raised = True
    assert raised, "the same (now-stale) decision_id must be rejected on a second application (duplicate commit)"
    assert session.get_run_state() == run_state_after_first, "a rejected duplicate must not change Main state further"


def test_map_observation_only_never_changes_state():
    session = _fresh_session_at_map()
    before = session.get_run_state()
    before_obs = session.get_observation()
    session.get_observation()
    session.get_map_rooms()
    session.get_room_context()
    after = session.get_run_state()
    after_obs = session.get_observation()
    assert before == after
    assert before_obs == after_obs


def test_map_invalid_room_id_rejected_and_no_state_change():
    session = _fresh_session_at_map()
    did = decision_id(session)
    before = session.get_run_state()
    raised = False
    try:
        apply_external_room_choice(session, did, 999999)
    except ValueError:
        raised = True
    assert raised
    assert session.get_run_state() == before, "a rejected invalid room_id must not change Main state"


def test_combat_stable_stale_and_duplicate_action_rejected():
    session = _fresh_session_at_map()
    rooms = session.get_map_rooms()
    combat_room = next(r for r in rooms if r["point_type"] == "Monster")
    did = decision_id(session)
    apply_external_room_choice(session, did, combat_room["room_id"])
    assert session.get_observation()["boundary"] == "stable"

    did2 = decision_id(session)
    legal = session.get_legal_actions()
    end_turn = next(a for a in legal if a["action_type"] == "system")
    apply_external_action(session, did2, end_turn["action_id"])
    state_after = session.get_run_state()

    raised = False
    try:
        apply_external_action(session, did2, end_turn["action_id"])
    except StaleDecisionError:
        raised = True
    assert raised, "re-applying the same now-stale in-Combat decision_id must be rejected"
    assert session.get_run_state() == state_after


def test_combat_invalid_action_id_rejected_and_no_state_change():
    session = _fresh_session_at_map()
    rooms = session.get_map_rooms()
    combat_room = next(r for r in rooms if r["point_type"] == "Monster")
    did = decision_id(session)
    apply_external_room_choice(session, did, combat_room["room_id"])
    did2 = decision_id(session)
    before = session.get_run_state()
    raised = False
    try:
        apply_external_action(session, did2, 999999)
    except ValueError:
        raised = True
    assert raised
    assert session.get_run_state() == before


def test_start_of_combat_pending_multi_candidate_requires_explicit_action_and_rejects_stale():
    session = _fresh_session_at_map()
    map_snapshot = session.save_state()
    del session

    injected = inject_relic(map_snapshot, "TOOLBOX")
    sibling = WholeRunSession()
    sibling.enable_god_mode_for_testing()
    sibling.load_state(injected)
    rooms = sibling.get_map_rooms()
    combat_room = next(r for r in rooms if r["point_type"] == "Monster")
    did = decision_id(sibling)
    apply_external_room_choice(sibling, did, combat_room["room_id"])
    assert sibling.get_observation()["boundary"] == PENDING_CHOICE

    legal = sibling.get_legal_actions()
    assert len(legal) >= 2, "Start-of-Combat Pending (TOOLBOX) must offer multiple real candidates"
    did2 = decision_id(sibling)
    chosen = legal[0]
    apply_external_action(sibling, did2, chosen["action_id"])
    state_after = sibling.get_run_state()

    raised = False
    try:
        apply_external_action(sibling, did2, chosen["action_id"])
    except StaleDecisionError:
        raised = True
    assert raised, "re-applying the same Start-of-Combat Pending decision after resolution must be rejected"
    assert sibling.get_run_state() == state_after


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
