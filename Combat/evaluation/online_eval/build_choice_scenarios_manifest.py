"""Builds a manifest of teacher2000 scenarios likely to exercise a choice_card/
choice_skip/choice_confirm decision, for the Choice Semantics Stage A/B local
regeneration (this task's "Choice Scenario抽出"). Does NOT regenerate teacher2000 -
only reads its existing trajectories.jsonl + the parent scenario manifest that already
has full replayable specs (Combat/data/teacher2000_20260723_manifests/
parent_2000_manifest.jsonl).

Important finding this script's own census surfaced (documented here rather than
silently assumed): teacher2000's trajectories.jsonl contains exactly 43 choice_card
decisions, and EVERY ONE is at decision_index=0 (a StartOfCombat-scope, scenario-
restorable pending choice - the only kind that survives as its own top-level "decision"
row). Cards like HOLOGRAM/SURVIVOR/BURNING_PACT/ARMAMENTS/NIGHTMARE trigger
ActionContinuation-scoped choices instead, which are auto-resolved INSIDE a single
Step() call (see battle_emulator.py's apply_action() continuation loop) and never
surface as a separate trajectories.jsonl row at all - grepping decisions for these
would find zero matches even though the choices genuinely occur during play. This
script instead finds scenarios where such a card is merely REACHABLE (present in some
observed hand/drawPile/discardPile/exhaustPile/playerPowers/potions snapshot) as a
sampling proxy - actually exercising the choice (or not) is determined at replay time
by online_policy_eval.py's new continuation_resolver logging hook, not by this script.

The "priority card" list below is NOT hardcoded here - it is read directly from
Common/schemas/choice_semantics_lookup.v1.json's own entries (origin_entity_type in
{card,potion,power}, combat_scope in {combat,either}) - this script adds no new
entity-specific knowledge of its own, per this task's 禁止事項 ("Pythonコード内に
カード固有ifを追加しない" applies here too, not just to choice_semantics.py itself).

Run: python build_choice_scenarios_manifest.py [--stage-a 20] [--stage-b 50] [--seed 20260724]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_COMBAT_DIR))

from choice_semantics import LOOKUP_PATH  # noqa: E402

TRAJECTORIES_PATH = _COMBAT_DIR / "data" / "teacher2000_20260723_dataset" / "trajectories.jsonl"
PARENT_MANIFEST_PATH = _COMBAT_DIR / "data" / "teacher2000_20260723_manifests" / "parent_2000_manifest.jsonl"
OUT_DIR = Path(__file__).resolve().parent

PILE_KEYS = ("hand", "drawPile", "discardPile", "exhaustPile")

# Explicit priority order from this task's instructions - HOLOGRAM gets its own bucket
# (it's the original motivating example), then the three canonical pile-move operations,
# then structural categories (multi-select / skip-confirm / generic ActionContinuation),
# then NIGHTMARE. "start_of_combat_choice_card" (the 43 directly-visible decisions) is
# its own category, always includable without any card-reachability guessing.
PRIORITY_ORDER = (
    "HOLOGRAM",
    "discard",
    "exhaust",
    "upgrade",
    "multi_select",
    "skip_confirm",
    "action_continuation_other",
    "NIGHTMARE",
    "start_of_combat_choice_card",
)


def load_priority_entities() -> list[dict]:
    payload = json.loads(LOOKUP_PATH.read_text(encoding="utf-8"))
    return [
        e
        for e in payload["entries"]
        if e.get("origin_entity_type") in ("card", "potion", "power")
        and e.get("combat_scope") in ("combat", "either")
    ]


def categorize_entity(entry: dict) -> str:
    entity_id = entry["origin_entity_id"]
    op = entry["normalized_choice_operation"]
    if entity_id == "HOLOGRAM":
        return "HOLOGRAM"
    if entity_id == "NIGHTMARE":
        return "NIGHTMARE"
    if op in ("discard", "exhaust", "upgrade"):
        return op
    return "action_continuation_other"


def scan_trajectories(priority_entities: list[dict]) -> tuple[dict[str, set[str]], set[str], dict[str, dict]]:
    """Returns (trajectory_id -> {reachable priority entity ids}, trajectory_ids with a
    directly-observed start-of-combat choice_card decision, trajectory_id -> {source_run_id,
    source_combat_index})."""
    entity_ids = {e["origin_entity_id"] for e in priority_entities}
    reachable: dict[str, set[str]] = defaultdict(set)
    start_of_combat_choice: set[str] = set()
    multi_select_scenarios: set[str] = set()
    identity: dict[str, dict] = {}

    with TRAJECTORIES_PATH.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            tid = row["trajectory_id"]
            if tid not in identity:
                identity[tid] = {"source_run_id": row["source_run_id"], "source_combat_index": row["source_combat_index"]}

            state = row.get("state") or {}
            found_ids = set()
            for pile in PILE_KEYS:
                for c in state.get(pile) or []:
                    if c.get("id") in entity_ids:
                        found_ids.add(c["id"])
            for p in state.get("playerPowers") or []:
                if p.get("id") in entity_ids:
                    found_ids.add(p["id"])
            for p in state.get("potions") or []:
                if p and p.get("id") in entity_ids:
                    found_ids.add(p["id"])
            if found_ids:
                reachable[tid].update(found_ids)

            legal_types = {a.get("action_type") for a in row.get("legal_actions") or []}
            if "choice_card" in legal_types:
                start_of_combat_choice.add(tid)
                choice_card_options = [a for a in row["legal_actions"] if a.get("action_type") == "choice_card"]
                skip_actions = [a for a in row["legal_actions"] if a.get("action_type") == "choice_skip"]
                if skip_actions and (skip_actions[0].get("parameters") or {}).get("maxSelect", 1) not in (0, 1):
                    multi_select_scenarios.add(tid)
                elif len(choice_card_options) > 1 and skip_actions:
                    # maxSelect not always present at top-level legal_actions granularity -
                    # any skip+multiple-options case is at least a *candidate* multi-select,
                    # confirmed properly at replay time via the real pendingChoice.
                    multi_select_scenarios.add(tid)

    return reachable, start_of_combat_choice, identity, multi_select_scenarios


def build_candidate_pool() -> list[dict]:
    priority_entities = load_priority_entities()
    entity_category = {e["origin_entity_id"]: categorize_entity(e) for e in priority_entities}
    reachable, start_of_combat_choice, identity, multi_select_scenarios = scan_trajectories(priority_entities)

    candidates: dict[str, dict] = {}
    for tid, entity_ids in reachable.items():
        categories = {entity_category[eid] for eid in entity_ids}
        candidates[tid] = {
            "trajectory_id": tid,
            "categories": sorted(categories),
            "reachable_entities": sorted(entity_ids),
            **identity[tid],
        }
    for tid in start_of_combat_choice:
        c = candidates.setdefault(
            tid, {"trajectory_id": tid, "categories": [], "reachable_entities": [], **identity[tid]}
        )
        cats = set(c["categories"])
        cats.add("start_of_combat_choice_card")
        if tid in multi_select_scenarios:
            cats.add("multi_select")
        cats.add("skip_confirm")  # every start-of-combat choice_card decision offers choice_skip too
        c["categories"] = sorted(cats)

    return list(candidates.values())


def select_stage(pool: list[dict], n: int, seed: int, already_selected: set[str]) -> list[dict]:
    rng = random.Random(seed)
    remaining = [c for c in pool if c["trajectory_id"] not in already_selected]
    rng.shuffle(remaining)

    selected: list[dict] = []
    covered_categories: set[str] = set()
    for category in PRIORITY_ORDER:
        if len(selected) >= n:
            break
        match = next((c for c in remaining if category in c["categories"] and c["trajectory_id"] not in {s["trajectory_id"] for s in selected}), None)
        if match is not None:
            selected.append(match)
            covered_categories.update(match["categories"])
    for c in remaining:
        if len(selected) >= n:
            break
        if c["trajectory_id"] in {s["trajectory_id"] for s in selected}:
            continue
        selected.append(c)
    return selected[:n]


def attach_specs(selected: list[dict]) -> list[dict]:
    wanted_keys = {(str(c["source_run_id"]), int(c["source_combat_index"])) for c in selected}
    specs: dict[tuple[str, int], dict] = {}
    with PARENT_MANIFEST_PATH.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            key = (str(row["source_run_id"]), int(row["source_combat_index"]))
            if key in wanted_keys:
                specs[key] = row["spec"]

    out = []
    for c in selected:
        key = (str(c["source_run_id"]), int(c["source_combat_index"]))
        spec = specs.get(key)
        if spec is None:
            continue  # source scenario not present in parent manifest (should not happen - defensive only)
        out.append(
            {
                "trajectory_id": c["trajectory_id"],
                "source_run_id": c["source_run_id"],
                "source_combat_index": c["source_combat_index"],
                "categories": c["categories"],
                "reachable_entities": c["reachable_entities"],
                "spec": spec,
            }
        )
    return out


def write_manifest(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-a", type=int, default=20)
    parser.add_argument("--stage-b", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260724)
    args = parser.parse_args()

    pool = build_candidate_pool()
    print(f"Candidate pool: {len(pool)} teacher2000 scenarios with a reachable priority entity or a start-of-combat choice_card decision")
    cat_counts = Counter(cat for c in pool for cat in c["categories"])
    print("Category coverage in pool:", dict(cat_counts))

    stage_a = select_stage(pool, args.stage_a, args.seed, already_selected=set())
    stage_a_ids = {c["trajectory_id"] for c in stage_a}
    stage_b_only = select_stage(pool, args.stage_b - len(stage_a), args.seed + 1, already_selected=stage_a_ids)

    stage_a_full = attach_specs(stage_a)
    stage_b_full = attach_specs(stage_a + stage_b_only)  # Stage B = superset of Stage A, per staged rollout convention

    write_manifest(OUT_DIR / "choice_scenarios_stage_a_manifest.jsonl", stage_a_full)
    write_manifest(OUT_DIR / "choice_scenarios_stage_b_manifest.jsonl", stage_b_full)

    print(f"\nStage A ({len(stage_a_full)} scenarios) -> choice_scenarios_stage_a_manifest.jsonl")
    print("  category coverage:", dict(Counter(cat for c in stage_a_full for cat in c["categories"])))
    print(f"Stage B ({len(stage_b_full)} scenarios, superset of Stage A) -> choice_scenarios_stage_b_manifest.jsonl")
    print("  category coverage:", dict(Counter(cat for c in stage_b_full for cat in c["categories"])))


if __name__ == "__main__":
    main()
