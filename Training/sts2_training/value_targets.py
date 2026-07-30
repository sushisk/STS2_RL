from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import Dataset

from .dataset import encounter_key, iter_rows
from .encoding import ExportEncoder


# These labels are derived entirely from fields already present in the Data Contract v1 export
# (combat_outcome, decision_index, raw_next_state.hp). They are a Training-side post-processing
# artifact, not an addition to the RL-owned Data Contract, and can be regenerated at any time from
# complete_{split}.jsonl / partial_{split}.jsonl.

VALUE_ELIGIBLE_DATA_USAGE = {"usable_complete"}

# remaining_decisions_target is stored raw in the sidecar file; this scale is only applied when
# tensorizing for the network (matches termination_limit:50 seen across the source trajectories).
REMAINING_DECISIONS_SCALE = 50.0


def _final_hp(decision_row: dict[str, Any]) -> float | None:
    next_state = decision_row.get("raw_next_state") or {}
    hp = next_state.get("hp")
    if hp is None:
        hp = (decision_row.get("observation") or {}).get("hp")
    return float(hp) if isinstance(hp, (int, float)) else None


def _max_hp(decision_row: dict[str, Any]) -> float:
    observation = decision_row.get("observation") or {}
    max_hp = observation.get("maxHp")
    return float(max_hp) if isinstance(max_hp, (int, float)) and max_hp else 1.0


def build_value_targets(export_root: Path, split: str, dataset_kind: str = "complete") -> list[dict[str, Any]]:
    by_trajectory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rows(export_root, split, dataset_kind):
        by_trajectory[row["trajectory_id"]].append(row)

    targets: list[dict[str, Any]] = []
    for trajectory_id, rows in by_trajectory.items():
        rows.sort(key=lambda r: r["decision_index"])
        last_row = rows[-1]
        last_index = last_row["decision_index"]
        eligible = last_row.get("data_usage_classification") in VALUE_ELIGIBLE_DATA_USAGE
        win_target = None
        if last_row.get("combat_outcome") == "victory":
            win_target = 1.0
        elif last_row.get("combat_outcome") == "defeat":
            win_target = 0.0
        final_hp = _final_hp(last_row)
        max_hp = _max_hp(last_row)
        final_hp_target = (final_hp / max_hp) if (final_hp is not None and eligible) else None

        for row in rows:
            remaining = last_index - row["decision_index"]
            row_eligible = eligible and win_target is not None and final_hp_target is not None
            targets.append(
                {
                    "decision_id": row["decision_id"],
                    "trajectory_id": trajectory_id,
                    "decision_index": row["decision_index"],
                    "next_decision_id": (
                        f"{trajectory_id}:{row['decision_index'] + 1}" if row["decision_index"] < last_index else None
                    ),
                    "is_terminal": row["decision_index"] == last_index and last_row.get("termination_reason") == "terminal",
                    "is_truncated": row["decision_index"] == last_index and last_row.get("termination_reason") != "terminal",
                    "value_training_eligible": row_eligible,
                    "win_target": win_target if row_eligible else None,
                    "final_hp_target": final_hp_target if row_eligible else None,
                    "remaining_decisions_target": float(remaining) if row_eligible else None,
                }
            )
    return targets


def write_value_targets(export_root: Path, dataset_kind: str = "complete") -> dict[str, Path]:
    out_paths: dict[str, Path] = {}
    derived_dir = export_root / "derived"
    for split in ("train", "validation", "test"):
        targets = build_value_targets(export_root, split, dataset_kind)
        out_path = derived_dir / f"value_targets_{split}.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for row in targets:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        out_paths[split] = out_path
    readme = derived_dir / "README.md"
    readme.write_text(
        "# Derived value targets\n\n"
        "Training-side post-processing artifacts, not part of the RL-owned Data Contract v1 export.\n"
        "Derived solely from fields already present in complete_{split}.jsonl "
        "(combat_outcome, decision_index, raw_next_state.hp). Regenerate any time via "
        "sts2_training.value_targets.write_value_targets.\n\n"
        "`value_training_eligible` is only true for usable_complete trajectories (confirmed terminal "
        "win/loss). usable_partial trajectories have no confirmed final outcome, so Value labels are "
        "left null for them in this v1 pass; bootstrapped/TD labeling for partial trajectories is a "
        "future-work item, not something guessed here.\n",
        encoding="utf-8",
    )
    return out_paths


def load_value_targets(export_root: Path, split: str) -> dict[str, dict[str, Any]]:
    path = export_root / "derived" / f"value_targets_{split}.jsonl"
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line)
                result[row["decision_id"]] = row
    return result


class STS2ValueDataset(Dataset):
    def __init__(
        self,
        export_root: Path,
        split: str,
        encoder: ExportEncoder,
        dataset_kind: str = "complete",
        max_rows: int | None = None,
    ) -> None:
        self.encoder = encoder
        targets = load_value_targets(export_root, split)
        self.rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in iter_rows(export_root, split, dataset_kind):
            target = targets.get(row["decision_id"])
            if target is None or not target.get("value_training_eligible"):
                continue
            self.rows.append((row, target))
            if max_rows is not None and len(self.rows) >= max_rows:
                break

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row, target = self.rows[index]
        return {
            "decision_id": row["decision_id"],
            "trajectory_id": row["trajectory_id"],
            "combat_outcome": row.get("combat_outcome"),
            "encounter_key": encounter_key(row.get("observation") or {}),
            "state": self.encoder.encode_state(row["observation"]),
            "win_target": torch.tensor(target["win_target"], dtype=torch.float32),
            "final_hp_target": torch.tensor(target["final_hp_target"], dtype=torch.float32),
            "remaining_decisions_target": torch.tensor(
                target["remaining_decisions_target"] / REMAINING_DECISIONS_SCALE, dtype=torch.float32
            ),
        }


def collate_value(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "decision_id": [item["decision_id"] for item in items],
        "trajectory_id": [item["trajectory_id"] for item in items],
        "combat_outcome": [item["combat_outcome"] for item in items],
        "encounter_key": [item["encounter_key"] for item in items],
        "state": torch.stack([item["state"] for item in items]),
        "win_target": torch.stack([item["win_target"] for item in items]),
        "final_hp_target": torch.stack([item["final_hp_target"] for item in items]),
        "remaining_decisions_target": torch.stack([item["remaining_decisions_target"] for item in items]),
    }
