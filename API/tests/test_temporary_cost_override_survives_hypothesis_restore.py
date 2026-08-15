"""Regression coverage for a confirmed bug: a card's TEMPORARY cost override
(`CardModel.SetToFreeThisTurn()`, used by e.g. `AttackPotion` to make the chosen
generated card free for the turn it was picked) is lost whenever that card's hand is
re-derived through a hypothesis-substituted Restore (`derive_substituted_snapshot()` /
`emulate_action()`).

Root cause (confirmed by direct investigation, 2026-08-15): `CardInstanceSnapshot`
(`Combat/combat_state_snapshot.py`) only captures a flat `Cost: int` field - there is no
field for the underlying `TemporaryCardCost.ThisTurnOrUntilPlayed` mechanism that makes
the override temporary. When the Emulator restores a `CombatStateSnapshot`, the card's
cost reverts to its normal, full base cost. If that full cost exceeds the player's
available energy, the card silently disappears from `legal_actions` entirely (it does
not raise; `SemanticAction.resolve()` just reports 0 matches), even though the SAME card
is genuinely present and playable at the TRUE (real, un-hypothesized) root.

Minimal, 100%-reproducing repro (verified manually before writing this test): player
starts combat with only 1 energy, uses ATTACK_POTION, and is offered a high-cost
generated card (seed=3 -> METEOR_STRIKE, true design cost 5). Choosing it makes hand
show `METEOR_STRIKE` at cost 0 (free this turn) and it resolves as a legal `card`
action at the TRUE root. Re-deriving that SAME root under ANY hypothesis rng_id
(1..29 all tried) currently fails 29/29 times - the hypothesis-restored hand contains
only `DEFEND_DEFECT` and `system` (End Turn); METEOR_STRIKE is gone because its
hypothesis-restored cost reverts to 5, unaffordable at energy=1.

This test currently FAILS (documents the bug) - it should start passing once the fix
(preserving/re-applying the temporary cost override across Restore, or an equivalent
mechanism) lands.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402

_TARGET_CARD_ID = "METEOR_STRIKE"  # cost 5, offered by ATTACK_POTION at seed=3
_HYPOTHESIS_RNG_IDS = range(1, 6)  # keep the regression run cheap; the manual repro used 1..29


def _low_energy_attack_potion_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "DEFECT",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["DEFEND_DEFECT"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "relics": [],
        "potions": [{"slot": 0, "potion_id": "ATTACK_POTION"}],
        "energy": 1,
        "seed": 3,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 200, "max_hp": 200}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _find(response: dict, action_type: str, card_id: "str | None" = None) -> "dict | None":
    for action in _legal_actions(response):
        if action["action_type"] != action_type:
            continue
        if card_id is not None and (action["parameters"] or {}).get("cardId") != card_id:
            continue
        return action
    return None


def test_free_this_turn_generated_card_stays_playable_under_hypothesis_restore() -> None:
    inst = CombatInstance("temp-cost-override-regression", _low_energy_attack_potion_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        assert start["masked_emulator_dto"]["energy"] == 1, "test setup: expected to start at 1 energy"

        potion_action = _find(start, "potion")
        assert potion_action is not None, "test setup: ATTACK_POTION must be usable"
        pending = inst.commit_action(start["decision_point_id"], potion_action["action_id"])
        assert pending["status"] == "completed", pending

        choice_action = next(
            (
                a
                for a in _legal_actions(pending)
                if a["action_type"] == "choice_card" and (a["parameters"] or {}).get("cardId") == _TARGET_CARD_ID
            ),
            None,
        )
        assert choice_action is not None, (
            f"test setup: ATTACK_POTION did not offer {_TARGET_CARD_ID} at seed=3 - "
            "the fixed seed/card pool may have changed upstream; re-derive a new repro card"
        )
        stable = inst.commit_action(pending["decision_point_id"], choice_action["action_id"])
        assert stable["status"] == "completed", stable

        # Sanity: the TRUE root really does have the card, free this turn, as a legal action.
        target_action = _find(stable, "card", _TARGET_CARD_ID)
        assert target_action is not None, "TRUE root does not show the generated card as playable at all"
        assert target_action["parameters"]["cost"] == 0, "TRUE root: generated card should be free this turn"

        # The actual regression: re-deriving this SAME root under a hypothesis must not
        # lose the temporary cost override and make the card silently disappear.
        failures: list[tuple[int, str]] = []
        for rng_id in _HYPOTHESIS_RNG_IDS:
            result = inst.emulate_action(
                parent_branch_id="root",
                branch_id=f"hyp-{rng_id}",
                rng_id=rng_id,
                decision_point_id=stable["decision_point_id"],
                action_id=target_action["action_id"],
                simulation_options=None,
            )
            if result["status"] != "completed":
                failures.append((rng_id, result.get("error", "<no error message>")))

        assert not failures, (
            f"{_TARGET_CARD_ID}'s temporary free-this-turn cost override did not survive "
            f"hypothesis-substituted Restore for {len(failures)}/{len(list(_HYPOTHESIS_RNG_IDS))} "
            f"hypotheses: {failures}"
        )
    finally:
        inst.close()


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
