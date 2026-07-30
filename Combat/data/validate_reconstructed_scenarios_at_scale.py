"""Stage-2 Emulator validation for the full-scale reconstruction
(reconstruct_all_runs.py's output): at least 1 combat per run (the earliest
exact/ambiguous_upgrade encounter in that run), plus stratified extra sampling across
the requested priority categories (5 characters, schema v8/v9, Act 1-3, monster/elite/
boss, upgraded cards present, potions present, multiple enemies, many relics as a proxy
for "special relics present", low HP fraction).

Deliberately excludes unsupported_id/history_inconsistent records from Emulator calls -
those are already known-bad for a known, offline-confirmed reason (see
reconstruct_floor_state.classify_restore_status); spending Emulator time re-confirming
them adds no information here (a small confirmatory subsample would, but is not this
script's job).

Does NOT rewrite the (large) floor_states_*.jsonl files - writes validation_status
results to a separate emulator_validation.jsonl keyed by (source_run_id, combat_index),
per the "keep floor_states/scenario_manifest/conversion_errors/emulator_validation/
reconstruction_summary separated" requirement. Written incrementally (flushed after
every record) so a long run's partial progress is never lost if interrupted.

Per worker_timeout_policy.md's contract: on ScenarioInitializationTimeoutException, this
process still just catches, classifies as emulator_timeout, and moves on (a single
sequential process, not a discardable multi-worker pool) - acceptable here because
EnterRoomWithTimeout's 30s guard is confirmed (Combat/evaluation/reports/emulator_hang/)
to reliably return control rather than hanging forever; a real multi-worker Phase 1
rollout pool would still need to retire the whole process per that policy, this
validation script is not that pool.

Run: python validate_reconstructed_scenarios_at_scale.py [--extra-per-category N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/

from reconstruct_floor_state import encounter_to_scenario_spec  # noqa: E402
from scenario_from_runs import load_monster_hp  # noqa: E402
from battle_emulator import BattleEmulator  # noqa: E402

OUT_DIR = Path(__file__).parent / "full_reconstruction"
SPLITS = ["train", "validation", "test", "benchmark"]
CANDIDATE_STATUSES = {"exact", "ambiguous_upgrade"}


def stream_all_records():
    for split in SPLITS:
        path = OUT_DIR / f"floor_states_{split}.jsonl"
        with path.open(encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


def select_samples(extra_per_category: int, seed: int = 20260721):
    rng = random.Random(seed)
    baseline: dict = {}
    candidates = []
    for rec in stream_all_records():
        if rec["restore_status"] not in CANDIDATE_STATUSES:
            continue
        rid = rec["source_run_id"]
        if rid not in baseline or rec["combat_index"] < baseline[rid]["combat_index"]:
            baseline[rid] = rec
        candidates.append(rec)

    rng.shuffle(candidates)
    dims = {
        "character": lambda r: r["character"],
        "schema_version": lambda r: r["schema_version"],
        "act": lambda r: r["act"],
        "pool_type": lambda r: r["pool_type"],
        "has_upgraded": lambda r: any(c["upgraded"] for c in r["deck"]),
        "has_potions": lambda r: bool(r["potions"]),
        "multi_enemy": lambda r: len(r["monster_ids"]) >= 2,
        "low_hp": lambda r: r["player_hp"] is not None and r["player_max_hp"] and r["player_hp"] / r["player_max_hp"] < 0.3,
        "many_relics": lambda r: len(r["relics"]) >= 10,
    }
    counts: dict = {}
    baseline_keys = {(r["source_run_id"], r["combat_index"]) for r in baseline.values()}
    extra: list = []
    for rec in candidates:
        key = (rec["source_run_id"], rec["combat_index"])
        if key in baseline_keys:
            continue
        take = False
        for dim, fn in dims.items():
            bucket = (dim, fn(rec))
            c = counts.get(bucket, 0)
            if c < extra_per_category:
                counts[bucket] = c + 1
                take = True
        if take:
            extra.append(rec)

    return list(baseline.values()), extra, dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra-per-category", type=int, default=15)
    parser.add_argument("--limit", type=int, default=None, help="cap total validations (debug only)")
    args = parser.parse_args()

    print("selecting samples...", flush=True)
    baseline, extra, category_counts = select_samples(args.extra_per_category)
    all_selected = baseline + extra
    if args.limit:
        all_selected = all_selected[: args.limit]
    print(f"baseline={len(baseline)} extra={len(extra)} total={len(all_selected)}", flush=True)
    print("category_counts:", json.dumps({f"{k[0]}={k[1]}": v for k, v in category_counts.items()}, ensure_ascii=False), flush=True)

    monster_hp = load_monster_hp()
    rng = random.Random(2026)
    emu = BattleEmulator()

    out_path = OUT_DIR / "emulator_validation.jsonl"
    status_counts: dict = {}
    t_start = time.time()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, rec in enumerate(all_selected):
            spec = encounter_to_scenario_spec(rec, monster_hp, rng)
            result = {
                "source_run_id": rec["source_run_id"],
                "combat_index": rec["combat_index"],
                "character": rec["character"],
                "schema_version": rec["schema_version"],
                "act": rec["act"],
                "pool_type": rec["pool_type"],
            }
            t0 = time.time()
            try:
                state = emu.initialize(spec)
                legal = emu.enumerate_legal_actions(state)
                result["validation_status"] = "emulator_valid"
                result["legal_action_count"] = len(legal)
            except Exception as exc:  # noqa: BLE001
                type_name = type(exc).__name__
                if "Timeout" in type_name:
                    result["validation_status"] = "emulator_timeout"
                else:
                    result["validation_status"] = "emulator_invalid"
                result["error_type"] = type_name
                result["error_message"] = str(exc)[:300]
            result["elapsed_s"] = round(time.time() - t0, 2)
            status_counts[result["validation_status"]] = status_counts.get(result["validation_status"], 0) + 1

            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_f.flush()

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t_start
                rate = (i + 1) / elapsed
                remaining = (len(all_selected) - (i + 1)) / rate if rate > 0 else float("inf")
                print(
                    f"...{i + 1}/{len(all_selected)} done, {elapsed:.0f}s elapsed, "
                    f"~{remaining:.0f}s remaining, status_counts={status_counts}",
                    flush=True,
                )

    elapsed_total = time.time() - t_start
    print(f"\nDONE: {len(all_selected)} validations in {elapsed_total:.1f}s")
    print("final status_counts:", status_counts)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
