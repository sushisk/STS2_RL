from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import Dataset

from .encoding import ExportEncoder, Vocab

# Adopted merge map (reports/choice_meaning_analysis_report.md, section 4/5): 13 raw
# choice-meaning tokens -> 8 categories. Canonical source - do not redefine elsewhere.
#   - retrieve_to_hand + retrieve_to_draw_pile_top -> retrieve (similar avg candidate count ~7-8,
#     tied meaning-vs-no-meaning delta in the 1-seed analysis)
#   - apply_effect_in_place + select_for_power_association + upgrade + transform_to_specific_card
#     -> other_normalized_rare (each individually n=2-20, mostly 0 test rows)
#   - return_to_draw_pile_top is deliberately NOT merged into retrieve (different avg candidate
#     count ~4.8 vs ~7-8, opposite-sign delta)
#   - relic:GAMBLING_CHIP kept independent per explicit instruction (different evaluation criteria)
#   - discard / exhaust / transform / add_generated_to_hand kept standalone (well-populated,
#     distinct behavior)
CHOICE_MEANING_MERGE_MAP_VERSION = "choice_meaning_merge_map.v1"
CHOICE_MEANING_MERGE_MAP = {
    "retrieve_to_hand": "retrieve",
    "retrieve_to_draw_pile_top": "retrieve",
    "apply_effect_in_place": "other_normalized_rare",
    "select_for_power_association": "other_normalized_rare",
    "upgrade": "other_normalized_rare",
    "transform_to_specific_card": "other_normalized_rare",
}

# Choice teacher data lives under Combat (RL-owned, read-only from here):
#   C:\STS2_RL\Combat\evaluation\reports\choice_teacher_data_full_20260725\
#     choice_teacher_data.jsonl           (all 667 rows)
#     choice_teacher_data_eligible.jsonl  (656 rows RL marked eligible)
#     choice_teacher_data_excluded.jsonl  (11 rows, all operation_mode_unknown)
#
# On top of RL's eligibility flag, Training applies one additional scope filter (not a Data
# Contract concern, just this task's framing): decisions whose teacher_action is choice_confirm
# (no card was actually chosen - nothing to rank) are out of scope for card ranking, per the
# instruction that choice_confirm/choice_skip are not trained on this pass. The one synthetic
# nested-choice scenario is held out entirely from the split, for a dedicated inference check.

DEFAULT_SOURCE_DIR = Path(
    r"C:\STS2_RL\Combat\evaluation\reports\choice_teacher_data_full_20260725"
)
SYNTHETIC_PREFIX = "synthetic:"
DICTIONARY_VERSION = "v1"


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def choice_meaning_token(row: dict[str, Any]) -> str | None:
    """Coalesce the two mutually-exclusive choice-meaning fields into one token.

    normalized rows populate resolved.normalizedChoiceOperation and leave exceptionEntityKey
    null; passthrough rows are the reverse. Never both, per the source schema.
    """
    resolved = row.get("resolved") or {}
    return resolved.get("normalizedChoiceOperation") or resolved.get(
        "exceptionEntityKey"
    )


def choice_card_candidates(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        a
        for a in (row.get("legal_actions") or [])
        if a.get("action_type") == "choice_card"
    ]


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


def build_dictionary(name: str, values: list[str]) -> dict:
    entries = [{"id": 0, "token": "__UNKNOWN__"}]
    for idx, value in enumerate(sorted(values), start=1):
        entries.append({"id": idx, "token": value})
    return {
        "name": name,
        "version": DICTIONARY_VERSION,
        "generation_rule": "existing ids must remain fixed; append new tokens at the tail; changing an existing id requires a new major dictionary version",
        "entries": entries,
    }


def build_merged_vocab(
    source_choice_meaning_dict: dict[str, Any], merge_map: dict[str, str]
) -> tuple[dict, Vocab]:
    """Build the merged-category dictionary + a Vocab keyed by the *raw* 13 tokens.

    Encoding a raw token (e.g. "retrieve_to_hand") through the returned Vocab yields the merged
    category's id directly, so callers never need a separate remapping step. __UNKNOWN__ (id 0)
    is preserved as the reserved fallback id and is never a merge target.
    """
    raw_tokens = [
        e["token"]
        for e in source_choice_meaning_dict["entries"]
        if e["token"] != "__UNKNOWN__"
    ]
    merged_token_names = sorted({merge_map.get(t, t) for t in raw_tokens})
    merged_dict = build_dictionary("choice_meaning_merged", merged_token_names)
    merged_id_by_token = {e["token"]: e["id"] for e in merged_dict["entries"]}
    raw_token_to_id = {
        raw: merged_id_by_token[merge_map.get(raw, raw)] for raw in raw_tokens
    }
    return merged_dict, Vocab(
        token_to_id=raw_token_to_id, size=len(merged_dict["entries"])
    )


