from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.utils.data import DataLoader

from sts2_training.dataset import load_export_info, load_json
from sts2_training.encoding import ExportEncoder
from sts2_training.model import ValueNet
from sts2_training.value_targets import (
    REMAINING_DECISIONS_SCALE,
    STS2ValueDataset,
    collate_value,
    write_value_targets,
)


DEFAULT_EXPORT_ROOT = Path("exports/teacher2000_20260723_dataset_export_v1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the STS2 Value model (win probability / final HP / remaining decisions).")
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--checkpoint-dir", type=Path, default=Path("checkpoints/value_baseline"))
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--max-train-decisions", type=int, default=None)
    parser.add_argument("--max-val-decisions", type=int, default=None)
    parser.add_argument("--max-test-decisions", type=int, default=None)
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--evaluate-test", action="store_true")
    parser.add_argument("--metrics-output", type=Path, default=None)
    parser.add_argument("--skip-target-build", action="store_true", help="Skip regenerating derived/value_targets_*.jsonl.")
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)


def build_model(encoder: ExportEncoder, hidden_dim: int) -> ValueNet:
    return ValueNet(state_dim=encoder.state_dim, hidden_dim=hidden_dim)


def run_epoch(
    model: ValueNet,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer | None = None,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    bce = nn.BCEWithLogitsLoss()
    smooth_l1 = nn.SmoothL1Loss()
    total = 0
    total_loss = 0.0
    win_correct = 0
    hp_abs_error = 0.0
    hp_abs_error_raw_hp = 0.0
    remaining_abs_error = 0.0
    with torch.set_grad_enabled(training):
        for batch in loader:
            output = model(batch["state"])
            win_loss = bce(output["win_logit"], batch["win_target"])
            hp_loss = smooth_l1(output["final_hp"], batch["final_hp_target"])
            remaining_loss = smooth_l1(output["remaining_decisions"], batch["remaining_decisions_target"])
            loss = win_loss + hp_loss + remaining_loss
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
            batch_size = batch["state"].shape[0]
            total += batch_size
            total_loss += float(loss.item()) * batch_size
            win_pred = (torch.sigmoid(output["win_logit"]) >= 0.5).float()
            win_correct += int((win_pred == batch["win_target"]).sum().item())
            hp_abs_error += float((output["final_hp"] - batch["final_hp_target"]).abs().sum().item())
            remaining_abs_error += float(
                ((output["remaining_decisions"] - batch["remaining_decisions_target"]) * REMAINING_DECISIONS_SCALE).abs().sum().item()
            )
    return {
        "loss": total_loss / max(1, total),
        "win_accuracy": win_correct / max(1, total),
        "final_hp_mae_fraction": hp_abs_error / max(1, total),
        "remaining_decisions_mae": remaining_abs_error / max(1, total),
        "decisions": float(total),
    }


def _bucket() -> dict[str, float]:
    return {"total": 0.0, "correct": 0.0}


def evaluate_detailed(model: ValueNet, loader: DataLoader) -> dict[str, Any]:
    model.eval()
    bce = nn.BCEWithLogitsLoss()
    smooth_l1 = nn.SmoothL1Loss()
    total = 0
    total_loss = 0.0
    win_correct = 0
    hp_abs_error = 0.0
    remaining_abs_error = 0.0
    by_outcome: dict[str, dict[str, float]] = defaultdict(_bucket)
    by_encounter: dict[str, dict[str, float]] = defaultdict(_bucket)
    with torch.no_grad():
        for batch in loader:
            output = model(batch["state"])
            win_loss = bce(output["win_logit"], batch["win_target"])
            hp_loss = smooth_l1(output["final_hp"], batch["final_hp_target"])
            remaining_loss = smooth_l1(output["remaining_decisions"], batch["remaining_decisions_target"])
            loss = win_loss + hp_loss + remaining_loss
            batch_size = batch["state"].shape[0]
            total += batch_size
            total_loss += float(loss.item()) * batch_size
            win_pred = (torch.sigmoid(output["win_logit"]) >= 0.5).float()
            correct = win_pred == batch["win_target"]
            win_correct += int(correct.sum().item())
            hp_abs_error += float((output["final_hp"] - batch["final_hp_target"]).abs().sum().item())
            remaining_abs_error += float(
                ((output["remaining_decisions"] - batch["remaining_decisions_target"]) * REMAINING_DECISIONS_SCALE).abs().sum().item()
            )
            for i in range(batch_size):
                outcome = batch["combat_outcome"][i] or "unknown"
                by_outcome[outcome]["total"] += 1.0
                by_outcome[outcome]["correct"] += 1.0 if bool(correct[i].item()) else 0.0
                encounter = batch["encounter_key"][i]
                by_encounter[encounter]["total"] += 1.0
                by_encounter[encounter]["correct"] += 1.0 if bool(correct[i].item()) else 0.0
    return {
        "loss": total_loss / max(1, total),
        "win_accuracy": win_correct / max(1, total),
        "final_hp_mae_fraction": hp_abs_error / max(1, total),
        "remaining_decisions_mae": remaining_abs_error / max(1, total),
        "decisions": float(total),
        "win_accuracy_by_outcome": {
            key: {"accuracy": v["correct"] / max(1.0, v["total"]), "total": v["total"]}
            for key, v in sorted(by_outcome.items(), key=lambda kv: -kv[1]["total"])
        },
        "win_accuracy_by_encounter_top20": {
            key: {"accuracy": v["correct"] / max(1.0, v["total"]), "total": v["total"]}
            for key, v in sorted(by_encounter.items(), key=lambda kv: -kv[1]["total"])[:20]
        },
    }


def save_checkpoint(
    path: Path,
    model: ValueNet,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    config: dict[str, Any],
    metrics: dict[str, Any],
    dictionaries: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "epoch": epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "config": config,
            "metrics": metrics,
            "history": metrics.get("history", []),
            "best_epoch": metrics.get("best_epoch", epoch),
            "best_loss": metrics.get("best_loss", metrics.get("validation", {}).get("loss")),
            "dictionaries": dictionaries,
        },
        path,
    )


