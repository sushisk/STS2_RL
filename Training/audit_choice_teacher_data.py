from __future__ import annotations

import argparse
import json
from pathlib import Path

from sts2_training.choice_data import DEFAULT_SOURCE_DIR, audit_and_split, sha256_file


DEFAULT_OUT_DIR = Path("exports/choice_policy_v1")
DEFAULT_SPLIT_SEED = 20260725


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the RL Choice teacher dataset and build the train/validation/test split.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    return parser.parse_args()


def dump_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    result = audit_and_split(args.source_dir, args.split_seed)

    write_jsonl(out_dir / "manifest.jsonl", result["manifest_rows"])
    write_jsonl(
        out_dir / "split_manifest.jsonl",
        [{"trajectory_id": tid, "split": split} for tid, split in sorted(result["split_map"].items())],
    )
    dump_json(out_dir / "dictionaries.json", {"choice_meaning_dict": result["choice_meaning_dict"]})

    source_data_path = args.source_dir / "choice_teacher_data.jsonl"
    dataset_metadata = {
        "source_dir": str(args.source_dir),
        "source_choice_teacher_data_sha256": sha256_file(source_data_path),
        "source_eligibility_summary": json.loads((args.source_dir / "eligibility_summary.json").read_text(encoding="utf-8")),
        "split_seed": args.split_seed,
        "counts": {
            "rl_eligible": result["eligible_count"],
            "rl_excluded": result["excluded_by_rl_count"],
            "synthetic_holdout": result["synthetic_count"],
            "training_scope_excluded_choice_confirm": result["out_of_scope_count"],
            "in_scope_for_training": result["in_scope_count"],
            "in_scope_trajectories": result["in_scope_trajectory_count"],
        },
        "split_counts_trajectories": result["split_counts_trajectories"],
        "split_counts_decisions": result["split_counts_decisions"],
        "exclusion_categories": {
            "excluded_by_rl": "operation_mode_unknown (RL eligibility filter; see source eligibility_summary.json)",
            "synthetic_holdout": "synthetic:nested_choice_decisions_decisions_burning_pact - reserved for dedicated inference check, not in train/val/test",
            "training_scope_excluded_choice_confirm": "teacher_action_type == choice_confirm - no card was chosen, out of scope for card-ranking per this task's instructions",
        },
    }
    dump_json(out_dir / "dataset_metadata.json", dataset_metadata)

    summary = {
        "out_dir": str(out_dir),
        "rl_eligible": result["eligible_count"],
        "rl_excluded": result["excluded_by_rl_count"],
        "synthetic_holdout": result["synthetic_count"],
        "training_scope_excluded_choice_confirm": result["out_of_scope_count"],
        "in_scope_for_training": result["in_scope_count"],
        "in_scope_trajectories": result["in_scope_trajectory_count"],
        "split_counts_trajectories": result["split_counts_trajectories"],
        "split_counts_decisions": result["split_counts_decisions"],
        "choice_meaning_token_count": len(result["choice_meaning_dict"]["entries"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
