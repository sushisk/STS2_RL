from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a few exported training rows without Emulator."
    )
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument(
        "--split", choices=["train", "validation", "test"], default="train"
    )
    parser.add_argument("--count", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.export_root / f"complete_{args.split}.jsonl"
    shown = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            preview = {
                "decision_id": row["decision_id"],
                "trajectory_id": row["trajectory_id"],
                "teacher_action": row["teacher_action"],
                "legal_action_count": len(row["legal_actions"]),
                "candidate_action_count": len(row["candidate_actions"]),
                "outcome": row["combat_outcome"],
                "termination_reason": row["termination_reason"],
            }
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            shown += 1
            if shown >= args.count:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
