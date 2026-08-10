"""Regression coverage for `StartRun`/`WholeRunSession.start_run`'s `ascension` parameter
actually taking effect end to end.

Background: `StartRun`/`start_run`/`drive_rooms` have accepted an `ascension` argument
since introduction, but until this file was added no test in this package ever launched
a Whole Run with a non-zero ascension - every existing reference was `ascension=0`
(confirmed by grepping this package's test suite). This left the parameter's actual
in-game effect completely unverified from the RL side, even though the Emulator-side
mechanism (`MegaCrit.Sts2.Core.Entities.Ascension.AscensionManager`) was never in doubt.

Two ascension effects are asserted here, both confirmed live at seed=1/IRONCLAD before
writing these assertions (see this file's git history / the investigation that added it):
- Ascension 5+ ("AscendersBane" in `AscensionLevel`) adds one ASCENDERS_BANE curse to the
  player's starting deck (`AscensionManager.ApplyEffectsTo`).
- Ascension 8+ ("ToughEnemies") raises a monster's `MinInitialHp`/`MaxInitialHp` via
  `AscensionHelper.GetValueIfAscension` - every Imported monster file that reads
  `AscensionLevel.ToughEnemies` for its own HP does so this way.

Ascension 10 (`AscensionManager.maxAscensionAllowed`) triggers both, so it is used here as
a single, unambiguous "ascension is definitely active" comparison point against ascension 0.
Same seed/character for both runs so the map generation and first encounter's monster
roster are identical (confirmed live: ascension does not perturb map-gen RNG) - only the
ascension-driven values themselves should differ.

Native assertion runner, no pytest dependency (matches this package's convention).
Run: `python test_ascension_level_effects.py`.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parents[1]
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from choice_branch_runner import new_session  # noqa: E402
from whole_run_session import MAP_SELECT, RUN_TERMINAL, pick_default_action  # noqa: E402

_SEED = 1
_CHARACTER_ID = "IRONCLAD"
_MAX_STEPS = 200


def _drive_to_first_combat(session, ascension: int) -> tuple[list[str], list[dict]]:
    """Starts a run at `ascension` and drives forward (resolving Neow/any other pending
    decision with the same deterministic filler policy `room_progression_driver` uses)
    until the first CombatRoom is entered. Returns (starting deck card ids, that combat's
    enemies) straight from `GameObservation.State` (`GameInstance.BuildFullStateDict`) -
    the same raw source `room_progression_driver.full_state_fields` reads.

    The starting deck reflects ascension effects immediately after `start_run` (Player
    creation applies them before Neow's interactive choice is even reachable) - room
    navigation is only needed to reach a combat for the enemy HP comparison.
    """
    session.start_run(_SEED, _CHARACTER_ID, ascension)
    obs = session.get_observation()
    starting_deck = sorted(c["id"] for c in obs["state"]["deck"])

    for _ in range(_MAX_STEPS):
        if obs["boundary"] == RUN_TERMINAL:
            raise RuntimeError(f"ascension={ascension}: run_terminal before reaching a combat room")
        if obs["boundary"] == MAP_SELECT:
            rooms = session.get_map_rooms()
            entered = session.choose_room(rooms[0]["room_id"])
            obs = session.get_observation()
            if entered["is_combat"]:
                return starting_deck, obs["state"]["enemies"]
            continue
        actions = session.get_legal_actions()
        if not actions:
            raise RuntimeError(f"ascension={ascension}: no legal actions before reaching a combat room")
        result = session.step(pick_default_action(actions)["action_id"])
        obs = result["observation"]

    raise RuntimeError(f"ascension={ascension}: no combat room reached within {_MAX_STEPS} steps")


def test_ascension_10_vs_0_deck_and_enemy_hp_differ():
    deck_0, enemies_0 = _drive_to_first_combat(new_session(), ascension=0)
    deck_10, enemies_10 = _drive_to_first_combat(new_session(), ascension=10)

    # Same seed/character -> same first encounter's monster roster, in the same order -
    # a prerequisite for the HP comparison below to mean anything (otherwise a mismatch
    # could just be "a different monster", not ascension scaling).
    ids_0 = [e["id"] for e in enemies_0]
    ids_10 = [e["id"] for e in enemies_10]
    assert ids_0 == ids_10, (
        f"expected the same monster roster at both ascension levels (same seed), "
        f"got {ids_0} vs {ids_10}"
    )

    assert deck_0 != deck_10, f"expected starting deck to differ, both were {deck_0}"
    assert "ASCENDERS_BANE" not in deck_0, deck_0
    assert deck_10.count("ASCENDERS_BANE") == 1, deck_10
    # The only expected difference is exactly one added curse, not some other divergence.
    assert sorted(deck_10) == sorted(deck_0 + ["ASCENDERS_BANE"]), (deck_0, deck_10)

    hp_0 = [e["hp"] for e in enemies_0]
    hp_10 = [e["hp"] for e in enemies_10]
    assert hp_0 != hp_10, f"expected enemy HP to differ, both were {hp_0}"
    assert all(a > b for a, b in zip(hp_10, hp_0)), (
        f"expected every enemy's ascension-10 HP to exceed its ascension-0 HP, got {hp_0} vs {hp_10}"
    )
    max_hp_0 = [e["maxHp"] for e in enemies_0]
    max_hp_10 = [e["maxHp"] for e in enemies_10]
    assert max_hp_0 == hp_0 and max_hp_10 == hp_10, "expected full-HP enemies at combat start"


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
