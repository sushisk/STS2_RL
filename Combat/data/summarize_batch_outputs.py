from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize saved batch outputs without running Emulator.")
    parser.add_argument("--run-dir", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir
    scenario_results = load_jsonl(run_dir / "scenario_results.jsonl")
    error_events = load_jsonl(run_dir / "error_events.jsonl")

    status_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    event_type_counts: dict[str, int] = {}
    scenarios_by_event_type: dict[str, list[str]] = {}
    stderr_scenarios: list[str] = []

    for row in scenario_results:
        status = row.get("status")
        status_counts[status] = status_counts.get(status, 0) + 1
        quality = ((row.get("quality") or {}).get("data_usage"))
        if quality:
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
        if row.get("stderr_excerpt"):
            stderr_scenarios.append(row["trajectory_id"])

    for event in error_events:
        event_type = event.get("event_type") or "unknown"
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        bucket = scenarios_by_event_type.setdefault(event_type, [])
        trajectory_id = event["trajectory_id"]
        if trajectory_id not in bucket:
            bucket.append(trajectory_id)

    summary = {
        "run_dir": str(run_dir),
        "scenario_count": len(scenario_results),
        "status_counts": status_counts,
        "data_usage_counts": quality_counts,
        "error_event_count": len(error_events),
        "error_type_counts": event_type_counts,
        "scenarios_by_error_type": scenarios_by_event_type,
        "stderr_scenarios": stderr_scenarios,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
