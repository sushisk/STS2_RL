from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.utils.data import DataLoader

from sts2_training.choice_data import (
    DEFAULT_SOURCE_DIR,
    ChoiceDecisionDataset,
    audit_and_split,
    choice_card_candidates,
    choice_meaning_token,
    collate_choice,
    decision_row_id,
    rows_for_split,
    sha256_file,
)
from sts2_training.encoding import ExportEncoder
from sts2_training.model import (
    ChoicePolicyNet,
    load_shared_encoder_weights,
    masked_logits,
)

DEFAULT_POLICY_CHECKPOINT = Path("checkpoints/policy_teacher2000_seed_20260724/best.pt")
DEFAULT_CHOICE_SEMANTICS_BASELINE = Path(
    r"C:\STS2_RL\Combat\policy_baseline\choice_semantics_baseline_722b019_v1_20260725.json"
)
SPLITS = ("train", "validation", "test")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train an initial Choice Policy (offline-eval only) on RL's Choice teacher data."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument(
        "--policy-checkpoint", type=Path, default=DEFAULT_POLICY_CHECKPOINT
    )
    parser.add_argument(
        "--choice-semantics-baseline-file",
        type=Path,
        default=DEFAULT_CHOICE_SEMANTICS_BASELINE,
    )
    parser.add_argument("--split-seed", type=int, default=20260725)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--choice-meaning-embedding-dim", type=int, default=8)
    parser.add_argument("--random-baseline-trials", type=int, default=200)
    parser.add_argument("--max-misclassified", type=int, default=20)
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("checkpoints/choice_policy_seed_20260725"),
    )
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/choice_policy_baseline")
    )
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)


# ---------------------------------------------------------------------------
# Ranking metrics shared by trained models and both non-parametric baselines
# ---------------------------------------------------------------------------


def rank_metrics_from_scores(
    scores: torch.Tensor, candidate_mask: torch.Tensor, teacher_index: torch.Tensor
) -> dict[str, torch.Tensor]:
    masked = scores.masked_fill(~candidate_mask, -1.0e9)
    order = torch.argsort(masked, dim=1, descending=True)
    batch_size = scores.shape[0]
    ranks = torch.zeros(batch_size, dtype=torch.long)
    for b in range(batch_size):
        ranks[b] = (
            int((order[b] == teacher_index[b]).nonzero(as_tuple=True)[0].item()) + 1
        )
    probs = torch.softmax(masked, dim=1)
    predicted_index = masked.argmax(dim=1)
    confidence = probs.gather(1, predicted_index.unsqueeze(1)).squeeze(1)
    illegal = ~candidate_mask.gather(1, predicted_index.unsqueeze(1)).squeeze(1)
    return {
        "ranks": ranks,
        "predicted_index": predicted_index,
        "confidence": confidence,
        "illegal": illegal,
        "order": order,
    }


def aggregate_rank_metrics(
    ranks: torch.Tensor, illegal: torch.Tensor
) -> dict[str, float]:
    ranks_f = ranks.float()
    return {
        "top_1_accuracy": float((ranks == 1).float().mean().item()),
        "top_3_accuracy": float((ranks <= 3).float().mean().item()),
        "top_5_accuracy": float((ranks <= 5).float().mean().item()),
        "mrr": float((1.0 / ranks_f).mean().item()),
        "illegal_prediction_rate": float(illegal.float().mean().item()),
        "decisions": int(ranks.shape[0]),
    }


def bucket_accuracy(keys: list[Any], hits: list[bool]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"correct": 0.0, "total": 0.0}
    )
    for key, hit in zip(keys, hits):
        label = "unknown" if key is None else str(key)
        buckets[label]["total"] += 1.0
        buckets[label]["correct"] += 1.0 if hit else 0.0
    result: dict[str, dict[str, Any]] = {}
    for label, v in sorted(buckets.items(), key=lambda kv: -kv[1]["total"]):
        result[label] = {
            "accuracy": v["correct"] / v["total"],
            "total": int(v["total"]),
            "reference_only_low_n": v["total"] < 10,
        }
    return result


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------


