from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))

from battle_emulator import BattleEmulator, BattleState  # noqa: E402


TRAJECTORY_PATH = ROOT / "Combat" / "data" / "trajectories_fixed50_orbapi_20260721_3" / "trajectories.jsonl"
TRAJECTORY_ID = "fixed50:6420-19"


def summarize_state(state: dict) -> dict:
    return {
        "characterId": state.get("characterId"),
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "maxEnergy": state.get("maxEnergy"),
        "turnNumber": state.get("turnNumber"),
        "combatRoundNumber": state.get("combatRoundNumber"),
        "stepIndex": state.get("stepIndex"),
        "playerPowers": [(p["id"], p["amount"]) for p in state.get("playerPowers") or []],
        "hand": [c["id"] for c in state.get("hand") or []],
        "drawPileTop10": [c["id"] for c in (state.get("drawPile") or [])[:10]],
        "discardPile": [c["id"] for c in state.get("discardPile") or []],
        "exhaustPile": [c["id"] for c in state.get("exhaustPile") or []],
        "enemies": state.get("enemies") or [],
    }


def load_rows() -> list[dict]:
    rows = []
    with TRAJECTORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("trajectory_id") == TRAJECTORY_ID:
                rows.append(row)
    if not rows:
        raise RuntimeError(f"trajectory not found: {TRAJECTORY_ID}")
    return rows


def main() -> int:
    rows = load_rows()
    print(f"trajectory_id={TRAJECTORY_ID}")
    print(f"decision_count={len(rows)}")
    print()
    print("== last 3 decisions ==")
    for row in rows[-3:]:
        print(
            f"decision={row['decision_index']} "
            f"selected={row['selected_action']['label']} "
            f"enemy_before={[(e['id'], (e.get('intent') or {}).get('stateId'), e.get('hp')) for e in row['state'].get('enemies') or []]} "
            f"enemy_after={[(e['id'], (e.get('intent') or {}).get('stateId'), e.get('hp')) for e in row['next_state'].get('enemies') or []]}"
        )
    print()

    stunned_state = rows[-1]["next_state"]
    print("== stunned state ==")
    print(json.dumps(summarize_state(stunned_state), ensure_ascii=False, indent=2))
    print()

    emulator = BattleEmulator()
    battle_state = BattleState(
        engine_state=stunned_state,
        is_terminal=False,
        outcome="in_progress",
        turn=int(stunned_state.get("turnNumber") or 1),
        enemy_max_hps={e["index"]: e["maxHp"] for e in stunned_state.get("enemies") or [] if e.get("index") is not None},
    )

    print("== attempting enumerate_legal_actions from restored STUNNED state ==")
    try:
        legal_actions = emulator.enumerate_legal_actions(battle_state)
        print(f"legal_action_count={len(legal_actions)}")
        print(json.dumps(legal_actions, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"exception_type={type(exc).__name__}")
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
