from __future__ import annotations

import argparse
import json
from pathlib import Path


TEMPLATE = """from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "Combat" / "data" / "repro_from_batch_run.py"
RUN_DIR = Path(__file__).resolve().parents[1]

raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            str(SCRIPT),
            "--run-dir",
            str(RUN_DIR),
            "--trajectory-id",
            "{trajectory_id}",
        ]
    )
)
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate replay wrapper scripts from saved error_events.jsonl.")
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
    repros_dir = run_dir / "generated_repros"
    repros_dir.mkdir(parents=True, exist_ok=True)

    error_events = load_jsonl(run_dir / "error_events.jsonl")
    trajectory_ids = sorted({event["trajectory_id"] for event in error_events})
    written: list[str] = []
    for trajectory_id in trajectory_ids:
        safe_name = trajectory_id.replace(":", "_").replace("-", "_")
        path = repros_dir / f"repro_{safe_name}.py"
        path.write_text(TEMPLATE.format(trajectory_id=trajectory_id), encoding="utf-8")
        written.append(path.name)

    print(json.dumps({"run_dir": str(run_dir), "generated_count": len(written), "files": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
