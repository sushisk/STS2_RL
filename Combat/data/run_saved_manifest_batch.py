from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "Combat", ROOT / "Combat" / "data", ROOT / "Combat" / "env"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from run_trajectory_batch import load_scenario_manifest, run_batch  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a saved scenario manifest through run_batch().")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-decisions", type=int, default=50)
    parser.add_argument("--determinism-sample", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = load_scenario_manifest(args.manifest)
    summary = run_batch(
        scenarios,
        args.out,
        max_decisions=args.max_decisions,
        determinism_sample=args.determinism_sample,
        workers=args.workers,
        resume=args.resume,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
