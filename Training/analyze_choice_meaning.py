from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import DataLoader

from sts2_training.choice_data import (
    DEFAULT_SOURCE_DIR,
    ChoiceDecisionDataset,
    audit_and_split,
    build_dictionary,
    choice_card_candidates,
    choice_meaning_token,
    collate_choice,
    decision_row_id,
    rows_for_split,
)
from sts2_training.encoding import ExportEncoder, Vocab
from sts2_training.model import ChoicePolicyNet
from train_choice_policy import (
    DEFAULT_POLICY_CHECKPOINT,
    aggregate_rank_metrics,
    build_model,
    rank_metrics_from_scores,
    train_variant,
)

SPLITS = ("train", "validation", "test")
MEANING_CHECKPOINT = Path("checkpoints/choice_policy_seed_20260725/best.pt")

# Section 5 merge proposal - grounded in section3/section4 findings from this same run:
#   - retrieve_to_hand (n=79) and retrieve_to_draw_pile_top (n=24) share similar avg_candidate_count
#     (~7-8, notably higher than other operations) and both show meaning-vs-no-meaning delta=0 on
#     test -> merged into "retrieve".
#   - return_to_draw_pile_top is NOT merged into "retrieve" despite the similar name: its
#     avg_candidate_count (4.8) and delta sign (-0.09) differ from the retrieve_* pair.
#   - apply_effect_in_place/select_for_power_association/upgrade/transform_to_specific_card are each
#     individually tiny (n=2-20, mostly 0 test rows, 1-2 distinct scenarios) -> merged into a single
#     "other_normalized_rare" catch-all so they get at least some pooled signal instead of near-empty
#     per-token embeddings.
#   - discard (n=238), exhaust (n=40), transform (n=35), add_generated_to_hand (n=73) are each
#     well-populated with distinct behavior (see section3) -> kept standalone.
#   - relic:GAMBLING_CHIP is explicitly NOT merged per this task's instructions (different evaluation
#     criteria from ordinary discard/exhaust judgement calls), also well-populated (n=96) on its own.
MERGE_MAP = {
    "retrieve_to_hand": "retrieve",
    "retrieve_to_draw_pile_top": "retrieve",
    "apply_effect_in_place": "other_normalized_rare",
    "select_for_power_association": "other_normalized_rare",
    "upgrade": "other_normalized_rare",
    "transform_to_specific_card": "other_normalized_rare",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline analysis of why meaning/no-meaning Choice Policy variants scored similarly."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument(
        "--policy-checkpoint", type=Path, default=DEFAULT_POLICY_CHECKPOINT
    )
    parser.add_argument("--meaning-checkpoint", type=Path, default=MEANING_CHECKPOINT)
    parser.add_argument("--split-seed", type=int, default=20260725)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/choice_meaning_analysis")
    )
    return parser.parse_args()


class _Args:
    """Minimal stand-in for train_choice_policy's argparse Namespace, for reusing train_variant()."""

    def __init__(
        self, ns: argparse.Namespace, choice_meaning_embedding_dim: int
    ) -> None:
        self.epochs = ns.epochs
        self.patience = ns.patience
        self.min_delta = ns.min_delta
        self.lr = ns.lr
        self.choice_meaning_embedding_dim = choice_meaning_embedding_dim


def predict_all(
    model: ChoicePolicyNet, dataset: ChoiceDecisionDataset, loader: DataLoader
) -> dict[str, dict[str, Any]]:
    model.eval()
    predictions: dict[str, dict[str, Any]] = {}
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
            batch_size = batch["state"].shape[0]
            for i in range(batch_size):
                row = dataset.rows[offset + i]
                candidates = choice_card_candidates(row)
                predicted_index = int(metrics["predicted_index"][i].item())
                predictions[decision_row_id(row)] = {
                    "rank": int(metrics["ranks"][i].item()),
                    "confidence": float(metrics["confidence"][i].item()),
                    "predicted_label": candidates[predicted_index]["label"],
                }
            offset += batch_size
    return predictions


def candidate_label_set(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(c["label"] for c in choice_card_candidates(row)))


