from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate exported training datasets without Emulator."
    )
    parser.add_argument("--export-root", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def validate_decision_rows(rows: list[dict], dataset_kind: str) -> dict:
    required = [
        "trajectory_id",
        "scenario_id",
        "decision_id",
        "decision_index",
        "observation",
        "legal_actions",
        "teacher_action",
        "teacher_action_type",
        "candidate_actions",
        "combat_outcome",
        "termination_reason",
        "data_usage_classification",
        "emulator_commit",
        "emulator_dll_sha256",
        "heuristic_version",
        "split",
    ]
    missing_counts = Counter()
    trajectory_to_split: dict[str, str] = {}
    decision_ids = set()
    duplicate_decision_ids = []
    teacher_missing_from_legal = []
    teacher_unavailable = []
    selected_index_out_of_range = []
    empty_legal_actions = []
    for row in rows:
        for key in required:
            if key not in row or row[key] is None:
                missing_counts[key] += 1
        decision_id = row["decision_id"]
        if decision_id in decision_ids:
            duplicate_decision_ids.append(decision_id)
        decision_ids.add(decision_id)
        prior_split = trajectory_to_split.setdefault(row["trajectory_id"], row["split"])
        if prior_split != row["split"]:
            raise AssertionError(
                f"trajectory {row['trajectory_id']} appears in multiple splits for {dataset_kind}"
            )
        legal_actions = row.get("legal_actions") or []
        if not legal_actions:
            empty_legal_actions.append(decision_id)
        teacher_action = row.get("teacher_action") or {}
        selected_index = (row.get("teacher_target") or {}).get("selected_action_index")
        if selected_index is not None and not (
            0 <= selected_index < len(legal_actions)
        ):
            selected_index_out_of_range.append(decision_id)
        if legal_actions:
            matched = any(
                action.get("action_id") == teacher_action.get("action_id")
                and action.get("action_type") == teacher_action.get("action_type")
                for action in legal_actions
            )
            if not matched:
                teacher_missing_from_legal.append(decision_id)
            elif not teacher_action.get("is_available", False):
                teacher_unavailable.append(decision_id)
    return {
        "dataset_kind": dataset_kind,
        "row_count": len(rows),
        "trajectory_count": len(trajectory_to_split),
        "missing_counts": dict(missing_counts),
        "duplicate_decision_ids": duplicate_decision_ids,
        "teacher_missing_from_legal": teacher_missing_from_legal,
        "teacher_unavailable": teacher_unavailable,
        "selected_index_out_of_range": selected_index_out_of_range,
        "empty_legal_actions": empty_legal_actions,
    }


def validate_manifests(export_root: Path, dataset_kind: str) -> dict:
    manifest_dir = export_root / "manifests"
    seen = set()
    overlap = []
    counts = {}
    for split in ("train", "validation", "test"):
        rows = load_jsonl(manifest_dir / f"{dataset_kind}_{split}_manifest.jsonl")
        ids = [row["trajectory_id"] for row in rows]
        counts[split] = len(ids)
        for trajectory_id in ids:
            if trajectory_id in seen:
                overlap.append(trajectory_id)
            seen.add(trajectory_id)
    return {"dataset_kind": dataset_kind, "split_counts": counts, "overlap": overlap}


def main() -> int:
    args = parse_args()
    export_root = args.export_root
    complete_rows = load_jsonl(export_root / "complete_all.jsonl")
    partial_rows = load_jsonl(export_root / "partial_all.jsonl")
    summary = {
        "complete": validate_decision_rows(complete_rows, "complete"),
        "partial": validate_decision_rows(partial_rows, "partial"),
        "complete_manifests": validate_manifests(export_root, "complete"),
        "partial_manifests": validate_manifests(export_root, "partial"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
