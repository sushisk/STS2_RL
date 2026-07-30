"""Splits unused_500_manifest.jsonl into 5 non-overlapping, order-preserving 100-scenario
batches for the staged 500-scenario evaluation (see this task's "500 Scenario評価の分割
実行" instructions) - batch N = rows[(N-1)*100 : N*100] of the parent manifest, so
batches are disjoint and their union is exactly the parent 500 by construction (no
separate dedup logic needed). Writes `unused500_batch0{1..5}_manifest.jsonl` plus
`batch_split_manifest.json` recording each batch's source index range, trajectory_ids,
and SHA256 of both the parent and each batch file (the "使用manifestとSHA256"
requirement).

Run: python split_500_into_batches.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_DIR = Path(__file__).resolve().parent
PARENT_PATH = MANIFEST_DIR / "unused_500_manifest.jsonl"
BATCH_SIZE = 100
N_BATCHES = 5


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    with PARENT_PATH.open(encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]
    assert len(lines) == BATCH_SIZE * N_BATCHES, f"expected {BATCH_SIZE * N_BATCHES} rows, got {len(lines)}"

    parent_sha256 = sha256_file(PARENT_PATH)
    batches = []
    for b in range(N_BATCHES):
        start, end = b * BATCH_SIZE, (b + 1) * BATCH_SIZE
        batch_lines = lines[start:end]
        rows = [json.loads(line) for line in batch_lines]
        batch_name = f"unused500_batch{b + 1:02d}"
        batch_path = MANIFEST_DIR / f"{batch_name}_manifest.jsonl"
        with batch_path.open("w", encoding="utf-8") as bf:
            bf.writelines(batch_lines)
        batches.append(
            {
                "batch_name": batch_name,
                "batch_file": str(batch_path),
                "batch_sha256": sha256_file(batch_path),
                "source_manifest": str(PARENT_PATH),
                "source_manifest_sha256": parent_sha256,
                "source_index_range": [start, end],
                "n": len(rows),
                "trajectory_ids": [r["trajectory_id"] for r in rows],
            }
        )
        print(f"{batch_name}: rows[{start}:{end}] -> {batch_path} (sha256={batches[-1]['batch_sha256'][:16]}...)")

    all_ids = [tid for b in batches for tid in b["trajectory_ids"]]
    assert len(all_ids) == len(set(all_ids)) == 500, "batch split produced duplicates or wrong total"

    mapping_path = MANIFEST_DIR / "batch_split_manifest.json"
    with mapping_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "parent_manifest": str(PARENT_PATH),
                "parent_manifest_sha256": parent_sha256,
                "parent_total": len(lines),
                "batch_size": BATCH_SIZE,
                "n_batches": N_BATCHES,
                "batches": batches,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nWrote mapping -> {mapping_path}")
    print(f"Verified: {len(all_ids)} unique trajectory_ids across all batches, matches parent's {len(lines)}.")


if __name__ == "__main__":
    main()
