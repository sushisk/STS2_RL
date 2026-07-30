"""Builds the held-out scenario manifests used by online_policy_eval.py's staged
rollout ("unused50" / "unused200" / "unused500" - see this task's initial instructions,
section "評価手順").

"Unused" here means: never sampled for teacher2000 trajectory generation, and therefore
never seen by the Policy/Value checkpoints in any form (not train, not validation/test
splits of the imitation dataset - those all come from floor_states_train.jsonl only).
run_trajectory_batch.py::load_reconstructed_sample() enforces this already for
dev-set generation ("dev sampling stays within train, never touches validation/test/
benchmark" - see its own comment) - this script deliberately samples the COMPLEMENT: the
validation/test/benchmark splits it explicitly avoids, using the exact same conversion
path (reconstruct_floor_state.encounter_to_scenario_spec) so the resulting specs are
apples-to-apples with what teacher2000 was built from.

The three manifests are NESTED (unused50 subset of unused200 subset of unused500) - one
shuffle of the combined validation+test+benchmark pool, sliced at each size - so the
staged 50 -> 200 -> 500 rollout never redoes work or risks drawing an inconsistent
sample between stages.

Run: python build_unused_manifests.py [--seed 20260724] [--max 500]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
OUT_DIR = Path(__file__).resolve().parent
FULL_RECON_DIR = DATA_DIR / "full_reconstruction"
NEVER_USED_SPLITS = ("validation", "test", "benchmark")

sys.path.insert(0, str(DATA_DIR))


def load_fixed50_signatures() -> set[tuple[str, "int | None", "str | None", "str | None"]]:
    fixed50_path = Path(__file__).resolve().parents[1] / "benchmark_states" / "fixed_50_scenarios.json"
    with fixed50_path.open(encoding="utf-8") as f:
        specs = json.load(f)
    signatures = set()
    for spec in specs:
        source = spec.get("source") or {}
        signatures.add(
            (str(source.get("server_id")), source.get("floor"), source.get("encounter"), spec.get("character_id"))
        )
    return signatures


def load_teacher2000_keys() -> set[tuple[str, int]]:
    """Defense in depth only - by construction teacher2000 never sampled these splits,
    this just guards against that invariant ever silently breaking."""
    keys: set[tuple[str, int]] = set()
    manifest_path = DATA_DIR / "teacher2000_20260723_manifests" / "parent_2000_manifest.jsonl"
    if not manifest_path.exists():
        return keys
    with manifest_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            keys.add((str(row.get("source_run_id")), int(row.get("source_combat_index"))))
    return keys


def build_pool(seed: int, max_n: int) -> list[dict]:
    from reconstruct_floor_state import encounter_to_scenario_spec
    from scenario_from_runs import load_monster_hp

    rng = random.Random(seed)
    monster_hp = load_monster_hp()
    fixed50_signatures = load_fixed50_signatures()
    excluded_keys = load_teacher2000_keys()

    candidates = []
    for split in NEVER_USED_SPLITS:
        path = FULL_RECON_DIR / f"floor_states_{split}.jsonl"
        with path.open(encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("restore_status") not in ("exact", "ambiguous_upgrade"):
                    continue
                signature = (
                    str(rec.get("source_run_id")),
                    rec.get("floor"),
                    rec.get("encounter_id"),
                    rec.get("character"),
                )
                if signature in fixed50_signatures:
                    continue
                key = (str(rec.get("source_run_id")), int(rec.get("combat_index")))
                if key in excluded_keys:
                    continue
                candidates.append(rec)
    rng.shuffle(candidates)
    chosen = candidates[:max_n]

    pool = []
    for rec in chosen:
        spec = encounter_to_scenario_spec(rec, monster_hp, rng)
        pool.append(
            {
                "trajectory_id": f"unused:{rec['source_run_id']}-{rec['combat_index']}",
                "source_run_id": rec["source_run_id"],
                "source_combat_index": rec["combat_index"],
                "source_split": rec.get("split"),
                "spec": spec,
            }
        )
    return pool


def write_manifest(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--max", type=int, default=500)
    args = parser.parse_args()

    pool = build_pool(args.seed, args.max)
    print(f"Built pool of {len(pool)} never-used scenarios (seed={args.seed}) from {NEVER_USED_SPLITS}")

    for n in (50, 200, 500):
        subset = pool[:n]
        out_path = OUT_DIR / f"unused_{n}_manifest.jsonl"
        write_manifest(out_path, subset)
        print(f"  wrote {len(subset)} scenarios -> {out_path}")


if __name__ == "__main__":
    main()
