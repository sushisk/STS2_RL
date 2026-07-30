from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))

from generate_heuristic_trajectories import build_default_agent, generate_trajectory  # noqa: E402
from repro_from_batch_run import load_manifest_row, load_result_row  # noqa: E402


RUN_DIR = ROOT / "Combat" / "data" / "trajectories_train500_20260722_w4"
TRAJECTORY_ID = "5362-18"
EXPECTED_FAILURE_DECISION_INDEX = 30
EXPECTED_WARNING = "step_exception:TimeoutException:candidate_evaluation"


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
                "powers": [(p.get("id"), p.get("amount")) for p in e.get("powers") or []],
            }
            for e in state.get("enemies") or []
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def main() -> int:
    manifest_row = load_manifest_row(RUN_DIR, TRAJECTORY_ID)
    saved_row = load_result_row(RUN_DIR, TRAJECTORY_ID)
    if saved_row is None:
        raise ValueError(f"saved result row not found: {TRAJECTORY_ID}")

    saved_decisions = saved_row["result"].get("decisions") or []
    last_saved_decision = saved_decisions[-1]

    print("== target ==")
    print(
        json.dumps(
            {
                "run_dir": str(RUN_DIR),
                "trajectory_id": TRAJECTORY_ID,
                "source_run_id": manifest_row["source_run_id"],
                "source_combat_index": manifest_row["source_combat_index"],
                "expected_failure_decision_index": EXPECTED_FAILURE_DECISION_INDEX,
                "expected_warning": EXPECTED_WARNING,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print()

    print("== saved last committed decision before timeout ==")
    print(
        json.dumps(
            {
                "decision_index": last_saved_decision["decision_index"],
                "selected_action": last_saved_decision["selected_action"],
                "selected_enemy_index": last_saved_decision.get("selected_enemy_index"),
                "legal_actions": last_saved_decision.get("legal_actions"),
                "state_summary": summarize_state(last_saved_decision["state"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print()

    emulator, agent = build_default_agent()
    result = generate_trajectory(
        manifest_row["spec"],
        emulator,
        agent,
        manifest_row["trajectory_id"],
        manifest_row["source_run_id"],
        manifest_row["source_combat_index"],
        max_decisions=50,
    )

    print("== replay summary ==")
    print(
        json.dumps(
            {
                "trajectory_id": result["trajectory_id"],
                "status": result["status"],
                "decision_count": result.get("decision_count"),
                "termination_reason": result.get("termination_reason"),
                "warnings": result.get("warnings"),
                "final_outcome": result.get("final_outcome"),
                "truncated": result.get("truncated"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print()

    if result.get("decisions"):
        replay_last = result["decisions"][-1]
        print("== replay last committed decision ==")
        print(
            json.dumps(
                {
                    "decision_index": replay_last["decision_index"],
                    "selected_action": replay_last["selected_action"],
                    "selected_enemy_index": replay_last.get("selected_enemy_index"),
                    "next_state_summary": summarize_state(replay_last["next_state"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    warning_hit = EXPECTED_WARNING in (result.get("warnings") or [])
    if not warning_hit:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
