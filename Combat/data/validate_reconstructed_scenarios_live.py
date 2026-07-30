"""Validates reconstruct_floor_state.py's output against the LIVE emulator: draws
reconstructed pre-combat encounters from a sample of runs, converts each to a scenario
spec (encounter_to_scenario_spec), and calls BattleEmulator.initialize() on it - not
just structural/offline validation (validate_run_reconstruction), but "does this actually
load into the real game".

Run: python validate_reconstructed_scenarios_live.py
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/

from reconstruct_floor_state import (  # noqa: E402
    encounter_to_scenario_spec,
    load_runs,
    reconstruct_encounters_for_run,
)
from scenario_from_runs import load_monster_hp  # noqa: E402
from battle_emulator import BattleEmulator  # noqa: E402


def usable(run: dict) -> bool:
    return (
        not run.get("was_abandoned")
        and not run.get("_isCheated")
        and run.get("game_mode") == "standard"
        and run.get("players")
    )


def main() -> None:
    rng = random.Random(2026)
    monster_hp = load_monster_hp()
    runs = [r for r in load_runs(limit=3000) if usable(r)][:30]

    emu = BattleEmulator()
    results = []
    for run in runs:
        encounters = reconstruct_encounters_for_run(run)
        if not encounters:
            continue
        # sample up to 2 encounters per run (early + a later one) for coverage without
        # spending the whole budget on any single run
        sample_indices = sorted({0, len(encounters) // 2, len(encounters) - 1})
        for idx in sample_indices[:2]:
            encounter = encounters[idx]
            spec = encounter_to_scenario_spec(encounter, monster_hp, rng)
            entry = {
                "server_id": encounter["source_run_id"],
                "act": encounter["act"],
                "floor": encounter["floor"],
                "encounter_id": encounter["encounter_id"],
                "character": encounter["character"],
                "deck_size": len(encounter["deck"]),
                "relics": len(encounter["relics"]),
                "potions": len(encounter["potions"]),
                "hp": f"{encounter['player_hp']}/{encounter['player_max_hp']}",
            }
            try:
                t0 = time.time()
                state = emu.initialize(spec)
                legal = emu.enumerate_legal_actions(state)
                entry["result"] = "ok"
                entry["elapsed_s"] = round(time.time() - t0, 2)
                entry["legal_action_count"] = len(legal)
            except Exception as exc:  # noqa: BLE001
                entry["result"] = "failed"
                entry["error_type"] = type(exc).__name__
                entry["error_message"] = str(exc)[:300]
            results.append(entry)

    ok = sum(1 for r in results if r["result"] == "ok")
    failed = [r for r in results if r["result"] != "ok"]

    out_path = Path(__file__).parent / "reconstructed_scenarios_live_validation.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"total": len(results), "ok": ok, "failed": len(failed), "results": results}, f, indent=2, ensure_ascii=False)

    print(f"total={len(results)} ok={ok} failed={len(failed)}")
    for r in failed:
        print(" FAIL", r["server_id"], r["act"], r["floor"], r["encounter_id"], r.get("error_type"), r.get("error_message", "")[:100])
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
