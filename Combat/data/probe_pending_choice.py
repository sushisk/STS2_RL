from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))

from battle_emulator import BattleEmulator, BattleState  # noqa: E402


DEFAULT_TRAJECTORIES = ROOT / "Combat" / "data" / "trajectories_fixed50_orbapi_20260721_3" / "trajectories.jsonl"


def load_first_row(trajectory_path: Path, trajectory_id: str) -> dict:
    with trajectory_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id and row.get("decision_index") == 0:
                return row
    raise ValueError(f"decision_index=0 row not found for trajectory_id={trajectory_id}")


def action_by_id_or_label(legal_actions: list[dict], action_id: int | None, label: str | None) -> dict:
    if action_id is not None:
        for action in legal_actions:
            if action["action_id"] == action_id:
                return action
        raise ValueError(f"action_id={action_id} not found")
    if label is not None:
        matches = [a for a in legal_actions if a["label"] == label]
        if not matches:
            raise ValueError(f"label={label!r} not found")
        if len(matches) > 1:
            ids = [a["action_id"] for a in matches]
            raise ValueError(f"label={label!r} is ambiguous; candidate action_ids={ids}")
        return matches[0]
    raise ValueError("either action_id or label is required")


def summarize_state(state: dict) -> dict:
    return {
        "characterId": state.get("characterId"),
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "maxEnergy": state.get("maxEnergy"),
        "stars": state.get("stars"),
        "turnNumber": state.get("turnNumber"),
        "combatRoundNumber": state.get("combatRoundNumber"),
        "stepIndex": state.get("stepIndex"),
        "orbSlots": state.get("orbSlots"),
        "orbs": state.get("orbs"),
        "hand": [c["id"] for c in state.get("hand") or []],
        "drawPileTop": [c["id"] for c in (state.get("drawPile") or [])[:10]],
        "discardPileTop": [c["id"] for c in (state.get("discardPile") or [])[:10]],
        "playPile": [c["id"] for c in state.get("playPile") or []],
        "enemies": [
            {
                "index": e.get("index"),
                "id": e.get("id"),
                "hp": e.get("hp"),
                "maxHp": e.get("maxHp"),
                "block": e.get("block"),
                "intent": (e.get("intent") or {}).get("stateId"),
            }
            for e in state.get("enemies") or []
        ],
    }


def replay_prefix(state0: dict, prefix: list[int]) -> tuple[BattleEmulator, BattleState]:
    emulator = BattleEmulator()
    battle_state = BattleState(
        engine_state=state0,
        is_terminal=False,
        outcome="in_progress",
        turn=int(state0.get("turnNumber") or 1),
        enemy_max_hps={e["index"]: e["maxHp"] for e in state0.get("enemies") or [] if e.get("index") is not None},
    )
    for action_id in prefix:
        legal_actions = emulator.enumerate_legal_actions(battle_state)
        action = action_by_id_or_label(legal_actions, action_id=action_id, label=None)
        battle_state = emulator.apply_action(battle_state, action)
    return emulator, battle_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a pending-choice state and try a specific offered choice_card/choice action."
    )
    parser.add_argument("--trajectory-path", type=Path, default=DEFAULT_TRAJECTORIES)
    parser.add_argument("--trajectory-id", default="fixed50:3337-22")
    parser.add_argument(
        "--prefix",
        nargs="*",
        type=int,
        default=[1, 1, 1],
        help="Action IDs to replay before probing the pending-choice state.",
    )
    parser.add_argument("--choice-action-id", type=int, help="Action ID to execute at the pending-choice state.")
    parser.add_argument("--choice-label", help="Action label to execute at the pending-choice state.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    row0 = load_first_row(args.trajectory_path, args.trajectory_id)
    emulator, battle_state = replay_prefix(row0["state"], args.prefix)
    legal_actions = emulator.enumerate_legal_actions(battle_state)

    print("== pending-choice state ==")
    print(json.dumps(summarize_state(battle_state.engine_state), ensure_ascii=False, indent=2))
    print("== legal actions ==")
    print(json.dumps(legal_actions, ensure_ascii=False, indent=2))

    if args.choice_action_id is None and not args.choice_label:
        return 0

    action = action_by_id_or_label(legal_actions, action_id=args.choice_action_id, label=args.choice_label)
    print("== selected choice ==")
    print(json.dumps(action, ensure_ascii=False, indent=2))

    try:
        next_state = emulator.apply_action(battle_state, action)
    except Exception as exc:
        print("== step exception ==")
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print("== step ok ==")
    print(json.dumps(summarize_state(next_state.engine_state), ensure_ascii=False, indent=2))
    print("== next legal actions ==")
    print(json.dumps(emulator.enumerate_legal_actions(next_state), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