def teacher_label(row: dict[str, Any]) -> str:
    candidates = choice_card_candidates(row)
    teacher_action_id = row["teacher_action"]["action_id"]
    return next(c["label"] for c in candidates if c["action_id"] == teacher_action_id)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


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

    meaning_checkpoint = torch.load(
        args.meaning_checkpoint, map_location="cpu", weights_only=False
    )
    choice_meaning_dict = meaning_checkpoint["choice_meaning_dict"]
    choice_meaning_vocab = ExportEncoder._load_vocab(choice_meaning_dict)
    meaning_config = meaning_checkpoint["config"]
    assert (
        meaning_config["split_seed"] == args.split_seed
    ), "must reuse the exact split that produced the approved checkpoint"

    audit = audit_and_split(args.source_dir, args.split_seed)
    all_rows = audit["in_scope_rows"]
    split_map = audit["split_map"]

    datasets = {
        s: ChoiceDecisionDataset(
            rows_for_split(all_rows, split_map, s), encoder, choice_meaning_vocab
        )
        for s in SPLITS
    }
    # shuffle=False everywhere so predict_all's offset-based row lookup matches dataset.rows order;
    # a separately-shuffled train loader is used only for the retraining call below (matches how
    # train_choice_policy.py trains), never for prediction extraction.
    loaders = {
        s: DataLoader(
            datasets[s],
            batch_size=args.batch_size,
            shuffle=False,
            collate_fn=collate_choice,
        )
        for s in SPLITS
    }
    train_loader_shuffled = DataLoader(
        datasets["train"],
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_choice,
    )

    # ---- Model 1: meaning (13-token), reuse the already-approved checkpoint, no retraining ----
    meaning_model = build_model(
        state_dim=encoder.state_dim,
        card_vocab=encoder.vocab_size("card"),
        choice_meaning_vocab=choice_meaning_vocab.size,
        card_embedding_dim=card_embedding_dim,
        choice_meaning_embedding_dim=meaning_config["choice_meaning_embedding_dim"],
        hidden_dim=meaning_config["hidden_dim"],
        use_choice_meaning=True,
    )
    meaning_model.load_state_dict(meaning_checkpoint["model_state"])

    # ---- Model 2: no-meaning, same seed/split/frozen-encoder as the approved comparison (retrained; weights weren't persisted before) ----
    sargs = _Args(args, meaning_config["choice_meaning_embedding_dim"])
    common_kwargs = {
        "train_loader": train_loader_shuffled,
        "val_loader": loaders["validation"],
        "policy_model_state": policy_checkpoint["model_state"],
        "state_dim": encoder.state_dim,
        "card_vocab": encoder.vocab_size("card"),
        "card_embedding_dim": card_embedding_dim,
        "args": sargs,
    }
    print(json.dumps({"stage": "retraining_no_meaning_for_prediction_extraction"}))
    no_meaning_result = train_variant(
        "freeze_no_meaning",
        use_choice_meaning=False,
        freeze_encoder=True,
        seed=args.seed,
        choice_meaning_vocab=choice_meaning_vocab.size,
        **common_kwargs,
    )
    no_meaning_model = no_meaning_result["model"]

    predictions = {"meaning": {}, "no_meaning": {}}
    for split in SPLITS:
        predictions["meaning"][split] = predict_all(
            meaning_model, datasets[split], loaders[split]
        )
        predictions["no_meaning"][split] = predict_all(
            no_meaning_model, datasets[split], loaders[split]
        )

    test_meaning_top1 = sum(
        1 for v in predictions["meaning"]["test"].values() if v["rank"] == 1
    ) / len(predictions["meaning"]["test"])
    test_no_meaning_top1 = sum(
        1 for v in predictions["no_meaning"]["test"].values() if v["rank"] == 1
    ) / len(predictions["no_meaning"]["test"])
    sanity_check = {
        "test_top1_meaning": test_meaning_top1,
        "test_top1_no_meaning": test_no_meaning_top1,
        "expected_from_approved_report": {"meaning": 0.6029, "no_meaning": 0.6176},
        "matches_approved_report_within_0.01": abs(test_meaning_top1 - 0.6029) < 0.01
        and abs(test_no_meaning_top1 - 0.6176) < 0.01,
    }
    print(json.dumps({"sanity_check": sanity_check}))

    # ================================================================
    # Build one flat per-decision record set (all splits) for analysis
    # ================================================================
    records: list[dict[str, Any]] = []
    for split in SPLITS:
        for decision_id, row in {
            decision_row_id(r): r for r in rows_for_split(all_rows, split_map, split)
        }.items():
            resolved = row.get("resolved") or {}
            m = predictions["meaning"][split][decision_id]
            nm = predictions["no_meaning"][split][decision_id]
            records.append(
                {
                    "decision_id": decision_id,
                    "trajectory_id": row["trajectory_id"],
                    "scenario_hash": row.get("scenario_hash"),
                    "source": row.get("source"),
                    "split": split,
                    "candidate_count": len(choice_card_candidates(row)),
                    "candidate_label_set": candidate_label_set(row),
                    "operation_mode": resolved.get("operationMode"),
                    "meaning_token": choice_meaning_token(row),
                    "remaining_select_count": row.get("remaining_select_count"),
                    "teacher_label": teacher_label(row),
                    "meaning_rank": m["rank"],
                    "meaning_confidence": m["confidence"],
                    "meaning_predicted_label": m["predicted_label"],
                    "no_meaning_rank": nm["rank"],
                    "no_meaning_confidence": nm["confidence"],
                    "no_meaning_predicted_label": nm["predicted_label"],
                }
            )

    # ================================================================
    # Section 2: per-test-decision 4-way comparison
    # ================================================================
    test_records = [r for r in records if r["split"] == "test"]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in test_records:
        m_correct = r["meaning_rank"] == 1
        nm_correct = r["no_meaning_rank"] == 1
        if m_correct and nm_correct:
            key = "both_correct"
        elif m_correct and not nm_correct:
            key = "meaning_only_correct"
        elif nm_correct and not m_correct:
            key = "no_meaning_only_correct"
        else:
            key = "both_wrong"
        buckets[key].append(r)

    section2 = {
        "counts": {k: len(v) for k, v in buckets.items()},
        "representative_examples": {
            k: [
                {
                    "decision_id": r["decision_id"],
                    "candidate_count": r["candidate_count"],
                    "operation_mode": r["operation_mode"],
                    "meaning_token": r["meaning_token"],
                    "remaining_select_count": r["remaining_select_count"],
                    "teacher_label": r["teacher_label"],
                    "meaning_rank": r["meaning_rank"],
                    "meaning_confidence": round(r["meaning_confidence"], 4),
                    "meaning_predicted_label": r["meaning_predicted_label"],
                    "no_meaning_rank": r["no_meaning_rank"],
                    "no_meaning_confidence": round(r["no_meaning_confidence"], 4),
                    "no_meaning_predicted_label": r["no_meaning_predicted_label"],
                }
                for r in v[:4]
            ]
            for k, v in buckets.items()
        },
    }

    # ================================================================
    # Section 3: per meaning-token analysis (13 dictionary tokens)
    # ================================================================
    token_names = [e["token"] for e in choice_meaning_dict["entries"]]
    section3: dict[str, Any] = {}
    for token in token_names:
        token_records_all = [
            r for r in records if (r["meaning_token"] or "__UNKNOWN__") == token
        ]
        token_records_test = [r for r in token_records_all if r["split"] == "test"]
        split_counts = Counter(r["split"] for r in token_records_all)
        n_all = len(token_records_all)
        scenario_count = len({r["trajectory_id"] for r in token_records_all})
        avg_candidates = (
            (sum(r["candidate_count"] for r in token_records_all) / n_all)
            if n_all
            else None
        )
        top1_test = (
            (
                sum(1 for r in token_records_test if r["meaning_rank"] == 1)
                / len(token_records_test)
            )
            if token_records_test
            else None
        )
        mrr_test = (
            (
                sum(1.0 / r["meaning_rank"] for r in token_records_test)
                / len(token_records_test)
            )
            if token_records_test
            else None
        )
        top1_test_no_meaning = (
            (
                sum(1 for r in token_records_test if r["no_meaning_rank"] == 1)
                / len(token_records_test)
            )
            if token_records_test
            else None
        )
        teacher_dist = Counter(r["teacher_label"] for r in token_records_all)
        candidate_sets = Counter(r["candidate_label_set"] for r in token_records_all)
        duplicate_rate = (
            (sum(c for c in candidate_sets.values() if c > 1) / n_all)
            if n_all
            else None
        )  # fraction of rows whose exact candidate set recurs elsewhere within this token
        section3[token] = {
            "split_counts": dict(split_counts),
            "total": n_all,
            "scenario_count": scenario_count,
            "avg_candidate_count": (
                round(avg_candidates, 2) if avg_candidates is not None else None
            ),
            "top1_test_meaning_model": (
                round(top1_test, 4) if top1_test is not None else None
            ),
            "top1_test_no_meaning_model": (
                round(top1_test_no_meaning, 4)
                if top1_test_no_meaning is not None
                else None
            ),
            "meaning_minus_no_meaning_top1_delta": (
                round(top1_test - top1_test_no_meaning, 4)
                if top1_test is not None and top1_test_no_meaning is not None
                else None
            ),
            "mrr_test_meaning_model": (
                round(mrr_test, 4) if mrr_test is not None else None
            ),
            "test_n_reference_only_low_n": len(token_records_test) < 10,
            "teacher_card_distribution_top5": teacher_dist.most_common(5),
            "distinct_candidate_sets": len(candidate_sets),
            "candidate_set_duplicate_rate": (
                round(duplicate_rate, 4) if duplicate_rate is not None else None
            ),
        }

    # ================================================================
    # Section 4: information-redundancy / leakage checks
    # ================================================================
    # 4a. operation -> candidate-set almost fixed?
    op_key = lambda r: r["meaning_token"] or "__UNKNOWN__"
    sets_per_op: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        sets_per_op[op_key(r)][r["candidate_label_set"]] += 1
    op_fixed_set_ratio = {
        token: {
            "distinct_sets": len(counter),
            "total": sum(counter.values()),
            "top_set_share": (
                round(max(counter.values()) / sum(counter.values()), 4)
                if counter
                else None
            ),
        }
        for token, counter in sets_per_op.items()
    }

    # 4b. a single card always chosen within an operation?
    teacher_per_op: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        teacher_per_op[op_key(r)][r["teacher_label"]] += 1
    always_same_card = {
        token: {
            "top_card": counter.most_common(1)[0][0],
            "share": round(counter.most_common(1)[0][1] / sum(counter.values()), 4),
        }
        for token, counter in teacher_per_op.items()
        if counter and (counter.most_common(1)[0][1] / sum(counter.values())) >= 0.8
    }

    # 4c. candidate-set alone -> operation inferable? (purity of token given exact candidate set)
    op_given_set: dict[tuple, Counter] = defaultdict(Counter)
    for r in records:
        op_given_set[r["candidate_label_set"]][op_key(r)] += 1
    n_sets = len(op_given_set)
    n_pure_sets = sum(1 for counter in op_given_set.values() if len(counter) == 1)
    weighted_purity = sum(
        max(counter.values()) for counter in op_given_set.values()
    ) / len(records)

    # 4e. split leakage: any scenario_hash repeated, or any trajectory split across multiple splits?
    hash_to_splits: dict[str, set[str]] = defaultdict(set)
    traj_to_splits: dict[str, set[str]] = defaultdict(set)
    for r in records:
        if r["scenario_hash"]:
            hash_to_splits[r["scenario_hash"]].add(r["split"])
        traj_to_splits[r["trajectory_id"]].add(r["split"])
    hash_leak = {h: sorted(s) for h, s in hash_to_splits.items() if len(s) > 1}
    traj_leak = {t: sorted(s) for t, s in traj_to_splits.items() if len(s) > 1}

    # 4f. action_continuation duplication within a trajectory
    continuation_counts = Counter(r["source"] for r in records)
    traj_decision_counts = Counter(r["trajectory_id"] for r in records)
    multi_decision_trajectories = sum(1 for c in traj_decision_counts.values() if c > 1)

    section4 = {
        "candidate_set_almost_fixed_per_operation": op_fixed_set_ratio,
        "operations_with_dominant_single_teacher_card_ge_80pct": always_same_card,
        "candidate_set_purity": {
            "distinct_candidate_sets": n_sets,
            "sets_mapping_to_exactly_one_operation": n_pure_sets,
            "pure_set_fraction": round(n_pure_sets / n_sets, 4) if n_sets else None,
            "weighted_purity_across_all_decisions": round(weighted_purity, 4),
            "interpretation": "weighted_purity = fraction of decisions whose exact candidate-card-set is dominated by a single operation token; high values mean candidate identity alone (visible to the no-meaning model via card_embedding) already predicts the operation, explaining the small meaning-vs-no-meaning gap.",
        },
        "split_leakage_check": {
            "scenario_hash_appearing_in_multiple_splits": hash_leak,
            "trajectory_id_appearing_in_multiple_splits": traj_leak,
            "confirmed_no_leakage": len(hash_leak) == 0 and len(traj_leak) == 0,
        },
        "action_continuation_duplication": {
            "source_counts": dict(continuation_counts),
            "trajectories_with_multiple_in_scope_decisions": multi_decision_trajectories,
            "note": "multiple decisions per trajectory are expected for multi-step/multi-select choices (e.g. discard N cards one at a time); split is by trajectory_id so these can never straddle splits (see split_leakage_check).",
        },
    }

    dump_json(args.report_dir / "records.json", records)
    dump_json(args.report_dir / "section2_pairwise_comparison.json", section2)
    dump_json(args.report_dir / "section3_per_token_analysis.json", section3)
    dump_json(args.report_dir / "section4_redundancy_checks.json", section4)
    dump_json(args.report_dir / "sanity_check.json", sanity_check)

    print(json.dumps({"stage": "sections_2_3_4_written"}, indent=2))
    print(json.dumps({"section2_counts": section2["counts"]}, indent=2))

    # ================================================================
    # Section 5/6: merged-token dictionary + one small ablation training
    # (same seed/split/frozen encoder as the other two variants; see MERGE_MAP above for rationale)
    # ================================================================
    merged_token_names = sorted(
        {MERGE_MAP.get(t, t) for t in token_names if t != "__UNKNOWN__"}
    )
    merged_dict = build_dictionary("choice_meaning_merged", merged_token_names)
    merged_id_by_token = {e["token"]: e["id"] for e in merged_dict["entries"]}
    raw_token_to_id = {
        raw: merged_id_by_token[MERGE_MAP.get(raw, raw)]
        for raw in token_names
        if raw != "__UNKNOWN__"
    }
    merged_vocab_raw_keyed = Vocab(
        token_to_id=raw_token_to_id, size=len(merged_dict["entries"])
    )

    merged_datasets = {
        s: ChoiceDecisionDataset(
            rows_for_split(all_rows, split_map, s), encoder, merged_vocab_raw_keyed
        )
        for s in SPLITS
    }
    merged_loaders = {
        s: DataLoader(
            merged_datasets[s],
            batch_size=args.batch_size,
            shuffle=False,
            collate_fn=collate_choice,
        )
        for s in SPLITS
    }
    merged_train_loader_shuffled = DataLoader(
        merged_datasets["train"],
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_choice,
    )

    print(
        json.dumps(
            {
                "stage": "training_merged_meaning_ablation",
                "merged_tokens": merged_token_names,
            }
        )
    )
    merged_result = train_variant(
        "freeze_merged_meaning",
        use_choice_meaning=True,
        freeze_encoder=True,
        seed=args.seed,
        train_loader=merged_train_loader_shuffled,
        val_loader=merged_loaders["validation"],
        policy_model_state=policy_checkpoint["model_state"],
        state_dim=encoder.state_dim,
        card_vocab=encoder.vocab_size("card"),
        choice_meaning_vocab=merged_vocab_raw_keyed.size,
        card_embedding_dim=card_embedding_dim,
        args=sargs,
    )
    merged_model = merged_result["model"]
    merged_model.eval()
    merged_test_ranks: list[torch.Tensor] = []
    merged_test_illegal: list[torch.Tensor] = []
    with torch.no_grad():
        for batch in merged_loaders["test"]:
            logits = merged_model(
                batch["state"],
                batch["card_ids"],
                batch["choice_meaning_id"],
                batch["remaining_select_count"],
            )
            m = rank_metrics_from_scores(
                logits, batch["candidate_mask"], batch["teacher_index"]
            )
            merged_test_ranks.append(m["ranks"])
            merged_test_illegal.append(m["illegal"])
    merged_test_metrics = aggregate_rank_metrics(
        torch.cat(merged_test_ranks), torch.cat(merged_test_illegal)
    )

    section5_6 = {
        "merge_map": MERGE_MAP,
        "merged_token_count": len(merged_token_names),
        "merged_tokens": merged_token_names,
        "three_way_test_comparison": {
            "meaning_13_token": {
                "top_1_accuracy": test_meaning_top1,
                "best_epoch": None,
            },
            "no_meaning": {
                "top_1_accuracy": test_no_meaning_top1,
                "best_epoch": no_meaning_result["best_epoch"],
            },
            "meaning_merged_9_token": {
                **merged_test_metrics,
                "best_epoch": merged_result["best_epoch"],
            },
        },
    }
    dump_json(args.report_dir / "section5_6_merge_and_ablation.json", section5_6)
    print(
        json.dumps(
            {
                "stage": "section5_6_written",
                "three_way_test_top1": {
                    "meaning_13token": round(test_meaning_top1, 4),
                    "no_meaning": round(test_no_meaning_top1, 4),
                    "meaning_merged": round(merged_test_metrics["top_1_accuracy"], 4),
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
