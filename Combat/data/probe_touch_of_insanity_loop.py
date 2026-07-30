from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))

from battle_emulator import BattleEmulator, BattleState, battle_state_key  # noqa: E402


DEFAULT_TRAJECTORIES = (
    ROOT
    / "Combat"
    / "data"
    / "trajectories_fixed50_20260721_rerun_actioncontinuation_api"
    / "trajectories.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce the fixed50 self-loop where TOUCH_OF_INSANITY is legal, chosen, "
            "and returns an unchanged state."
        )
    )
    parser.add_argument("--trajectory-path", type=Path, default=DEFAULT_TRAJECTORIES)
    parser.add_argument("--trajectory-id", default="fixed50:4228-34")
    parser.add_argument("--decision-index", type=int, default=3)
    return parser.parse_args()


def load_row(trajectory_path: Path, trajectory_id: str, decision_index: int) -> dict:
    with trajectory_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id and row.get("decision_index") == decision_index:
                return row
    raise ValueError(
        f"row not found: trajectory_id={trajectory_id} decision_index={decision_index} path={trajectory_path}"
    )


def summarize_state(state: dict) -> dict:
    return {
        "characterId": state.get("characterId"),
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "turnNumber": state.get("turnNumber"),
        "combatRoundNumber": state.get("combatRoundNumber"),
        "stepIndex": state.get("stepIndex"),
        "relics": [r["id"] for r in state.get("relics") or []],
        "potions": state.get("potions"),
        "playerPowers": [(p["id"], p.get("amount")) for p in state.get("playerPowers") or []],
        "hand": [c["id"] for c in state.get("hand") or []],
        "drawPileTop10": [c["id"] for c in (state.get("drawPile") or [])[:10]],
        "discardPileTop10": [c["id"] for c in (state.get("discardPile") or [])[:10]],
        "exhaustPileTop10": [c["id"] for c in (state.get("exhaustPile") or [])[:10]],
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
        "pendingChoice": state.get("pendingChoice"),
    }


def diff_summary(before: dict, after: dict) -> dict:
    out: dict[str, object] = {}
    keys = [
        "hp",
        "maxHp",
        "block",
        "energy",
        "turnNumber",
        "combatRoundNumber",
        "stepIndex",
        "potions",
        "pendingChoice",
    ]
    for key in keys:
        if before.get(key) != after.get(key):
            out[key] = {"before": before.get(key), "after": after.get(key)}
    pile_keys = ["hand", "drawPile", "discardPile", "exhaustPile", "playerPowers", "enemies"]
    for key in pile_keys:
        if before.get(key) != after.get(key):
            out[key] = {"before": before.get(key), "after": after.get(key)}
    return out


def main() -> int:
    args = parse_args()
    row = load_row(args.trajectory_path, args.trajectory_id, args.decision_index)
    state = row["state"]

    battle_state = BattleState(
        engine_state=state,
        is_terminal=False,
        outcome="in_progress",
        turn=int(state.get("turnNumber") or 1),
        enemy_max_hps={e["index"]: e["maxHp"] for e in state.get("enemies") or [] if e.get("index") is not None},
    )
    emulator = BattleEmulator()
    legal_actions = emulator.enumerate_legal_actions(battle_state)
    action = next((a for a in legal_actions if a.get("label") == "TOUCH_OF_INSANITY"), None)
    if action is None:
        print("touch_of_insanity_present=false")
        print(json.dumps(legal_actions, ensure_ascii=False, indent=2))
        return 1

    print(f"trajectory_id={args.trajectory_id}")
    print(f"decision_index={args.decision_index}")
    print("== restored state summary ==")
    print(json.dumps(summarize_state(state), ensure_ascii=False, indent=2))
    print()
    print("== legal actions ==")
    print(json.dumps(legal_actions, ensure_ascii=False, indent=2))
    print()
    print("== attempting TOUCH_OF_INSANITY ==")
    print(json.dumps(action, ensure_ascii=False, indent=2))

    next_state = emulator.apply_action(battle_state, action)
    before_key = battle_state_key(battle_state)
    after_key = battle_state_key(next_state)
    same_key = before_key == after_key

    print("== next state summary ==")
    print(json.dumps(summarize_state(next_state.engine_state), ensure_ascii=False, indent=2))
    print()
    print(f"same_state_key={same_key}")
    print("== state diff ==")
    print(json.dumps(diff_summary(state, next_state.engine_state), ensure_ascii=False, indent=2))
    return 0 if same_key else 2


if __name__ == "__main__":
    raise SystemExit(main())
