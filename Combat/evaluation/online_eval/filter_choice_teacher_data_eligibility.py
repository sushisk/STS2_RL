"""Applies this task's Choice teacher-data 学習適格性 (section 5) gates to a
choice_teacher_data.jsonl produced by generate_choice_teacher_data.py. Read-only -
writes a NEW eligible/excluded split next to the input, does not modify the input file,
choice_semantics.py, or the lookup.

Eligibility (指示書5節):
    1. operationMode in {normalized, passthrough}
    2. teacher action present in that decision's legal_actions (teacher_action_in_legal)
    3. candidate card identifiable (candidate_identifiable)
    4. no semantic mismatch (row's resolved facts vs its own lookup row's evidence,
       same check as analyze_reverification_722b019.compute_semantic_mismatch, applied
       per-row here)
    5. deterministically reproducible - this is a RUN-LEVEL property (verified once via
       a repeated-run diff, not per-row - see this task's report for the actual
       determinism check), passed in via --deterministic-run/--non-deterministic-run

Reset-time Gambling Chip null-origin rows are explicitly NOT excluded by rule 1/2/3/4
above (choiceType alone determines passthrough+exceptionEntityKey=relic:GAMBLING_CHIP,
independent of origin - see choice_semantics_baseline_722b019_v1_20260725.json's
gambling_chip_reset_null_eligibility note) - nothing extra is needed here, the existing
gates already pass those rows through correctly since operationMode is already
"passthrough" for them regardless of origin.

Run: python filter_choice_teacher_data_eligibility.py --in <choice_teacher_data.jsonl> --out-dir <dir> [--deterministic]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_COMBAT_DIR))
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from analyze_reverification_722b019 import compute_semantic_mismatch  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def eligibility_of(row: dict, table: ChoiceSemanticsTable, deterministic: bool) -> dict:
    resolved = row["resolved"]
    operation_mode_ok = resolved["operationMode"] in ("normalized", "passthrough")
    teacher_in_legal_ok = bool(row.get("teacher_action_in_legal"))
    candidate_ok = bool(row.get("candidate_identifiable"))
    semantic_mismatch = compute_semantic_mismatch([row], table) > 0
    semantic_ok = not semantic_mismatch

    reasons = []
    if not operation_mode_ok:
        reasons.append(f"operation_mode_{resolved['operationMode']}")
    if not teacher_in_legal_ok:
        reasons.append("teacher_action_not_in_legal")
    if not candidate_ok:
        reasons.append("candidate_not_identifiable")
    if semantic_mismatch:
        reasons.append("semantic_mismatch")
    if not deterministic:
        reasons.append("determinism_not_confirmed_for_this_run")

    eligible = operation_mode_ok and teacher_in_legal_ok and candidate_ok and semantic_ok and deterministic
    return {"eligible": eligible, "reasons": reasons, "semantic_mismatch": semantic_mismatch}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--deterministic", action="store_true", help="set once the run-level determinism check has passed")
    args = parser.parse_args()

    table = ChoiceSemanticsTable()
    rows = load_jsonl(args.in_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    eligible_rows, excluded_rows = [], []
    reason_counts = Counter()
    operation_mode_counts = Counter()
    source_counts = Counter()
    matched_rule_counts = Counter()
    exception_entity_counts = Counter()
    origin_dependent_lookup_statuses = {
        "resolved_lookup", "resolved_emulator_fact_confirmed_by_lookup", "resolved_emulator_fact",
        "resolved_emulator_fact_ambiguous_lookup", "resolved_lookup_unknown",
    }
    origin_dependent_count = 0
    reset_gambling_chip_null_count = 0

    for row in rows:
        e = eligibility_of(row, table, args.deterministic)
        row_out = {**row, "eligibility": e}
        operation_mode_counts[row["resolved"]["operationMode"]] += 1
        source_counts[row["source"]] += 1
        if row["resolved"].get("matchedRuleId"):
            matched_rule_counts[row["resolved"]["matchedRuleId"]] += 1
        if row["resolved"].get("exceptionEntityKey"):
            exception_entity_counts[row["resolved"]["exceptionEntityKey"]] += 1
        if row["resolved"]["lookupStatus"] in origin_dependent_lookup_statuses:
            origin_dependent_count += 1
        if (
            row["emulator_fact"].get("choiceType") == "GamblingChipDiscard"
            and row["emulator_fact"].get("originEntityType") is None
            and row["emulator_fact"].get("originEntityId") is None
        ):
            reset_gambling_chip_null_count += 1

        if e["eligible"]:
            eligible_rows.append(row_out)
        else:
            excluded_rows.append(row_out)
            for r in e["reasons"]:
                reason_counts[r] += 1

    with (args.out_dir / "choice_teacher_data_eligible.jsonl").open("w", encoding="utf-8") as f:
        for r in eligible_rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with (args.out_dir / "choice_teacher_data_excluded.jsonl").open("w", encoding="utf-8") as f:
        for r in excluded_rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    summary = {
        "input": str(args.in_path),
        "total": len(rows),
        "eligible_count": len(eligible_rows),
        "excluded_count": len(excluded_rows),
        "exclusion_reason_counts": dict(reason_counts),
        "operation_mode_counts": dict(operation_mode_counts),
        "source_counts": dict(source_counts),
        "matched_rule_counts": dict(matched_rule_counts),
        "exception_entity_counts": dict(exception_entity_counts),
        "origin_dependent_count": origin_dependent_count,
        "reset_time_gambling_chip_null_count": reset_gambling_chip_null_count,
        "deterministic_flag_used": args.deterministic,
    }
    (args.out_dir / "eligibility_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
