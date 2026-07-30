"""Compares two online_policy_eval.py runs of the SAME
choice_semantics_reverification manifest - one against the pre-fix Emulator build
("before"), one against the Emulator team's fixed build ("after") - per this task's
Emulator調査完了までの待機・再検証準備 instructions sections 3-4.

NOT run automatically as part of this task - prepared ahead of time, to be invoked once
an "after" run exists:

    python compare_choice_semantics_reverification.py --before <before_dir> --after <after_dir>

Does not modify choice_semantics.py, the lookup, the alias table, or any Scenario/
manifest. Read-only comparison over two already-produced output directories.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_run(out_dir: Path) -> dict[str, Any]:
    return {
        "summary": json.loads((out_dir / "summary.json").read_text(encoding="utf-8")),
        "combats": load_jsonl(out_dir / "combats.jsonl"),
        "choice_log": load_jsonl(out_dir / "choice_log.jsonl"),
    }


def index_by_scenario(rows: list[dict], key: str = "trajectory_id") -> dict[str, dict]:
    return {r[key]: r for r in rows}


def choice_sequences(choice_log: list[dict]) -> dict[tuple[str, str], list[dict]]:
    seq: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in choice_log:
        seq[(row["trajectory_id"], row["arm"])].append(row)
    return seq


def compare(before_dir: Path, after_dir: Path) -> dict[str, Any]:
    before = load_run(before_dir)
    after = load_run(after_dir)

    findings: dict[str, Any] = {"before_dir": str(before_dir), "after_dir": str(after_dir)}

    # --- gate metrics (should already be 0/0/0 in both, but re-verify explicitly) ---
    for label, run in (("before", before), ("after", after)):
        s = run["summary"]
        findings[f"{label}_gates"] = {
            "illegal_action": s["policy_arm"]["illegal_action_count"] + s["heuristic_arm"]["illegal_action_count"],
            "exception": s["policy_arm"]["exception_count"] + s["heuristic_arm"]["exception_count"],
            "mapping_mismatch": s["policy_arm"]["action_mapping_mismatch_count"] + s["heuristic_arm"]["action_mapping_mismatch_count"],
            "emulator_commit": s.get("emulator_commit_at_eval_time"),
        }

    # --- per-scenario: final action / final state / choice decision count+order ---
    before_by_id = index_by_scenario(before["combats"])
    after_by_id = index_by_scenario(after["combats"])
    common_ids = sorted(set(before_by_id) & set(after_by_id))
    findings["scenario_ids_only_in_before"] = sorted(set(before_by_id) - set(after_by_id))
    findings["scenario_ids_only_in_after"] = sorted(set(after_by_id) - set(before_by_id))

    final_action_diffs, final_state_diffs, choice_count_order_diffs = [], [], []
    for sid in common_ids:
        b, a = before_by_id[sid], after_by_id[sid]
        if b["status"] != "ok" or a["status"] != "ok":
            continue
        for arm in ("policy", "heuristic"):
            be, ae = b[arm], a[arm]
            b_decisions = be["decisions"]
            a_decisions = ae["decisions"]
            b_final_action = b_decisions[-1]["chosen_action"]["label"] if b_decisions else None
            a_final_action = a_decisions[-1]["chosen_action"]["label"] if a_decisions else None
            if b_final_action != a_final_action:
                final_action_diffs.append((sid, arm, b_final_action, a_final_action))
            if (be["final_outcome"], be["final_hp"]) != (ae["final_outcome"], ae["final_hp"]):
                final_state_diffs.append((sid, arm, (be["final_outcome"], be["final_hp"]), (ae["final_outcome"], ae["final_hp"])))

    b_seq = choice_sequences(before["choice_log"])
    a_seq = choice_sequences(after["choice_log"])
    for key in sorted(set(b_seq) & set(a_seq)):
        b_types = [r["emulator_fact"]["choiceType"] for r in b_seq[key]]
        a_types = [r["emulator_fact"]["choiceType"] for r in a_seq[key]]
        if len(b_seq[key]) != len(a_seq[key]) or b_types != a_types:
            choice_count_order_diffs.append((key, len(b_seq[key]), len(a_seq[key]), b_types, a_types))

    findings["final_action_diffs"] = final_action_diffs
    findings["final_state_diffs"] = final_state_diffs
    findings["choice_count_or_order_diffs"] = choice_count_order_diffs

    # --- per-choice: raw origin / resolutionPath / operationMode / normalizedChoiceOperation
    #     / exceptionEntityKey / originValidationStatus ---
    def choice_key(row: dict) -> tuple:
        return (row["trajectory_id"], row["arm"], row["decision_index"], row.get("source"))

    b_choices = {choice_key(r): r for r in before["choice_log"]}
    a_choices = {choice_key(r): r for r in after["choice_log"]}
    common_choice_keys = sorted(set(b_choices) & set(a_choices))

    compare_fields = (
        ("emulator_fact", "originEntityType"), ("emulator_fact", "originEntityId"),
        ("resolved", "operationMode"), ("resolved", "normalizedChoiceOperation"),
        ("resolved", "exceptionEntityKey"), ("resolved", "originValidationStatus"),
    )
    field_diffs = Counter()
    per_field_examples = defaultdict(list)
    for k in common_choice_keys:
        b, a = b_choices[k], a_choices[k]
        for group, field in compare_fields:
            bv, av = b[group].get(field), a[group].get(field)
            if bv != av:
                field_diffs[field] += 1
                if len(per_field_examples[field]) < 10:
                    per_field_examples[field].append({"key": k, "before": bv, "after": av})

    findings["choice_field_diff_counts"] = dict(field_diffs)
    findings["choice_field_diff_examples"] = {k: v for k, v in per_field_examples.items()}

    # --- origin leak resolution: how many previously-suspect rows are fixed ---
    origin_leak_before = [r for r in before["choice_log"] if r["resolved"].get("originValidationStatus") == "suspected_context_leak"]
    leak_keys = {choice_key(r) for r in origin_leak_before}
    leak_status_after = Counter()
    for k in leak_keys:
        if k in a_choices:
            leak_status_after[a_choices[k]["resolved"].get("originValidationStatus")] += 1
    findings["origin_leak_before_count"] = len(origin_leak_before)
    findings["origin_leak_after_status_counts"] = dict(leak_status_after)
    findings["origin_leak_resolved_count"] = leak_status_after.get("valid", 0)
    findings["origin_leak_degraded_to_missing_count"] = leak_status_after.get("missing", 0)
    findings["origin_leak_still_leaking_count"] = leak_status_after.get("suspected_context_leak", 0)

    # --- determinism within each run (re-affirm both still deterministic on their own) ---
    findings["before_run_semantic_mismatch_note"] = "compute via cross-reference against choice_semantics_lookup.v1.json evidence, same method as rl_choice_semantics_stage_b_report_20260724.md section 4 / rl_choice_semantics_rule_update_report_20260724.md section 2.1"

    return findings


ADOPTION_CRITERIA_CHECKLIST = """
採用条件チェックリスト(この比較結果を見て手動判定 - このスクリプトは自動判定しない):
[ ] illegal / exception / mapping mismatch が before/after とも 0
[ ] 統合前後の行動一致 (final_action_diffs が0件)
[ ] 最終状態一致 (final_state_diffs が0件)
[ ] origin漏洩0 (origin_leak_still_leaking_count が0)
[ ] Suspect 10件が解消、または正しいnullへ変化 (origin_leak_resolved_count + origin_leak_degraded_to_missing_count == origin_leak_before_count)
[ ] Origin-dependent Choiceが正しいoriginで決定論的に解決 (別途、afterディレクトリ単体でのdeterminism再実行が必要 - このスクリプト単体では検証しない)
[ ] semantic mismatch 0 (別途 choice_semantics_lookup.v1.json の evidence と突合)
[ ] 既存Safe Choiceの意味判定に意図しない変化なし (choice_field_diff_counts のうち、Safe分類だった行の変化が0件であることを個別確認)
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = compare(args.before, args.after)
    out_path = args.out or (Path(__file__).resolve().parent / "choice_semantics_reverification_comparison.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(ADOPTION_CRITERIA_CHECKLIST)


if __name__ == "__main__":
    main()
