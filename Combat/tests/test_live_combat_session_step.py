"""Tests for LiveCombatSession.step(stop_at_pending=...).

Native assertion runner, no pytest dependency. Uses real Emulator/LiveCombatSession
fixtures and never mocks the Emulator. Run:
cd C:\\STS2_RL\\Combat\\tests && python test_live_combat_session_step.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import is_action_continuation_pending_choice  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.decision_context import BOUNDARY_PENDING, BOUNDARY_STABLE, boundary_of_battle_state  # noqa: E402


def _liquid_memories_spec(discard_pile):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": discard_pile,
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _liquid_memories_action(state):
    return next(a for a in state._cached_legal_actions if a["action_type"] == "potion")  # noqa: SLF001


def test_stop_at_pending_true_returns_action_continuation_pending():
    session = LiveCombatSession()
    state = session.start_combat(_liquid_memories_spec(["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]))

    next_state = session.step(state, _liquid_memories_action(state), stop_at_pending=True)

    assert boundary_of_battle_state(next_state) == BOUNDARY_PENDING, next_state.engine_state.get("pendingChoice")
    assert is_action_continuation_pending_choice(next_state.engine_state), next_state.engine_state.get("pendingChoice")
    assert {a["action_type"] for a in next_state._cached_legal_actions} == {"choice_card"}  # noqa: SLF001


def test_default_step_auto_resolves_action_continuation_pending_to_stable():
    session = LiveCombatSession()
    state = session.start_combat(_liquid_memories_spec(["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"]))

    next_state = session.step(state, _liquid_memories_action(state))

    assert boundary_of_battle_state(next_state) == BOUNDARY_STABLE, next_state.engine_state.get("pendingChoice")
    assert not is_action_continuation_pending_choice(next_state.engine_state)


def test_single_candidate_liquid_memories_never_publishes_pending_choice():
    for stop_at_pending in (False, True):
        session = LiveCombatSession()
        state = session.start_combat(_liquid_memories_spec(["DEFEND_IRONCLAD"]))

        next_state = session.step(state, _liquid_memories_action(state), stop_at_pending=stop_at_pending)

        assert boundary_of_battle_state(next_state) == BOUNDARY_STABLE, next_state.engine_state.get("pendingChoice")
        assert not is_action_continuation_pending_choice(next_state.engine_state)
        assert next_state.engine_state.get("pendingChoice") is None


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
