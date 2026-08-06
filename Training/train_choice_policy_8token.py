from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import DataLoader

from sts2_training.choice_data import (
    CHOICE_MEANING_MERGE_MAP,
    CHOICE_MEANING_MERGE_MAP_VERSION,
    DEFAULT_SOURCE_DIR,
    ChoiceDecisionDataset,
    audit_and_split,
    build_merged_vocab,
    choice_card_candidates,
    choice_meaning_token,
    collate_choice,
    rows_for_split,
    sha256_file,
)
from sts2_training.encoding import ExportEncoder
from train_choice_policy import (
    DEFAULT_CHOICE_SEMANTICS_BASELINE,
    DEFAULT_POLICY_CHECKPOINT,
    bucket_accuracy,
    frequency_baseline,
    random_baseline,
    rank_metrics_from_scores,
    run_epoch,
    train_variant,
)
from train_choice_policy import (
    synthetic_check as _synthetic_check_fn,
)

SPLITS = ("train", "validation", "test")
SEEDS = [20260725, 20260726, 20260727]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="3-seed evaluation of the adopted 8-token (merged) Choice Policy configuration."
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
    parser.add_argument(
        "--exports-dir", type=Path, default=Path("exports/choice_policy_v1")
    )
    parser.add_argument("--split-seed", type=int, default=20260725)
    parser.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--choice-meaning-embedding-dim", type=int, default=8)
    parser.add_argument("--random-baseline-trials", type=int, default=200)
    parser.add_argument("--checkpoint-dir-root", type=Path, default=Path("checkpoints"))
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/choice_policy_8token_3seed")
    )
    return parser.parse_args()


class _Args:
    def __init__(self, ns: argparse.Namespace) -> None:
        self.epochs = ns.epochs
        self.patience = ns.patience
        self.min_delta = ns.min_delta
        self.lr = ns.lr
        self.choice_meaning_embedding_dim = ns.choice_meaning_embedding_dim


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def mean_std(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "values": values,
    }


