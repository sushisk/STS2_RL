"""Builds the scenario manifest for Choice teacher-data generation (this task's
"Choiceデータ生成対象" section), on top of the newly-adopted 722b019 Emulator baseline
(Combat/policy_baseline/choice_semantics_baseline_722b019_v1_20260725.json).

Does NOT regenerate the whole 2000-scenario teacher2000 dataset - it only reads its
existing trajectories.jsonl (static census, same technique as build_choice_scenarios_
manifest.py) plus the parent manifest's replayable specs, and extracts the subset that
is likely to exercise a Choice or ActionContinuation-scoped Choice. Whether a candidate
actually produces a Choice (and how many, and in what order) is confirmed at REPLAY time
by generate_choice_teacher_data.py's logging continuation-resolver wrapper, not guessed
here - this script only builds a sampling pool, same division of responsibility as
build_choice_scenarios_manifest.py.

Categories requested (指示書3節), each covered by at least one selection bucket below:
  - Choice/ActionContinuation Choice occurred in teacher2000           -> all buckets
  - origin-dependent Choice                                           -> origin_dependent flag
  - Gambling Chip                                                     -> gambling_chip (sourced
                                                                          from choice_semantics.
                                                                          CHOICE_TYPE_RULES, not
                                                                          hardcoded fresh here)
  - Potion-origin Choice                                              -> potion_origin
  - discard / exhaust / upgrade / retrieve                            -> discard/exhaust/upgrade/retrieve
  - multi-select                                                      -> multi_select
  - skip / confirm                                                    -> skip_confirm
  - consecutive Choices within one Step                               -> candidate_multi_choice_in_one_step
                                                                          (static proxy: 2+ priority
                                                                          entities reachable at once;
                                                                          confirmed for real at replay)
  - synthetic nested Choice                                           -> reused verbatim from
                                                                          choice_semantics_reverification_
                                                                          manifest.jsonl (same synthetic
                                                                          REGENT/DECISIONS_DECISIONS/
                                                                          BURNING_PACT scenario built for
                                                                          the 722b019 re-verification -
                                                                          teacher2000 has zero naturally-
                                                                          occurring same-step origin
                                                                          re-attribution cases, per that
                                                                          manifest's own note)

Per this task's 禁止事項 ("unknownを推測補完しない" / no card-specific `if` in Python),
all entity-specific knowledge is read from choice_semantics_lookup.v1.json's own entries
and choice_semantics.CHOICE_TYPE_RULES - this script adds no new entity ids of its own.

Run: python build_choice_teacher_data_manifest.py [--target 200] [--seed 20260725]
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

from choice_semantics import LOOKUP_PATH, CHOICE_TYPE_RULES  # noqa: E402

TRAJECTORIES_PATH = _COMBAT_DIR / "data" / "teacher2000_20260723_dataset" / "trajectories.jsonl"
PARENT_MANIFEST_PATH = _COMBAT_DIR / "data" / "teacher2000_20260723_manifests" / "parent_2000_manifest.jsonl"
REVERIFICATION_MANIFEST_PATH = Path(__file__).resolve().parent / "choice_semantics_reverification_manifest.jsonl"
OUT_DIR = Path(__file__).resolve().parent

ZONE_KEYS = ("hand", "drawPile", "discardPile", "exhaustPile")

PRIORITY_ORDER = (
    "gambling_chip",
    "potion_origin",
    "HOLOGRAM",
    "discard",
    "exhaust",
    "upgrade",
    "retrieve",
    "multi_select",
    "skip_confirm",
    "candidate_multi_choice_in_one_step",
    "action_continuation_other",
    "NIGHTMARE",
    "start_of_combat_choice_card",
)

RETRIEVE_OPS = frozenset(
    {
        "retrieve_to_hand",
        "retrieve_to_hand_free",
        "retrieve_to_draw_pile_top",
        "return_to_draw_pile_top",
        "clone_to_hand",
        "clone_to_deck",
    }
)


def load_priority_entities() -> list[dict]:
    payload = json.loads(LOOKUP_PATH.read_text(encoding="utf-8"))
    entries = [
        e
        for e in payload["entries"]
        if e.get("origin_entity_type") in ("card", "potion", "power", "relic")
        and e.get("combat_scope") in ("combat", "either")
    ]
    # Gambling Chip is choiceType-rule-based, not lookup-based (see choice_semantics.py's
    # CHOICE_TYPE_RULES docstring) - it has no lookup.v1.json row at all. Sourced here from
    # the existing rule table's own valid_origin, not invented fresh.
    for choice_type, rule in CHOICE_TYPE_RULES.items():
        origin_type, origin_id = rule["valid_origin"]
        entries.append(
            {
                "origin_entity_type": origin_type,
                "origin_entity_id": origin_id,
                "normalized_choice_operation": None,
                "combat_scope": "combat",
                "_choice_type_rule": choice_type,
            }
        )
    return entries


def categorize_entity(entry: dict) -> str:
    entity_id = entry["origin_entity_id"]
    entity_type = entry["origin_entity_type"]
    op = entry["normalized_choice_operation"]
    if entry.get("_choice_type_rule") == "GamblingChipDiscard":
        return "gambling_chip"
    if entity_type == "potion":
        return "potion_origin"
    if entity_id == "HOLOGRAM":
        return "HOLOGRAM"
    if entity_id == "NIGHTMARE":
        return "NIGHTMARE"
    if op in ("discard", "exhaust", "upgrade"):
        return op
    if op in RETRIEVE_OPS:
        return "retrieve"
    return "action_continuation_other"


def scan_trajectories(priority_entities: list[dict]) -> tuple[dict[str, set[str]], set[str], dict[str, dict], set[str]]:
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
            for zone in ZONE_KEYS:
                for c in state.get(zone) or []:
                    if c.get("id") in entity_ids:
                        found_ids.add(c["id"])
            for p in state.get("playerPowers") or []:
                if p.get("id") in entity_ids:
                    found_ids.add(p["id"])
            for p in state.get("potions") or []:
                if p and p.get("id") in entity_ids:
                    found_ids.add(p["id"])
            for r in state.get("relics") or []:
                if r and r.get("id") in entity_ids:
                    found_ids.add(r["id"])
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
                    multi_select_scenarios.add(tid)

    return reachable, start_of_combat_choice, identity, multi_select_scenarios


def build_candidate_pool() -> list[dict]:
    priority_entities = load_priority_entities()
    entity_category: dict[str, str] = {}
    for e in priority_entities:
        entity_category[e["origin_entity_id"]] = categorize_entity(e)
    # Gambling Chip origin resolution does not depend on origin at all (choiceType rule
    # takes absolute priority, per choice_semantics.py) - every other bucket here IS
    # origin-dependent (Tier 2/3 of resolve()). Recorded per-entity, not per-scenario,
    # since a scenario can reach more than one entity.
    origin_dependent_entity = {eid: (cat != "gambling_chip") for eid, cat in entity_category.items()}

    reachable, start_of_combat_choice, identity, multi_select_scenarios = scan_trajectories(priority_entities)

    candidates: dict[str, dict] = {}
    for tid, entity_ids in reachable.items():
        categories = {entity_category[eid] for eid in entity_ids}
        if len(entity_ids) >= 2:
            categories.add("candidate_multi_choice_in_one_step")
        candidates[tid] = {
            "trajectory_id": tid,
            "categories": sorted(categories),
            "reachable_entities": sorted(entity_ids),
            "origin_dependent": any(origin_dependent_entity[eid] for eid in entity_ids),
            **identity[tid],
        }
    for tid in start_of_combat_choice:
        c = candidates.setdefault(
            tid,
            {
                "trajectory_id": tid,
                "categories": [],
                "reachable_entities": [],
                "origin_dependent": False,
                **identity[tid],
            },
        )
        cats = set(c["categories"])
        cats.add("start_of_combat_choice_card")
        if tid in multi_select_scenarios:
            cats.add("multi_select")
        cats.add("skip_confirm")
        c["categories"] = sorted(cats)

    return list(candidates.values())


def select(pool: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    remaining = list(pool)
    rng.shuffle(remaining)

    selected: list[dict] = []
    selected_ids: set[str] = set()
    for category in PRIORITY_ORDER:
        if len(selected) >= n:
            break
        pool_for_cat = [c for c in remaining if category in c["categories"] and c["trajectory_id"] not in selected_ids]
        # Take up to a few examples per category (not just one) so the final set has real
        # depth per bucket, not a single representative - divide n across len(PRIORITY_ORDER)
        # generously, then top up from the general pool afterwards.
        take = max(1, n // (len(PRIORITY_ORDER) * 2))
        for c in pool_for_cat[:take]:
            if len(selected) >= n:
                break
            selected.append(c)
            selected_ids.add(c["trajectory_id"])
    for c in remaining:
        if len(selected) >= n:
            break
        if c["trajectory_id"] in selected_ids:
            continue
        selected.append(c)
        selected_ids.add(c["trajectory_id"])
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
            continue
        out.append(
            {
                "trajectory_id": c["trajectory_id"],
                "source_run_id": c["source_run_id"],
                "source_combat_index": c["source_combat_index"],
                "categories": c["categories"],
                "reachable_entities": c["reachable_entities"],
                "origin_dependent": c["origin_dependent"],
                "spec": spec,
                "synthetic": False,
            }
        )
    return out


def load_synthetic_nested_choice() -> dict:
    with REVERIFICATION_MANIFEST_PATH.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("synthetic") and "nested_choice" in row.get("categories", []):
                return {
                    "trajectory_id": row["trajectory_id"],
                    "source_run_id": None,
                    "source_combat_index": None,
                    "categories": row["categories"] + ["candidate_multi_choice_in_one_step"],
                    "reachable_entities": ["DECISIONS_DECISIONS", "BURNING_PACT"],
                    "origin_dependent": True,
                    "spec": row["spec"],
                    "synthetic": True,
                }
    raise RuntimeError("synthetic nested-choice scenario not found in choice_semantics_reverification_manifest.jsonl")


def write_manifest(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=200, help="total scenarios, must be within [100, 300]")
    parser.add_argument("--smoke", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260725)
    args = parser.parse_args()
    if not (100 <= args.target <= 300):
        raise SystemExit(f"--target must be within [100, 300] per this task's cap, got {args.target}")

    pool = build_candidate_pool()
    print(f"Candidate pool: {len(pool)} teacher2000 scenarios with a reachable priority entity or a start-of-combat choice_card decision")
    cat_counts = Counter(cat for c in pool for cat in c["categories"])
    print("Category coverage in pool:", dict(cat_counts))
    print("origin_dependent candidates:", sum(1 for c in pool if c["origin_dependent"]))

    synthetic_row = load_synthetic_nested_choice()
    selected = select(pool, args.target - 1, args.seed)  # -1 slot reserved for the synthetic nested-choice row
    full = attach_specs(selected)
    full.append(synthetic_row)

    write_manifest(OUT_DIR / "choice_teacher_data_manifest.jsonl", full)
    write_manifest(OUT_DIR / "choice_teacher_data_smoke20_manifest.jsonl", full[: args.smoke])

    print(f"\nFull manifest ({len(full)} scenarios) -> choice_teacher_data_manifest.jsonl")
    print("  category coverage:", dict(Counter(cat for c in full for cat in c["categories"])))
    print(f"Smoke manifest (first {args.smoke}) -> choice_teacher_data_smoke20_manifest.jsonl")
    print("  category coverage:", dict(Counter(cat for c in full[: args.smoke] for cat in c["categories"])))


if __name__ == "__main__":
    main()
