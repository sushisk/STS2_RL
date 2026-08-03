"""Regression coverage for battle_emulator.py's StepResult.Transition translation.

Emulator commit dd8c800 ("Separate combat completion result from current Boundary in
StepResult") added `StepResult.Transition` (`TransitionOutcome`), the new authoritative
Combat-end signal (`Transition.Kind == "combat_completed"`). `BattleEmulator.step_live_action`
was updated to use it (wrapping `Transition.FinalObservation` instead of
`StepResult.Observation` on the concluding Step) - this file exists so a future change to
that translation can't silently regress Combat's existing terminal/outcome contract, which
Search/Main Loop/Shadow/endurance all depend on transitively via `BattleState.is_terminal`.

Native assertion runner, no pytest dependency - matches this package's existing test files.
Run: `python test_battle_emulator_transition_outcome.py`.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from live_combat_session import LiveCombatSession  # noqa: E402


def _spec(*, hand: list[str], enemy_hp: int = 1, seed: int = 1) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand,
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": seed,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def test_victory_step_wraps_final_observation_and_reports_terminal_victory():
    session = LiveCombatSession()
    state = session.start_combat(_spec(hand=["WHIRLWIND"], enemy_hp=1))
    legal = session._emulator.enumerate_legal_actions(state)  # noqa: SLF001
    whirlwind = next(a for a in legal if a["action_type"] == "card" and a["parameters"].get("cardId") == "WHIRLWIND")
    next_state = session.step(state, whirlwind)

    assert next_state.is_terminal is True
    assert next_state.outcome == "victory"
    # No living enemies left, matching the wrapped FinalObservation - see
    # battle_emulator.state_has_living_enemies (the same field this Transition-based
    # translation must still agree with, since coerce_terminal_observation's defensive
    # fallback further down the same function relies on it for non-Transition cases).
    assert not any(
        (e.get("isAlive", True) and (e.get("hp", 0) or 0) > 0) for e in (next_state.engine_state.get("enemies") or [])
    )


def test_defeat_step_reports_terminal_defeat():
    session = LiveCombatSession()
    # STUNNED-immune large enemy, weak player action, godmode off - not directly settable
    # here, so instead drive a losing combat via an enemy that can one-shot a low-HP
    # player using the existing player_hp override.
    state = session.start_combat(
        {
            "character_id": "IRONCLAD",
            "player_hp": 1,
            "player_max_hp": 80,
            "hand": ["DEFEND_IRONCLAD"],
            "draw_pile": [],
            "discard_pile": [],
            "exhaust_pile": [],
            "player_powers": [],
            "relics": [],
            "potions": [],
            "seed": 1,
            "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999}],
        }
    )
    legal = session._emulator.enumerate_legal_actions(state)  # noqa: SLF001
    end_turn = next(a for a in legal if a["action_type"] == "system")
    next_state = state
    for _ in range(5):
        if next_state.is_terminal:
            break
        next_state = session.step(next_state, end_turn)
        legal = session._emulator.enumerate_legal_actions(next_state)
        end_turn = next((a for a in legal if a["action_type"] == "system"), end_turn)

    assert next_state.is_terminal is True
    assert next_state.outcome == "defeat"


def test_non_concluding_step_leaves_state_non_terminal():
    session = LiveCombatSession()
    state = session.start_combat(_spec(hand=["DEFEND_IRONCLAD"], enemy_hp=999))
    legal = session._emulator.enumerate_legal_actions(state)  # noqa: SLF001
    defend = next(a for a in legal if a["action_type"] == "card" and a["parameters"].get("cardId") == "DEFEND_IRONCLAD")
    next_state = session.step(state, defend)

    assert next_state.is_terminal is False
    assert next_state.outcome == "in_progress"


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
