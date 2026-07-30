from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.utils.data import DataLoader

from sts2_training.dataset import (
    STS2DecisionDataset,
    collate_decisions,
    load_export_info,
    load_json,
    validate_rows,
    iter_rows,
)
from sts2_training.encoding import ExportEncoder
from sts2_training.model import CandidatePolicyNet, masked_logits


DEFAULT_EXPORT_ROOT = Path("exports/train500_export_20260722_v1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a minimal STS2 imitation policy from exported JSONL.")
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--checkpoint-dir", type=Path, default=Path("checkpoints/min_policy"))
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument("--max-train-decisions", type=int, default=None)
    parser.add_argument("--max-val-decisions", type=int, default=None)
    parser.add_argument("--max-test-decisions", type=int, default=None)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--evaluate-test", action="store_true")
    parser.add_argument("--metrics-output", type=Path, default=None)
    parser.add_argument("--misclassified-output", type=Path, default=None)
    parser.add_argument("--max-misclassified", type=int, default=100)
    parser.add_argument("--resume", type=Path, default=None)
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)


def build_model(encoder: ExportEncoder, hidden_dim: int) -> CandidatePolicyNet:
    return CandidatePolicyNet(
        state_dim=encoder.state_dim,
        action_numeric_dim=encoder.action_numeric_dim,
        action_type_vocab=encoder.vocab_size("action_type"),
        card_vocab=encoder.vocab_size("card"),
        potion_vocab=encoder.vocab_size("potion"),
        hidden_dim=hidden_dim,
    )


