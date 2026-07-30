from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))

from battle_emulator import BattleEmulator  # noqa: E402


def make_single_enemy_spec() -> dict:
    return {
        "character_id": "SILENT",
        "player_hp": 70,
        "player_max_hp": 70,
        "hand_cards": [
            {"card_id": "WHISTLE", "is_upgraded": False},
        ],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [
            {"monster_id": "CALCIFIED_CULTIST", "hp": 48, "max_hp": 48},
        ],
    }


def make_multi_enemy_spec() -> dict:
    return {
        "character_id": "SILENT",
        "player_hp": 70,
        "player_max_hp": 70,
        "hand_cards": [
            {"card_id": "WHISTLE", "is_upgraded": False},
        ],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [
            {"monster_id": "CALCIFIED_CULTIST", "hp": 48, "max_hp": 48},
            {"monster_id": "SLUDGE_SPINNER", "hp": 42, "max_hp": 42},
        ],
    }


def enemy_summary(state: dict) -> list[dict]:
    enemies = []
    for enemy in state.get("enemies") or []:
        intent = enemy.get("intent") or {}
        enemies.append(
            {
                "index": enemy.get("index"),
                "id": enemy.get("id"),
                "hp": enemy.get("hp"),
                "maxHp": enemy.get("maxHp"),
                "isAlive": enemy.get("isAlive"),
                "intentStateId": intent.get("stateId"),
                "intentTypes": intent.get("intentTypes"),
                "attackDamage": intent.get("attackDamage"),
                "attackRepeats": intent.get("attackRepeats"),
                "stateLog": enemy.get("stateLog") or [],
            }
        )
    return enemies


def player_summary(state: dict) -> dict:
    return {
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "turnNumber": state.get("turnNumber"),
        "combatRoundNumber": state.get("combatRoundNumber"),
        "stepIndex": state.get("stepIndex"),
        "hand": [c["id"] for c in state.get("hand") or []],
        "discardPile": [c["id"] for c in state.get("discardPile") or []],
        "exhaustPile": [c["id"] for c in state.get("exhaustPile") or []],
    }


def find_action(legal_actions: list[dict], label: str, action_type: str | None = None) -> dict:
    matches = [
        action for action in legal_actions
        if action["label"] == label and (action_type is None or action["action_type"] == action_type)
    ]
    if not matches:
        raise RuntimeError(f"action not found: label={label!r} action_type={action_type!r}")
    return matches[0]


def snapshot(tag: str, state: dict, legal_actions: list[dict]) -> dict:
    return {
        "tag": tag,
        "player": player_summary(state),
        "enemies": enemy_summary(state),
        "legalActions": [
            {
                "action_id": a["action_id"],
                "action_type": a["action_type"],
                "label": a["label"],
                "parameters": a.get("parameters") or {},
            }
            for a in legal_actions
        ],
    }


def run_branch(spec: dict, use_whistle: bool, target_enemy_index: int, post_stun_end_turns: int) -> list[dict]:
    emulator = BattleEmulator()
    state = emulator.initialize(deepcopy(spec))
    history = [snapshot("initial", state.engine_state, emulator.enumerate_legal_actions(state))]

    if use_whistle:
        whistle = find_action(history[-1]["legalActions"], "WHISTLE", "card")
        state = emulator.apply_action(
            state,
            whistle,
            target_enemy_index=target_enemy_index,
        )
        history.append(snapshot("after_whistle", state.engine_state, emulator.enumerate_legal_actions(state)))

    end_turn = find_action(emulator.enumerate_legal_actions(state), "End Turn", "system")
    state = emulator.apply_action(state, end_turn)
    history.append(snapshot("after_end_turn_1", state.engine_state, emulator.enumerate_legal_actions(state)))

    for i in range(post_stun_end_turns - 1):
        end_turn = find_action(emulator.enumerate_legal_actions(state), "End Turn", "system")
        state = emulator.apply_action(state, end_turn)
        history.append(snapshot(f"after_end_turn_{i + 2}", state.engine_state, emulator.enumerate_legal_actions(state)))

    return history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe whether player-applied STUNNED via WHISTLE changes enemy move sequencing or enemy ordering."
    )
    parser.add_argument(
        "--scenario",
        choices=("single", "multi"),
        default="multi",
        help="single = one target enemy only, multi = target plus a second control enemy.",
    )
    parser.add_argument(
        "--target-enemy-index",
        type=int,
        default=0,
        help="Stable enemy index to target with WHISTLE in the Whistle branch.",
    )
    parser.add_argument(
        "--post-stun-end-turns",
        type=int,
        default=3,
        help="How many player End Turns to execute after the Whistle branch reaches the stun point.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spec = make_single_enemy_spec() if args.scenario == "single" else make_multi_enemy_spec()

    baseline = run_branch(spec, use_whistle=False, target_enemy_index=args.target_enemy_index, post_stun_end_turns=args.post_stun_end_turns)
    whistle = run_branch(spec, use_whistle=True, target_enemy_index=args.target_enemy_index, post_stun_end_turns=args.post_stun_end_turns)

    output = {
        "scenario": args.scenario,
        "target_enemy_index": args.target_enemy_index,
        "card": "WHISTLE",
        "notes": [
            "Compare baseline vs whistle branch to see whether enemy intent order, stateLog progression, and enemy index ordering diverge.",
            "after_whistle should show the stunned target with intentStateId=STUNNED if the effect is applied immediately.",
            "after_end_turn_1 is the key frame for whether STUNNED consumes the enemy's next move and what follow-up move appears next.",
        ],
        "spec": spec,
        "baseline": baseline,
        "whistle": whistle,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
