from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import Dataset

from .encoding import ExportEncoder

SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class ExportInfo:
    export_root: Path
    export_version: str
    data_contract_version: str | None
    dictionary_version: str | None
    export_script_version: str | None
    split_seed_complete: int | None
    complete_trajectory_count: int
    complete_decision_count: int
    emulator_commit: str | None = None
    emulator_dll_sha256: str | None = None
    heuristic_version: str | None = None


@dataclass(frozen=True)
class ValidationStats:
    rows: int
    trajectories: int
    teacher_index_missing: int
    teacher_index_out_of_range: int
    teacher_unavailable: int
    semantic_mismatch: int
    max_legal_actions: int


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_export_info(export_root: Path) -> ExportInfo:
    metadata = load_json(export_root / "export_metadata.json")
    split_seed = metadata.get("split_seed")
    if isinstance(split_seed, dict):
        split_seed_complete = split_seed.get("complete")
    else:
        split_seed_complete = metadata.get("split_seed_complete")
    first_row = next(iter_rows(export_root, "train"), {})
    return ExportInfo(
        export_root=export_root,
        export_version=str(metadata.get("export_version")),
        data_contract_version=metadata.get("data_contract_version"),
        dictionary_version=metadata.get("dictionary_version"),
        export_script_version=metadata.get("export_script_version"),
        split_seed_complete=split_seed_complete,
        complete_trajectory_count=int(metadata.get("complete_trajectory_count", 0)),
        complete_decision_count=int(metadata.get("complete_decision_count", 0)),
        emulator_commit=first_row.get("emulator_commit"),
        emulator_dll_sha256=first_row.get("emulator_dll_sha256"),
        heuristic_version=metadata.get("heuristic_version")
        or first_row.get("heuristic_version"),
    )


def iter_rows(
    export_root: Path, split: str, dataset_kind: str = "complete"
) -> Iterable[dict[str, Any]]:
    path = export_root / f"{dataset_kind}_{split}.jsonl"
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def action_semantic_key(action: dict[str, Any]) -> tuple[Any, ...]:
    return (
        action.get("action_type"),
        action.get("label"),
        action.get("card_id"),
        action.get("potion_id"),
        action.get("target_type"),
        action.get("target_enemy_index"),
    )


def action_semantic_key_without_target(action: dict[str, Any]) -> tuple[Any, ...]:
    return action_semantic_key({**action, "target_enemy_index": None})


def find_teacher_index(row: dict[str, Any]) -> int | None:
    legal_actions = row.get("legal_actions") or []
    selected = (row.get("teacher_target") or {}).get("selected_action_index")
    teacher = row.get("teacher_action") or {}
    if isinstance(selected, int) and 0 <= selected < len(legal_actions):
        return selected

    teacher_key = action_semantic_key(teacher)
    for idx, action in enumerate(legal_actions):
        if action_semantic_key(action) == teacher_key:
            return idx

    teacher_key_without_target = action_semantic_key_without_target(teacher)
    for idx, action in enumerate(legal_actions):
        if action_semantic_key_without_target(action) == teacher_key_without_target:
            return idx
    return None


def validate_rows(rows: Iterable[dict[str, Any]]) -> ValidationStats:
    row_count = 0
    trajectories: set[str] = set()
    missing = 0
    out_of_range = 0
    unavailable = 0
    semantic_mismatch = 0
    max_legal = 0
    for row in rows:
        row_count += 1
        trajectories.add(str(row.get("trajectory_id")))
        legal_actions = row.get("legal_actions") or []
        max_legal = max(max_legal, len(legal_actions))
        selected = (row.get("teacher_target") or {}).get("selected_action_index")
        if selected is None:
            missing += 1
        elif not isinstance(selected, int) or not 0 <= selected < len(legal_actions):
            out_of_range += 1

        teacher_idx = find_teacher_index(row)
        if teacher_idx is None:
            semantic_mismatch += 1
            continue
        if legal_actions[teacher_idx].get("is_available") is not True:
            unavailable += 1
        teacher_key = action_semantic_key(row.get("teacher_action") or {})
        legal_key = action_semantic_key(legal_actions[teacher_idx])
        if legal_key != teacher_key and action_semantic_key_without_target(
            legal_actions[teacher_idx]
        ) != action_semantic_key_without_target(row.get("teacher_action") or {}):
            semantic_mismatch += 1
    return ValidationStats(
        row_count,
        len(trajectories),
        missing,
        out_of_range,
        unavailable,
        semantic_mismatch,
        max_legal,
    )


