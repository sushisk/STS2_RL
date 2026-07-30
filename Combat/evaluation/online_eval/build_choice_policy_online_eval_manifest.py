"""Builds the fixed 30-scenario manifest for this task's "Choice Policy限定オンライン
評価" (指示書5節), selected from the SAME 200-scenario pool used for Choice teacher-data
generation (Combat/evaluation/online_eval/choice_teacher_data_manifest.jsonl) - reuses
each row's already-attached `spec` directly, no new teacher2000 sampling.

Selection is grounded in what ACTUALLY happened at replay time
(Combat/evaluation/reports/choice_teacher_data_full_20260725/{scenarios,choice_teacher_
data}.jsonl), not just the static category tags used to build that pool - per this task's
"Choiceが実際に発生する" requirement, only scenarios with choice_decision_count > 0 are
eligible. Not restricted to the Choice Policy training split's "test" scenarios only (this
task's own note: "学習splitのtestだけに偏らせず") - draws from train/validation/test
alike (Training/exports/choice_policy_v1/split_manifest.jsonl, read-only).

The synthetic nested-choice scenario is intentionally NOT part of the 30 - run separately
per 指示書5節's "synthetic nested Scenarioは別枠で1件実行する".

Run: python build_choice_policy_online_eval_manifest.py [--n 30] [--seed 20260725]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]

FULL_MANIFEST_PATH = _HERE / "choice_teacher_data_manifest.jsonl"
FULL_RUN_DIR = _COMBAT_DIR / "evaluation" / "reports" / "choice_teacher_data_full_20260725"
SPLIT_MANIFEST_PATH = Path(r"C:\STS2_RL\Training\exports\choice_policy_v1\split_manifest.jsonl")
OUT_PATH = _HERE / "choice_policy_online_eval_manifest.jsonl"
SYNTHETIC_OUT_PATH = _HERE / "choice_policy_online_eval_synthetic_manifest.jsonl"

# Each bucket's minimum representation among the 30 - deliberately generous relative to
# what's actually available (see this script's own printed pool stats) so coverage is
# real, not a single token example per category.
MIN_REQUIREMENTS = {
    "gambling_chip": 3,
    "passthrough_any": 5,
    "potion_origin": 5,
    "discard": 5,
    "exhaust": 3,
    "retrieve": 5,
    "upgrade": 2,
    "multi_choice": 10,
    "few_candidates": 3,   # max choice_card candidates at any single decision <= 3
    "many_candidates": 3,  # max choice_card candidates at any single decision >= 10
}
PRIORITY_ORDER = (
    "gambling_chip", "passthrough_any", "upgrade", "exhaust", "few_candidates",
    "many_candidates", "potion_origin", "discard", "retrieve", "multi_choice",
)


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_pool() -> list[dict]:
    full_manifest = {r["trajectory_id"]: r for r in load_jsonl(FULL_MANIFEST_PATH)}
    scenarios = load_jsonl(FULL_RUN_DIR / "scenarios.jsonl")
    choices = load_jsonl(FULL_RUN_DIR / "choice_teacher_data.jsonl")
    splits = {r["trajectory_id"]: r["split"] for r in load_jsonl(SPLIT_MANIFEST_PATH)}

    by_traj: dict[str, list[dict]] = defaultdict(list)
    for r in choices:
        by_traj[r["trajectory_id"]].append(r)

    pool = []
    for s in scenarios:
        tid = s["trajectory_id"]
        if s.get("synthetic") or s["status"] != "ok" or s.get("choice_decision_count", 0) <= 0:
            continue
        if tid not in full_manifest:
            continue
        rows = by_traj[tid]
        op_modes = {r["resolved"]["operationMode"] for r in rows}
        has_gambling = any(r["resolved"].get("matchedRuleId") == "GamblingChipDiscard" for r in rows)
        has_passthrough = "passthrough" in op_modes
        candidate_counts = [
            len([a for a in r["legal_actions"] if a.get("action_type") == "choice_card"]) for r in rows
        ]
        max_candidates = max(candidate_counts) if candidate_counts else 0
        min_candidates = min(candidate_counts) if candidate_counts else 0
        categories = set(full_manifest[tid].get("categories") or [])

        buckets = set()
        if has_gambling:
            buckets.add("gambling_chip")
        if has_passthrough:
            buckets.add("passthrough_any")
        if "potion_origin" in categories:
            buckets.add("potion_origin")
        if "discard" in categories:
            buckets.add("discard")
        if "exhaust" in categories:
            buckets.add("exhaust")
        if "retrieve" in categories:
            buckets.add("retrieve")
        if "upgrade" in categories:
            buckets.add("upgrade")
        if s["choice_decision_count"] >= 2:
            buckets.add("multi_choice")
        if max_candidates <= 3:
            buckets.add("few_candidates")
        if max_candidates >= 10:
            buckets.add("many_candidates")

        pool.append(
            {
                "trajectory_id": tid,
                "spec": full_manifest[tid]["spec"],
                "categories": sorted(categories),
                "buckets": sorted(buckets),
                "choice_decision_count": s["choice_decision_count"],
                "max_candidates": max_candidates,
                "min_candidates": min_candidates,
                "op_modes": sorted(op_modes),
                "split": splits.get(tid, "not_in_training_scope"),
            }
        )
    return pool


def select(pool: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    remaining = list(pool)
    rng.shuffle(remaining)

    selected: list[dict] = []
    selected_ids: set[str] = set()
    bucket_counts: dict[str, int] = defaultdict(int)

    for bucket in PRIORITY_ORDER:
        need = MIN_REQUIREMENTS[bucket]
        for c in remaining:
            if bucket_counts[bucket] >= need or len(selected) >= n:
                break
            if c["trajectory_id"] in selected_ids or bucket not in c["buckets"]:
                continue
            selected.append(c)
            selected_ids.add(c["trajectory_id"])
            for b in c["buckets"]:
                bucket_counts[b] += 1

    # Split diversity: ensure at least one non-"train" scenario if available, before
    # topping up arbitrarily.
    for c in remaining:
        if len(selected) >= n:
            break
        if c["trajectory_id"] in selected_ids:
            continue
        if c["split"] in ("validation", "test") and not any(
            s["split"] in ("validation", "test") for s in selected
        ):
            selected.append(c)
            selected_ids.add(c["trajectory_id"])

    for c in remaining:
        if len(selected) >= n:
            break
        if c["trajectory_id"] in selected_ids:
            continue
        selected.append(c)
        selected_ids.add(c["trajectory_id"])

    return selected[:n], dict(bucket_counts)


def load_synthetic() -> dict:
    with (_HERE / "choice_teacher_data_manifest.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("synthetic"):
                return row
    raise RuntimeError("synthetic scenario not found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260725)
    args = parser.parse_args()

    pool = build_pool()
    print(f"Pool: {len(pool)} scenarios with real Choice occurrence (choice_decision_count > 0)")

    selected, bucket_counts = select(pool, args.n, args.seed)
    print(f"Selected {len(selected)} scenarios. Bucket coverage: {bucket_counts}")
    from collections import Counter
    print("Split distribution:", dict(Counter(s["split"] for s in selected)))

    with OUT_PATH.open("w", encoding="utf-8") as f:
        for row in selected:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(f"-> {OUT_PATH}")

    synthetic = load_synthetic()
    with SYNTHETIC_OUT_PATH.open("w", encoding="utf-8") as f:
        f.write(json.dumps(synthetic, ensure_ascii=False, default=str) + "\n")
    print(f"-> {SYNTHETIC_OUT_PATH} (1 synthetic nested-choice scenario, run separately)")


if __name__ == "__main__":
    main()