def build_model(
    state_dim: int,
    card_vocab: int,
    choice_meaning_vocab: int,
    card_embedding_dim: int,
    choice_meaning_embedding_dim: int,
    hidden_dim: int,
    use_choice_meaning: bool,
) -> ChoicePolicyNet:
    return ChoicePolicyNet(
        state_dim=state_dim,
        card_vocab=card_vocab,
        choice_meaning_vocab=choice_meaning_vocab,
        card_embedding_dim=card_embedding_dim,
        choice_meaning_embedding_dim=choice_meaning_embedding_dim,
        hidden_dim=hidden_dim,
        use_choice_meaning=use_choice_meaning,
    )


def run_epoch(
    model: ChoicePolicyNet,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer | None = None,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    criterion = nn.CrossEntropyLoss()
    total_loss = 0.0
    all_ranks: list[torch.Tensor] = []
    all_illegal: list[torch.Tensor] = []
    total = 0
    with torch.set_grad_enabled(training):
        for batch in loader:
            logits = model(
                batch["state"],
                batch["card_ids"],
                batch["choice_meaning_id"],
                batch["remaining_select_count"],
            )
            masked = masked_logits(logits, batch["candidate_mask"])
            loss = criterion(masked, batch["teacher_index"])
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
            batch_size = batch["state"].shape[0]
            total += batch_size
            total_loss += float(loss.item()) * batch_size
            metrics = rank_metrics_from_scores(
                logits, batch["candidate_mask"], batch["teacher_index"]
            )
            all_ranks.append(metrics["ranks"])
            all_illegal.append(metrics["illegal"])
    ranks = torch.cat(all_ranks)
    illegal = torch.cat(all_illegal)
    result = aggregate_rank_metrics(ranks, illegal)
    result["loss"] = total_loss / max(1, total)
    return result


def train_variant(
    name: str,
    use_choice_meaning: bool,
    freeze_encoder: bool,
    seed: int,
    train_loader: DataLoader,
    val_loader: DataLoader,
    policy_model_state: dict[str, torch.Tensor],
    state_dim: int,
    card_vocab: int,
    choice_meaning_vocab: int,
    card_embedding_dim: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    set_seed(seed)
    model = build_model(
        state_dim,
        card_vocab,
        choice_meaning_vocab,
        card_embedding_dim,
        args.choice_meaning_embedding_dim,
        hidden_dim=64,
        use_choice_meaning=use_choice_meaning,
    )
    copied_keys = load_shared_encoder_weights(model, policy_model_state)
    model.set_shared_encoder_trainable(not freeze_encoder)
    trainable_params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable_params, lr=args.lr)

    best_loss = float("inf")
    best_epoch = -1
    best_state = None
    epochs_without_improvement = 0
    history = []
    for epoch in range(args.epochs):
        train_metrics = run_epoch(model, train_loader, optimizer)
        val_metrics = run_epoch(model, val_loader)
        history.append(
            {"epoch": epoch, "train": train_metrics, "validation": val_metrics}
        )
        improved = val_metrics["loss"] < best_loss - args.min_delta
        if improved:
            best_loss = val_metrics["loss"]
            best_epoch = epoch
            epochs_without_improvement = 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            epochs_without_improvement += 1
        if epochs_without_improvement >= args.patience:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return {
        "name": name,
        "use_choice_meaning": use_choice_meaning,
        "freeze_encoder": freeze_encoder,
        "model": model,
        "history": history,
        "best_epoch": best_epoch,
        "best_validation_loss": best_loss,
        "shared_encoder_keys_copied": copied_keys,
    }


# ---------------------------------------------------------------------------
# Non-parametric baselines
# ---------------------------------------------------------------------------


def random_baseline(
    loader: DataLoader, trials: int, base_seed: int
) -> dict[str, float]:
    trial_metrics: list[dict[str, float]] = []
    for trial in range(trials):
        rng = torch.Generator().manual_seed(base_seed + trial)
        all_ranks: list[torch.Tensor] = []
        all_illegal: list[torch.Tensor] = []
        for batch in loader:
            scores = torch.rand(batch["candidate_mask"].shape, generator=rng)
            metrics = rank_metrics_from_scores(
                scores, batch["candidate_mask"], batch["teacher_index"]
            )
            all_ranks.append(metrics["ranks"])
            all_illegal.append(metrics["illegal"])
        trial_metrics.append(
            aggregate_rank_metrics(torch.cat(all_ranks), torch.cat(all_illegal))
        )
    keys = [
        "top_1_accuracy",
        "top_3_accuracy",
        "top_5_accuracy",
        "mrr",
        "illegal_prediction_rate",
    ]
    return {k: sum(m[k] for m in trial_metrics) / trials for k in keys} | {
        "trials": trials,
        "decisions": trial_metrics[0]["decisions"],
    }


def frequency_baseline(
    train_rows: list[dict[str, Any]], encoder: ExportEncoder, loader: DataLoader
) -> dict[str, float]:
    freq: Counter = Counter()
    for row in train_rows:
        card_id = row["teacher_action"]["parameters"].get("cardId") or row[
            "teacher_action"
        ].get("label")
        freq[encoder.vocabs["card"].encode(card_id)] += 1

    all_ranks: list[torch.Tensor] = []
    all_illegal: list[torch.Tensor] = []
    for batch in loader:
        card_ids = batch["card_ids"]
        scores = torch.zeros_like(card_ids, dtype=torch.float32)
        for token_id, count in freq.items():
            scores += (card_ids == token_id).float() * float(count)
        # deterministic tie-break favoring earlier-listed candidates
        tie_break = torch.arange(card_ids.shape[1], dtype=torch.float32).flip(0) * 1e-6
        scores = scores + tie_break.unsqueeze(0)
        metrics = rank_metrics_from_scores(
            scores, batch["candidate_mask"], batch["teacher_index"]
        )
        all_ranks.append(metrics["ranks"])
        all_illegal.append(metrics["illegal"])
    return aggregate_rank_metrics(torch.cat(all_ranks), torch.cat(all_illegal))


# ---------------------------------------------------------------------------
# Detailed evaluation (breakdowns + misclassified) for the primary model
# ---------------------------------------------------------------------------


def detailed_evaluation(
    model: ChoicePolicyNet,
    dataset: ChoiceDecisionDataset,
    loader: DataLoader,
    max_misclassified: int,
) -> dict[str, Any]:
    model.eval()
    all_ranks: list[torch.Tensor] = []
    all_illegal: list[torch.Tensor] = []
    candidate_counts: list[int] = []
    operation_modes: list[str | None] = []
    normalized_ops: list[str | None] = []
    exception_entities: list[str | None] = []
    remaining_counts: list[int] = []
    misclassified: list[dict[str, Any]] = []
    offset = 0
    with torch.no_grad():
        for batch in loader:
            logits = model(
                batch["state"],
                batch["card_ids"],
                batch["choice_meaning_id"],
                batch["remaining_select_count"],
            )
            metrics = rank_metrics_from_scores(
                logits, batch["candidate_mask"], batch["teacher_index"]
            )
            all_ranks.append(metrics["ranks"])
            all_illegal.append(metrics["illegal"])
            candidate_counts.extend(batch["candidate_count"])
            operation_modes.extend(batch["operation_mode"])
            normalized_ops.extend(batch["normalized_operation"])
            exception_entities.extend(batch["exception_entity"])
            remaining_counts.extend(
                int(x.item()) for x in batch["remaining_select_count"]
            )

            batch_size = batch["state"].shape[0]
            for i in range(batch_size):
                rank = int(metrics["ranks"][i].item())
                if rank == 1 or len(misclassified) >= max_misclassified:
                    continue
                row = dataset.rows[offset + i]
                candidates = choice_card_candidates(row)
                order = metrics["order"][i].tolist()
                ranking = [
                    {
                        "index": idx,
                        "label": (
                            candidates[idx]["label"] if idx < len(candidates) else None
                        ),
                        "score": float(logits[i, idx].item()),
                    }
                    for idx in order
                    if idx < len(candidates)
                ]
                misclassified.append(
                    {
                        "decision_id": decision_row_id(row),
                        "trajectory_id": row["trajectory_id"],
                        "battle_state_summary": {
                            "characterId": row["battle_state"].get("characterId"),
                            "hp": row["battle_state"].get("hp"),
                            "maxHp": row["battle_state"].get("maxHp"),
                            "turnNumber": row["battle_state"].get("turnNumber"),
                            "enemy_ids": [
                                e.get("id")
                                for e in row["battle_state"].get("enemies") or []
                            ],
                        },
                        "choice_meaning": {
                            "operationMode": row["resolved"].get("operationMode"),
                            "normalizedChoiceOperation": row["resolved"].get(
                                "normalizedChoiceOperation"
                            ),
                            "exceptionEntityKey": row["resolved"].get(
                                "exceptionEntityKey"
                            ),
                        },
                        "legal_candidate_cards": [c.get("label") for c in candidates],
                        "teacher_choice": candidates[
                            int(batch["teacher_index"][i].item())
                        ].get("label"),
                        "model_ranking": ranking,
                        "confidence": float(metrics["confidence"][i].item()),
                        "rank_of_teacher": rank,
                    }
                )
            offset += batch_size

    ranks = torch.cat(all_ranks)
    illegal = torch.cat(all_illegal)
    top1_hits = [r == 1 for r in ranks.tolist()]
    return {
        "aggregate": aggregate_rank_metrics(ranks, illegal),
        "by_candidate_count": bucket_accuracy(candidate_counts, top1_hits),
        "by_normalized_operation": bucket_accuracy(
            [
                op if mode == "normalized" else None
                for op, mode in zip(normalized_ops, operation_modes)
            ],
            top1_hits,
        ),
        "by_passthrough_exception_entity": bucket_accuracy(
            [
                ent if mode == "passthrough" else None
                for ent, mode in zip(exception_entities, operation_modes)
            ],
            top1_hits,
        ),
        "by_remaining_select_count": bucket_accuracy(remaining_counts, top1_hits),
        "misclassified": misclassified,
    }


# ---------------------------------------------------------------------------
# Synthetic nested-choice dedicated check
# ---------------------------------------------------------------------------


def synthetic_check(
    model: ChoicePolicyNet,
    synthetic_rows: list[dict[str, Any]],
    encoder: ExportEncoder,
    choice_meaning_vocab: Any,
) -> list[dict[str, Any]]:
    model.eval()
    results = []
    for row in synthetic_rows:
        candidates = choice_card_candidates(row)
        if not candidates:
            results.append(
                {
                    "decision_id": decision_row_id(row),
                    "skipped": "no_choice_card_candidates",
                }
            )
            continue
        card_ids = torch.tensor(
            [
                [
                    encoder.vocabs["card"].encode(
                        c["parameters"].get("cardId") or c.get("label")
                    )
                    for c in candidates
                ]
            ],
            dtype=torch.long,
        )
        state = encoder.encode_state(row["battle_state"]).unsqueeze(0)
        meaning_id = torch.tensor(
            [choice_meaning_vocab.encode(choice_meaning_token(row))], dtype=torch.long
        )
        remaining = torch.tensor(
            [float(row.get("remaining_select_count") or 0.0)], dtype=torch.float32
        )
        with torch.no_grad():
            logits = model(state, card_ids, meaning_id, remaining)
        predicted_index = int(logits.argmax(dim=1).item())
        teacher_action_id = row["teacher_action"]["action_id"]
        teacher_index = next(
            (
                i
                for i, c in enumerate(candidates)
                if c["action_id"] == teacher_action_id
            ),
            None,
        )
        results.append(
            {
                "decision_id": decision_row_id(row),
                "candidate_count": len(candidates),
                "predicted_label": candidates[predicted_index]["label"],
                "teacher_label": (
                    candidates[teacher_index]["label"]
                    if teacher_index is not None
                    else None
                ),
                "matched_teacher": predicted_index == teacher_index,
                "ran_without_exception": True,
            }
        )
    return results


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    args = parse_args()
    started_at = time.perf_counter()

    policy_checkpoint = torch.load(
        args.policy_checkpoint, map_location="cpu", weights_only=False
    )
    dictionaries = policy_checkpoint["dictionaries"]
    encoder = ExportEncoder(dictionaries)
    card_embedding_dim = int(
        policy_checkpoint["model_state"]["card_embedding.weight"].shape[1]
    )
    hidden_dim = int(policy_checkpoint["model_state"]["state_net.0.weight"].shape[0])
    assert (
        hidden_dim == 64
    ), f"ChoicePolicyNet hidden_dim is hardcoded to 64 to match the Policy checkpoint; got {hidden_dim}"

    audit = audit_and_split(args.source_dir, args.split_seed)
    choice_meaning_vocab = ExportEncoder._load_vocab(audit["choice_meaning_dict"])

    train_rows = rows_for_split(audit["in_scope_rows"], audit["split_map"], "train")
    val_rows = rows_for_split(audit["in_scope_rows"], audit["split_map"], "validation")
    test_rows = rows_for_split(audit["in_scope_rows"], audit["split_map"], "test")

    train_dataset = ChoiceDecisionDataset(train_rows, encoder, choice_meaning_vocab)
    val_dataset = ChoiceDecisionDataset(val_rows, encoder, choice_meaning_vocab)
    test_dataset = ChoiceDecisionDataset(test_rows, encoder, choice_meaning_vocab)
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_choice,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate_choice,
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate_choice,
    )

    common_kwargs = {
        "train_loader": train_loader,
        "val_loader": val_loader,
        "policy_model_state": policy_checkpoint["model_state"],
        "state_dim": encoder.state_dim,
        "card_vocab": encoder.vocab_size("card"),
        "choice_meaning_vocab": choice_meaning_vocab.size,
        "card_embedding_dim": card_embedding_dim,
        "args": args,
    }
    print(json.dumps({"stage": "training_primary_freeze_meaning"}))
    primary = train_variant(
        "freeze_meaning",
        use_choice_meaning=True,
        freeze_encoder=True,
        seed=args.seed,
        **common_kwargs,
    )
    print(json.dumps({"stage": "training_baseline_freeze_no_meaning"}))
    no_meaning = train_variant(
        "freeze_no_meaning",
        use_choice_meaning=False,
        freeze_encoder=True,
        seed=args.seed,
        **common_kwargs,
    )
    print(json.dumps({"stage": "training_finetune_meaning"}))
    finetune = train_variant(
        "finetune_meaning",
        use_choice_meaning=True,
        freeze_encoder=False,
        seed=args.seed,
        **common_kwargs,
    )

    print(json.dumps({"stage": "baselines"}))
    random_test = random_baseline(
        test_loader, args.random_baseline_trials, base_seed=args.seed
    )
    random_val = random_baseline(
        val_loader, args.random_baseline_trials, base_seed=args.seed + 1
    )
    frequency_test = frequency_baseline(train_rows, encoder, test_loader)
    frequency_val = frequency_baseline(train_rows, encoder, val_loader)

    print(json.dumps({"stage": "detailed_evaluation"}))
    primary_val_detail = detailed_evaluation(
        primary["model"], val_dataset, val_loader, args.max_misclassified
    )
    primary_test_detail = detailed_evaluation(
        primary["model"], test_dataset, test_loader, args.max_misclassified
    )
    no_meaning_test_detail = detailed_evaluation(
        no_meaning["model"], test_dataset, test_loader, 0
    )
    finetune_test_detail = detailed_evaluation(
        finetune["model"], test_dataset, test_loader, 0
    )

    print(json.dumps({"stage": "synthetic_check"}))
    synthetic_results = synthetic_check(
        primary["model"], audit["synthetic_rows"], encoder, choice_meaning_vocab
    )

    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        args.report_dir / "misclassified_validation.jsonl",
        primary_val_detail["misclassified"],
    )
    write_jsonl(
        args.report_dir / "misclassified_test.jsonl",
        primary_test_detail["misclassified"],
    )
    dump_json(args.report_dir / "synthetic_check.json", synthetic_results)
    write_jsonl(
        args.report_dir / "excluded_rows.jsonl",
        [
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "reason": "operation_mode_unknown",
            }
            for r in audit["excluded_rows"]
        ]
        + [
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "reason": "teacher_action_type_not_choice_card",
            }
            for r in audit["out_of_scope_rows"]
        ]
        + [
            {
                "decision_id": decision_row_id(r),
                "trajectory_id": r["trajectory_id"],
                "reason": "synthetic_nested_scenario_holdout",
            }
            for r in audit["synthetic_rows"]
        ],
    )

    choice_semantics_baseline = json.loads(
        args.choice_semantics_baseline_file.read_text(encoding="utf-8")
    )
    source_summary = json.loads(
        (args.source_dir / "summary.json").read_text(encoding="utf-8")
    )
    provenance = {
        "emulator_commit": source_summary.get("emulator_commit"),
        "emulator_dll_sha256": source_summary.get("emulator_dll_sha256"),
        "choice_semantics_baseline_version": choice_semantics_baseline.get(
            "baseline_id"
        ),
        "choice_semantics_lookup_sha256": choice_semantics_baseline.get(
            "choice_semantics_lookup", {}
        ).get("sha256"),
        "choice_semantics_origin_alias_sha256": choice_semantics_baseline.get(
            "origin_type_alias_lookup", {}
        ).get("sha256"),
        "source_choice_dataset_sha256": sha256_file(
            args.source_dir / "choice_teacher_data.jsonl"
        ),
        "training_commit": "not_a_git_repository (C:\\STS2_RL\\Training has no .git)",
        "policy_checkpoint_used_for_shared_encoder": str(args.policy_checkpoint),
    }

    config = {
        "seed": args.seed,
        "split_seed": args.split_seed,
        "source_dir": str(args.source_dir),
        "state_dim": encoder.state_dim,
        "card_vocab": encoder.vocab_size("card"),
        "choice_meaning_vocab": choice_meaning_vocab.size,
        "card_embedding_dim": card_embedding_dim,
        "choice_meaning_embedding_dim": args.choice_meaning_embedding_dim,
        "hidden_dim": hidden_dim,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "in_scope_decisions": audit["in_scope_count"],
        "in_scope_trajectories": audit["in_scope_trajectory_count"],
        "split_counts_decisions": audit["split_counts_decisions"],
    }

    checkpoint_payload = {
        "model_state": primary["model"].state_dict(),
        "config": config,
        "provenance": provenance,
        "dictionaries": dictionaries,
        "choice_meaning_dict": audit["choice_meaning_dict"],
        "use_choice_meaning": True,
        "freeze_encoder": True,
        "best_epoch": primary["best_epoch"],
        "best_validation_loss": primary["best_validation_loss"],
        "shared_encoder_keys_copied": primary["shared_encoder_keys_copied"],
    }
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint_payload, args.checkpoint_dir / "best.pt")

    summary = {
        "config": config,
        "provenance": provenance,
        "variants": {
            "primary_freeze_meaning": {
                "best_epoch": primary["best_epoch"],
                "best_validation_loss": primary["best_validation_loss"],
                "validation": primary_val_detail["aggregate"],
                "test": primary_test_detail["aggregate"],
            },
            "baseline_freeze_no_meaning": {
                "best_epoch": no_meaning["best_epoch"],
                "best_validation_loss": no_meaning["best_validation_loss"],
                "test": no_meaning_test_detail["aggregate"],
            },
            "encoder_comparison_finetune_meaning": {
                "best_epoch": finetune["best_epoch"],
                "best_validation_loss": finetune["best_validation_loss"],
                "test": finetune_test_detail["aggregate"],
            },
        },
        "non_parametric_baselines": {
            "random_candidate_selection": {
                "validation": random_val,
                "test": random_test,
            },
            "fixed_card_frequency_order": {
                "validation": frequency_val,
                "test": frequency_test,
            },
        },
        "primary_breakdowns": {
            "validation": {
                k: v for k, v in primary_val_detail.items() if k != "misclassified"
            },
            "test": {
                k: v for k, v in primary_test_detail.items() if k != "misclassified"
            },
        },
        "synthetic_check": synthetic_results,
        "dataset_audit": {
            "rl_eligible": audit["eligible_count"],
            "rl_excluded": audit["excluded_by_rl_count"],
            "synthetic_holdout": audit["synthetic_count"],
            "training_scope_excluded_choice_confirm": audit["out_of_scope_count"],
            "in_scope_for_training": audit["in_scope_count"],
            "in_scope_trajectories": audit["in_scope_trajectory_count"],
            "split_counts_trajectories": audit["split_counts_trajectories"],
            "split_counts_decisions": audit["split_counts_decisions"],
        },
        "training_time_seconds": time.perf_counter() - started_at,
        "checkpoint_path": str((args.checkpoint_dir / "best.pt").resolve()),
    }
    dump_json(args.report_dir / "metrics.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