def run_epoch(
    model: CandidatePolicyNet,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer | None = None,
    top_k: list[int] | None = None,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    criterion = nn.CrossEntropyLoss()
    total_loss = 0.0
    total = 0
    correct = 0
    illegal_selected = 0
    top_k = top_k or [1, 3, 5]
    top_counts = {k: 0 for k in top_k}
    with torch.set_grad_enabled(training):
        for batch in loader:
            logits = model(
                batch["state"],
                batch["action_type"],
                batch["card"],
                batch["potion"],
                batch["action_numeric"],
            )
            masked = masked_logits(logits, batch["legal_mask"])
            loss = criterion(masked, batch["teacher_index"])
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
            predictions = masked.argmax(dim=1)
            total_loss += float(loss.item()) * batch["state"].shape[0]
            total += batch["state"].shape[0]
            correct += int((predictions == batch["teacher_index"]).sum().item())
            illegal_selected += int((~batch["legal_mask"]).gather(1, predictions.unsqueeze(1)).sum().item())
            for k in top_k:
                kk = min(k, masked.shape[1])
                top_indices = masked.topk(kk, dim=1).indices
                top_counts[k] += int((top_indices == batch["teacher_index"].unsqueeze(1)).any(dim=1).sum().item())
    metrics = {
        "loss": total_loss / max(1, total),
        "accuracy": correct / max(1, total),
        "illegal_action_rate": illegal_selected / max(1, total),
        "decisions": float(total),
    }
    for k in top_k:
        metrics[f"top_{k}_accuracy"] = top_counts[k] / max(1, total)
    return metrics


def _bucket() -> dict[str, float]:
    return {"total": 0.0, "correct": 0.0}


def _add_bucket(buckets: dict[str, dict[str, float]], key: Any, correct: bool) -> None:
    label = "__UNKNOWN__" if key is None else str(key)
    buckets[label]["total"] += 1.0
    buckets[label]["correct"] += 1.0 if correct else 0.0


def _finalize_buckets(buckets: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    return {
        key: {
            "accuracy": value["correct"] / max(1.0, value["total"]),
            "correct": value["correct"],
            "total": value["total"],
        }
        for key, value in sorted(buckets.items(), key=lambda item: (-item[1]["total"], item[0]))
    }


def evaluate_detailed(
    model: CandidatePolicyNet,
    dataset: STS2DecisionDataset,
    loader: DataLoader,
    top_k: list[int],
    misclassified_output: Path | None = None,
    max_misclassified: int = 100,
) -> dict[str, Any]:
    model.eval()
    criterion = nn.CrossEntropyLoss()
    total_loss = 0.0
    total = 0
    correct_count = 0
    illegal_selected = 0
    top_counts = {k: 0 for k in top_k}
    by_action_type: dict[str, dict[str, float]] = defaultdict(_bucket)
    by_outcome: dict[str, dict[str, float]] = defaultdict(_bucket)
    by_legal_count: dict[str, dict[str, float]] = defaultdict(_bucket)
    by_encounter: dict[str, dict[str, float]] = defaultdict(_bucket)
    misclassified: list[dict[str, Any]] = []
    offset = 0

    with torch.no_grad():
        for batch in loader:
            logits = model(
                batch["state"],
                batch["action_type"],
                batch["card"],
                batch["potion"],
                batch["action_numeric"],
            )
            masked = masked_logits(logits, batch["legal_mask"])
            loss = criterion(masked, batch["teacher_index"])
            predictions = masked.argmax(dim=1)
            batch_size = batch["state"].shape[0]
            total_loss += float(loss.item()) * batch_size
            total += batch_size
            illegal_selected += int((~batch["legal_mask"]).gather(1, predictions.unsqueeze(1)).sum().item())

            for k in top_k:
                kk = min(k, masked.shape[1])
                top_indices = masked.topk(kk, dim=1).indices
                top_counts[k] += int((top_indices == batch["teacher_index"].unsqueeze(1)).any(dim=1).sum().item())

            for item_idx in range(batch_size):
                row = dataset.rows[offset + item_idx]
                pred_idx = int(predictions[item_idx].item())
                teacher_idx = int(batch["teacher_index"][item_idx].item())
                is_correct = pred_idx == teacher_idx
                correct_count += 1 if is_correct else 0
                _add_bucket(by_action_type, batch["teacher_action_type"][item_idx], is_correct)
                _add_bucket(by_outcome, batch["combat_outcome"][item_idx], is_correct)
                _add_bucket(by_legal_count, batch["legal_action_count"][item_idx], is_correct)
                _add_bucket(by_encounter, batch["encounter_key"][item_idx], is_correct)

                if not is_correct and len(misclassified) < max_misclassified:
                    legal_actions = row.get("legal_actions") or []
                    misclassified.append(
                        {
                            "decision_id": row.get("decision_id"),
                            "trajectory_id": row.get("trajectory_id"),
                            "combat_outcome": row.get("combat_outcome"),
                            "termination_reason": row.get("termination_reason"),
                            "encounter_key": batch["encounter_key"][item_idx],
                            "legal_action_count": len(legal_actions),
                            "observation": row.get("observation"),
                            "legal_actions": legal_actions,
                            "teacher_index": teacher_idx,
                            "predicted_index": pred_idx,
                            "teacher_action": legal_actions[teacher_idx] if 0 <= teacher_idx < len(legal_actions) else row.get("teacher_action"),
                            "predicted_action": legal_actions[pred_idx] if 0 <= pred_idx < len(legal_actions) else None,
                            "top_scores": [
                                {
                                    "index": int(idx),
                                    "score": float(masked[item_idx, idx].item()),
                                    "action": legal_actions[int(idx)] if int(idx) < len(legal_actions) else None,
                                }
                                for idx in masked[item_idx].topk(min(max(top_k), masked.shape[1])).indices.tolist()
                            ],
                        }
                    )
            offset += batch_size

    if misclassified_output is not None:
        misclassified_output.parent.mkdir(parents=True, exist_ok=True)
        with misclassified_output.open("w", encoding="utf-8") as f:
            for row in misclassified:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    metrics: dict[str, Any] = {
        "loss": total_loss / max(1, total),
        "accuracy": correct_count / max(1, total),
        "illegal_action_rate": illegal_selected / max(1, total),
        "decisions": float(total),
        "by_action_type": _finalize_buckets(by_action_type),
        "by_outcome": _finalize_buckets(by_outcome),
        "by_legal_action_count": _finalize_buckets(by_legal_count),
        "by_encounter": _finalize_buckets(by_encounter),
        "misclassified_saved": len(misclassified),
    }
    for k in top_k:
        metrics[f"top_{k}_accuracy"] = top_counts[k] / max(1, total)
    return metrics


def save_checkpoint(
    path: Path,
    model: CandidatePolicyNet,
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

    train_dataset = STS2DecisionDataset(export_root, "train", encoder, max_rows=args.max_train_decisions)
    val_dataset = STS2DecisionDataset(export_root, "validation", encoder, max_rows=args.max_val_decisions)
    test_dataset = STS2DecisionDataset(export_root, "test", encoder, max_rows=args.max_test_decisions) if args.evaluate_test else None
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate_decisions)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_decisions)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_decisions) if test_dataset else None

    model = build_model(encoder, args.hidden_dim)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    start_epoch = 0

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
        "split_seed_complete": info.split_seed_complete,
        "state_dim": encoder.state_dim,
        "action_numeric_dim": encoder.action_numeric_dim,
        "hidden_dim": args.hidden_dim,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "max_train_decisions": args.max_train_decisions,
        "max_val_decisions": args.max_val_decisions,
        "max_test_decisions": args.max_test_decisions,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "top_k": args.top_k,
    }
    best_loss = float("inf")
    best_epoch = -1
    epochs_without_improvement = 0
    history = []

    if args.resume is not None:
        checkpoint = torch.load(args.resume, map_location="cpu")
        model.load_state_dict(checkpoint["model_state"])
        optimizer.load_state_dict(checkpoint["optimizer_state"])
        start_epoch = int(checkpoint["epoch"]) + 1
        best_loss = float(checkpoint.get("best_loss", checkpoint.get("metrics", {}).get("validation", {}).get("loss", best_loss)))
        best_epoch = int(checkpoint.get("best_epoch", checkpoint.get("epoch", -1)))
        history = list(checkpoint.get("history", []))

    train_stats = validate_rows(iter_rows(export_root, "train"))
    val_stats = validate_rows(iter_rows(export_root, "validation"))
    print(json.dumps({"config": config, "train_validation": asdict(train_stats), "val_validation": asdict(val_stats)}, indent=2))

    for epoch in range(start_epoch, start_epoch + args.epochs):
        train_metrics = run_epoch(model, train_loader, optimizer, args.top_k)
        val_metrics = run_epoch(model, val_loader, top_k=args.top_k)
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
        save_checkpoint(args.checkpoint_dir / f"epoch_{epoch:03d}.pt", model, optimizer, epoch, config, row, dictionaries)
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
        "train": evaluate_detailed(model, train_dataset, DataLoader(train_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_decisions), args.top_k),
        "validation": evaluate_detailed(
            model,
            val_dataset,
            val_loader,
            args.top_k,
            args.misclassified_output,
            args.max_misclassified,
        ),
    }
    if test_dataset is not None and test_loader is not None:
        test_misclassified = None
        if args.misclassified_output is not None:
            test_misclassified = args.misclassified_output.with_name(args.misclassified_output.stem + "_test.jsonl")
        detailed["test"] = evaluate_detailed(model, test_dataset, test_loader, args.top_k, test_misclassified, args.max_misclassified)

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
