"""Extracts this task's 指示書9節 divergence log: every scenario where the Choice
Policy arm's chosen action differed from what Heuristic would have chosen at some Choice
decision (shadow-compared), with full per-divergence detail (board state, legal
candidates, Choice meaning, ranking/confidence, Heuristic action, post-choice state diff,
final outcome/HP, Policy-better/Heuristic-better/comparable classification). Read-only
over an already-produced combats.jsonl - does not re-run anything.

Run: python extract_choice_policy_divergences.py --in <stage_c_dir>/combats.jsonl --out <path>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize_action(a: "dict | None") -> "dict | None":
    if a is None:
        return None
    params = a.get("parameters") or {}
    return {"action_id": a.get("action_id"), "action_type": a.get("action_type"), "label": a.get("label"), "cardId": params.get("cardId")}


def summarize_state(engine_state: dict) -> dict:
    return {
        "hp": engine_state.get("hp"), "maxHp": engine_state.get("maxHp"), "block": engine_state.get("block"),
        "turnNumber": engine_state.get("turnNumber"), "combatRoundNumber": engine_state.get("combatRoundNumber"),
        "hand_ids": [c.get("id") for c in (engine_state.get("hand") or [])],
        "enemies": [{"id": e.get("id"), "hp": e.get("hp"), "isAlive": e.get("isAlive")} for e in (engine_state.get("enemies") or [])],
    }


def classify(cp_outcome: str, hc_outcome: str) -> str:
    if cp_outcome == "victory" and hc_outcome != "victory":
        return "policy_better"
    if hc_outcome == "victory" and cp_outcome != "victory":
        return "heuristic_better"
    return "comparable"


def extract_divergence_points(episode: dict) -> list[dict]:
    points = []
    for d in episode["decisions"]:
        if d.get("agrees_with_heuristic") is False:
            points.append({
                "source": "top_level_decision", "decision_index": d["decision_index"],
                "continuation_step_index": None,
                "choice_semantics": d.get("choice_semantics"),
                "legal_choice_candidates": [
                    summarize_action(a) for a in (d.get("legal_actions") or [])  # not present on top-level record; see combats.jsonl's own decision row
                ] if "legal_actions" in d else None,
                "choice_policy_ranking": (d.get("choice_policy_result") or {}).get("ranking"),
                "choice_policy_top1_confidence": (d.get("choice_policy_result") or {}).get("top1_confidence"),
                "choice_policy_confidence_margin": (d.get("choice_policy_result") or {}).get("confidence_margin"),
                "arm_action": summarize_action(d.get("chosen_action")),
                "heuristic_action": summarize_action(d.get("heuristic_shadow_action")),
            })
    for cc in episode["continuation_choices"]:
        if cc.get("agrees_with_heuristic") is False:
            points.append({
                "source": "action_continuation", "decision_index": cc["decision_index"],
                "continuation_step_index": cc["continuation_step_index"],
                "choice_semantics": {"emulator_fact": cc["emulator_fact"], "resolved": cc["resolved"]},
                "legal_choice_candidates": [summarize_action(a) for a in (cc.get("legal_actions") or []) if a.get("action_type") == "choice_card"],
                "choice_policy_ranking": (cc.get("choice_policy_result") or {}).get("ranking"),
                "choice_policy_top1_confidence": (cc.get("choice_policy_result") or {}).get("top1_confidence"),
                "choice_policy_confidence_margin": (cc.get("choice_policy_result") or {}).get("confidence_margin"),
                "board_state_before": summarize_state(cc["battle_state"]),
                "arm_action": summarize_action(cc.get("chosen_action")),
                "heuristic_action": summarize_action(cc.get("heuristic_shadow_action")),
            })
    points.sort(key=lambda p: (p["decision_index"], p["continuation_step_index"] if p["continuation_step_index"] is not None else -1))
    return points


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    rows = load_jsonl(args.in_path)
    out = []
    for r in rows:
        if r["status"] != "ok":
            continue
        cp = r["choice_policy_arm"]
        hc = r["heuristic_choice_arm"]
        points = extract_divergence_points(cp)
        if not points:
            continue
        out.append({
            "trajectory_id": r["trajectory_id"],
            "divergence_point_count": len(points),
            "divergence_points": points,
            "choice_policy_final_outcome": cp["final_outcome"], "choice_policy_final_hp": cp["final_hp"],
            "heuristic_choice_final_outcome": hc["final_outcome"], "heuristic_choice_final_hp": hc["final_hp"],
            "outcomes_differ": cp["final_outcome"] != hc["final_outcome"],
            "classification": classify(cp["final_outcome"], hc["final_outcome"]),
        })

    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"{len(out)} scenarios with at least one Choice action divergence -> {args.out}")
    from collections import Counter
    print("classification counts:", dict(Counter(o["classification"] for o in out)))


if __name__ == "__main__":
    main()