def main() -> int:
    args = parse_args()
    started_at = time.perf_counter()
    set_seed(args.seed)
    export_root = args.export_root.resolve()
    dictionaries = load_json(export_root / "id_dictionaries.v1.json")
    encoder = ExportEncoder(dictionaries)
    info = load_export_info(export_root)

    if not args.skip_target_build:
        write_value_targets(export_root, dataset_kind="complete")

    train_dataset = STS2ValueDataset(export_root, "train", encoder, max_rows=args.max_train_decisions)
    val_dataset = STS2ValueDataset(export_root, "validation", encoder, max_rows=args.max_val_decisions)
    test_dataset = STS2ValueDataset(export_root, "test", encoder, max_rows=args.max_test_decisions) if args.evaluate_test else None
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate_value)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_value)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_value) if test_dataset else None

    model = build_model(encoder, args.hidden_dim)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)

    config = {
        "seed": args.seed,
        "export_root": str(export_root),
        "dataset_kind": "complete",
        "dataset_version": info.export_version,
        "contract_version": info.data_contract_version,
        "dictionary_version": info.dictionary_version,
        "export_script_version": info.export_script_version,
        "emulator_commit": info.emulator_commit,
        "emulator_dll_sha256": info.emulator_dll_sha256,
        "heuristic_version": info.heuristic_version,
        "state_dim": encoder.state_dim,
        "hidden_dim": args.hidden_dim,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "remaining_decisions_scale": REMAINING_DECISIONS_SCALE,
        "value_training_eligible_train_rows": len(train_dataset),
        "value_training_eligible_val_rows": len(val_dataset),
    }
    best_loss = float("inf")
    best_epoch = -1
    epochs_without_improvement = 0
    history = []

    print(json.dumps({"config": config}, indent=2))

    for epoch in range(args.epochs):
        train_metrics = run_epoch(model, train_loader, optimizer)
        val_metrics = run_epoch(model, val_loader)
        row = {"epoch": epoch, "train": train_metrics, "validation": val_metrics}
        history.append(row)
        print(json.dumps(row, indent=2))
        improved = val_metrics["loss"] < best_loss - args.min_delta
        if improved:
            best_loss = val_metrics["loss"]
            best_epoch = epoch
            epochs_without_improvement = 0
            save_checkpoint(args.checkpoint_dir / "best.pt", model, optimizer, epoch, config, row, dictionaries)
        else:
            epochs_without_improvement += 1
        save_checkpoint(
            args.checkpoint_dir / "latest.pt",
            model,
            optimizer,
            epoch,
            config,
            row | {"history": history, "best_epoch": best_epoch, "best_loss": best_loss},
            dictionaries,
        )
        if epochs_without_improvement >= args.patience:
            print(json.dumps({"early_stopping": True, "epoch": epoch, "best_epoch": best_epoch, "best_validation_loss": best_loss}, indent=2))
            break

    best_path = args.checkpoint_dir / "best.pt"
    if best_path.exists():
        checkpoint = torch.load(best_path, map_location="cpu")
        model.load_state_dict(checkpoint["model_state"])

    detailed = {
        "train": evaluate_detailed(model, DataLoader(train_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_value)),
        "validation": evaluate_detailed(model, val_loader),
    }
    if test_dataset is not None and test_loader is not None:
        detailed["test"] = evaluate_detailed(model, test_loader)

    summary = {
        "config": config,
        "best_epoch": best_epoch,
        "best_validation_loss": best_loss,
        "history": history,
        "metrics": detailed,
        "training_time_seconds": time.perf_counter() - started_at,
        "checkpoint_dir": str(args.checkpoint_dir.resolve()),
        "best_checkpoint": str(best_path.resolve()),
    }
    print(json.dumps({"final_summary": summary}, ensure_ascii=False, indent=2))
    if args.metrics_output is not None:
        args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
