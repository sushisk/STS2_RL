"""One-off empirical probe (not part of the production harness, does not modify
choice_semantics.py/online_policy_eval.py/schema/lookup files) for Stage B's two open
questions:

1. GamblingChipDiscard - which relic triggers it, and what does it ACTUALLY do
   (observed via real before/after hand/discard-pile state, not inferred from the name).
2. Potion originEntityType naming - is the PascalCase C# class name pattern consistent
   across multiple different potions (not just the 5 seen in Stage A), i.e. is it a
   predictable, mechanical transform of the potion id.

Run: cd C:\\STS2_RL\\Combat\\evaluation\\online_eval && python probe_gambling_chip_and_potions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleEmulator  # noqa: E402
from emulator_bridge import to_plain, legal_actions_to_list  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402


def probe_gambling_chip():
    print("=== GAMBLING_CHIP relic ===")
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "relics": ["GAMBLING_CHIP"],
        "hand_cards": [
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
        ],
        "draw_pile_cards": [{"card_id": "BASH", "is_upgraded": False}],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    engine_state = state.engine_state
    pending = engine_state.get("pendingChoice")
    print("pendingChoice at combat start:", pending)
    if pending is None:
        print("(no pendingChoice at start - GAMBLING_CHIP may fire later or need a different trigger)")
        return

    table = ChoiceSemanticsTable()
    resolution = table.resolve(pending)
    print("choice_semantics.resolve():", resolution)

    hand_before = [c["id"] for c in engine_state.get("hand") or []]
    draw_before = [c["id"] for c in engine_state.get("drawPile") or []]
    discard_before = [c["id"] for c in engine_state.get("discardPile") or []]
    print(f"before: hand={hand_before} draw={draw_before} discard={discard_before}")

    # Answer the choice: pick both STRIKE_IRONCLAD entries to discard (mirrors
    # smoke_choice_context.py's own pattern of driving a real choice_card/choice_confirm
    # sequence through the live GameInstance).
    game = emu._restore(state)  # noqa: SLF001 - white-box, matches existing test pattern
    legal = legal_actions_to_list(game.GetLegalActions())
    choice_actions = [a for a in legal if a["action_type"] == "choice_card"]
    print("choice_card options:", [(a["label"], a["parameters"]) for a in choice_actions])
    picked_labels = []
    for a in choice_actions[:2]:
        result = game.Step(a["action_id"])
        picked_labels.append(a["label"])
        new_pending = to_plain(result.Observation.State).get("pendingChoice")
        print(f"  after picking {a['label']}: pendingChoice={new_pending}")
        legal = legal_actions_to_list(result.LegalActions)
        confirm = next((x for x in legal if x["action_type"] == "choice_confirm"), None)
        if new_pending is None:
            break
    else:
        confirm_actions = legal_actions_to_list(result.LegalActions)
        confirm = next((x for x in confirm_actions if x["action_type"] == "choice_confirm"), None)
        if confirm:
            result = game.Step(confirm["action_id"])

    final_state = to_plain(result.Observation.State)
    hand_after = [c["id"] for c in final_state.get("hand") or []]
    draw_after = [c["id"] for c in final_state.get("drawPile") or []]
    discard_after = [c["id"] for c in final_state.get("discardPile") or []]
    print(f"picked: {picked_labels}")
    print(f"after:  hand={hand_after} draw={draw_after} discard={discard_after}")
    print(f"pendingChoice after resolution: {final_state.get('pendingChoice')}")

    # Determinism check - run the whole thing again, compare.
    print("\n--- determinism re-check ---")
    state2 = emu.initialize(spec)
    pending2 = state2.engine_state.get("pendingChoice")
    print("second run pendingChoice matches first:", pending2 == pending)


def probe_potion_origin_types():
    print("\n=== potion originEntityType naming across multiple potions ===")
    potions_to_test = [
        "COLORLESS_POTION", "SKILL_POTION", "POWER_POTION", "ATTACK_POTION",
        "TOUCH_OF_INSANITY", "ASHWATER", "DROPLET_OF_PRECOGNITION", "LIQUID_MEMORIES",
    ]
    for potion_id in potions_to_test:
        emu = BattleEmulator()
        spec = {
            "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
            "potions": [{"slot": 0, "potion_id": potion_id}],
            "hand_cards": [
                {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
                {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
            ],
            "discard_pile_cards": [{"card_id": "BASH", "is_upgraded": False}],
            "draw_pile_cards": [{"card_id": "STRIKE_IRONCLAD", "is_upgraded": False}],
            "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
        }
        try:
            state = emu.initialize(spec)
            legal = emu.enumerate_legal_actions(state)
            potion_action = next(
                (a for a in legal if a["action_type"] == "potion" and a["parameters"].get("potionId") == potion_id), None
            )
            if potion_action is None:
                print(f"{potion_id}: no potion legal_action found (targetType/slot issue?) - skipped")
                continue
            game = emu._restore(state)  # noqa: SLF001
            result = game.Step(potion_action["action_id"])
            pending = to_plain(result.Observation.State).get("pendingChoice")
            if pending is None:
                print(f"{potion_id}: no pendingChoice triggered (unexpected - all 8 should per Stage A/lookup table)")
                continue
            print(f"{potion_id}: originEntityType={pending.get('originEntityType')!r} originEntityId={pending.get('originEntityId')!r} choiceOperation={pending.get('choiceOperation')!r} sourceZone={pending.get('sourceZone')!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{potion_id}: EXCEPTION {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    probe_gambling_chip()
    probe_potion_origin_types()
