from __future__ import annotations

import argparse
import time
from pathlib import Path

from sts2_training.choice_data import DEFAULT_SOURCE_DIR, audit_and_split, choice_meaning_token
from sts2_training.choice_inference import ChoiceDecision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline smoke-test of ChoiceDecision against real and synthetic Choice decisions. Does NOT connect to any Combat adapter."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--checkpoint", type=Path, default=Path("checkpoints/choice_policy_8token_best/best.pt"))
    parser.add_argument("--split-seed", type=int, default=20260725)
    parser.add_argument("--count", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    decision = ChoiceDecision(args.checkpoint)
    audit = audit_and_split(args.source_dir, args.split_seed)

    latencies_ms: list[float] = []
    print("=== real test-split decisions ===")
    test_rows = [r for r in audit["in_scope_rows"] if audit["split_map"][r["trajectory_id"]] == "test"][: args.count]
    for row in test_rows:
        resolved = row.get("resolved") or {}
        start = time.perf_counter()
        result = decision(
            row["battle_state"],
            row["legal_actions"],
            resolved.get("operationMode"),
            resolved.get("normalizedChoiceOperation"),
            resolved.get("exceptionEntityKey"),
            row.get("remaining_select_count"),
        )
        latencies_ms.append((time.perf_counter() - start) * 1000.0)
        teacher_action_id = row["teacher_action"]["action_id"]
        print(
            {
                "trajectory_id": row["trajectory_id"],
                "meaning_token": choice_meaning_token(row),
                "teacher_action_id": teacher_action_id,
                "top1_action_id": result["top1_action_id"],
                "matched_teacher": result["top1_action_id"] == teacher_action_id,
                "top1_confidence": round(result["top1_confidence"], 4) if result["top1_confidence"] is not None else None,
                "confidence_margin": round(result["confidence_margin"], 4) if result["confidence_margin"] is not None else None,
                "fallback_reason": result["fallback_reason"],
            }
        )

    print()
    print("=== synthetic nested-choice scenario ===")
    for row in audit["synthetic_rows"]:
        resolved = row.get("resolved") or {}
        result = decision(
            row["battle_state"],
            row["legal_actions"],
            resolved.get("operationMode"),
            resolved.get("normalizedChoiceOperation"),
            resolved.get("exceptionEntityKey"),
            row.get("remaining_select_count"),
        )
        print(
            {
                "trajectory_id": row["trajectory_id"],
                "decision_index": row["decision_index"],
                "top1_action_id": result["top1_action_id"],
                "fallback_reason": result["fallback_reason"],
                "ran_without_exception": True,
            }
        )

    print()
    print("=== fallback triggers (synthetic edge cases) ===")
    print("unknown operationMode ->", decision({}, [{"action_type": "choice_card", "action_id": 0, "label": "X", "parameters": {"cardId": "STRIKE"}}], "unknown")["fallback_reason"])
    print("no choice_card candidates ->", decision({}, [{"action_type": "choice_confirm", "action_id": 0, "label": "Confirm", "parameters": {}}], "normalized")["fallback_reason"])

    if latencies_ms:
        latencies_ms.sort()
        n = len(latencies_ms)
        print()
        print(f"latency over {n} decisions: mean={sum(latencies_ms)/n:.4f}ms p50={latencies_ms[n//2]:.4f}ms max={latencies_ms[-1]:.4f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
