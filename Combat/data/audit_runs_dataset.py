"""Phase 0 dataset audit for the existing human-run dataset.

Audits C:\\STS2_Data\\runs-all-before-2026-06.json (6796 run-level records, NOT
turn-by-turn combat logs - see module docstring caveat below) per the mid-term plan's
Phase 0 / section 6.1: version distribution, character/ascension/outcome distribution,
and card/relic/potion ID coverage against the v109 canonical ID dictionaries built by
Common/ids/build_id_dictionaries.py.

Each record is a full run's *final* snapshot (deck/relics/potions as of run end, plus
map_point_history and per-act outcome), not a sequence of in-combat decisions. This
script exists to answer, concretely: how many of these runs are usable, as-is, as
realistic combat *starting-state* material (real decks/relics/potions/encounters) for
STS2_RL/Combat/scenario_set.py - not to produce state->action training pairs (the
dataset cannot support that; see plan section 5.3).

Run: python audit_runs_dataset.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

RUNS_PATH = Path(r"C:\STS2_Data\runs-all-before-2026-06.json")
IDS_DIR = Path(r"C:\STS2_RL\Common\ids")
OUT_PATH = Path(r"C:\STS2_RL\Outputs\reports\dataset_audit_report.json")


def normalize(entry_id: str) -> str:
    return entry_id.replace("_", "").lower()


def load_canonical_normalized(filename: str) -> set[str]:
    with (IDS_DIR / filename).open(encoding="utf-8") as f:
        data = json.load(f)
    return {normalize(k) for k in data.keys()}


def strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def quantiles(values: list[int], qs: list[float]) -> dict[str, float]:
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    out = {}
    for q in qs:
        idx = min(n - 1, max(0, int(round(q * (n - 1)))))
        out[f"p{int(q * 100)}"] = s[idx]
    return out


def main() -> None:
    canonical_cards = load_canonical_normalized("cards.json")
    canonical_relics = load_canonical_normalized("relics.json")
    canonical_potions = load_canonical_normalized("potions.json")

    build_id_counts: Counter = Counter()
    schema_version_counts: Counter = Counter()
    ascension_counts: Counter = Counter()
    character_counts: Counter = Counter()
    game_mode_counts: Counter = Counter()
    platform_counts: Counter = Counter()
    win_counts: Counter = Counter()
    abandoned_counts: Counter = Counter()
    cheated_counts: Counter = Counter()
    acts_signature_counts: Counter = Counter()
    killed_by_encounter_counts: Counter = Counter()
    killed_by_event_counts: Counter = Counter()

    deck_sizes: list[int] = []
    relic_counts: list[int] = []
    potion_counts: list[int] = []
    run_times: list[int] = []

    unmatched_cards: Counter = Counter()
    unmatched_relics: Counter = Counter()
    unmatched_potions: Counter = Counter()
    total_card_refs = 0
    total_relic_refs = 0
    total_potion_refs = 0

    usable_for_scenarios = 0  # win or not-abandoned/not-cheated, single player, standard-ish
    total = 0
    parse_errors = 0

    with RUNS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            total += 1

            build_id_counts[rec.get("build_id")] += 1
            schema_version_counts[rec.get("schema_version")] += 1
            ascension_counts[rec.get("ascension")] += 1
            game_mode_counts[rec.get("game_mode")] += 1
            platform_counts[rec.get("platform_type")] += 1
            win_counts[bool(rec.get("win"))] += 1
            abandoned_counts[bool(rec.get("was_abandoned"))] += 1
            cheated_counts[bool(rec.get("_isCheated"))] += 1
            killed_by_encounter_counts[rec.get("killed_by_encounter")] += 1
            killed_by_event_counts[rec.get("killed_by_event")] += 1
            acts_signature_counts[",".join(rec.get("acts") or [])] += 1
            run_times.append(rec.get("run_time") or 0)

            players = rec.get("players") or []
            if players:
                p0 = players[0]
                character_counts[p0.get("character")] += 1
                deck = p0.get("deck") or []
                relics = p0.get("relics") or []
                potions = p0.get("potions") or []
                deck_sizes.append(len(deck))
                relic_counts.append(len(relics))
                potion_counts.append(len(potions))

                for c in deck:
                    total_card_refs += 1
                    entry = normalize(strip_prefix(c.get("id", ""), "CARD."))
                    if entry not in canonical_cards:
                        unmatched_cards[c.get("id")] += 1
                for r in relics:
                    total_relic_refs += 1
                    entry = normalize(strip_prefix(r.get("id", ""), "RELIC."))
                    if entry not in canonical_relics:
                        unmatched_relics[r.get("id")] += 1
                for pot in potions:
                    total_potion_refs += 1
                    entry = normalize(strip_prefix(pot.get("id", ""), "POTION."))
                    if entry not in canonical_potions:
                        unmatched_potions[pot.get("id")] += 1

            if (
                not rec.get("was_abandoned")
                and not rec.get("_isCheated")
                and rec.get("game_mode") == "standard"
                and players
            ):
                usable_for_scenarios += 1

    report = {
        "source": str(RUNS_PATH),
        "total_records": total,
        "parse_errors": parse_errors,
        "build_id_counts": dict(build_id_counts.most_common()),
        "schema_version_counts": {str(k): v for k, v in schema_version_counts.most_common()},
        "ascension_counts": {str(k): v for k, v in sorted(ascension_counts.items(), key=lambda x: (x[0] is None, x[0]))},
        "character_counts": dict(character_counts.most_common()),
        "game_mode_counts": dict(game_mode_counts.most_common()),
        "platform_counts": dict(platform_counts.most_common()),
        "win_counts": {str(k): v for k, v in win_counts.items()},
        "abandoned_counts": {str(k): v for k, v in abandoned_counts.items()},
        "cheated_counts": {str(k): v for k, v in cheated_counts.items()},
        "acts_signature_top20": dict(acts_signature_counts.most_common(20)),
        "acts_signature_distinct": len(acts_signature_counts),
        "killed_by_encounter_top20": dict(killed_by_encounter_counts.most_common(20)),
        "killed_by_event_top20": dict(killed_by_event_counts.most_common(20)),
        "deck_size": {"min": min(deck_sizes), "max": max(deck_sizes), **quantiles(deck_sizes, [0.1, 0.5, 0.9])},
        "relic_count": {"min": min(relic_counts), "max": max(relic_counts), **quantiles(relic_counts, [0.1, 0.5, 0.9])},
        "potion_count": {"min": min(potion_counts), "max": max(potion_counts), **quantiles(potion_counts, [0.1, 0.5, 0.9])},
        "run_time_seconds": {"min": min(run_times), "max": max(run_times), **quantiles(run_times, [0.1, 0.5, 0.9])},
        "id_coverage_against_v109_canonical": {
            "card_refs_total": total_card_refs,
            "card_refs_unmatched": sum(unmatched_cards.values()),
            "card_refs_unmatched_pct": round(100 * sum(unmatched_cards.values()) / max(1, total_card_refs), 3),
            "unmatched_card_ids_top20": dict(unmatched_cards.most_common(20)),
            "relic_refs_total": total_relic_refs,
            "relic_refs_unmatched": sum(unmatched_relics.values()),
            "relic_refs_unmatched_pct": round(100 * sum(unmatched_relics.values()) / max(1, total_relic_refs), 3),
            "unmatched_relic_ids_top20": dict(unmatched_relics.most_common(20)),
            "potion_refs_total": total_potion_refs,
            "potion_refs_unmatched": sum(unmatched_potions.values()),
            "potion_refs_unmatched_pct": round(100 * sum(unmatched_potions.values()) / max(1, total_potion_refs), 3),
            "unmatched_potion_ids_top20": dict(unmatched_potions.most_common(20)),
        },
        "usable_for_scenario_generation": {
            "count": usable_for_scenarios,
            "criteria": "not was_abandoned, not _isCheated, game_mode == 'standard', has players[0]",
            "pct_of_total": round(100 * usable_for_scenarios / max(1, total), 2),
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUT_PATH}")
    print(f"total={total} parse_errors={parse_errors}")
    print("build_id:", report["build_id_counts"])
    print("schema_version:", report["schema_version_counts"])
    print("character:", report["character_counts"])
    print("win:", report["win_counts"], "abandoned:", report["abandoned_counts"], "cheated:", report["cheated_counts"])
    print("usable_for_scenario_generation:", report["usable_for_scenario_generation"])
    print("card id coverage unmatched%:", report["id_coverage_against_v109_canonical"]["card_refs_unmatched_pct"])
    print("relic id coverage unmatched%:", report["id_coverage_against_v109_canonical"]["relic_refs_unmatched_pct"])
    print("potion id coverage unmatched%:", report["id_coverage_against_v109_canonical"]["potion_refs_unmatched_pct"])


if __name__ == "__main__":
    main()