def main() -> int:
    args = parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    policy_checkpoint = torch.load(
        args.policy_checkpoint, map_location="cpu", weights_only=False
    )
    dictionaries = policy_checkpoint["dictionaries"]
    encoder = ExportEncoder(dictionaries)
    card_embedding_dim = int(
        policy_checkpoint["model_state"]["card_embedding.weight"].shape[1]
    )
    hidden_dim = int(policy_checkpoint["model_state"]["state_net.0.weight"].shape[0])

    audit = audit_and_split(args.source_dir, args.split_seed)
    all_rows = audit["in_scope_rows"]
    split_map = audit["split_map"]
    source_13_token_dict = audit["choice_meaning_dict"]
    merged_dict, merged_vocab = build_merged_vocab(
        source_13_token_dict, CHOICE_MEANING_MERGE_MAP
    )

    datasets = {
        s: ChoiceDecisionDataset(
            rows_for_split(all_rows, split_map, s), encoder, merged_vocab
        )
        for s in SPLITS
    }
    assert all(
        d.excluded_unknown_meaning_count == 0 for d in datasets.values()
    ), "unexpected __UNKNOWN__ meaning rows in scope"
    loaders_eval = {
        s: DataLoader(
            datasets[s],
            batch_size=args.batch_size,
            shuffle=False,
            collate_fn=collate_choice,
        )
        for s in SPLITS
    }

    train_rows_for_freq = rows_for_split(all_rows, split_map, "train")

    sargs = _Args(args)
    per_seed_results: dict[int, dict[str, Any]] = {}
    for seed in args.seeds:
        print(json.dumps({"stage": f"training_seed_{seed}"}))
        train_loader_shuffled = DataLoader(
            datasets["train"],
            batch_size=args.batch_size,
            shuffle=True,
            collate_fn=collate_choice,
        )
        result = train_variant(
            f"freeze_merged8_seed_{seed}",
            use_choice_meaning=True,
            freeze_encoder=True,
            seed=seed,
            train_loader=train_loader_shuffled,
            val_loader=loaders_eval["validation"],
            policy_model_state=policy_checkpoint["model_state"],
            state_dim=encoder.state_dim,
            card_vocab=encoder.vocab_size("card"),
            choice_meaning_vocab=merged_vocab.size,
            card_embedding_dim=card_embedding_dim,
            args=sargs,
        )
        model = result["model"]
        model.eval()

        split_metrics = {}
        for split in SPLITS:
            m = run_epoch(model, loaders_eval[split])
            split_metrics[split] = m

        # per-category (merged token) and per-candidate-count accuracy on test
        test_ranks: list[int] = []
        test_candidate_counts: list[int] = []
        test_tokens: list[str] = []
        offset = 0
        with torch.no_grad():
            for batch in loaders_eval["test"]:
                logits = model(
                    batch["state"],
                    batch["card_ids"],
                    batch["choice_meaning_id"],
                    batch["remaining_select_count"],
                )
                metrics = rank_metrics_from_scores(
                    logits, batch["candidate_mask"], batch["teacher_index"]
                )
                batch_size = batch["state"].shape[0]
                for i in range(batch_size):
                    row = datasets["test"].rows[offset + i]
                    test_ranks.append(int(metrics["ranks"][i].item()))
                    test_candidate_counts.append(len(choice_card_candidates(row)))
                    raw_token = choice_meaning_token(row)
                    merged_token_name = CHOICE_MEANING_MERGE_MAP.get(
                        raw_token, raw_token
                    )
                    test_tokens.append(merged_token_name)
                offset += batch_size
        top1_hits = [r == 1 for r in test_ranks]
        by_category = bucket_accuracy(test_tokens, top1_hits)
        by_candidate_count = bucket_accuracy(test_candidate_counts, top1_hits)

        synthetic_results = _synthetic_check_fn(
            model, audit["synthetic_rows"], encoder, merged_vocab
        )

        per_seed_results[seed] = {
            "best_epoch": result["best_epoch"],
            "best_validation_loss": result["best_validation_loss"],
            "train": split_metrics["train"],
            "validation": split_metrics["validation"],
            "test": split_metrics["test"],
            "by_category_test": by_category,
            "by_candidate_count_test": by_candidate_count,
            "synthetic_check": synthetic_results,
            "shared_encoder_keys_copied": result["shared_encoder_keys_copied"],
        }
        per_seed_results[seed][
            "_model_state"
        ] = model.state_dict()  # kept only in memory, not dumped to JSON

    # ---- baselines (seed-independent) ----
    print(json.dumps({"stage": "baselines"}))
    random_test = random_baseline(
        loaders_eval["test"], args.random_baseline_trials, base_seed=args.split_seed
    )
    frequency_test = frequency_baseline(
        train_rows_for_freq, encoder, loaders_eval["test"]
    )

    # ---- aggregate across seeds ----
    test_top1 = [per_seed_results[s]["test"]["top_1_accuracy"] for s in args.seeds]
    test_top3 = [per_seed_results[s]["test"]["top_3_accuracy"] for s in args.seeds]
    test_top5 = [per_seed_results[s]["test"]["top_5_accuracy"] for s in args.seeds]
    test_mrr = [per_seed_results[s]["test"]["mrr"] for s in args.seeds]
    test_illegal = [
        per_seed_results[s]["test"]["illegal_prediction_rate"] for s in args.seeds
    ]
    aggregate = {
        "test_top_1_accuracy": mean_std(test_top1),
        "test_top_3_accuracy": mean_std(test_top3),
        "test_top_5_accuracy": mean_std(test_top5),
        "test_mrr": mean_std(test_mrr),
        "test_illegal_prediction_rate": mean_std(test_illegal),
    }

    # ---- stability checks (section 4 of the instruction) ----
    all_synthetic_ok = all(
        all(
            r.get("ran_without_exception", True) and "skipped" not in r
            for r in per_seed_results[s]["synthetic_check"]
        )
        for s in args.seeds
    )
    # category collapse check: any category with 0% top-1 accuracy in ALL 3 seeds simultaneously (n>=10 only, to ignore noise)
    category_names = sorted(
        {cat for s in args.seeds for cat in per_seed_results[s]["by_category_test"]}
    )
    collapsed_categories = []
    for cat in category_names:
        per_seed_acc = []
        for s in args.seeds:
            entry = per_seed_results[s]["by_category_test"].get(cat)
            if entry is not None and not entry["reference_only_low_n"]:
                per_seed_acc.append(entry["accuracy"])
        if per_seed_acc and all(acc == 0.0 for acc in per_seed_acc):
            collapsed_categories.append(cat)

    stability = {
        "top1_range_across_seeds": max(test_top1) - min(test_top1),
        "illegal_rate_all_zero": all(v == 0.0 for v in test_illegal),
        "beats_random_baseline_on_average": aggregate["test_top_1_accuracy"]["mean"]
        > random_test["top_1_accuracy"],
        "beats_frequency_baseline_on_average": aggregate["test_top_1_accuracy"]["mean"]
        > frequency_test["top_1_accuracy"],
        "synthetic_ok_all_seeds": all_synthetic_ok,
        "collapsed_categories_ge10n_all_seeds_zero": collapsed_categories,
    }

    # ---- checkpoint selection: validation MRR -> validation top-1 -> illegal rate (validation, not test) ----
    def selection_key(seed: int) -> tuple[float, float, float]:
        v = per_seed_results[seed]["validation"]
        return (-v["mrr"], -v["top_1_accuracy"], v["illegal_prediction_rate"])

    best_seed = min(args.seeds, key=selection_key)
    print(json.dumps({"stage": "selected_best_seed", "best_seed": best_seed}))

    # ---- save winning checkpoint with full provenance ----
    choice_semantics_baseline = json.loads(
        args.choice_semantics_baseline_file.read_text(encoding="utf-8")
    )
    source_summary = json.loads(
        (args.source_dir / "summary.json").read_text(encoding="utf-8")
    )
    merge_map_path = args.exports_dir / "merge_map.v1.json"
    split_manifest_path = args.exports_dir / "split_manifest.jsonl"

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
        "merge_map_version": CHOICE_MEANING_MERGE_MAP_VERSION,
        "merge_map_sha256": sha256_file(merge_map_path),
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "seed": best_seed,
        "training_commit": "not_a_git_repository (C:\\STS2_RL\\Training has no .git)",
        "policy_checkpoint_used_for_shared_encoder": str(args.policy_checkpoint),
        "selection_criteria": "validation MRR, then validation top-1, then illegal rate (validation, not test)",
    }
    config = {
        "seed": best_seed,
        "split_seed": args.split_seed,
        "source_dir": str(args.source_dir),
        "state_dim": encoder.state_dim,
        "card_vocab": encoder.vocab_size("card"),
        "choice_meaning_vocab": merged_vocab.size,
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
        "meaning_category_count": merged_vocab.size,
    }
    checkpoint_dir = args.checkpoint_dir_root / "choice_policy_8token_best"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state": per_seed_results[best_seed]["_model_state"],
            "config": config,
            "provenance": provenance,
            "dictionaries": dictionaries,
            "choice_meaning_dict": merged_dict,
            "merge_map": CHOICE_MEANING_MERGE_MAP,
            "use_choice_meaning": True,
            "freeze_encoder": True,
            "best_epoch": per_seed_results[best_seed]["best_epoch"],
            "best_validation_loss": per_seed_results[best_seed]["best_validation_loss"],
        },
        checkpoint_dir / "best.pt",
    )

    # ---- write reports (strip in-memory model states before dumping) ----
    per_seed_dump = {
        str(s): {k: v for k, v in per_seed_results[s].items() if k != "_model_state"}
        for s in args.seeds
    }
    summary = {
        "seeds": args.seeds,
        "split_seed": args.split_seed,
        "config_shared": config,
        "per_seed": per_seed_dump,
        "aggregate": aggregate,
        "stability": stability,
        "baselines": {
            "random_candidate_selection_test": random_test,
            "fixed_card_frequency_order_test": frequency_test,
        },
        "best_seed_selected": best_seed,
        "best_checkpoint_path": str((checkpoint_dir / "best.pt").resolve()),
        "provenance": provenance,
    }
    dump_json(args.report_dir / "metrics.json", summary)
    print(
        json.dumps(
            {
                "stage": "done",
                "aggregate": aggregate,
                "stability": stability,
                "best_seed": best_seed,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
