from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT_DIR = ROOT / "Combat"
if str(COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(COMBAT_DIR))

from generate_heuristic_trajectories import build_default_agent, generate_trajectory  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay one saved batch scenario by trajectory_id.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--max-decisions", type=int, default=50)
    return parser.parse_args()


def load_manifest_row(run_dir: Path, trajectory_id: str) -> dict:
    path = run_dir / "scenario_manifest.jsonl"
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id:
                return row
    raise ValueError(f"trajectory_id not found in scenario_manifest.jsonl: {trajectory_id}")


def load_result_row(run_dir: Path, trajectory_id: str) -> dict | None:
    path = run_dir / "scenario_results.jsonl"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id:
                return row
    return None


def main() -> int:
    args = parse_args()
    manifest_row = load_manifest_row(args.run_dir, args.trajectory_id)
    saved_row = load_result_row(args.run_dir, args.trajectory_id)

    emulator, agent = build_default_agent()
    result = generate_trajectory(
        manifest_row["spec"],
        emulator,
        agent,
        manifest_row["trajectory_id"],
        manifest_row["source_run_id"],
        manifest_row["source_combat_index"],
        max_decisions=args.max_decisions,
    )

    summary = {
        "trajectory_id": result["trajectory_id"],
        "status": result["status"],
        "decision_count": result.get("decision_count"),
        "truncated": result.get("truncated"),
        "final_outcome": result.get("final_outcome"),
        "final_is_terminal": result.get("final_is_terminal"),
        "warnings": result.get("warnings"),
        "termination_reason": result.get("termination_reason"),
        "saved_status": saved_row.get("status") if saved_row else None,
        "saved_error_events": saved_row.get("error_events") if saved_row else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.get("decisions"):
        tail = result["decisions"][-1]
        print(
            json.dumps(
                {
                    "last_decision": tail["decision_index"],
                    "selected_action": tail["selected_action"],
                    "next_state": tail["next_state"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
