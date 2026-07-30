"""BEFORE (pre-fix, commit 163bf04-era/0d16130) vs AFTER (commit 722b019) comparison for
the 41-scenario choice_semantics_reverification_manifest.jsonl re-verification.

Does NOT modify choice_semantics.py, the lookup, the origin-type-alias table, the
manifest, or compare_choice_semantics_reverification.py (that script stays as the
generic before/after diff tool; this is a NEW, dedicated script for this specific
722b019 adoption decision, since the required analysis - 7-way diff classification,
synthetic/real split, re-run origin audit on AFTER - goes beyond what that generic tool
computes). No Scenario is re-executed here - reads only the already-saved BEFORE/AFTER
run directories.

Run: python analyze_reverification_722b019.py --before <dir> --after <dir> [--after-determinism <dir>]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_COMBAT_DIR = Path(__file__).resolve().parents[2]
import sys  # noqa: E402
sys.path.insert(0, str(_COMBAT_DIR))
from choice_semantics import ChoiceSemanticsTable, NORMALIZABLE_OPERATIONS  # noqa: E402

# Known-benign origin values for a genuine Reset-time GamblingChipDiscard trigger.
GAMBLING_CHIP_RULE_ID = "GamblingChipDiscard"


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_run(out_dir: Path) -> dict[str, Any]:
    return {
        "summary": json.loads((out_dir / "summary.json").read_text(encoding="utf-8")),
        "combats": load_jsonl(out_dir / "combats.jsonl"),
        "choice_log": load_jsonl(out_dir / "choice_log.jsonl"),
        "decisions_policy": load_jsonl(out_dir / "decisions_policy.jsonl"),
        "decisions_heuristic": load_jsonl(out_dir / "decisions_heuristic.jsonl"),
    }


def is_synthetic(trajectory_id: str) -> bool:
    return trajectory_id.startswith("synthetic:")


def choice_seq_key(row: dict) -> tuple:
    return (row["trajectory_id"], row["arm"], row["source"])


def index_choice_sequences(choice_log: list[dict]) -> dict[tuple, list[dict]]:
    seq = defaultdict(list)
    for row in choice_log:
        seq[choice_seq_key(row)].append(row)
    return seq


def index_decisions(rows: list[dict]) -> dict[tuple, list[dict]]:
    seq = defaultdict(list)
    for row in rows:
        seq[row["trajectory_id"]].append(row)
    return seq


def classify_choice_diff(before_row: "dict | None", after_row: "dict | None") -> tuple[str, str]:
    """Returns (category, note) - one of the 7 required categories (指示7節)."""
    if before_row is None or after_row is None:
        return "unresolved", "choice present in only one of before/after at this sequence position"

    b_ef, a_ef = before_row["emulator_fact"], after_row["emulator_fact"]
    b_res, a_res = before_row["resolved"], after_row["resolved"]

    same_origin = (b_ef["originEntityType"], b_ef["originEntityId"]) == (a_ef["originEntityType"], a_ef["originEntityId"])
    same_choice_type = b_ef["choiceType"] == a_ef["choiceType"]
    same_semantic = (
        b_res["operationMode"] == a_res["operationMode"]
        and b_res["normalizedChoiceOperation"] == a_res["normalizedChoiceOperation"]
        and b_res["exceptionEntityKey"] == a_res["exceptionEntityKey"]
    )

    if same_origin and same_choice_type and same_semantic:
        return "no_change", "identical"

    # known_reset_gambling_chip_null: genuine Reset-time trigger, origin null in both
    # (or null in after where before was also null/leaked-but-now-corrected-to-null)
    if a_ef["choiceType"] == GAMBLING_CHIP_RULE_ID and a_ef["originEntityType"] is None and a_ef["originEntityId"] is None:
        if b_ef["choiceType"] == GAMBLING_CHIP_RULE_ID and b_ef["originEntityType"] is None:
            return "no_change", "genuine reset-time GamblingChipDiscard, origin null in both"
        # before had a leaked/other origin, after correctly shows null
        return "known_reset_gambling_chip_null", (
            f"origin corrected to null (before origin was {b_ef['originEntityType']}/{b_ef['originEntityId']}, "
            f"choiceType before={b_ef['choiceType']})"
        )

    # expected_choice_type_fix: choiceType itself changes from one of the special/
    # dedicated types this task names (GamblingChipDiscard, and per section 5's
    # required focus list, also WishDrawToHand/ChoicesParadoxAddToHand/ToolboxChooseCard)
    # to a more generic/correct choiceType. This is unconditionally benign whether or
    # not origin/semantic content also changes alongside it - two live-observed
    # sub-patterns, both expected:
    #   (a) origin unchanged (was already correct even before the fix - e.g. GUARDS/
    #       POWER_POTION/SKILL_POTION), but semantic content DOES change, because
    #       CHOICE_TYPE_RULES's Tier-1 priority was forcing an incorrect
    #       passthrough/GAMBLING_CHIP result onto what was actually that entity's own
    #       genuine choice the whole time - removing the wrong choiceType label lets
    #       the (always-correct) origin drive the resolution instead. The semantic
    #       change here is the FIX, not a regression - was root-caused precisely,
    #       not assumed.
    #   (b) origin AND semantic content both stay identical - only the choiceType
    #       label itself is refined (observed for ToolboxChooseCard's continuation
    #       picks within one multi-select).
    special_choice_types = {GAMBLING_CHIP_RULE_ID, "ToolboxChooseCard", "WishDrawToHand", "ChoicesParadoxAddToHand"}
    if b_ef["choiceType"] in special_choice_types and a_ef["choiceType"] not in special_choice_types:
        return "expected_choice_type_fix", (
            f"choiceType corrected from {b_ef['choiceType']!r} to {a_ef['choiceType']!r}; "
            f"origin before={b_ef['originEntityType']}/{b_ef['originEntityId']}, "
            f"after={a_ef['originEntityType']}/{a_ef['originEntityId']}; "
            f"origin_unchanged={same_origin}, semantic_unchanged={same_semantic}"
        )

    # expected_origin_fix: choiceType unchanged, but origin value changed (either to
    # null, or to a different, presumably-correct entity) AND before was flagged
    # suspected_context_leak (or origin plainly differs for a choiceType known to be
    # leak-prone: GamblingChipDiscard/ToolboxChooseCard/WishDrawToHand/ChoicesParadoxAddToHand).
    leak_prone_types = {"GamblingChipDiscard", "ToolboxChooseCard", "WishDrawToHand", "ChoicesParadoxAddToHand"}
    if same_choice_type and not same_origin:
        was_flagged_leak = b_res.get("originValidationStatus") == "suspected_context_leak"
        if was_flagged_leak or b_ef["choiceType"] in leak_prone_types:
            return "expected_origin_fix", (
                f"origin changed {b_ef['originEntityType']}/{b_ef['originEntityId']} -> "
                f"{a_ef['originEntityType']}/{a_ef['originEntityId']} for choiceType={a_ef['choiceType']!r}"
            )

    # unintended_semantic_change: semantic content (operationMode/normalizedChoiceOperation/
    # exceptionEntityKey) changed without matching any expected-fix pattern above.
    if not same_semantic:
        return "unintended_semantic_change", (
            f"operationMode {b_res['operationMode']!r}->{a_res['operationMode']!r}, "
            f"normalizedChoiceOperation {b_res['normalizedChoiceOperation']!r}->{a_res['normalizedChoiceOperation']!r}, "
            f"exceptionEntityKey {b_res['exceptionEntityKey']!r}->{a_res['exceptionEntityKey']!r}"
        )

    # Origin/choiceType changed but semantic content happens to be identical, and it
    # doesn't match a known-good pattern - flag for manual review rather than silently
    # accepting.
    return "unresolved", (
        f"origin/choiceType changed without matching an expected-fix pattern: "
        f"choiceType {b_ef['choiceType']!r}->{a_ef['choiceType']!r}, "
        f"origin {b_ef['originEntityType']}/{b_ef['originEntityId']} -> {a_ef['originEntityType']}/{a_ef['originEntityId']}"
    )


def compute_semantic_mismatch(choice_log: list[dict], table: ChoiceSemanticsTable) -> int:
    mismatches = 0
    for row in choice_log:
        rule_id = row["resolved"].get("matchedRuleId")
        if not rule_id or row["resolved"]["lookupStatus"] == "resolved_choice_type_rule":
            continue
        norm_type = row["resolved"].get("normalizedOriginEntityType")
        table_row = table.lookup_row(norm_type, rule_id)
        if table_row is None:
            continue
        ev = table_row["evidence"]
        ef = row["emulator_fact"]
        if ev.get("emulator_choice_operation") is not None and ev["emulator_choice_operation"] != ef["choiceOperation"]:
            mismatches += 1
        elif ev.get("emulator_destination_zone") is not None and ev["emulator_destination_zone"] != ef["destinationZone"]:
            mismatches += 1
    return mismatches


def audit_risk_categories(choice_log: list[dict], table: ChoiceSemanticsTable) -> dict:
    """Same classification as audit_choice_semantics_origin_dependency.py, reimplemented
    here read-only against a caller-supplied choice_log (no shared mutable state, no
    change to that script)."""
    SAFE_PATHS = {"emulator_choice_operation", "combined_rule", "dedicated_choice_type_rule", "unknown"}
    ORIGIN_DEPENDENT_PATHS = {"origin_entity_rule", "origin_type_alias", "passthrough_rule"}

    def path_of(resolved: dict, raw_type) -> str:
        status = resolved["lookupStatus"]
        mode = resolved["operationMode"]
        if status == "resolved_choice_type_rule":
            return "dedicated_choice_type_rule"
        if status == "resolved_emulator_fact_confirmed_by_lookup":
            return "combined_rule"
        if status in ("resolved_emulator_fact", "resolved_emulator_fact_no_origin", "resolved_emulator_fact_ambiguous_lookup"):
            return "emulator_choice_operation"
        if status == "resolved_lookup":
            if mode == "passthrough":
                return "passthrough_rule"
            norm_type = resolved.get("normalizedOriginEntityType")
            return "origin_type_alias" if raw_type != norm_type else "origin_entity_rule"
        return "unknown"

    by_seq = index_choice_sequences(choice_log)
    risk_counts = Counter()
    excluded_reasons = Counter()
    for key, rows in by_seq.items():
        for row in rows:
            ef = row["emulator_fact"]
            resolved = table.resolve(ef)["resolved"]
            path = path_of(resolved, ef["originEntityType"])
            suspect = resolved.get("originValidationStatus") == "suspected_context_leak"
            for other in rows:
                if other is row:
                    continue
                o_ef = other["emulator_fact"]
                if (o_ef["originEntityType"], o_ef["originEntityId"]) == (ef["originEntityType"], ef["originEntityId"]) and o_ef["choiceType"] != ef["choiceType"] and ef["originEntityType"] is not None:
                    suspect = True
            if suspect:
                risk_counts["Suspect"] += 1
                excluded_reasons["origin_validation_suspected_context_leak"] += 1
            elif path in SAFE_PATHS:
                risk_counts["Safe"] += 1
            elif path in ORIGIN_DEPENDENT_PATHS:
                risk_counts["Origin-dependent"] += 1
            else:
                risk_counts["Safe"] += 1
    n = sum(risk_counts.values())
    eligible = risk_counts["Safe"] + risk_counts["Origin-dependent"]
    return {
        "n": n,
        "risk_category_counts": dict(risk_counts),
        "provisional_eligible": eligible,
        "provisional_excluded": risk_counts["Suspect"],
        "exclusion_reason_counts": dict(excluded_reasons),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--after-determinism", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "reverification_722b019_analysis.json")
    args = parser.parse_args()

    table = ChoiceSemanticsTable()
    assert table.loaded_ok, table.load_error

    before = load_run(args.before)
    after = load_run(args.after)

    b_summary, a_summary = before["summary"], after["summary"]

    # --- provenance ---
    provenance = {
        "before_emulator_commit": b_summary.get("emulator_commit_at_eval_time"),
        "after_emulator_commit": a_summary.get("emulator_commit_at_eval_time"),
        "before_dir": str(args.before),
        "after_dir": str(args.after),
        "choice_semantics_provenance": a_summary.get("choice_semantics_provenance"),
    }

    # --- gates ---
    gates = {}
    for label, s in (("before", b_summary), ("after", a_summary)):
        gates[label] = {
            "illegal_action": s["policy_arm"]["illegal_action_count"] + s["heuristic_arm"]["illegal_action_count"],
            "exception": s["policy_arm"]["exception_count"] + s["heuristic_arm"]["exception_count"],
            "mapping_mismatch": s["policy_arm"]["action_mapping_mismatch_count"] + s["heuristic_arm"]["action_mapping_mismatch_count"],
            "ok_scenarios": s["ok"],
            "quarantined": s["quarantined"],
        }

    # --- scenario-level: final state, final outcome, action sequence, legal_action_count sequence ---
    b_combats = {c["trajectory_id"]: c for c in before["combats"]}
    a_combats = {c["trajectory_id"]: c for c in after["combats"]}
    common_scenarios = sorted(set(b_combats) & set(a_combats))

    scenario_diffs = {"real": [], "synthetic": []}
    for sid in common_scenarios:
        bc, ac = b_combats[sid], a_combats[sid]
        bucket = "synthetic" if is_synthetic(sid) else "real"
        if bc["status"] != "ok" or ac["status"] != "ok":
            scenario_diffs[bucket].append({"scenario_id": sid, "diff": "status_mismatch", "before": bc["status"], "after": ac["status"]})
            continue
        for arm in ("policy", "heuristic"):
            be, ae = bc[arm], ac[arm]
            state_match = (be["final_outcome"], be["final_hp"], be["final_max_hp"], be["final_potion_count"]) == \
                          (ae["final_outcome"], ae["final_hp"], ae["final_max_hp"], ae["final_potion_count"])
            b_action_seq = [d["chosen_action"]["label"] for d in be["decisions"]]
            a_action_seq = [d["chosen_action"]["label"] for d in ae["decisions"]]
            action_match = b_action_seq == a_action_seq
            b_legal_counts = [d["legal_action_count"] for d in be["decisions"]]
            a_legal_counts = [d["legal_action_count"] for d in ae["decisions"]]
            legal_count_match = b_legal_counts == a_legal_counts
            if not (state_match and action_match and legal_count_match):
                scenario_diffs[bucket].append({
                    "scenario_id": sid, "arm": arm,
                    "state_match": state_match, "action_sequence_match": action_match,
                    "legal_action_count_sequence_match": legal_count_match,
                    "before_final": (be["final_outcome"], be["final_hp"]), "after_final": (ae["final_outcome"], ae["final_hp"]),
                    "before_decision_count": len(b_action_seq), "after_decision_count": len(a_action_seq),
                })

    # --- choice-level diffs, classified into 7 categories ---
    b_seq = index_choice_sequences(before["choice_log"])
    a_seq = index_choice_sequences(after["choice_log"])
    common_keys = sorted(set(b_seq) & set(a_seq))

    diff_records = {"real": [], "synthetic": []}
    diff_category_counts = {"real": Counter(), "synthetic": Counter()}
    choice_count_diffs = {"real": [], "synthetic": []}

    for key in common_keys:
        sid = key[0]
        bucket = "synthetic" if is_synthetic(sid) else "real"
        b_rows, a_rows = b_seq[key], a_seq[key]
        if len(b_rows) != len(a_rows):
            choice_count_diffs[bucket].append({"key": key, "before_count": len(b_rows), "after_count": len(a_rows)})
        for i in range(max(len(b_rows), len(a_rows))):
            b_row = b_rows[i] if i < len(b_rows) else None
            a_row = a_rows[i] if i < len(a_rows) else None
            category, note = classify_choice_diff(b_row, a_row)
            if category != "no_change":
                diff_category_counts[bucket][category] += 1
                diff_records[bucket].append({
                    "scenario_id": sid, "arm": key[1], "source": key[2], "sequence_index": i,
                    "category": category, "note": note,
                    "before": b_row["emulator_fact"] if b_row else None,
                    "after": a_row["emulator_fact"] if a_row else None,
                    "before_resolved": b_row["resolved"] if b_row else None,
                    "after_resolved": a_row["resolved"] if a_row else None,
                })

    # --- semantic mismatch (after) ---
    semantic_mismatch_after = compute_semantic_mismatch(after["choice_log"], table)
    semantic_mismatch_before = compute_semantic_mismatch(before["choice_log"], table)

    # --- determinism (after vs after-rerun, if provided) ---
    determinism = None
    if args.after_determinism is not None:
        after2 = load_run(args.after_determinism)
        a2_seq = index_choice_sequences(after2["choice_log"])
        mism = 0
        total = 0
        for key in set(a_seq) & set(a2_seq):
            for r1, r2 in zip(a_seq[key], a2_seq[key]):
                total += 1
                if r1["emulator_fact"] != r2["emulator_fact"] or r1["resolved"] != r2["resolved"]:
                    mism += 1
        determinism = {"total_compared": total, "mismatches": mism, "deterministic": mism == 0}

    # --- origin-dependency audit, recomputed on AFTER ---
    real_after_choice_log = [r for r in after["choice_log"] if not is_synthetic(r["trajectory_id"])]
    audit_after = audit_risk_categories(real_after_choice_log, table)
    real_before_choice_log = [r for r in before["choice_log"] if not is_synthetic(r["trajectory_id"])]
    audit_before = audit_risk_categories(real_before_choice_log, table)

    # --- Suspect-10 tracking (exact set from the origin audit report) ---
    suspect_10_keys = [
        ("7551-16", "policy", 4), ("7551-16", "heuristic", 4),
        ("7413-9", "policy", 12), ("7413-9", "heuristic", 11),
        ("1934-19", "heuristic", 24),
    ]
    # (the audit report counted 10 individual choice rows, several sharing a
    # (scenario,arm,decision_index) - re-derive precisely from the before choice_log
    # instead of hardcoding counts here)
    suspect_before_rows = [r for r in before["choice_log"] if r["resolved"].get("originValidationStatus") == "suspected_context_leak"]
    suspect_tracking = []
    for row in suspect_before_rows:
        key = choice_seq_key(row)
        # find matching position in after by (decision_index within that seq)
        seq_b = b_seq[key]
        seq_a = a_seq.get(key, [])
        idx = seq_b.index(row)
        after_row = seq_a[idx] if idx < len(seq_a) else None
        suspect_tracking.append({
            "scenario_id": row["trajectory_id"], "arm": row["arm"], "decision_index": row["decision_index"],
            "before_origin": (row["emulator_fact"]["originEntityType"], row["emulator_fact"]["originEntityId"]),
            "before_choiceType": row["emulator_fact"]["choiceType"],
            "after_origin": (after_row["emulator_fact"]["originEntityType"], after_row["emulator_fact"]["originEntityId"]) if after_row else None,
            "after_choiceType": after_row["emulator_fact"]["choiceType"] if after_row else None,
            "after_originValidationStatus": after_row["resolved"].get("originValidationStatus") if after_row else None,
        })

    result = {
        "provenance": provenance,
        "gates": gates,
        "scenario_diffs": scenario_diffs,
        "choice_count_diffs": choice_count_diffs,
        "diff_category_counts": {k: dict(v) for k, v in diff_category_counts.items()},
        "diff_records": diff_records,
        "semantic_mismatch": {"before": semantic_mismatch_before, "after": semantic_mismatch_after},
        "determinism": determinism,
        "origin_audit": {"before": audit_before, "after": audit_after},
        "suspect_10_tracking": suspect_tracking,
    }

    with args.out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"Wrote analysis -> {args.out}")

    # concise console summary
    print(json.dumps({
        "gates": gates,
        "scenario_diffs_real_count": len(scenario_diffs["real"]),
        "scenario_diffs_synthetic_count": len(scenario_diffs["synthetic"]),
        "choice_count_diffs_real": len(choice_count_diffs["real"]),
        "diff_category_counts_real": dict(diff_category_counts["real"]),
        "diff_category_counts_synthetic": dict(diff_category_counts["synthetic"]),
        "semantic_mismatch": {"before": semantic_mismatch_before, "after": semantic_mismatch_after},
        "determinism": determinism,
        "origin_audit_after": audit_after,
        "origin_audit_before": audit_before,
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
