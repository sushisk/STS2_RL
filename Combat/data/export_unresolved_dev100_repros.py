from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "Combat" / "data" / "trajectories_dev100_20260722_stepindex_w4"
OUT_DIR = ROOT / "Combat" / "data" / "repros_dev100_unresolved"
REPORT_PATH = ROOT / "Outputs" / "reports" / "dev100_unresolved_issues_report.md"
REPRO_DRIVER = ROOT / "Combat" / "data" / "repro_from_batch_run.py"

TARGET_ORDER = [
    "4861-21",
    "3122-10",
    "2080-15",
    "2986-17",
    "3109-22",
    "780-17",
    "5944-3",
    "4755-5",
    "2641-8",
    "659-6",
    "5021-11",
]


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _safe_slug(text: str) -> str:
    return (
        text.lower()
        .replace(":", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(" ", "_")
    )


def make_wrapper_name(trajectory_id: str, event_type: str) -> str:
    return f"repro_{_safe_slug(trajectory_id)}_{_safe_slug(event_type)}.py"


def write_wrapper(path: Path, trajectory_id: str) -> None:
    content = f"""from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "Combat" / "data" / "repro_from_batch_run.py"
RUN_DIR = ROOT / "Combat" / "data" / "trajectories_dev100_20260722_stepindex_w4"

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
    path.write_text(content, encoding="utf-8")


def summarize_row(row: dict) -> dict:
    result = row["result"]
    quality = row["quality"]
    events = row.get("error_events") or []
    event = events[0] if events else {}
    spec = result.get("input_state") or {}
    source = spec.get("source") or {}
    first_decision = (result.get("decisions") or [None])[0]
    last_decision = (result.get("decisions") or [None])[-1]
    first_state = first_decision.get("state") if first_decision else None
    last_state = last_decision.get("state") if last_decision else None
    last_next_state = last_decision.get("next_state") if last_decision else None
    return {
        "trajectory_id": row["trajectory_id"],
        "event_type": event.get("event_type"),
        "status": row["status"],
        "elapsed_s": row.get("elapsed_s"),
        "character_id": spec.get("character_id"),
        "encounter": source.get("encounter"),
        "floor": source.get("floor"),
        "relics": spec.get("relics") or [],
        "step_index_spec": spec.get("step_index", spec.get("stepIndex")),
        "first_step_index": first_state.get("stepIndex") if first_state else None,
        "last_step_index": last_state.get("stepIndex") if last_state else None,
        "warnings": result.get("warnings") or [],
        "termination_reason": result.get("termination_reason"),
        "decision_count": result.get("decision_count"),
        "final_outcome": result.get("final_outcome"),
        "data_usage": quality.get("data_usage"),
        "classification": quality.get("classification"),
        "stderr_excerpt": row.get("stderr_excerpt"),
        "stderr_log_path": row.get("stderr_log_path"),
        "worker_pid": row.get("worker_pid"),
        "last_action": last_decision.get("selected_action") if last_decision else None,
        "last_enemy_hps": [e.get("hp") for e in (last_next_state.get("enemies") or [])] if last_next_state else None,
    }


def render_report(entries: list[dict]) -> str:
    lines = [
        "# dev100 unresolved issues",
        "",
        f"Source run dir: `{RUN_DIR}`",
        "",
        "This report lists every unresolved issue from the 100-scenario workers=4 batch,",
        "along with a dedicated replay wrapper script for each case.",
        "",
    ]
    for entry in entries:
        lines.append(f"## {entry['trajectory_id']} - {entry['event_type']}")
        lines.append("")
        lines.append(f"- wrapper: `{entry['wrapper_path']}`")
        lines.append(f"- status: `{entry['status']}`")
        lines.append(f"- data_usage: `{entry['data_usage']}`")
        lines.append(f"- classification: `{entry['classification']}`")
        lines.append(f"- character: `{entry['character_id']}`")
        lines.append(f"- encounter: `{entry['encounter']}`")
        lines.append(f"- floor: `{entry['floor']}`")
        lines.append(f"- elapsed_s: `{entry['elapsed_s']}`")
        lines.append(f"- decision_count: `{entry['decision_count']}`")
        lines.append(f"- termination_reason: `{entry['termination_reason']}`")
        lines.append(f"- warnings: `{entry['warnings']}`")
        lines.append(f"- final_outcome: `{entry['final_outcome']}`")
        lines.append(f"- stepIndex in saved spec: `{entry['step_index_spec']}`")
        lines.append(f"- first observed stepIndex: `{entry['first_step_index']}`")
        lines.append(f"- last observed stepIndex: `{entry['last_step_index']}`")
        lines.append(f"- last action: `{entry['last_action']}`")
        lines.append(f"- last enemy hps: `{entry['last_enemy_hps']}`")
        lines.append(f"- stderr log: `{entry['stderr_log_path']}`")
        lines.append("- relics:")
        for relic in entry["relics"]:
            lines.append(f"  - `{relic}`")
        if entry["stderr_excerpt"]:
            lines.append("- stderr excerpt:")
            lines.append("```text")
            excerpt = entry["stderr_excerpt"]
            if len(excerpt) > 4000:
                excerpt = excerpt[:4000] + "\n...<truncated>..."
            lines.append(excerpt)
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = {row["trajectory_id"]: row for row in load_jsonl(RUN_DIR / "scenario_results.jsonl")}
    entries: list[dict] = []
    for trajectory_id in TARGET_ORDER:
        row = rows[trajectory_id]
        summary = summarize_row(row)
        wrapper_name = make_wrapper_name(trajectory_id, summary["event_type"])
        wrapper_path = OUT_DIR / wrapper_name
        write_wrapper(wrapper_path, trajectory_id)
        summary["wrapper_path"] = str(wrapper_path)
        entries.append(summary)

    REPORT_PATH.write_text(render_report(entries), encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "wrapper_dir": str(OUT_DIR), "count": len(entries)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
