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
            "Reproduce the fixed50 End Turn timeout from a saved pre-timeout state. "
            "Default target is fixed50:2428-11, which times out when End Turn is "
            "evaluated from decision 17."
        )
    )
    parser.add_argument("--trajectory-path", type=Path, default=DEFAULT_TRAJECTORIES)
    parser.add_argument("--trajectory-id", default="fixed50:2428-11")
    parser.add_argument(
        "--decision-index",
        type=int,
        default=17,
        help="Decision row to restore before attempting End Turn.",
    )
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

    print(f"trajectory_id={args.trajectory_id}")
    print(f"decision_index={args.decision_index}")
    print("== restored state summary ==")
    print(json.dumps(summarize_state(state), ensure_ascii=False, indent=2))
    print()

    legal_actions = emulator.enumerate_legal_actions(battle_state)
    print("== legal actions ==")
    print(json.dumps(legal_actions, ensure_ascii=False, indent=2))
    print()

    end_turn = next((action for action in legal_actions if action.get("action_id") == 0), None)
    if end_turn is None:
        print("end_turn_present=false")
        return 1

    print("== attempting End Turn ==")
    print(json.dumps(end_turn, ensure_ascii=False, indent=2))

    try:
        next_state = emulator.apply_action(battle_state, end_turn)
    except Exception as exc:  # noqa: BLE001
        print("== step exception ==")
        print(f"exception_type={type(exc).__name__}")
        print(str(exc))
        return 1

    print("== step ok ==")
    print(json.dumps(summarize_state(next_state.engine_state), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
