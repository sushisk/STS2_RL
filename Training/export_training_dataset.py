from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator


DATA_CONTRACT_VERSION = "v1"
EXPORT_SCRIPT_VERSION = "v3"
DICTIONARY_VERSION = "v1"
EXPORT_VERSION = "v1"
SPLIT_SEED = 20260722

SPLITS = ("train", "validation", "test")
DATASET_KINDS = ("complete", "partial")
USABLE_DATA_USAGE_KINDS = {"usable_complete": "complete", "usable_partial": "partial"}

# This exporter streams one decision at a time end-to-end (never materializes the full
# 50k+ decision dataset in memory) because the source machine is memory-constrained.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export learning-ready datasets from a saved trajectory batch run.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--split-seed", type=int, default=SPLIT_SEED)
    parser.add_argument(
        "--source-format",
        choices=["auto", "legacy", "flat"],
        default="auto",
        help=(
            "'legacy': run_dir/scenario_results.jsonl with nested result.decisions (500-scenario trial layout). "
            "'flat': run_dir/trajectories.jsonl + trajectory_quality.jsonl, one decision per row, outcome fields "
            "already denormalized onto every row (teacher2000 layout). 'auto' picks 'flat' if trajectories.jsonl "
            "exists, else 'legacy'."
        ),
    )
    parser.add_argument(
        "--export-name",
        type=str,
        default=None,
        help="Export root directory name under --out-dir. Defaults to '<run_dir name>_export_<export_version>'.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def current_rl_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def split_trajectories(trajectory_ids: list[str], seed: int) -> dict[str, str]:
    ids = sorted(trajectory_ids)
    rng = random.Random(seed)
    rng.shuffle(ids)
    total = len(ids)
    train_cut = int(total * 0.8)
    valid_cut = int(total * 0.9)
    split_map: dict[str, str] = {}
    for i, trajectory_id in enumerate(ids):
        if i < train_cut:
            split_map[trajectory_id] = "train"
        elif i < valid_cut:
            split_map[trajectory_id] = "validation"
        else:
            split_map[trajectory_id] = "test"
    return split_map


def normalize_action(action: dict, selected_enemy_index: int | None = None) -> dict:
    parameters = action.get("parameters") or {}
    return {
        "action_id": action.get("action_id"),
        "action_type": action.get("action_type"),
        "label": action.get("label"),
        "is_available": action.get("is_available"),
        "card_id": parameters.get("cardId"),
        "potion_id": parameters.get("potionId"),
        "target_type": parameters.get("targetType"),
        "target_enemy_index": selected_enemy_index if selected_enemy_index is not None else parameters.get("enemyIndex"),
        "raw_parameters": parameters,
        "action_id_scope": "state_local_ephemeral",
        "semantic_id_note": "label is display/debug text only; do not use as a stable semantic id",
    }


def normalized_candidates(decision: dict) -> list[dict]:
    candidates = []
    for candidate in decision.get("action_scores") or []:
        candidates.append(
            {
                "action_id": candidate.get("action_id"),
                "label": candidate.get("label"),
                "target_index": candidate.get("target_index"),
                "enemy_index": candidate.get("enemy_index"),
                "score": candidate.get("score"),
                "skipped_reason": candidate.get("skipped_reason"),
                "exception_type": candidate.get("exception_type"),
                "exception_message": candidate.get("exception_message"),
                "is_training_target_eligible": (
                    candidate.get("score") is not None
                    and not candidate.get("skipped_reason")
                    and not candidate.get("exception_type")
                ),
                "score_scope": "same_decision_comparison_only",
            }
        )
    return candidates


def selected_teacher_score(decision: dict) -> float | None:
    selected = decision.get("selected_action") or {}
    selected_id = selected.get("action_id")
    selected_enemy_index = decision.get("selected_enemy_index")
    for candidate in decision.get("action_scores") or []:
        if candidate.get("action_id") != selected_id:
            continue
        if candidate.get("enemy_index") != selected_enemy_index:
            continue
        return candidate.get("score")
    return None


def export_decision_row(
    decision: dict,
    split: str,
    dataset_kind: str,
    combat_outcome: Any,
    termination_reason: Any,
    data_usage: Any,
    truncation_classification: Any,
) -> dict:
    state = decision["state"]
    legal_actions = [normalize_action(a) for a in (decision.get("legal_actions") or [])]
    teacher_action = normalize_action(decision["selected_action"], decision.get("selected_enemy_index"))
    return {
        "export_version": EXPORT_VERSION,
        "dataset_kind": dataset_kind,
        "split": split,
        "trajectory_id": decision["trajectory_id"],
        "scenario_id": decision.get("scenario_id") or decision["trajectory_id"],
        "decision_id": f"{decision['trajectory_id']}:{decision['decision_index']}",
        "decision_index": decision["decision_index"],
        "source_run_id": decision["source_run_id"],
        "source_combat_index": decision["source_combat_index"],
        "observation": state,
        "legal_actions": legal_actions,
        "teacher_action": teacher_action,
        "teacher_action_type": (decision.get("selected_action") or {}).get("action_type"),
        "teacher_target": {
            "selected_enemy_index": decision.get("selected_enemy_index"),
            "selected_action_index": decision.get("selected_action_index"),
        },
        "teacher_score": selected_teacher_score(decision),
        "candidate_actions": normalized_candidates(decision),
        "combat_outcome": combat_outcome,
        "termination_reason": termination_reason,
        "data_usage_classification": data_usage,
        "truncation_classification": truncation_classification,
        "emulator_commit": decision.get("emulator_commit"),
        "emulator_dll_sha256": decision.get("emulator_dll_sha256"),
        "heuristic_version": decision.get("heuristic_version"),
        "raw_next_state": decision.get("next_state"),
    }


def resolve_source_format(run_dir: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    return "flat" if (run_dir / "trajectories.jsonl").exists() else "legacy"


def resolve_source_manifest_path(run_dir: Path, source_format: str) -> Path:
    if source_format == "legacy":
        return run_dir / "scenario_manifest.jsonl"
    return run_dir / "provenance_manifest.jsonl"


def usage_map_legacy(run_dir: Path) -> dict[str, str | None]:
    return {row["trajectory_id"]: (row.get("quality") or {}).get("data_usage") for row in iter_jsonl(run_dir / "scenario_results.jsonl")}


def usage_map_flat(run_dir: Path) -> dict[str, str | None]:
    return {row["trajectory_id"]: row.get("data_usage") for row in iter_jsonl(run_dir / "trajectory_quality.jsonl")}


def iter_legacy_decisions(run_dir: Path) -> Iterator[tuple[dict, dict]]:
    """Yield (decision, context) pairs. Loads scenario_results.jsonl fully (small: ~500 scenarios)."""
    for row in load_jsonl(run_dir / "scenario_results.jsonl"):
        quality = row.get("quality") or {}
        data_usage = quality.get("data_usage")
        if data_usage not in USABLE_DATA_USAGE_KINDS:
            continue
        result = row["result"]
        context = {
            "combat_outcome": result.get("final_outcome"),
            "termination_reason": result.get("termination_reason"),
            "data_usage": data_usage,
            "truncation_classification": quality.get("classification"),
        }
        for decision in result["decisions"]:
            yield decision, context


def iter_flat_decisions(run_dir: Path, usage_by_id: dict[str, str | None]) -> Iterator[tuple[dict, dict]]:
    """Yield (decision, context) pairs by streaming trajectories.jsonl one line at a time.

    Outcome/termination/data-usage fields are already denormalized onto every decision row in
    this source format, so no per-trajectory grouping or buffering is required.
    """
    for decision in iter_jsonl(run_dir / "trajectories.jsonl"):
        data_usage = usage_by_id.get(decision["trajectory_id"])
        if data_usage not in USABLE_DATA_USAGE_KINDS:
            continue
        context = {
            "combat_outcome": decision.get("final_outcome"),
            "termination_reason": decision.get("termination_reason"),
            "data_usage": data_usage,
            "truncation_classification": decision.get("truncation_classification"),
        }
        yield decision, context


def build_dictionary(name: str, values: list[str]) -> dict:
    entries = [{"id": 0, "token": "__UNKNOWN__"}]
    for idx, value in enumerate(values, start=1):
        entries.append({"id": idx, "token": value})
    return {
        "name": name,
        "version": DICTIONARY_VERSION,
        "generation_rule": "existing ids must remain fixed; append new tokens at the tail; changing an existing id requires a new major dictionary version",
        "compatibility_policy": {
            "unknown_token_id": 0,
            "append_only_minor": True,
            "rebuild_reordering_requires_new_major": True,
        },
        "entries": entries,
    }


def validation_rules() -> dict:
    return {
        "teacher_action_must_exist_in_legal_actions": True,
        "teacher_action_must_be_available": True,
        "selected_action_index_must_be_in_range": True,
        "decision_id_must_be_unique": True,
        "trajectory_must_belong_to_single_split": True,
        "empty_legal_actions_decision_is_not_training_eligible": True,
        "skipped_or_exception_candidates_are_not_training_targets": True,
    }


def new_id_buckets() -> dict[str, set[str]]:
    return {name: set() for name in ("action_type", "card", "potion", "power", "relic", "enemy", "encounter", "character")}


def update_id_buckets(buckets: dict[str, set[str]], row: dict) -> None:
    obs = row["observation"]
    source = obs.get("source") or {}
    if obs.get("characterId"):
        buckets["character"].add(obs["characterId"])
    for pile_name in ("hand", "drawPile", "discardPile", "exhaustPile", "playPile", "deck"):
        for card in obs.get(pile_name) or []:
            if card.get("id"):
                buckets["card"].add(card["id"])
    for power in obs.get("playerPowers") or []:
        if power.get("id"):
            buckets["power"].add(power["id"])
        associated = power.get("associatedCard")
        if associated and associated.get("id"):
            buckets["card"].add(associated["id"])
    for relic in obs.get("relics") or []:
        if relic.get("id"):
            buckets["relic"].add(relic["id"])
    for potion in obs.get("potions") or []:
        if potion and potion.get("id"):
            buckets["potion"].add(potion["id"])
    for enemy in obs.get("enemies") or []:
        if enemy.get("id"):
            buckets["enemy"].add(enemy["id"])
        for power in enemy.get("powers") or []:
            if power.get("id"):
                buckets["power"].add(power["id"])
    if source.get("encounter"):
        buckets["encounter"].add(source["encounter"])
    for action in row.get("legal_actions") or []:
        if action.get("action_type"):
            buckets["action_type"].add(action["action_type"])
        if action.get("card_id"):
            buckets["card"].add(action["card_id"])
        if action.get("potion_id"):
            buckets["potion"].add(action["potion_id"])
    teacher_action = row.get("teacher_action") or {}
    if teacher_action.get("action_type"):
        buckets["action_type"].add(teacher_action["action_type"])
    if teacher_action.get("card_id"):
        buckets["card"].add(teacher_action["card_id"])
    if teacher_action.get("potion_id"):
        buckets["potion"].add(teacher_action["potion_id"])


def build_all_dictionaries_from_buckets(buckets: dict[str, set[str]]) -> dict:
    return {f"{name}_dict": build_dictionary(name, sorted(values)) for name, values in buckets.items()}


class RowAccumulator:
    """Incrementally computes the same summary shape the old full-list summarizer produced,
    without ever holding more than one row (plus small Counters) in memory at a time."""

    def __init__(self) -> None:
        self.action_type_counts: Counter = Counter()
        self.card_label_counts: Counter = Counter()
        self.outcome_counts: Counter = Counter()
        self.encounter_counts: Counter = Counter()
        self.missing_field_counts: Counter = Counter()
        self.choice_count = 0
        self.potion_action_count = 0
        self.end_turn_count = 0
        self.by_trajectory: dict[str, int] = defaultdict(int)
        self.total_rows = 0

    def add(self, row: dict) -> None:
        self.total_rows += 1
        self.by_trajectory[row["trajectory_id"]] += 1
        action_type = row["teacher_action_type"] or "unknown"
        self.action_type_counts[action_type] += 1
        if row["teacher_action"].get("label"):
            self.card_label_counts[row["teacher_action"]["label"]] += 1
        self.outcome_counts[row["combat_outcome"] or "unknown"] += 1
        encounter = ((row["observation"].get("source") or {}).get("encounter")) or "unknown"
        self.encounter_counts[encounter] += 1
        if action_type == "choice_card":
            self.choice_count += 1
        if action_type == "potion":
            self.potion_action_count += 1
        if row["teacher_action"].get("label") == "End Turn":
            self.end_turn_count += 1
        for field in ("teacher_score", "combat_outcome", "termination_reason", "emulator_commit", "emulator_dll_sha256", "heuristic_version"):
            if row.get(field) in (None, ""):
                self.missing_field_counts[field] += 1

    def finalize(self) -> dict:
        decision_counts = list(self.by_trajectory.values())
        return {
            "trajectory_count": len(self.by_trajectory),
            "decision_count": self.total_rows,
            "action_type_counts": dict(self.action_type_counts),
            "top_teacher_labels": dict(self.card_label_counts.most_common(50)),
            "outcome_counts": dict(self.outcome_counts),
            "encounter_counts_top50": dict(self.encounter_counts.most_common(50)),
            "decision_count_distribution": {
                "min": min(decision_counts) if decision_counts else 0,
                "max": max(decision_counts) if decision_counts else 0,
                "avg": round(sum(decision_counts) / len(decision_counts), 2) if decision_counts else 0.0,
            },
            "missing_field_counts": dict(self.missing_field_counts),
            "choice_count": self.choice_count,
            "potion_action_count": self.potion_action_count,
            "end_turn_count": self.end_turn_count,
            "end_turn_rate_pct": round(100.0 * self.end_turn_count / self.total_rows, 2) if self.total_rows else 0.0,
        }


def write_manifests_from_split_map(split_map: dict[str, str], export_root: Path, dataset_kind: str) -> dict[str, list[str]]:
    split_to_ids: dict[str, list[str]] = defaultdict(list)
    for trajectory_id, split in split_map.items():
        split_to_ids[split].append(trajectory_id)
    manifest_dir = export_root / "manifests"
    for split in SPLITS:
        ids = sorted(set(split_to_ids.get(split, [])))
        payload = [{"trajectory_id": tid, "scenario_id": tid, "dataset_kind": dataset_kind, "split": split} for tid in ids]
        write_jsonl(manifest_dir / f"{dataset_kind}_{split}_manifest.jsonl", payload)
    return {split: sorted(set(split_to_ids.get(split, []))) for split in SPLITS}


def residual_issue_report_legacy(run_dir: Path) -> dict:
    rows = load_jsonl(run_dir / "scenario_results.jsonl")
    target_ids = {"6304-18", "787-23", "2365-21", "4419-24", "5362-18", "7678-9", "6588-3"}
    report = []
    for row in rows:
        if row["trajectory_id"] not in target_ids:
            continue
        result = row["result"]
        quality = row["quality"]
        reasons = result.get("reasons") or []
        warnings = result.get("warnings") or []
        classification = "Heuristic品質上の制約"
        rationale = "manual_review_required"
        if quality.get("data_usage") == "exclude_heuristic_exception" and any("no living enemies" in w for w in warnings):
            classification = "RL側修正"
            rationale = "candidate evaluation reaches terminal kill state but treats no-living-enemies restore as exception"
        elif any("step_exception:TimeoutException" in w for w in warnings):
            classification = "Emulator側調査依頼"
            rationale = "candidate evaluation Step timeout remained after adapter fixes"
        elif "init_exception:ArgumentException" in reasons:
            classification = "データ不足による隔離"
            rationale = ((result.get("diffs") or {}).get("error_message")) or "init argument exception"
        elif quality.get("classification") == "C_state_or_implementation_loop":
            classification = "RL側修正"
            rationale = "loop around potion-driven branch should be broken earlier or resolved by better continuation handling"
        report.append(
            {
                "trajectory_id": row["trajectory_id"],
                "classification": classification,
                "termination_reason": quality.get("termination_reason"),
                "data_usage": quality.get("data_usage"),
                "rationale": rationale,
            }
        )
    return {
        "run_dir": str(run_dir),
        "classified_cases": sorted(report, key=lambda x: x["trajectory_id"]),
    }


def known_issues_flat(run_dir: Path) -> dict:
    error_summary_path = run_dir / "error_summary.json"
    quarantine_path = run_dir / "quarantine_report.jsonl"
    return {
        "run_dir": str(run_dir),
        "source": "verbatim copy of RL-provided error_summary.json / quarantine_report.jsonl; not re-derived by Training side",
        "error_summary": load_json(error_summary_path) if error_summary_path.exists() else None,
        "quarantined_trajectory_count": len(load_jsonl(quarantine_path)) if quarantine_path.exists() else None,
    }


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    source_format = resolve_source_format(run_dir, args.source_format)
    source_manifest_path = resolve_source_manifest_path(run_dir, source_format)

    usage_by_id = usage_map_flat(run_dir) if source_format == "flat" else usage_map_legacy(run_dir)
    complete_ids = [tid for tid, usage in usage_by_id.items() if usage == "usable_complete"]
    partial_ids = [tid for tid, usage in usage_by_id.items() if usage == "usable_partial"]
    split_map_by_kind = {
        "complete": split_trajectories(complete_ids, args.split_seed),
        "partial": split_trajectories(partial_ids, args.split_seed + 1),
    }

    export_dir_name = args.export_name or f"{run_dir.name}_export_{EXPORT_VERSION}"
    export_root = out_dir / export_dir_name
    export_root.mkdir(parents=True, exist_ok=True)

    handles: dict[str, Any] = {}
    for kind in DATASET_KINDS:
        handles[f"{kind}_all"] = (export_root / f"{kind}_all.jsonl").open("w", encoding="utf-8")
        for split in SPLITS:
            handles[f"{kind}_{split}"] = (export_root / f"{kind}_{split}.jsonl").open("w", encoding="utf-8")

    overall_acc = {kind: RowAccumulator() for kind in DATASET_KINDS}
    split_acc = {(kind, split): RowAccumulator() for kind in DATASET_KINDS for split in SPLITS}
    id_buckets = new_id_buckets()
    decision_counts = {"complete": 0, "partial": 0}
    heuristic_version = None

    decision_iter = iter_flat_decisions(run_dir, usage_by_id) if source_format == "flat" else iter_legacy_decisions(run_dir)
    for decision, context in decision_iter:
        trajectory_id = decision["trajectory_id"]
        data_usage = context["data_usage"]
        kind = USABLE_DATA_USAGE_KINDS[data_usage]
        split = split_map_by_kind[kind].get(trajectory_id)
        if split is None:
            continue
        row = export_decision_row(
            decision,
            split=split,
            dataset_kind=kind,
            combat_outcome=context["combat_outcome"],
            termination_reason=context["termination_reason"],
            data_usage=data_usage,
            truncation_classification=context["truncation_classification"],
        )
        if heuristic_version is None:
            heuristic_version = row.get("heuristic_version")
        line = json.dumps(row, ensure_ascii=False, default=str)
        handles[f"{kind}_all"].write(line + "\n")
        handles[f"{kind}_{split}"].write(line + "\n")
        overall_acc[kind].add(row)
        split_acc[(kind, split)].add(row)
        update_id_buckets(id_buckets, row)
        decision_counts[kind] += 1

    for handle in handles.values():
        handle.close()

    complete_manifests = write_manifests_from_split_map(split_map_by_kind["complete"], export_root, "complete")
    partial_manifests = write_manifests_from_split_map(split_map_by_kind["partial"], export_root, "partial")

    dictionaries = build_all_dictionaries_from_buckets(id_buckets)
    dump_json(export_root / "id_dictionaries.v1.json", dictionaries)
    dump_json(
        export_root / "export_metadata.json",
        {
            "data_contract_version": DATA_CONTRACT_VERSION,
            "export_version": EXPORT_VERSION,
            "export_script_version": EXPORT_SCRIPT_VERSION,
            "dictionary_version": DICTIONARY_VERSION,
            "source_format": source_format,
            "source_run_dir": str(run_dir),
            "source_manifest_sha256": sha256_file(source_manifest_path) if source_manifest_path.exists() else None,
            "rl_git_commit": current_rl_git_commit(),
            "split_seed": {
                "complete": args.split_seed,
                "partial": args.split_seed + 1,
            },
            "complete_trajectory_count": len(complete_ids),
            "partial_trajectory_count": len(partial_ids),
            "complete_decision_count": decision_counts["complete"],
            "partial_decision_count": decision_counts["partial"],
            "complete_split_counts": {split: len(ids) for split, ids in complete_manifests.items()},
            "partial_split_counts": {split: len(ids) for split, ids in partial_manifests.items()},
            "heuristic_version": heuristic_version,
            "validation_rules": validation_rules(),
        },
    )
    quality_report = {
        "complete": overall_acc["complete"].finalize(),
        "partial": overall_acc["partial"].finalize(),
        "complete_by_split": {split: split_acc[("complete", split)].finalize() for split in SPLITS},
        "partial_by_split": {split: split_acc[("partial", split)].finalize() for split in SPLITS},
        "known_issues": (
            residual_issue_report_legacy(run_dir) if source_format == "legacy" else known_issues_flat(run_dir)
        ),
    }
    dump_json(export_root / "quality_report.json", quality_report)

    lines = [
        "# Training Export Report",
        "",
        f"- export_root: `{export_root}`",
        f"- source_format: `{source_format}`",
        f"- complete trajectories: `{len(complete_ids)}`",
        f"- partial trajectories: `{len(partial_ids)}`",
        f"- complete decisions: `{decision_counts['complete']}`",
        f"- partial decisions: `{decision_counts['partial']}`",
        "",
        "## Complete Split Counts",
    ]
    for split, ids in sorted(complete_manifests.items()):
        lines.append(f"- `{split}`: `{len(ids)}` trajectories")
    lines.extend(["", "## Partial Split Counts"])
    for split, ids in sorted(partial_manifests.items()):
        lines.append(f"- `{split}`: `{len(ids)}` trajectories")
    (export_root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "export_root": str(export_root),
                "source_format": source_format,
                "complete_trajectories": len(complete_ids),
                "partial_trajectories": len(partial_ids),
                "complete_decisions": decision_counts["complete"],
                "partial_decisions": decision_counts["partial"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
