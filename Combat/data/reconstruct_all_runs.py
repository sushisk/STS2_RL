"""Full-scale floor-state reconstruction: all 5,997 usable runs from
runs-all-before-2026-06.json, run-level split into train/validation/test/benchmark,
offline restore_status classification (exact/ambiguous_upgrade/unsupported_id/
history_inconsistent), and separated output files per the requested structure.

This is the "全ランへのフロア時点状態復元" full-scale run - the prototype (50 runs) is
in reconstruct_floor_state.py / validate_reconstruction_staged.py, already validated.
Emulator-side validation is a SEPARATE stage - see validate_reconstructed_scenarios_at_scale.py
(this script produces `restore_status` only; `validation_status` is left "not_validated"
here and filled in by that follow-up script, written to a separate emulator_validation
file rather than requiring a rewrite of the (potentially large) floor_states files).

Run: python reconstruct_all_runs.py
Writes (all under Combat/data/full_reconstruction/):
  floor_states_{train,validation,test,benchmark}.jsonl  - one combat-ready state per line
  scenario_manifest.jsonl   - lightweight per-encounter index (for fast filtering/sampling
                               without loading full floor_states records)
  conversion_errors.jsonl   - runs that failed reconstruction outright (exception raised)
  reconstruction_summary.json - aggregate stats (see report doc for the full field list)
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from reconstruct_floor_state import (
    ReplayState,
    classify_restore_status,
    load_runs,
    reconstruct_encounters_for_run,
    strip_prefix,
    validate_run_reconstruction,
)

OUT_DIR = Path(__file__).parent / "full_reconstruction"
SPLITS = ["train", "validation", "test", "benchmark"]


def usable(run: dict) -> bool:
    return (
        not run.get("was_abandoned")
        and not run.get("_isCheated")
        and run.get("game_mode") == "standard"
        and run.get("players")
    )


def split_for_run(server_id) -> str:
    """Deterministic, run-level (not encounter-level) split via a stable hash of
    source_run_id - every encounter from the same run always lands in the same split,
    per the explicit instruction not to mix same-run states across splits. 80/10/5/5."""
    h = hashlib.sha256(str(server_id).encode("utf-8")).hexdigest()
    bucket = int(h[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    if bucket < 95:
        return "test"
    return "benchmark"


def final_state_for_run(run: dict) -> dict:
    players = run.get("players") or []
    character_entry = strip_prefix(players[0].get("character", ""), "CHARACTER.") if players else ""
    state = ReplayState(character_entry, ascension=run.get("ascension"))
    for act_points in run.get("map_point_history") or []:
        for point in act_points:
            ps_list = point.get("player_stats") or []
            if ps_list:
                state.apply_point_stats(ps_list[0])
    return state.snapshot()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    floor_state_files = {s: (OUT_DIR / f"floor_states_{s}.jsonl").open("w", encoding="utf-8") for s in SPLITS}
    manifest_file = (OUT_DIR / "scenario_manifest.jsonl").open("w", encoding="utf-8")
    errors_file = (OUT_DIR / "conversion_errors.jsonl").open("w", encoding="utf-8")

    restore_status_counts: Counter = Counter()
    hp_status_counts: Counter = Counter()
    split_run_counts: Counter = Counter()
    split_encounter_counts: Counter = Counter()
    character_counts: Counter = Counter()
    act_counts: Counter = Counter()
    pool_type_counts: Counter = Counter()
    schema_version_counts: Counter = Counter()
    deck_match_count = 0
    relic_match_count = 0
    comparable_runs = 0

    total_runs_processed = 0
    total_runs_failed = 0
    total_encounters = 0

    for run in load_runs():
        if not usable(run):
            continue
        total_runs_processed += 1
        server_id = run.get("_serverId")
        split = split_for_run(server_id)
        split_run_counts[split] += 1
        schema_version_counts[run.get("schema_version")] += 1

        try:
            encounters = reconstruct_encounters_for_run(run)
        except Exception as exc:  # noqa: BLE001
            total_runs_failed += 1
            errors_file.write(
                json.dumps(
                    {"source_run_id": server_id, "error_type": type(exc).__name__, "error_message": str(exc)[:500]},
                    ensure_ascii=False,
                )
                + "\n"
            )
            continue

        try:
            final_state = final_state_for_run(run)
            validation = validate_run_reconstruction(run, final_state)
            if validation.get("comparable"):
                comparable_runs += 1
                if validation["deck_matches_final_state"]:
                    deck_match_count += 1
                if validation["relics_match_final_state"]:
                    relic_match_count += 1
        except Exception as exc:  # noqa: BLE001
            errors_file.write(
                json.dumps(
                    {"source_run_id": server_id, "error_type": f"validation:{type(exc).__name__}", "error_message": str(exc)[:500]},
                    ensure_ascii=False,
                )
                + "\n"
            )

        for combat_index, e in enumerate(encounters):
            status, unsupported = classify_restore_status(e)
            restore_status_counts[status] += 1
            hp_status_counts[e["hp_restore_status"]] += 1
            character_counts[e["character"]] += 1
            act_counts[e["act"]] += 1
            pool_type_counts[e["pool_type"]] += 1
            split_encounter_counts[split] += 1
            total_encounters += 1

            record = {
                "source_run_id": e["source_run_id"],
                "map_point_index": e["map_point_index"],
                "combat_index": combat_index,
                "schema_version": e["schema_version"],
                "build_id": e["build_id"],
                "character": e["character"],
                "ascension": e["ascension"],
                "act": e["act"],
                "floor": e["floor"],
                "encounter_id": e["encounter_id"],
                "monster_ids": e["monster_ids"],
                "pool_type": e["pool_type"],
                "player_hp": e["player_hp"],
                "player_max_hp": e["player_max_hp"],
                "deck": e["deck"],
                "relics": e["relics"],
                "potions": e["potions"],
                "gold": e["gold"],
                "restore_status": status,
                "validation_status": "not_validated",
                "warnings": e["warnings"],
                "unsupported_ids": unsupported,
                "split": split,
            }
            floor_state_files[split].write(json.dumps(record, ensure_ascii=False) + "\n")
            manifest_file.write(
                json.dumps(
                    {
                        "source_run_id": e["source_run_id"],
                        "combat_index": combat_index,
                        "map_point_index": e["map_point_index"],
                        "split": split,
                        "character": e["character"],
                        "schema_version": e["schema_version"],
                        "act": e["act"],
                        "floor": e["floor"],
                        "pool_type": e["pool_type"],
                        "encounter_id": e["encounter_id"],
                        "restore_status": status,
                        "has_upgraded_cards": any(c["upgraded"] for c in e["deck"]),
                        "has_potions": bool(e["potions"]),
                        "num_enemies": len(e["monster_ids"]),
                        "player_hp_fraction": (e["player_hp"] / e["player_max_hp"]) if e["player_hp"] is not None and e["player_max_hp"] else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        if total_runs_processed % 500 == 0:
            elapsed = time.time() - t_start
            print(f"...{total_runs_processed} runs processed, {total_encounters} encounters, {elapsed:.1f}s elapsed", flush=True)

    for f in floor_state_files.values():
        f.close()
    manifest_file.close()
    errors_file.close()

    elapsed_total = time.time() - t_start
    summary = {
        "total_runs_processed": total_runs_processed,
        "total_runs_failed": total_runs_failed,
        "total_encounters": total_encounters,
        "elapsed_seconds": round(elapsed_total, 1),
        "split_run_counts": dict(split_run_counts),
        "split_encounter_counts": dict(split_encounter_counts),
        "restore_status_counts": dict(restore_status_counts),
        "restore_status_pct": {k: round(100 * v / total_encounters, 2) for k, v in restore_status_counts.items()} if total_encounters else {},
        "hp_status_counts": dict(hp_status_counts),
        "hp_exact_pct": round(100 * hp_status_counts.get("exact", 0) / total_encounters, 2) if total_encounters else None,
        "character_counts": dict(character_counts),
        "act_counts": dict(act_counts),
        "pool_type_counts": dict(pool_type_counts),
        "schema_version_counts": {str(k): v for k, v in schema_version_counts.items()},
        "run_level_validation": {
            "comparable_runs": comparable_runs,
            "deck_match_count": deck_match_count,
            "deck_match_pct": round(100 * deck_match_count / comparable_runs, 2) if comparable_runs else None,
            "relic_match_count": relic_match_count,
            "relic_match_pct": round(100 * relic_match_count / comparable_runs, 2) if comparable_runs else None,
        },
    }
    with (OUT_DIR / "reconstruction_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote outputs to {OUT_DIR}")


if __name__ == "__main__":
    main()
