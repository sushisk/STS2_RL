"""Read-only origin-dependency audit over the Stage B saved choice_log.jsonl (Choice
Semantics baseline v1 - see Combat/policy_baseline/choice_semantics_baseline_v1_20260724.json).

Does NOT modify choice_semantics.py, choice_semantics_lookup.v1.json, or
choice_semantics_origin_type_aliases.v1.json - ChoiceSemanticsTable.resolve() is used
purely as a read-only black box, including for the counterfactual re-resolution (section
4 of this task's instructions). No Scenario is re-executed - everything is recomputed
from the already-saved `emulator_fact` dicts.

Produces:
    choice_semantics_resolution_path_audit.jsonl  - one row per choice decision (section 2)
    choice_semantics_counterfactual_audit.jsonl    - counterfactual re-resolution diffs (section 4)
    (printed) aggregate summary (section 5)

Run: cd C:\\STS2_RL\\Combat\\evaluation\\online_eval && python audit_choice_semantics_origin_dependency.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_COMBAT_DIR))

from choice_semantics import ChoiceSemanticsTable  # noqa: E402

STAGE_B_CHOICE_LOG = _COMBAT_DIR / "evaluation" / "reports" / "choice_stage_b_with_semantics" / "choice_log.jsonl"
OUT_DIR = Path(__file__).resolve().parent

# A real, fixture entity guaranteed present in the lookup table, resolved purely via
# Tier-3 origin lookup (not Tier-1/2 concrete-operation) - used as the "unrelated known
# entity" counterfactual substitute (section 4). Deliberately unrelated to every
# category audited here (not a potion, not GAMBLING_CHIP).
UNRELATED_KNOWN_ENTITY = ("card", "HOLOGRAM")

SAFE_PATHS = {"emulator_choice_operation", "combined_rule", "dedicated_choice_type_rule", "unknown"}
ORIGIN_DEPENDENT_PATHS = {"origin_entity_rule", "origin_type_alias", "passthrough_rule"}


def load_rows() -> list[dict]:
    with STAGE_B_CHOICE_LOG.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def classify_resolution_path(resolved: dict) -> str:
    """7-way partition per this task's section 2 categories."""
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
        raw_type = resolved.get("_raw_origin_type_for_classification")
        norm_type = resolved.get("normalizedOriginEntityType")
        return "origin_type_alias" if raw_type != norm_type else "origin_entity_rule"
    # miss / ambiguous_match / no_origin / load_error / resolved_lookup_unknown
    return "unknown"


