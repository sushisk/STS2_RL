"""Builds the re-verification manifest for use AFTER the Emulator team delivers a fix
for the origin-context-leak issue (Outputs/reports/rl_choice_semantics_origin_audit_report_20260724.md).
Does NOT change any code/lookup/rule - pure manifest construction from already-computed
audit data (choice_semantics_resolution_path_audit.jsonl) plus the existing
choice_scenarios_stage_b_manifest.jsonl's specs.

Coverage (this task's 待機中に許可する作業 list), deduplicated:
    - ALL 33 Origin-dependent scenarios (their resolution genuinely needs origin to be
      correct - the category most exposed to the leak bug)
    - The 3 Suspect scenarios (1934-19, 7413-9, 7551-16 - where the leak was observed)
    - 5 representative Safe scenarios (deterministic sample, seed 20260724, from the
      13 scenarios that are Safe-only i.e. not already in the above)
    - GamblingChipDiscard / GUARDS-post-leak / POWER_POTION-post-leak / SKILL_POTION-
      post-leak / multi-choice-in-one-step / relic-origin(TOOLBOX) - all already fully
      contained in the Origin-dependent+Suspect union (verified, see this task's
      investigation) - no separate entries needed
    - Card/Potion-origin choices - already present throughout the Origin-dependent set
    - Nested choice (DECISIONS_DECISIONS -> BURNING_PACT re-attribution) - NOT found
      anywhere in the real teacher2000-derived Stage A/B pool (verified: zero within-
      step origin changes across all 275 audited choices). Added as ONE hand-authored
      synthetic scenario (same spec already exercised live in
      Combat/tests/test_choice_semantics.py::test_nested_choice_reattributes_origin_and_classification),
      clearly tagged as synthetic, not teacher2000-sourced.

Run: python build_reverification_manifest.py
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
AUDIT_PATH = OUT_DIR / "choice_semantics_resolution_path_audit.jsonl"
STAGE_B_MANIFEST_PATH = OUT_DIR / "choice_scenarios_stage_b_manifest.jsonl"
OUT_PATH = OUT_DIR / "choice_semantics_reverification_manifest.jsonl"

SYNTHETIC_NESTED_CHOICE_SPEC = {
    "character_id": "REGENT", "player_hp": 80, "player_max_hp": 80, "seed": 1, "stars": 6,
    "hand_cards": [
        {"card_id": "DECISIONS_DECISIONS", "is_upgraded": False},
        {"card_id": "BURNING_PACT", "is_upgraded": False},
        {"card_id": "STRIKE_REGENT", "is_upgraded": False},
        {"card_id": "STRIKE_REGENT", "is_upgraded": False},
        {"card_id": "DEFEND_REGENT", "is_upgraded": False},
    ],
    "draw_pile_cards": [{"card_id": "STRIKE_REGENT", "is_upgraded": False} for _ in range(5)],
    "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
}


def load_audit_rows() -> list[dict]:
    with AUDIT_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_stage_b_specs() -> dict[str, dict]:
    specs = {}
    with STAGE_B_MANIFEST_PATH.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            specs[row["trajectory_id"]] = row
    return specs


def main() -> None:
    audit_rows = load_audit_rows()
    specs = load_stage_b_specs()

    by_scenario = defaultdict(list)
    for r in audit_rows:
        by_scenario[r["scenario_id"]].append(r)

    def scenario_categories(sid: str) -> set[str]:
        rows = by_scenario[sid]
        cats = set()
        risk = {r["riskCategory"] for r in rows}
        if "Safe" in risk:
            cats.add("safe_representative_candidate")
        if "Origin-dependent" in risk:
            cats.add("origin_dependent")
        if "Suspect" in risk:
            cats.add("suspect")
        if any(r["choiceType"] == "GamblingChipDiscard" for r in rows):
            cats.add("gambling_chip_discard")
        if any(r["rawOriginEntityId"] == "GUARDS" for r in rows):
            cats.add("guards_leak_source")
        if any(r["rawOriginEntityId"] == "POWER_POTION" for r in rows):
            cats.add("power_potion")
        if any(r["rawOriginEntityId"] == "SKILL_POTION" for r in rows):
            cats.add("skill_potion")
        if any(r["rawOriginEntityType"] == "relic" for r in rows):
            cats.add("relic_origin")
        if any(r["rawOriginEntityType"] == "card" for r in rows):
            cats.add("card_origin")
        if any(r["rawOriginEntityType"] not in (None, "card", "relic") for r in rows):
            cats.add("potion_origin")
        step_counts = defaultdict(int)
        for r in rows:
            step_counts[(r["arm"], r["decision_index"])] += 1
        if any(c > 1 for c in step_counts.values()):
            cats.add("multi_choice_one_step")
        return cats

    origin_dependent_ids = sorted(sid for sid in by_scenario if "Origin-dependent" in {r["riskCategory"] for r in by_scenario[sid]})
    suspect_ids = sorted(sid for sid in by_scenario if "Suspect" in {r["riskCategory"] for r in by_scenario[sid]})
    core_union = set(origin_dependent_ids) | set(suspect_ids)

    safe_only_pool = sorted(
        sid for sid in by_scenario
        if {r["riskCategory"] for r in by_scenario[sid]} == {"Safe"} and sid not in core_union
    )
    rng = random.Random(20260724)
    safe_representatives = sorted(rng.sample(safe_only_pool, min(5, len(safe_only_pool))))

    final_ids = sorted(core_union | set(safe_representatives))

    rows_out = []
    for sid in final_ids:
        source_row = specs.get(sid)
        if source_row is None:
            print(f"WARNING: no spec found for {sid} in Stage B manifest - skipped")
            continue
        rows_out.append(
            {
                "trajectory_id": sid,
                "source_run_id": source_row["source_run_id"],
                "source_combat_index": source_row["source_combat_index"],
                "spec": source_row["spec"],
                "categories": sorted(scenario_categories(sid)),
                "synthetic": False,
            }
        )

    rows_out.append(
        {
            "trajectory_id": "synthetic:nested_choice_decisions_decisions_burning_pact",
            "source_run_id": None,
            "source_combat_index": None,
            "spec": SYNTHETIC_NESTED_CHOICE_SPEC,
            "categories": ["nested_choice", "card_origin"],
            "synthetic": True,
            "synthetic_note": (
                "Hand-authored - no genuine nested-choice (within-step origin "
                "re-attribution) occurrence exists anywhere in the 275-choice Stage A/B "
                "audit pool. Same spec already exercised live in "
                "Combat/tests/test_choice_semantics.py::"
                "test_nested_choice_reattributes_origin_and_classification."
            ),
        }
    )

    with OUT_PATH.open("w", encoding="utf-8") as f:
        for row in rows_out:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    print(f"Wrote {len(rows_out)} scenarios -> {OUT_PATH}")
    print(f"  origin_dependent: {len(origin_dependent_ids)}")
    print(f"  suspect: {len(suspect_ids)} {suspect_ids}")
    print(f"  safe representatives: {safe_representatives}")
    print("  + 1 synthetic nested-choice scenario")
    from collections import Counter
    cat_counts = Counter(c for r in rows_out for c in r["categories"])
    print("  category coverage:", dict(cat_counts))


if __name__ == "__main__":
    main()
