"""Regenerates the fixed 50-scenario benchmark set using scenario_from_runs.py's
current (v2: upgrade-aware, potion-aware, relic-unfiltered) generator, per the
Emulator-side "優先度2" update. Each candidate is validated against the LIVE emulator
before being kept (BattleEmulator.initialize() + one legal-actions fetch) - a scenario
that fails validation is logged and skipped, not silently included.

Spans act_number in {1,2,3} x pool_type in {weak,normal,elite,boss} for encounter
diversity, mixing 5-10 scenarios per (act,pool) bucket up to 50 total, deterministic
given SEED. Ascension varies naturally with whichever real runs get sampled (recorded,
not controlled - CombatScenario cannot apply ascension at all, see
Common/schemas/combat_scenario_input_schema.json).

Run: python generate_fixed_50.py
Writes: fixed_50_scenarios.json (list of scenario specs) and
        fixed_50_manifest.json (per-scenario validation outcome + summary stats).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))  # Combat/data
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # Combat/

from scenario_from_runs import generate_scenarios  # noqa: E402
from battle_emulator import BattleEmulator  # noqa: E402

SEED = 20260720
TARGET_TOTAL = 50
BUCKETS = [
    (act, pool)
    for act in (1, 2, 3)
    for pool in ("weak", "normal", "elite", "boss")
]
PER_BUCKET = 5  # 3 acts * 4 pools * 5 = 60 candidates drawn, validated down toward 50


def main() -> None:
    emu = BattleEmulator()
    kept: list[dict] = []
    manifest: list[dict] = []

    for act_number, pool_type in BUCKETS:
        candidates = generate_scenarios(
            n=PER_BUCKET, act_number=act_number, pool_type=pool_type, seed=SEED + act_number * 10 + hash(pool_type) % 97
        )
        for spec in candidates:
            if len(kept) >= TARGET_TOTAL:
                break
            entry = {
                "character_id": spec["character_id"],
                "act_number": act_number,
                "pool_type": pool_type,
                "encounter": spec["source"]["encounter"],
                "enemies": [e["monster_id"] for e in spec["enemies"]],
                "ascension": spec["source"].get("ascension"),
                "build_id": spec["source"].get("build_id"),
            }
            try:
                state = emu.initialize(spec)
                legal = emu.enumerate_legal_actions(state)
                entry["validation"] = "ok"
                entry["legal_action_count"] = len(legal)
                kept.append(spec)
            except Exception as exc:  # noqa: BLE001
                entry["validation"] = "failed"
                entry["error_type"] = type(exc).__name__
                entry["error_message"] = str(exc)[:300]
            manifest.append(entry)

    out_dir = Path(__file__).parent
    with (out_dir / "fixed_50_scenarios.json").open("w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)

    summary = {
        "seed": SEED,
        "target_total": TARGET_TOTAL,
        "kept": len(kept),
        "candidates_tried": len(manifest),
        "failed": [m for m in manifest if m["validation"] == "failed"],
        "character_distribution": {},
        "act_pool_distribution": {},
        "entries": manifest,
    }
    for spec in kept:
        c = spec["character_id"]
        summary["character_distribution"][c] = summary["character_distribution"].get(c, 0) + 1
    for entry in manifest:
        if entry["validation"] != "ok":
            continue
        key = f"{entry['act_number']}:{entry['pool_type']}"
        summary["act_pool_distribution"][key] = summary["act_pool_distribution"].get(key, 0) + 1

    with (out_dir / "fixed_50_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"kept {len(kept)}/{TARGET_TOTAL} (tried {len(manifest)} candidates)")
    print("character_distribution:", summary["character_distribution"])
    print("act_pool_distribution:", summary["act_pool_distribution"])
    if summary["failed"]:
        print(f"{len(summary['failed'])} candidates failed validation:")
        for f_entry in summary["failed"]:
            print(" ", f_entry["character_id"], f_entry["encounter"], f_entry["error_type"], f_entry["error_message"][:100])


if __name__ == "__main__":
    main()
