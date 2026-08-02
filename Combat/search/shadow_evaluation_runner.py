"""Batch shadow evaluation runner for old-vs-new combat search.

This script intentionally evaluates the first stable decision of many independently
started real combats. Each old/new decision is then stepped once in a disposable
restored session to record the immediate real outcome context. It never commits either
decision into the designated Main reference session; the runner verifies Main's
observable state is byte-identical after the full batch.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from combat_state_snapshot import CombatStateSnapshot, canonical_json  # noqa: E402
from emulator_bridge import to_plain  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import BranchWorkerPool  # noqa: E402
from search.search_coordinator import SearchCoordinatorConfig  # noqa: E402
from search.shadow_adapter import ShadowComparisonResult, compare_paths  # noqa: E402


def _spec(
    *,
    hand: list[str],
    draw_pile: list[str] | None = None,
    discard_pile: list[str] | None = None,
    relics: list[str] | None = None,
    potions: list[dict] | None = None,
    enemies: list[dict] | None = None,
    player_hp: int | None = None,
    player_max_hp: int | None = None,
    seed: int = 1,
) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": player_hp,
        "player_max_hp": player_max_hp,
        "hand": hand,
        "draw_pile": draw_pile if draw_pile is not None else [],
        "discard_pile": discard_pile if discard_pile is not None else [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": relics if relics is not None else [],
        "potions": potions if potions is not None else [],
        "seed": seed,
        "enemies": enemies if enemies is not None else [{"monster_id": "CALCIFIED_CULTIST", "hp": 12}],
    }


def _scenario_specs(count: int) -> list[dict]:
    templates = [
        {"hand": ["STRIKE_IRONCLAD"], "draw_pile": ["BASH", "DEFEND_IRONCLAD"], "enemy_hp": 24},
        {"hand": ["BASH"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 28},
        {"hand": ["WHIRLWIND"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 30},
        {"hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], "draw_pile": ["BASH"], "enemy_hp": 14},
        {"hand": ["BASH", "DEFEND_IRONCLAD"], "draw_pile": ["STRIKE_IRONCLAD"], "enemy_hp": 18},
        {
            "hand": ["WHIRLWIND", "DEFEND_IRONCLAD"],
            "draw_pile": ["STRIKE_IRONCLAD", "BASH"],
            "enemy_hp": 28,
            "relics": ["ANCHOR"],
        },
        {
            "hand": ["STRIKE_IRONCLAD"],
            "draw_pile": ["BASH"],
            "enemy_hp": 26,
            "relics": ["BIIIG_HUG"],
        },
        {
            "hand": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
            "draw_pile": ["STRIKE_IRONCLAD", "BASH"],
            "enemy_hp": 22,
            "potions": [{"slot": 0, "potion_id": "FIRE_POTION"}],
            "relics": ["POTION_BELT"],
        },
        {
            "hand": ["WHIRLWIND"],
            "draw_pile": ["DEFEND_IRONCLAD"],
            "enemy_hp": 40,
        },
        {
            "hand": ["STRIKE_IRONCLAD", "BASH"],
            "draw_pile": ["DEFEND_IRONCLAD"],
            "discard_pile": ["STRIKE_IRONCLAD"],
            "enemy_hp": 35,
        },
    ]
    specs: list[dict] = []
    for index in range(count):
        template = templates[index % len(templates)]
        enemies = template.get("enemies")
        if enemies is None:
            enemies = [{"monster_id": "CALCIFIED_CULTIST", "hp": int(template["enemy_hp"]) + (index % 3)}]
        specs.append(
            _spec(
                hand=list(template["hand"]),
                draw_pile=list(template.get("draw_pile") or []),
                discard_pile=list(template.get("discard_pile") or []),
                relics=list(template.get("relics") or []),
                potions=[dict(p) for p in (template.get("potions") or [])],
                enemies=[dict(e) for e in enemies],
                player_hp=55 - (index % 9),
                player_max_hp=80,
                seed=1000 + index,
            )
        )
    return specs


def _deck_multiset(spec: dict) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for pile_name in ("hand", "draw_pile", "discard_pile", "exhaust_pile"):
        counts.update(spec.get(pile_name) or [])
    return dict(counts)


def _capture_snapshots(specs: list[dict]) -> list[CombatStateSnapshot]:
    snapshots = []
    for spec in specs:
        session = LiveCombatSession()
        session.start_combat(spec)
        snapshots.append(session.capture_snapshot())
    return snapshots


def _gameplay_projection(state: dict) -> dict:
    return {
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "stars": state.get("stars"),
        "hand": state.get("hand"),
        "drawPile": state.get("drawPile"),
        "discardPile": state.get("discardPile"),
        "exhaustPile": state.get("exhaustPile"),
        "playPile": state.get("playPile"),
        "playerPowers": state.get("playerPowers"),
        "relics": state.get("relics"),
        "potions": state.get("potions"),
        "enemies": [
            {
                "id": enemy.get("id"),
                "hp": enemy.get("hp"),
                "maxHp": enemy.get("maxHp"),
                "block": enemy.get("block"),
                "isAlive": enemy.get("isAlive"),
                "powers": enemy.get("powers"),
            }
            for enemy in (state.get("enemies") or [])
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def _pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * (len(ordered) - 1)))))
    return float(ordered[index])


def _dist(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p95": None, "min": None, "max": None}
    return {
        "mean": float(statistics.fmean(values)),
        "median": float(statistics.median(values)),
        "p95": _pct(values, 95),
        "min": float(min(values)),
        "max": float(max(values)),
    }


def _outcome_dict(result) -> dict[str, Any] | None:
    return None if result.outcome is None else dataclasses.asdict(result.outcome)


def _row(index: int, spec: dict, result: ShadowComparisonResult) -> dict[str, Any]:
    return {
        "index": index,
        "spec": {
            "hand": spec.get("hand"),
            "draw_pile": spec.get("draw_pile"),
            "discard_pile": spec.get("discard_pile"),
            "relics": spec.get("relics"),
            "potions": spec.get("potions"),
            "enemies": spec.get("enemies"),
            "player_hp": spec.get("player_hp"),
            "seed": spec.get("seed"),
        },
        "actions_agree": result.actions_agree,
        "old_action": None if result.old.action is None else dataclasses.asdict(result.old.action),
        "new_action": None if result.new.action is None else dataclasses.asdict(result.new.action),
        "old_score": result.old.score,
        "new_score": result.new.score,
        "old_elapsed_ms": result.old.elapsed_ms,
        "new_elapsed_ms": result.new.elapsed_ms,
        "old_outcome": _outcome_dict(result.old),
        "new_outcome": _outcome_dict(result.new),
        "old_metrics": dataclasses.asdict(result.old.metrics),
        "new_metrics": dataclasses.asdict(result.new.metrics),
        "new_status": result.new.status,
        "new_detail": result.new.detail,
    }


def _aggregate(rows: list[dict[str, Any]], *, elapsed_s: float, main_unchanged: bool) -> dict[str, Any]:
    n = len(rows)
    old_metrics = [row["old_metrics"] for row in rows]
    new_metrics = [row["new_metrics"] for row in rows]
    old_outcomes = [row["old_outcome"] for row in rows if row["old_outcome"] is not None]
    new_outcomes = [row["new_outcome"] for row in rows if row["new_outcome"] is not None]
    return {
        "combat_count": n,
        "elapsed_s": elapsed_s,
        "agreement_rate": sum(1 for row in rows if row["actions_agree"]) / float(n or 1),
        "new_status_counts": dict(Counter(row["new_status"] for row in rows)),
        "old_score": _dist([float(row["old_score"]) for row in rows if row["old_score"] is not None]),
        "new_score": _dist([float(row["new_score"]) for row in rows if row["new_score"] is not None]),
        "old_elapsed_ms": _dist([float(row["old_elapsed_ms"]) for row in rows]),
        "new_elapsed_ms": _dist([float(row["new_elapsed_ms"]) for row in rows]),
        "old_remaining_hp": _dist([float(item["remaining_player_hp"]) for item in old_outcomes]),
        "new_remaining_hp": _dist([float(item["remaining_player_hp"]) for item in new_outcomes]),
        "old_potion_slots_consumed": _dist([float(item["potion_slots_consumed"]) for item in old_outcomes]),
        "new_potion_slots_consumed": _dist([float(item["potion_slots_consumed"]) for item in new_outcomes]),
        "old_outcome_counts": dict(Counter(item["outcome"] for item in old_outcomes)),
        "new_outcome_counts": dict(Counter(item["outcome"] for item in new_outcomes)),
        "old_step_count": _dist([float(item["step_count"]) for item in old_metrics]),
        "new_step_count": _dist([float(item["step_count"]) for item in new_metrics]),
        "old_restore_count": _dist([float(item["restore_count"]) for item in old_metrics]),
        "new_restore_count": _dist([float(item["restore_count"]) for item in new_metrics]),
        "old_replay_count": _dist([float(item["replay_count"]) for item in old_metrics]),
        "new_replay_count": _dist([float(item["replay_count"]) for item in new_metrics]),
        "old_fault_count": _dist([float(item["fault_count"]) for item in old_metrics]),
        "new_fault_count": _dist([float(item["fault_count"]) for item in new_metrics]),
        "old_retry_count": _dist([float(item["retry_count"]) for item in old_metrics]),
        "new_retry_count": _dist([float(item["retry_count"]) for item in new_metrics]),
        "new_worker_utilization_fraction": _dist([float(item["worker_utilization_fraction"]) for item in new_metrics]),
        "new_hypothesis_count": _dist([float(item["hypothesis_count"]) for item in new_metrics]),
        "new_search_round_count": _dist([float(item["search_round_count"]) for item in new_metrics]),
        "new_plan_path_length": _dist([float(item["plan_path_length"]) for item in new_metrics]),
        "main_session_unchanged": main_unchanged,
    }


def run_batch(
    combat_count: int,
    *,
    worker_count: int = 3,
    width: int = 1,
    hypothesis_count: int = 1,
    max_retries: int = 0,
) -> dict[str, Any]:
    specs = _scenario_specs(combat_count)
    snapshots = _capture_snapshots(specs)
    main_session = LiveCombatSession()
    main_session.start_combat(_spec(hand=["WHIRLWIND"], enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 1}], seed=424242))
    main_snapshot = main_session.capture_snapshot()
    before_legal_json = json.dumps(main_session.get_legal_actions(), sort_keys=True, separators=(",", ":"), default=str)
    before_state_json = json.dumps(
        _gameplay_projection(to_plain(main_session.get_observation().State)),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    config = SearchCoordinatorConfig(
        width=width,
        hypothesis_count=hypothesis_count,
        max_retries=max_retries,
        request_timeout_s=120.0,
    )
    with BranchWorkerPool(worker_count=worker_count, request_timeout_s=120.0) as pool:
        for index, (spec, snapshot) in enumerate(zip(specs, snapshots), start=1):
            result = compare_paths(
                snapshot,
                pool=pool,
                config=config,
                combat_start_deck_multiset=_deck_multiset(spec),
                old_timeout_s=120.0,
            )
            rows.append(_row(index, spec, result))
            print(
                "SHADOW "
                f"{index}/{combat_count} agree={result.actions_agree} "
                f"old={result.old.action.comparison_key if result.old.action else None} "
                f"new={result.new.action.comparison_key if result.new.action else None} "
                f"old_ms={result.old.elapsed_ms:.1f} new_ms={result.new.elapsed_ms:.1f}",
                flush=True,
            )
    elapsed_s = time.perf_counter() - started

    LiveCombatSession().restore_snapshot(main_snapshot)
    after_legal_json = json.dumps(main_session.get_legal_actions(), sort_keys=True, separators=(",", ":"), default=str)
    after_state_json = json.dumps(
        _gameplay_projection(to_plain(main_session.get_observation().State)),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    main_unchanged = before_legal_json == after_legal_json and before_state_json == after_state_json
    return {
        "metric_definitions": {
            "old_restore_count": "1 initial snapshot Restore + each HeuristicAgent evaluated candidate apply_action Restore + 1 chosen-action outcome Restore.",
            "old_step_count": "Each successfully evaluated HeuristicAgent candidate Step + 1 chosen-action outcome Step.",
            "new_restore_count": "1 shadow context Restore + each BranchResult bootstrap_step + 1 final restoration of the preserved current-process snapshot.",
            "new_step_count": "Each SearchCoordinator branch worker Step attempt + 1 chosen-action outcome Step when a decision is produced.",
            "new_replay_count": "Count of bootstrap_step BranchResults, used as the restore+replay proxy for branch workers.",
            "worker_utilization_fraction": "distinct worker_ids used divided by configured worker_count for that decision.",
            "search_round_count": "SearchCoordinator dispatch retry-loop rounds, not multi-round beam depth. This runner intentionally uses the single-round build_search_strategy path.",
            "plan_path_length": "Length of SearchSuccess.planned_sequence for the selected new-path decision.",
            "outcome": "Immediate real outcome after stepping the chosen root action once in a disposable restored session.",
        },
        "summary": _aggregate(rows, elapsed_s=elapsed_s, main_unchanged=main_unchanged),
        "rows": rows,
        "config": {
            "strategy": "single_round_build_search_strategy",
            "width": width,
            "hypothesis_count": hypothesis_count,
            "max_retries": max_retries,
            "worker_count": worker_count,
        },
        "main_before_stable_json": canonical_json(dataclasses.asdict(main_snapshot), exclude_volatile=True),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--combats", type=int, default=100)
    parser.add_argument("--worker-count", type=int, default=3)
    parser.add_argument("--width", type=int, default=1)
    parser.add_argument("--hypothesis-count", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    report = run_batch(
        args.combats,
        worker_count=args.worker_count,
        width=args.width,
        hypothesis_count=args.hypothesis_count,
        max_retries=args.max_retries,
    )
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.json_out is not None:
        args.json_out.write_text(text, encoding="utf-8")
    print("SUMMARY_JSON_START")
    print(json.dumps(report["summary"], indent=2, sort_keys=True, default=str))
    print("SUMMARY_JSON_END")
    if not report["summary"]["main_session_unchanged"]:
        return 2
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