def build_merge_map_artifact(
    source_choice_meaning_dict: dict[str, Any],
) -> dict[str, Any]:
    merged_dict, _ = build_merged_vocab(
        source_choice_meaning_dict, CHOICE_MEANING_MERGE_MAP
    )
    merged_tokens = [
        e["token"] for e in merged_dict["entries"] if e["token"] != "__UNKNOWN__"
    ]
    raw_tokens = [
        e["token"]
        for e in source_choice_meaning_dict["entries"]
        if e["token"] != "__UNKNOWN__"
    ]
    unmerged = sorted(t for t in raw_tokens if t not in CHOICE_MEANING_MERGE_MAP)
    return {
        "merge_map_version": CHOICE_MEANING_MERGE_MAP_VERSION,
        "generated_from": "reports/choice_meaning_analysis_report.md",
        "source_13_token_dict_version": source_choice_meaning_dict.get("version"),
        "merge_map": CHOICE_MEANING_MERGE_MAP,
        "unmerged_tokens_passthrough": unmerged,
        "merged_token_list": merged_tokens,
        "rules": {
            "retrieve_merge": "retrieve_to_hand + retrieve_to_draw_pile_top -> retrieve",
            "rare_ops_merge": "apply_effect_in_place + select_for_power_association + upgrade + transform_to_specific_card -> other_normalized_rare",
            "return_to_draw_pile_top_not_merged": True,
            "gambling_chip_kept_independent": True,
            "unknown_excluded_from_training": True,
        },
    }


def decision_row_id(row: dict[str, Any]) -> str:
    continuation = row.get("continuation_step_index")
    suffix = f":{continuation}" if continuation is not None else ""
    return f"{row['trajectory_id']}:{row['decision_index']}{suffix}"


def audit_and_split(source_dir: Path, split_seed: int) -> dict[str, Any]:
    eligible = load_jsonl(source_dir / "choice_teacher_data_eligible.jsonl")
    excluded = load_jsonl(source_dir / "choice_teacher_data_excluded.jsonl")

    synthetic_rows = [
        r for r in eligible if r["trajectory_id"].startswith(SYNTHETIC_PREFIX)
    ]
    non_synthetic = [
        r for r in eligible if not r["trajectory_id"].startswith(SYNTHETIC_PREFIX)
    ]
    in_scope = [
        r for r in non_synthetic if r["teacher_action"]["action_type"] == "choice_card"
    ]
    out_of_scope = [
        r for r in non_synthetic if r["teacher_action"]["action_type"] != "choice_card"
    ]

    trajectory_ids = sorted({r["trajectory_id"] for r in in_scope})
    split_map = split_trajectories(trajectory_ids, split_seed)

    manifest_rows: list[dict[str, Any]] = []
    for r in excluded:
        manifest_rows.append(
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "status": "excluded_by_rl",
                "reasons": r["eligibility"]["reasons"],
                "split": None,
            }
        )
    for r in synthetic_rows:
        manifest_rows.append(
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "status": "synthetic_holdout",
                "reasons": ["synthetic_nested_scenario_reserved_for_dedicated_check"],
                "split": None,
            }
        )
    for r in out_of_scope:
        manifest_rows.append(
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "status": "excluded_out_of_scope",
                "reasons": [
                    f"teacher_action_type={r['teacher_action']['action_type']}_not_in_scope"
                ],
                "split": None,
            }
        )
    for r in in_scope:
        manifest_rows.append(
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "status": "included",
                "reasons": [],
                "split": split_map[r["trajectory_id"]],
            }
        )

    choice_meaning_values = sorted(
        {
            choice_meaning_token(r)
            for r in in_scope
            if choice_meaning_token(r) is not None
        }
    )
    choice_meaning_dict = build_dictionary("choice_meaning", choice_meaning_values)

    split_counts_traj: dict[str, int] = Counter(split_map.values())
    split_counts_decisions: dict[str, int] = Counter(
        split_map[r["trajectory_id"]] for r in in_scope
    )

    return {
        "eligible_count": len(eligible),
        "excluded_by_rl_count": len(excluded),
        "synthetic_count": len(synthetic_rows),
        "out_of_scope_count": len(out_of_scope),
        "in_scope_count": len(in_scope),
        "in_scope_trajectory_count": len(trajectory_ids),
        "split_map": split_map,
        "split_seed": split_seed,
        "split_counts_trajectories": dict(split_counts_traj),
        "split_counts_decisions": dict(split_counts_decisions),
        "manifest_rows": manifest_rows,
        "choice_meaning_dict": choice_meaning_dict,
        "in_scope_rows": in_scope,
        "synthetic_rows": synthetic_rows,
        "out_of_scope_rows": out_of_scope,
        "excluded_rows": excluded,
    }


