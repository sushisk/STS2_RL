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
    / "trajectories_fixed50_20260722_doomfix_serial"
    / "trajectories.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce fixed50:6485-37 where a non-terminal state ends up with no legal "
            "actions after playing VOID_FORM."
        )
    )
    parser.add_argument("--trajectory-path", type=Path, default=DEFAULT_TRAJECTORIES)
    parser.add_argument("--trajectory-id", default="fixed50:6485-37")
    parser.add_argument("--decision-index", type=int, default=10)
    parser.add_argument("--action-id", type=int, default=2, help="Default is VOID_FORM in decision 10.")
    return parser.parse_args()


def load_rows(trajectory_path: Path, trajectory_id: str) -> list[dict]:
    rows = []
    with trajectory_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id:
                rows.append(row)
    rows.sort(key=lambda row: row["decision_index"])
    if not rows:
        raise ValueError(f"trajectory not found: trajectory_id={trajectory_id} path={trajectory_path}")
    return rows


def load_row(trajectory_path: Path, trajectory_id: str, decision_index: int) -> dict:
    with trajectory_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
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
        "stars": state.get("stars"),
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
                "attackDamage": (e.get("intent") or {}).get("attackDamage"),
                "attackRepeats": (e.get("intent") or {}).get("attackRepeats"),
            }
            for e in state.get("enemies") or []
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def choose_matching_action(legal_actions: list[dict], expected_row: dict) -> dict:
    selected = expected_row["selected_action"]
    selected_action_id = selected["action_id"]
    selected_label = selected["label"]
    selected_enemy_index = expected_row.get("selected_enemy_index")
    matches = [a for a in legal_actions if a.get("action_id") == selected_action_id and a.get("label") == selected_label]
    if not matches:
        raise ValueError(f"could not find expected action in legal actions: {selected}")
    # Current action ids are unique in a given legal_actions list; if that changes, we still
    # prefer the exact id+label pair here and let BattleEmulator resolve targeting internally.
    _ = selected_enemy_index
    return matches[0]


def infer_pending_choice_action(legal_actions: list[dict], pending_state: dict, next_logged_state: dict | None) -> dict:
    option_ids = [c.get("id") for c in (pending_state.get("pendingChoice") or {}).get("options") or []]
    next_discard = [c.get("id") for c in next_logged_state.get("discardPile") or []] if next_logged_state else []
    next_exhaust = [c.get("id") for c in next_logged_state.get("exhaustPile") or []] if next_logged_state else []
    chosen_id = None
    for option_id in option_ids:
        if option_id in next_discard or option_id in next_exhaust:
            chosen_id = option_id
            break
    if chosen_id is None and option_ids:
        chosen_id = option_ids[0]
    for action in legal_actions:
        if action.get("action_type") == "choice_card" and action.get("label") == chosen_id:
            return action
    raise ValueError(f"could not infer pending-choice action for options={option_ids}")


def main() -> int:
    args = parse_args()
    rows = load_rows(args.trajectory_path, args.trajectory_id)
    row0 = rows[0]
    state0 = row0["state"]
    battle_state = BattleState(
        engine_state=state0,
        is_terminal=False,
        outcome="in_progress",
        turn=int(state0.get("turnNumber") or 1),
        enemy_max_hps={e["index"]: e["maxHp"] for e in state0.get("enemies") or [] if e.get("index") is not None},
    )
    emulator = BattleEmulator()

    print(f"trajectory_id={args.trajectory_id}")
    print(f"replay_through_decision_index={args.decision_index}")
    print("== initial state summary ==")
    print(json.dumps(summarize_state(state0), ensure_ascii=False, indent=2))
    print()

    row_by_index = {row["decision_index"]: row for row in rows}
    for row in rows:
        if row["decision_index"] > args.decision_index:
            break
        while battle_state.engine_state.get("pendingChoice"):
            legal_actions = emulator.enumerate_legal_actions(battle_state)
            inferred = infer_pending_choice_action(
                legal_actions,
                battle_state.engine_state,
                row.get("state"),
            )
            print("== resolving action continuation ==")
            print(json.dumps(battle_state.engine_state.get("pendingChoice"), ensure_ascii=False, indent=2))
            print("selected_continuation_action:")
            print(json.dumps(inferred, ensure_ascii=False, indent=2))
            try:
                battle_state = emulator.apply_action(battle_state, inferred)
            except Exception as exc:
                print("continuation_step_exception:")
                print(repr(exc))
                print("continuation_legal_actions:")
                print(json.dumps(legal_actions, ensure_ascii=False, indent=2))
                print("reproduced=action_continuation_illegal_action_mismatch")
                return 0
            print("post_continuation_state:")
            print(json.dumps(summarize_state(battle_state.engine_state), ensure_ascii=False, indent=2))
            print()
        legal_actions = emulator.enumerate_legal_actions(battle_state)
        action = choose_matching_action(legal_actions, row)
        print(f"== decision {row['decision_index']} ==")
        print("state:")
        print(json.dumps(summarize_state(battle_state.engine_state), ensure_ascii=False, indent=2))
        print("selected_action:")
        print(json.dumps(action, ensure_ascii=False, indent=2))
        battle_state = emulator.apply_action(battle_state, action)
        print("next_state:")
        print(json.dumps(summarize_state(battle_state.engine_state), ensure_ascii=False, indent=2))
        print(f"is_terminal={battle_state.is_terminal} outcome={battle_state.outcome}")
        print()

    next_legal = emulator.enumerate_legal_actions(battle_state)
    print(f"post_replay_legal_action_count={len(next_legal)}")
    print("== legal actions after replay ==")
    print(json.dumps(next_legal, ensure_ascii=False, indent=2))
    if not battle_state.is_terminal and len(next_legal) == 0:
        print("reproduced=no_legal_actions_while_non_terminal")
        return 0
    print("reproduced=false")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
