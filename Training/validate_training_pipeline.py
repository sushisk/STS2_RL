from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from torch.utils.data import DataLoader

from sts2_training.dataset import (
    STS2DecisionDataset,
    collate_decisions,
    iter_rows,
    load_json,
    validate_rows,
)
from sts2_training.encoding import ExportEncoder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate STS2 training export loading and teacher/legal alignment."
    )
    parser.add_argument(
        "--export-root", type=Path, default=Path("exports/train500_export_20260722_v1")
    )
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    export_root = args.export_root.resolve()
    encoder = ExportEncoder(load_json(export_root / "id_dictionaries.v1.json"))
    payload = {}
    for split in ("train", "validation", "test"):
        stats = validate_rows(iter_rows(export_root, split))
        dataset = STS2DecisionDataset(export_root, split, encoder)
        loader = DataLoader(
            dataset,
            batch_size=args.batch_size,
            shuffle=False,
            collate_fn=collate_decisions,
        )
        batch = next(iter(loader))
        payload[split] = {
            "rows": asdict(stats),
            "dataset_rows_after_filter": len(dataset),
            "state_shape": list(batch["state"].shape),
            "action_numeric_shape": list(batch["action_numeric"].shape),
            "legal_mask_shape": list(batch["legal_mask"].shape),
            "teacher_indices": batch["teacher_index"].tolist(),
        }
    payload["encoder"] = {
        "state_dim": encoder.state_dim,
        "action_numeric_dim": encoder.action_numeric_dim,
        "vocab_sizes": {name: encoder.vocab_size(name) for name in encoder.vocabs},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
