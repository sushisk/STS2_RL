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


SCENARIO_PATH = ROOT / "Combat" / "evaluation" / "benchmark_states" / "fixed_50_scenarios.json"
SOURCE_TRAJECTORY_PATH = ROOT / "Combat" / "data" / "trajectories_fixed50_orbapi_20260721_3" / "trajectories.jsonl"
DEFAULT_IDS = ["fixed50:3342-27", "fixed50:1642-31"]


def load_specs() -> list[dict]:
    with SCENARIO_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def get_spec(trajectory_id: str) -> dict:
    for spec in load_specs():
        if spec.get("source", {}).get("fixed50_trajectory_id") == trajectory_id:
            return spec
    raise ValueError(f"scenario not found: {trajectory_id}")


def load_source_row(trajectory_id: str, decision_index: int = 0) -> dict | None:
    with SOURCE_TRAJECTORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id and row.get("decision_index") == decision_index:
                return row
    return None


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
        "hand": [c["id"] for c in state.get("hand") or []],
        "drawPileTop10": [c["id"] for c in (state.get("drawPile") or [])[:10]],
        "relics": [r["id"] for r in state.get("relics") or []],
        "enemies": [
            {
                "index": e.get("index"),
                "id": e.get("id"),
                "hp": e.get("hp"),
                "maxHp": e.get("maxHp"),
                "intent": (e.get("intent") or {}).get("stateId"),
            }
            for e in state.get("enemies") or []
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce fixed50 start-of-combat card-choice states that initialize OK but "
            "fail when RL restores them again from Observation-derived state."
        )
    )
    parser.add_argument("--trajectory-id", action="append", dest="trajectory_ids")
    args = parser.parse_args()

    trajectory_ids = args.trajectory_ids or DEFAULT_IDS
    overall_rc = 0

    for trajectory_id in trajectory_ids:
        print(f"===== {trajectory_id} =====")
        spec = get_spec(trajectory_id)
        source_row = load_source_row(trajectory_id, decision_index=0)

        emulator = BattleEmulator()
        initial = emulator.initialize(spec)

        print("== spec relics ==")
        print(json.dumps(spec.get("relics") or [], ensure_ascii=False, indent=2))
        print("== initialized state summary ==")
        print(json.dumps(summarize_state(initial.engine_state), ensure_ascii=False, indent=2))
        print("== legal actions from ResetFromScenario(spec) ==")
        print(json.dumps(initial._cached_legal_actions or [], ensure_ascii=False, indent=2))

        if source_row is not None:
            print("== source trajectory decision0 selected action ==")
            print(json.dumps(source_row.get("selected_action"), ensure_ascii=False, indent=2))

        restored = BattleState(
            engine_state=initial.engine_state,
            is_terminal=initial.is_terminal,
            outcome=initial.outcome,
            turn=initial.turn,
            enemy_max_hps=initial.enemy_max_hps,
            shuffle_rng_seed=initial.shuffle_rng_seed,
            _cached_legal_actions=None,
        )

        print("== forcing restore from initialized Observation state ==")
        try:
            legal = emulator.enumerate_legal_actions(restored)
            print(f"restore_result=ok legal_action_count={len(legal)}")
            print(json.dumps(legal, ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            overall_rc = 1
            print(f"restore_result=exception")
            print(f"exception_type={type(exc).__name__}")
            print(str(exc))
        print()

    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
