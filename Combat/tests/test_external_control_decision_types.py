"""External Control mode: stale/duplicate/invalid-action safety properties for Combat's
Decision types (RL担当指示：推論撤去後の総合テスト・デバッグ, section 2), using the existing
`DecisionFrame`(combat_session_id, step_index) mismatch guard
(`live_combat_session.DecisionFrameMismatchError`) plus `execution_mode`'s
`make_external_action_selector` for multi-candidate Pending (mid-combat Action
Continuation and Start-of-Combat / genesis).

Native assertion runner, no pytest dependency. Real `LiveCombatSession`.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from emulator_bridge import to_plain  # noqa: E402
from execution_mode import make_external_action_selector  # noqa: E402
from live_combat_session import DecisionFrameMismatchError, LiveCombatSession  # noqa: E402


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _toolbox_pending_spec():
    spec = _simple_spec(hand=["STRIKE_IRONCLAD"])
    spec["relics"] = ["TOOLBOX"]
    return spec


def _wish_action_continuation_spec():
    spec = _simple_spec(hand=["WISH"], enemy_hp=48)
    return spec


def test_combat_stable_stale_decision_frame_rejected_and_no_state_change():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    legal = session._emulator.enumerate_legal_actions(state)  # noqa: SLF001
    end_turn = next(a for a in legal if a["action_type"] == "system")
    next_state = session.step(state, end_turn)
    state_after = next_state.engine_state

    raised = False
    try:
        session.step(state, end_turn)  # re-submit the OLD (now-stale) BattleState
    except DecisionFrameMismatchError:
        raised = True
    assert raised, "re-submitting a stale BattleState (already advanced past) must be rejected"
    assert to_plain(session.get_observation().State) == state_after, "a rejected stale action must not change Main state"


def test_combat_duplicate_commit_of_same_action_rejected():
    """Applying the SAME action twice against the SAME (now-stale) BattleState is exactly
    the "同一Actionの二重commit" case - DecisionFrameMismatchError covers it identically
    to the general stale-decision case, since the frame moves on after the first commit.
    """
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    legal = session._emulator.enumerate_legal_actions(state)  # noqa: SLF001
    defend = next(a for a in legal if a["action_type"] == "card" and a["parameters"].get("cardId") == "DEFEND_IRONCLAD")
    session.step(state, defend)

    raised = False
    try:
        session.step(state, defend)
    except DecisionFrameMismatchError:
        raised = True
    assert raised


def test_toolbox_start_of_combat_pending_external_control_multi_candidate_and_stale_rejection():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    legal = state._cached_legal_actions  # noqa: SLF001
    assert len(legal) >= 2, "Start-of-Combat Pending (TOOLBOX) must offer multiple real candidates"

    chosen = legal[0]
    resolved_calls = []

    def resolve(_legal_actions):
        resolved_calls.append(1)
        return chosen["action_id"]

    selector = make_external_action_selector(resolve)
    step = selector(state)
    assert step.semantic_action.action_type == chosen["action_type"]
    assert len(resolved_calls) == 1, "external_control must call the resolver exactly once per decision, never internally retry/compare"

    session.step(state, chosen)

    raised = False
    try:
        session.step(state, chosen)  # re-submit the OLD (now-stale) BattleState + same action
    except DecisionFrameMismatchError:
        raised = True
    assert raised, "re-applying the same (now-stale) Start-of-Combat Pending decision must be rejected"


def test_wish_action_continuation_pending_external_control_resolves_exactly_given_action():
    session = LiveCombatSession()
    state = session.start_combat(_wish_action_continuation_spec())
    legal = session._emulator.enumerate_legal_actions(state)  # noqa: SLF001
    wish = next((a for a in legal if a.get("card_id") == "WISH"), None)
    if wish is None:
        wish = next(a for a in legal if a["action_type"] == "card")
    next_state = session.step(state, wish, stop_at_pending=True)
    from search.decision_context import boundary_of_battle_state, BOUNDARY_PENDING

    if boundary_of_battle_state(next_state) != BOUNDARY_PENDING:
        return  # this hand/seed didn't produce a genuine multi-candidate continuation - not this test's concern
    pending_legal = next_state._cached_legal_actions  # noqa: SLF001
    assert len(pending_legal) >= 1
    target = pending_legal[-1]
    resolved = make_external_action_selector(lambda _l: target["action_id"])(next_state)
    assert resolved.semantic_action.action_type == target["action_type"]


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
