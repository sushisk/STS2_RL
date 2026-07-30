"""Targeted re-validation of the 284 scenarios that previously failed with
NullReferenceException (251 of them due to LOST_COFFER specifically, 2 ORRERY, 31
other/unrelated - see full_reconstruction/emulator_validation.PRE_LOSTCOFFER_FIX.jsonl),
against the rebuilt Emulator DLL (RunState.AppendToMapPointHistory +
RewardsSet.testSelector fix - see GameInstance.cs:347-410).

Checks, per scenario: init success/failure+type, no extra cards in hand/draw/discard/
exhaust piles beyond what was specified, no extra potions beyond what was specified,
relics list matches, one Step() (End Turn) succeeds afterward, and determinism (running
the identical scenario spec twice yields an identical resulting hand/hp/enemy-hp after
that one step).

Run: python revalidate_lostcoffer_fix.py
"""

from __future__ import annotations

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
PRE_FIX_VALIDATION = OUT_DIR / "emulator_validation.PRE_LOSTCOFFER_FIX.jsonl"


def load_previously_failed_keys() -> set:
    keys = set()
    with PRE_FIX_VALIDATION.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("error_type") == "NullReferenceException":
                keys.add((r["source_run_id"], r["combat_index"]))
    return keys


def fetch_records(keys: set) -> list[dict]:
    found = []
    remaining = set(keys)
    for split in SPLITS:
        if not remaining:
            break
        path = OUT_DIR / f"floor_states_{split}.jsonl"
        with path.open(encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                key = (rec["source_run_id"], rec["combat_index"])
                if key in remaining:
                    found.append(rec)
                    remaining.discard(key)
    return found


def piles_from_state(state_dict: dict) -> list[str]:
    ids = []
    for pile in ("hand", "drawPile", "discardPile", "exhaustPile", "playPile"):
        ids.extend(c["id"] for c in state_dict.get(pile) or [])
    return sorted(ids)


def main() -> None:
    keys = load_previously_failed_keys()
    print(f"previously-failed NullReferenceException scenarios: {len(keys)}")
    records = fetch_records(keys)
    print(f"fetched {len(records)} full records")

    monster_hp = load_monster_hp()
    emu = BattleEmulator()

    results = []
    init_ok = 0
    init_failed = 0
    pollution_found = 0
    step_failed = 0
    nondeterministic = 0
    error_type_counts: dict = {}

    for rec in records:
        rng = random.Random(2026)  # fixed seed for the spec-building shuffle, same both runs
        spec = encounter_to_scenario_spec(rec, monster_hp, rng)
        spec["seed"] = 12345  # fixed emulator seed for determinism check

        expected_cards = sorted(
            [c["card_id"] for c in spec["hand_cards"]]
            + [c["card_id"] for c in spec["draw_pile_cards"]]
            + [c["card_id"] for c in spec["discard_pile_cards"]]
            + [c["card_id"] for c in spec["exhaust_pile_cards"]]
        )
        expected_potions = sorted(p["potion_id"] for p in spec["potions"])
        expected_relics = sorted(spec["relics"])

        entry = {"source_run_id": rec["source_run_id"], "combat_index": rec["combat_index"], "relics": rec["relics"]}
        try:
            state = emu.initialize(spec)
        except Exception as exc:  # noqa: BLE001
            init_failed += 1
            entry["result"] = "init_failed"
            entry["error_type"] = type(exc).__name__
            entry["error_message"] = str(exc)[:200]
            error_type_counts[type(exc).__name__] = error_type_counts.get(type(exc).__name__, 0) + 1
            results.append(entry)
            continue

        init_ok += 1
        actual_cards = piles_from_state(state.engine_state)
        actual_potions = sorted(p["id"] for p in (state.engine_state.get("potions") or []) if p)
        actual_relics = sorted(r["id"] for r in (state.engine_state.get("relics") or []))

        card_pollution = actual_cards != expected_cards
        potion_pollution = actual_potions != expected_potions
        relic_mismatch = actual_relics != expected_relics
        if card_pollution or potion_pollution or relic_mismatch:
            pollution_found += 1
            entry["pollution"] = {
                "card_pollution": card_pollution,
                "potion_pollution": potion_pollution,
                "relic_mismatch": relic_mismatch,
                "expected_cards": expected_cards,
                "actual_cards": actual_cards,
                "expected_potions": expected_potions,
                "actual_potions": actual_potions,
                "expected_relics": expected_relics,
                "actual_relics": actual_relics,
            }

        legal = emu.enumerate_legal_actions(state)
        end_turn = next((a for a in legal if a["action_type"] == "system"), None)
        step_ok = True
        result1 = None
        if end_turn is not None:
            try:
                state1 = emu.apply_action(state, end_turn)
                result1 = (state1.engine_state.get("hp"), tuple(sorted(e["hp"] for e in state1.engine_state.get("enemies") or [])))
            except Exception as exc:  # noqa: BLE001
                step_ok = False
                step_failed += 1
                entry["step_error"] = f"{type(exc).__name__}: {str(exc)[:150]}"

        # determinism: re-run the identical spec from scratch, compare post-1-step result
        is_deterministic = True
        if end_turn is not None and step_ok:
            try:
                state_b = emu.initialize(spec)
                legal_b = emu.enumerate_legal_actions(state_b)
                end_turn_b = next(a for a in legal_b if a["action_type"] == "system")
                state1_b = emu.apply_action(state_b, end_turn_b)
                result1_b = (state1_b.engine_state.get("hp"), tuple(sorted(e["hp"] for e in state1_b.engine_state.get("enemies") or [])))
                is_deterministic = result1 == result1_b
                if not is_deterministic:
                    nondeterministic += 1
                    entry["determinism_mismatch"] = {"run1": result1, "run2": result1_b}
            except Exception as exc:  # noqa: BLE001
                entry["determinism_check_error"] = f"{type(exc).__name__}: {str(exc)[:150]}"

        entry["result"] = "ok" if (not card_pollution and not potion_pollution and not relic_mismatch and step_ok and is_deterministic) else "issue"
        results.append(entry)

    out_path = OUT_DIR / "lostcoffer_fix_revalidation.json"
    summary = {
        "total_checked": len(records),
        "init_ok": init_ok,
        "init_failed": init_failed,
        "pollution_found": pollution_found,
        "step_failed": step_failed,
        "nondeterministic": nondeterministic,
        "init_failure_error_types": error_type_counts,
        "results": results,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"init_ok={init_ok} init_failed={init_failed} pollution_found={pollution_found} "
          f"step_failed={step_failed} nondeterministic={nondeterministic}")
    print("init_failure_error_types:", error_type_counts)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