def rows_for_split(
    in_scope_rows: list[dict[str, Any]], split_map: dict[str, str], split: str
) -> list[dict[str, Any]]:
    return [r for r in in_scope_rows if split_map[r["trajectory_id"]] == split]


class ChoiceDecisionDataset(Dataset):
    """One item per in-scope Choice decision. Candidates are choice_card legal actions only -
    choice_confirm/choice_skip are filtered out of scope upstream in audit_and_split."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        encoder: ExportEncoder,
        choice_meaning_vocab: Any,
    ) -> None:
        # __UNKNOWN__ (id 0) is not a training target - exclude defensively, though in practice
        # every in-scope row resolves to a real token (verified: 0 occurrences in this dataset).
        self.excluded_unknown_meaning_count = 0
        kept_rows = []
        for r in rows:
            if choice_meaning_vocab.encode(choice_meaning_token(r)) == 0:
                self.excluded_unknown_meaning_count += 1
                continue
            kept_rows.append(r)
        self.rows = kept_rows
        self.encoder = encoder
        self.choice_meaning_vocab = choice_meaning_vocab

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = self.rows[index]
        candidates = choice_card_candidates(row)
        card_ids = [
            self.encoder.vocabs["card"].encode(
                c["parameters"].get("cardId") or c.get("label")
            )
            for c in candidates
        ]
        teacher_action_id = row["teacher_action"]["action_id"]
        teacher_index = next(
            i for i, c in enumerate(candidates) if c["action_id"] == teacher_action_id
        )
        meaning_id = self.choice_meaning_vocab.encode(choice_meaning_token(row))
        remaining = float(row.get("remaining_select_count") or 0.0)
        resolved = row.get("resolved") or {}
        return {
            "decision_id": decision_row_id(row),
            "trajectory_id": row["trajectory_id"],
            "state": self.encoder.encode_state(row["battle_state"]),
            "card_ids": torch.tensor(card_ids, dtype=torch.long),
            "choice_meaning_id": torch.tensor(meaning_id, dtype=torch.long),
            "remaining_select_count": torch.tensor(remaining, dtype=torch.float32),
            "teacher_index": torch.tensor(teacher_index, dtype=torch.long),
            "candidate_count": len(candidates),
            "operation_mode": resolved.get("operationMode"),
            "normalized_operation": resolved.get("normalizedChoiceOperation"),
            "exception_entity": resolved.get("exceptionEntityKey"),
        }


def collate_choice(items: list[dict[str, Any]]) -> dict[str, Any]:
    batch_size = len(items)
    max_candidates = max(item["card_ids"].shape[0] for item in items)
    card_ids = torch.zeros(batch_size, max_candidates, dtype=torch.long)
    candidate_mask = torch.zeros(batch_size, max_candidates, dtype=torch.bool)
    for b, item in enumerate(items):
        n = item["card_ids"].shape[0]
        card_ids[b, :n] = item["card_ids"]
        candidate_mask[b, :n] = True
    return {
        "decision_id": [item["decision_id"] for item in items],
        "trajectory_id": [item["trajectory_id"] for item in items],
        "state": torch.stack([item["state"] for item in items]),
        "card_ids": card_ids,
        "choice_meaning_id": torch.stack([item["choice_meaning_id"] for item in items]),
        "remaining_select_count": torch.stack(
            [item["remaining_select_count"] for item in items]
        ),
        "candidate_mask": candidate_mask,
        "teacher_index": torch.stack([item["teacher_index"] for item in items]),
        "candidate_count": [item["candidate_count"] for item in items],
        "operation_mode": [item["operation_mode"] for item in items],
        "normalized_operation": [item["normalized_operation"] for item in items],
        "exception_entity": [item["exception_entity"] for item in items],
    }
