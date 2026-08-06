from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sts2_training.inference import PolicyDecision, ValueDetermination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test PolicyDecision/ValueDetermination against a few exported decisions, without Emulator/pythonnet."
    )
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--policy-checkpoint", type=Path, required=True)
    parser.add_argument("--value-checkpoint", type=Path, required=True)
    parser.add_argument(
        "--split", choices=["train", "validation", "test"], default="test"
    )
    parser.add_argument("--count", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = PolicyDecision(args.policy_checkpoint)
    value = ValueDetermination(args.value_checkpoint)

    policy_latencies_ms: list[float] = []
    value_latencies_ms: list[float] = []
    fallback_count = 0
    shown = 0

    path = args.export_root / f"complete_{args.split}.jsonl"
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            observation = row["observation"]
            legal_actions = row["legal_actions"]

            start = time.perf_counter()
            policy_result = policy(observation, legal_actions)
            policy_latencies_ms.append((time.perf_counter() - start) * 1000.0)

            start = time.perf_counter()
            value_result = value(observation)
            value_latencies_ms.append((time.perf_counter() - start) * 1000.0)

            if policy_result["recommend_heuristic_fallback"]:
                fallback_count += 1

            if shown < 5:
                selected = legal_actions[policy_result["selected_action_index"]]
                print(
                    json.dumps(
                        {
                            "decision_id": row["decision_id"],
                            "teacher_action_label": row["teacher_action"].get("label"),
                            "policy_selected_label": selected.get("label"),
                            "policy_confidence": round(policy_result["confidence"], 4),
                            "recommend_heuristic_fallback": policy_result[
                                "recommend_heuristic_fallback"
                            ],
                            "value": {
                                "win_probability": round(
                                    value_result["win_probability"], 4
                                ),
                                "expected_final_hp": round(
                                    value_result["expected_final_hp"], 2
                                ),
                                "expected_remaining_decisions": round(
                                    value_result["expected_remaining_decisions"], 2
                                ),
                            },
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            shown += 1
            if shown >= args.count:
                break

    def _stats(values: list[float]) -> dict[str, float]:
        sorted_values = sorted(values)
        n = len(sorted_values)
        return {
            "mean_ms": round(sum(sorted_values) / max(1, n), 4),
            "p50_ms": round(sorted_values[n // 2], 4) if n else 0.0,
            "p95_ms": round(sorted_values[int(n * 0.95)], 4) if n else 0.0,
            "max_ms": round(sorted_values[-1], 4) if n else 0.0,
        }

    summary = {
        "decisions_evaluated": shown,
        "choice_card_fallback_count": fallback_count,
        "policy_latency": _stats(policy_latencies_ms),
        "value_latency": _stats(value_latencies_ms),
        "policy_provenance": policy.provenance,
        "value_provenance": value.provenance,
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