def build_sequences(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    seq: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        seq[(row["trajectory_id"], row["arm"])].append(row)
    return seq


def audit_row(row: dict, index_in_seq: int, prev_row: "dict | None", seq_rows: list[dict], table: ChoiceSemanticsTable) -> dict:
    ef = row["emulator_fact"]
    # IMPORTANT: recompute against the CURRENT (fixed) table - row["resolved"] in the
    # saved choice_log.jsonl reflects the PRE-fix resolution (Stage B ran before the
    # GamblingChipDiscard/Potion rule update), so it must not be reused directly here.
    resolved = dict(table.resolve(ef)["resolved"])
    resolved["_raw_origin_type_for_classification"] = ef["originEntityType"]
    path = classify_resolution_path(resolved)

    # --- Suspect criteria ---
    suspect_reasons = []
    if resolved.get("originValidationStatus") == "suspected_context_leak":
        suspect_reasons.append("origin_validation_suspected_context_leak")

    origin = (ef["originEntityType"], ef["originEntityId"])
    if origin != (None, None):
        # criterion 2/3 combined: same non-null origin shared with another row in the
        # same (trajectory_id, arm) sequence whose choiceType differs - covers both
        # "matches immediately preceding choice's entity" and "unnaturally fixed across
        # a chain of different choiceTypes within one step".
        for other in seq_rows:
            if other is row:
                continue
            other_ef = other["emulator_fact"]
            other_origin = (other_ef["originEntityType"], other_ef["originEntityId"])
            if other_origin == origin and other_ef["choiceType"] != ef["choiceType"]:
                suspect_reasons.append("origin_shared_across_different_choiceType")
                break

    if path in ("origin_entity_rule", "origin_type_alias", "passthrough_rule"):
        matched_id = resolved.get("matchedRuleId")
        norm_type = resolved.get("normalizedOriginEntityType")
        matched_row = table.lookup_row(norm_type, matched_id) if matched_id else None
        if matched_row is not None:
            expected_zone = matched_row.get("source_zone")
            actual_zone = ef.get("sourceZone")
            if expected_zone not in (None, "either", "hand_or_deck") and actual_zone not in (None, expected_zone):
                suspect_reasons.append("source_zone_contradicts_matched_rule")

    if suspect_reasons:
        risk_category = "Suspect"
    elif path in SAFE_PATHS:
        risk_category = "Safe"
    elif path in ORIGIN_DEPENDENT_PATHS:
        risk_category = "Origin-dependent"
    else:
        risk_category = "Safe"  # defensive fallback, should not occur

    return {
        "scenario_id": row["trajectory_id"],
        "arm": row["arm"],
        "decision_index": row["decision_index"],
        "choice_sequence_index": index_in_seq,
        "choiceType": ef["choiceType"],
        "rawOriginEntityType": ef["originEntityType"],
        "rawOriginEntityId": ef["originEntityId"],
        "operationMode": resolved["operationMode"],
        "normalizedChoiceOperation": resolved["normalizedChoiceOperation"],
        "exceptionEntityKey": resolved["exceptionEntityKey"],
        "matchedRuleId": resolved["matchedRuleId"],
        "resolutionPath": path,
        "originValidationStatus": resolved.get("originValidationStatus"),
        "riskCategory": risk_category,
        "suspectReasons": suspect_reasons,
        "trainingEligible": risk_category != "Suspect",
    }


def counterfactual_variants(ef: dict, prev_origin: "tuple | None") -> dict[str, dict]:
    variants = {}
    for name, origin in (
        ("null", (None, None)),
        ("prior_choice_origin", prev_origin),
        ("unrelated_known_entity", UNRELATED_KNOWN_ENTITY),
    ):
        if origin is None:
            continue
        variant_fact = dict(ef)
        variant_fact["originEntityType"], variant_fact["originEntityId"] = origin
        variants[name] = variant_fact
    return variants


def main() -> None:
    table = ChoiceSemanticsTable()
    assert table.loaded_ok, table.load_error
    assert table.origin_aliases_error is None, table.origin_aliases_error

    rows = load_rows()
    sequences = build_sequences(rows)

    audit_records = []
    counterfactual_records = []

    for (traj, arm), seq_rows in sequences.items():
        for i, row in enumerate(seq_rows):
            prev_row = seq_rows[i - 1] if i > 0 else None
            audit_records.append(audit_row(row, i, prev_row, seq_rows, table))

            ef = row["emulator_fact"]
            prev_origin = None
            if prev_row is not None:
                prev_ef = prev_row["emulator_fact"]
                prev_origin = (prev_ef["originEntityType"], prev_ef["originEntityId"])
            variants = counterfactual_variants(ef, prev_origin)

            # Baseline for the diff is the CURRENT table's resolution of the real,
            # unmodified emulator_fact - not the stale pre-fix value saved in
            # choice_log.jsonl.
            original = table.resolve(ef)["resolved"]
            compare_fields = ("operationMode", "normalizedChoiceOperation", "exceptionEntityKey", "matchedRuleId")
            semantic_fields = ("operationMode", "normalizedChoiceOperation", "exceptionEntityKey")
            cf_result = {
                "scenario_id": traj, "arm": arm, "decision_index": row["decision_index"],
                "choice_sequence_index": i, "choiceType": ef["choiceType"],
                "original": {k: original[k] for k in compare_fields},
                "variants": {},
            }
            for name, variant_fact in variants.items():
                new_resolved = table.resolve(variant_fact)["resolved"]
                diff_full = {k: (original[k], new_resolved[k]) for k in compare_fields if original[k] != new_resolved[k]}
                diff_semantic = {k: v for k, v in diff_full.items() if k in semantic_fields}
                cf_result["variants"][name] = {
                    "resolved": {k: new_resolved[k] for k in compare_fields},
                    "changed_fields_full": sorted(diff_full.keys()),
                    "changed_fields_semantic_only": sorted(diff_semantic.keys()),
                }
            counterfactual_records.append(cf_result)

    with (OUT_DIR / "choice_semantics_resolution_path_audit.jsonl").open("w", encoding="utf-8") as f:
        for r in audit_records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with (OUT_DIR / "choice_semantics_counterfactual_audit.jsonl").open("w", encoding="utf-8") as f:
        for r in counterfactual_records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    n = len(audit_records)
    risk_counts = Counter(r["riskCategory"] for r in audit_records)
    path_counts = Counter(r["resolutionPath"] for r in audit_records)
    eligible = sum(1 for r in audit_records if r["trainingEligible"])
    excluded = n - eligible
    exclusion_reasons = Counter(reason for r in audit_records if not r["trainingEligible"] for reason in r["suspectReasons"])

    null_changed_full = sum(1 for r in counterfactual_records if r["variants"].get("null", {}).get("changed_fields_full"))
    null_changed_semantic = sum(1 for r in counterfactual_records if r["variants"].get("null", {}).get("changed_fields_semantic_only"))
    unrelated_changed_full = sum(1 for r in counterfactual_records if r["variants"].get("unrelated_known_entity", {}).get("changed_fields_full"))
    unrelated_changed_semantic = sum(1 for r in counterfactual_records if r["variants"].get("unrelated_known_entity", {}).get("changed_fields_semantic_only"))
    prior_present = [r for r in counterfactual_records if "prior_choice_origin" in r["variants"]]
    prior_changed_full = sum(1 for r in prior_present if r["variants"]["prior_choice_origin"]["changed_fields_full"])
    prior_changed_semantic = sum(1 for r in prior_present if r["variants"]["prior_choice_origin"]["changed_fields_semantic_only"])

    summary = {
        "n": n,
        "risk_category_counts": dict(risk_counts),
        "risk_category_rates": {k: v / n for k, v in risk_counts.items()},
        "resolution_path_counts": dict(path_counts),
        "training_eligible": eligible,
        "training_eligible_rate": eligible / n,
        "training_excluded": excluded,
        "training_excluded_rate": excluded / n,
        "exclusion_reason_counts": dict(exclusion_reasons),
        "counterfactual": {
            "null_origin": {"n": n, "changed_full": null_changed_full, "changed_semantic_only": null_changed_semantic},
            "prior_choice_origin": {"n": len(prior_present), "changed_full": prior_changed_full, "changed_semantic_only": prior_changed_semantic},
            "unrelated_known_entity": {"n": n, "changed_full": unrelated_changed_full, "changed_semantic_only": unrelated_changed_semantic},
        },
    }
    with (OUT_DIR / "choice_semantics_origin_audit_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
