"""Staged validation of reconstruct_floor_state.py, per the requested rollout: schema v8
samples -> schema v9 samples -> a small win/loss/abandoned mix -> ~50 runs -> (not run
here) all usable runs. Each stage reports deck/relic reconstruction match rate against
the run's own final state (validate_run_reconstruction) and the HP status distribution
(exact/reconstructed/partial/unavailable/inconsistent) across every combat encounter
found. No stage silently continues past a widespread mismatch - see main()'s printed
per-stage summary.

Run: python validate_reconstruction_staged.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from reconstruct_floor_state import (
    ReplayState,
    load_runs,
    reconstruct_encounters_for_run,
    strip_prefix,
    validate_run_reconstruction,
)

RUNS_PATH = Path(r"C:\STS2_Data\runs-all-before-2026-06.json")


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


def evaluate_runs(runs: list[dict], label: str) -> dict:
    hp_status_counts: Counter = Counter()
    deck_match = 0
    relic_match = 0
    comparable = 0
    total_runs = 0
    total_encounters = 0
    runs_with_warnings = 0
    per_run_details = []

    for run in runs:
        total_runs += 1
        encounters = reconstruct_encounters_for_run(run)
        total_encounters += len(encounters)
        any_warnings = any(e["warnings"] for e in encounters)
        if any_warnings:
            runs_with_warnings += 1
        for e in encounters:
            hp_status_counts[e["hp_restore_status"]] += 1

        final_state = final_state_for_run(run)
        validation = validate_run_reconstruction(run, final_state)
        if validation.get("comparable"):
            comparable += 1
            if validation["deck_matches_final_state"]:
                deck_match += 1
            if validation["relics_match_final_state"]:
                relic_match += 1

        per_run_details.append(
            {
                "server_id": run.get("_serverId"),
                "schema_version": run.get("schema_version"),
                "encounters_found": len(encounters),
                "deck_matches": validation.get("deck_matches_final_state"),
                "relics_match": validation.get("relics_match_final_state"),
                "warnings_present": any_warnings,
            }
        )

    summary = {
        "label": label,
        "total_runs": total_runs,
        "total_encounters_found": total_encounters,
        "deck_match_rate": f"{deck_match}/{comparable}" if comparable else "n/a",
        "relic_match_rate": f"{relic_match}/{comparable}" if comparable else "n/a",
        "runs_with_warnings": f"{runs_with_warnings}/{total_runs}",
        "hp_status_distribution": dict(hp_status_counts),
        "per_run_details": per_run_details,
    }
    return summary


def usable(run: dict) -> bool:
    return (
        not run.get("was_abandoned")
        and not run.get("_isCheated")
        and run.get("game_mode") == "standard"
        and run.get("players")
    )


def main() -> None:
    results = {}

    # Stage 1: a handful of schema v8 runs
    v8_runs = [r for r in load_runs(schema_version=8, limit=200) if usable(r)][:8]
    results["stage1_v8_sample"] = evaluate_runs(v8_runs, "stage1_v8_sample")

    # Stage 2: a handful of schema v9 runs
    v9_runs = [r for r in load_runs(schema_version=9, limit=200) if usable(r)][:8]
    results["stage2_v9_sample"] = evaluate_runs(v9_runs, "stage2_v9_sample")

    # Stage 3: small mix including win/loss/abandoned (abandoned runs may have partial history)
    mix_runs = []
    wins = losses = abandoned = 0
    for r in load_runs(limit=2000):
        if r.get("was_abandoned") and abandoned < 3:
            mix_runs.append(r)
            abandoned += 1
        elif r.get("win") and wins < 3 and not r.get("was_abandoned"):
            mix_runs.append(r)
            wins += 1
        elif not r.get("win") and not r.get("was_abandoned") and losses < 3:
            mix_runs.append(r)
            losses += 1
        if wins >= 3 and losses >= 3 and abandoned >= 3:
            break
    results["stage3_win_loss_abandoned_mix"] = evaluate_runs(mix_runs, "stage3_win_loss_abandoned_mix")

    # Stage 4: ~50 usable runs
    fifty_runs = [r for r in load_runs(limit=3000) if usable(r)][:50]
    results["stage4_fifty_runs"] = evaluate_runs(fifty_runs, "stage4_fifty_runs")

    out_path = Path(__file__).parent / "reconstruction_validation_report.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    for stage_key, summary in results.items():
        print(f"=== {stage_key} ===")
        print(f"  total_runs={summary['total_runs']} total_encounters={summary['total_encounters_found']}")
        print(f"  deck_match_rate={summary['deck_match_rate']} relic_match_rate={summary['relic_match_rate']}")
        print(f"  runs_with_warnings={summary['runs_with_warnings']}")
        print(f"  hp_status_distribution={summary['hp_status_distribution']}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