class STS2DecisionDataset(Dataset):
    def __init__(
        self,
        export_root: Path,
        split: str,
        encoder: ExportEncoder,
        dataset_kind: str = "complete",
        max_rows: int | None = None,
    ) -> None:
        if dataset_kind != "complete":
            raise ValueError(
                "Initial training only supports usable_complete export files."
            )
        if split not in SPLITS:
            raise ValueError(f"Unknown split: {split}")
        self.export_root = export_root
        self.split = split
        self.encoder = encoder
        self.rows: list[dict[str, Any]] = []
        self.excluded_choice_count = 0
        for row in iter_rows(export_root, split, dataset_kind):
            if row.get("data_usage_classification") != "usable_complete":
                continue
            if row.get("teacher_action_type") == "choice_card":
                # RL does not yet supply choice purpose/source-zone/origin context, so choice
                # decisions cannot be learned honestly here. Tracked separately, routed to a
                # Heuristic fallback at inference (see sts2_training/inference.py).
                self.excluded_choice_count += 1
                continue
            if find_teacher_index(row) is None:
                continue
            self.rows.append(row)
            if max_rows is not None and len(self.rows) >= max_rows:
                break

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = self.rows[index]
        legal_actions = row["legal_actions"]
        action_features = [
            self.encoder.encode_action(action) for action in legal_actions
        ]
        teacher_index = find_teacher_index(row)
        teacher_action = legal_actions[teacher_index]
        return {
            "decision_id": row["decision_id"],
            "trajectory_id": row["trajectory_id"],
            "combat_outcome": row.get("combat_outcome"),
            "termination_reason": row.get("termination_reason"),
            "teacher_action_type": teacher_action.get("action_type"),
            "legal_action_count": len(legal_actions),
            "encounter_key": encounter_key(row.get("observation") or {}),
            "state": self.encoder.encode_state(row["observation"]),
            "actions": action_features,
            "legal_mask": self.encoder.legal_mask(legal_actions),
            "teacher_index": torch.tensor(teacher_index, dtype=torch.long),
        }


def collate_decisions(items: list[dict[str, Any]]) -> dict[str, Any]:
    batch_size = len(items)
    max_actions = max(len(item["actions"]) for item in items)
    state = torch.stack([item["state"] for item in items])
    action_type = torch.zeros(batch_size, max_actions, dtype=torch.long)
    card = torch.zeros(batch_size, max_actions, dtype=torch.long)
    potion = torch.zeros(batch_size, max_actions, dtype=torch.long)
    numeric_dim = items[0]["actions"][0]["numeric"].shape[0]
    action_numeric = torch.zeros(
        batch_size, max_actions, numeric_dim, dtype=torch.float32
    )
    legal_mask = torch.zeros(batch_size, max_actions, dtype=torch.bool)
    candidate_mask = torch.zeros(batch_size, max_actions, dtype=torch.bool)
    teacher_index = torch.stack([item["teacher_index"] for item in items])

    for batch_idx, item in enumerate(items):
        for action_idx, action in enumerate(item["actions"]):
            action_type[batch_idx, action_idx] = action["action_type"]
            card[batch_idx, action_idx] = action["card"]
            potion[batch_idx, action_idx] = action["potion"]
            action_numeric[batch_idx, action_idx] = action["numeric"]
        count = len(item["actions"])
        legal_mask[batch_idx, :count] = item["legal_mask"]
        candidate_mask[batch_idx, :count] = True

    return {
        "decision_id": [item["decision_id"] for item in items],
        "trajectory_id": [item["trajectory_id"] for item in items],
        "combat_outcome": [item["combat_outcome"] for item in items],
        "termination_reason": [item["termination_reason"] for item in items],
        "teacher_action_type": [item["teacher_action_type"] for item in items],
        "legal_action_count": [item["legal_action_count"] for item in items],
        "encounter_key": [item["encounter_key"] for item in items],
        "state": state,
        "action_type": action_type,
        "card": card,
        "potion": potion,
        "action_numeric": action_numeric,
        "legal_mask": legal_mask,
        "candidate_mask": candidate_mask,
        "teacher_index": teacher_index,
    }


def encounter_key(observation: dict[str, Any]) -> str:
    enemies = observation.get("enemies") or []
    ids = [
        str(enemy.get("id"))
        for enemy in enemies
        if isinstance(enemy, dict)
        and enemy.get("isAlive") is not False
        and enemy.get("id") is not None
    ]
    if not ids:
        ids = [
            str(enemy.get("id"))
            for enemy in enemies
            if isinstance(enemy, dict) and enemy.get("id") is not None
        ]
    return "+".join(sorted(ids)) if ids else "__UNKNOWN__"
