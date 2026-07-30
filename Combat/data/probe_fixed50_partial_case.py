from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
DATA_DIR = COMBAT_DIR / "data"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from generate_heuristic_trajectories import build_default_agent, generate_trajectory  # noqa: E402
from run_trajectory_batch import load_fixed_50  # noqa: E402


CASE_CONFIG = {
    "fixed50:1642-31": {
        "index": 31,
        "expected_warning": "truncated_at_max_decisions:50",
        "label": "B_heuristic_stagnation",
    },
    "fixed50:3342-27": {
        "index": 27,
        "expected_warning": "truncated_at_max_decisions:50",
        "label": "B_heuristic_stagnation",
    },
    "fixed50:6485-37": {
        "index": 37,
        "expected_warning": "no_legal_actions_while_non_terminal",
        "label": "no_legal_actions_while_non_terminal",
    },
}


def normalize_relic_ids(relics) -> list[str]:
    out = []
    for relic in relics or []:
        if isinstance(relic, str):
            out.append(relic)
        elif isinstance(relic, dict):
            out.append(relic.get("id") or "")
    return [r for r in out if r]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproduce one fixed50 usable_partial case with the current RL pipeline."
    )
    parser.add_argument("--case", choices=sorted(CASE_CONFIG), required=True)
    parser.add_argument("--max-decisions", type=int, default=50)
    return parser.parse_args()


def summarize_state(state: dict) -> dict:
    return {
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "stars": state.get("stars"),
        "turnNumber": state.get("turnNumber"),
        "combatRoundNumber": state.get("combatRoundNumber"),
        "stepIndex": state.get("stepIndex"),
        "hand": [c["id"] for c in (state.get("hand") or [])],
        "drawPileTop10": [c["id"] for c in (state.get("drawPile") or [])[:10]],
        "discardPileTop10": [c["id"] for c in (state.get("discardPile") or [])[:10]],
        "exhaustPileTop10": [c["id"] for c in (state.get("exhaustPile") or [])[:10]],
        "playerPowers": [(p["id"], p.get("amount")) for p in (state.get("playerPowers") or [])],
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
                "powers": [(p["id"], p.get("amount")) for p in (e.get("powers") or [])],
            }
            for e in (state.get("enemies") or [])
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def main() -> int:
    args = parse_args()
    spec, source_run_id, combat_index = load_fixed_50()[CASE_CONFIG[args.case]["index"]]
    if f"{source_run_id}-{combat_index}" != args.case:
        raise ValueError(f"fixed50 index mismatch: expected {args.case}, got {source_run_id}-{combat_index}")

    emulator, agent = build_default_agent()
    result = generate_trajectory(
        spec,
        emulator,
        agent,
        trajectory_id=args.case,
        source_run_id=source_run_id,
        combat_index=combat_index,
        max_decisions=args.max_decisions,
    )

    decisions = result.get("decisions") or []
    enemy_hp_progression = [
        sum(max(0, e.get("hp", 0) or 0) for e in d["state"].get("enemies") or [])
        for d in decisions
    ]
    player_hp_progression = [d["state"].get("hp") for d in decisions]
    if decisions:
        enemy_hp_progression.append(
            sum(max(0, e.get("hp", 0) or 0) for e in decisions[-1]["next_state"].get("enemies") or [])
        )
        player_hp_progression.append(decisions[-1]["next_state"].get("hp"))

    payload = {
        "case": args.case,
        "expected_label": CASE_CONFIG[args.case]["label"],
        "expected_warning": CASE_CONFIG[args.case]["expected_warning"],
        "status": result.get("status"),
        "decision_count": result.get("decision_count"),
        "warnings": result.get("warnings"),
        "termination_reason": result.get("termination_reason"),
        "final_outcome": result.get("final_outcome"),
        "final_is_terminal": result.get("final_is_terminal"),
        "relics": normalize_relic_ids(spec.get("relics", [])),
        "initial_state": summarize_state(decisions[0]["state"]) if decisions else None,
        "last_state": summarize_state(decisions[-1]["state"]) if decisions else None,
        "last_next_state": summarize_state(decisions[-1]["next_state"]) if decisions else None,
        "enemy_hp_progression": enemy_hp_progression,
        "player_hp_progression": player_hp_progression,
        "last_10_decisions": [
            {
                "decision_index": d["decision_index"],
                "selected_action": d["selected_action"],
                "selected_enemy_index": d.get("selected_enemy_index"),
                "legal_actions": d["legal_actions"],
                "state": summarize_state(d["state"]),
                "next_state": summarize_state(d["next_state"]),
            }
            for d in decisions[-10:]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
