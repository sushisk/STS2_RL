"""Re-runs ONLY the 3 scenarios that hit max_decisions=60 during Stage C
(Combat/evaluation/reports/choice_policy_online_eval_stage_c/) at max_decisions=150,
per this task's "max_decisions確認" section - purely a normal-completion check, not a
re-run of the adoption decision itself. shadow comparison (heuristic agreement logging)
is disabled since it isn't needed here. Nothing else changes: same code
(choice_policy_agent.py/choice_policy_online_eval.py, unmodified - this script only
calls their existing functions), same checkpoints, same lookup, same 3-scenario subset
of the already-fixed 30-scenario manifest, same max_wall_seconds (90.0 default - the
observed wall time at 60 decisions was well under that, so raising max_decisions alone
does not require raising the wall-clock budget too).

Run: python choice_policy_max_decisions_recheck.py
"""

from __future__ import annotations

import json
from pathlib import Path

from choice_policy_online_eval import (
    verify_emulator_baseline, load_jsonl, run_scenario_ab, _sha256_file,
)
from legacy.choice_policy_agent import ChoicePolicyAgent, build_choice_decision, DEFAULT_CHOICE_POLICY_CHECKPOINT
from choice_semantics import ChoiceSemanticsTable
from legacy.policy_agent import build_policy_agent

_HERE = Path(__file__).resolve().parent
MANIFEST_PATH = _HERE / "choice_policy_max_decisions_recheck_manifest.jsonl"
OUT_DIR = _HERE.parents[1] / "evaluation" / "reports" / "choice_policy_max_decisions_recheck_150"


def main() -> None:
    baseline_check = verify_emulator_baseline()
    print(f"Emulator baseline check: {baseline_check['match']}")

    choice_decision = build_choice_decision(DEFAULT_CHOICE_POLICY_CHECKPOINT)
    choice_table = ChoiceSemanticsTable()
    assert choice_table.loaded_ok, choice_table.load_error
    emulator, heuristic_agent, policy_agent = build_policy_agent()
    choice_policy_agent = ChoicePolicyAgent(policy_agent, choice_decision, choice_table)

    rows = load_jsonl(MANIFEST_PATH)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with (OUT_DIR / "combats.jsonl").open("w", encoding="utf-8") as cf:
        for row in rows:
            result = run_scenario_ab(
                row, emulator, heuristic_agent, policy_agent, choice_policy_agent, choice_decision, choice_table,
                max_decisions=150, max_wall_seconds=90.0, shadow_top_level=False,
            )
            if result["status"] == "ok":
                a, b = result["choice_policy_arm"], result["heuristic_choice_arm"]
                print(
                    f"{result['trajectory_id']}: cp outcome={a['final_outcome']} truncated={a['truncated']} "
                    f"decisions={a['decision_count']} hp={a['final_hp']} term={a['termination_reason']} exc={a['step_exception']} | "
                    f"hc outcome={b['final_outcome']} truncated={b['truncated']} decisions={b['decision_count']} "
                    f"hp={b['final_hp']} term={b['termination_reason']} exc={b['step_exception']}"
                )
            else:
                print(f"{result['trajectory_id']}: status={result['status']}")
            results.append(result)
            cf.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")

    summary = {
        "manifest": str(MANIFEST_PATH), "manifest_sha256": _sha256_file(MANIFEST_PATH),
        "max_decisions": 150, "max_wall_seconds": 90.0, "shadow_comparison": False,
        "emulator_commit": baseline_check["actual_commit"],
        "results": [
            {
                "trajectory_id": r["trajectory_id"],
                "choice_policy_arm": {
                    "final_outcome": r["choice_policy_arm"]["final_outcome"],
                    "truncated": r["choice_policy_arm"]["truncated"],
                    "decision_count": r["choice_policy_arm"]["decision_count"],
                    "final_hp": r["choice_policy_arm"]["final_hp"],
                    "termination_reason": r["choice_policy_arm"]["termination_reason"],
                    "illegal_action_count": r["choice_policy_arm"]["illegal_action_count"],
                    "step_exception": r["choice_policy_arm"]["step_exception"],
                },
                "heuristic_choice_arm": {
                    "final_outcome": r["heuristic_choice_arm"]["final_outcome"],
                    "truncated": r["heuristic_choice_arm"]["truncated"],
                    "decision_count": r["heuristic_choice_arm"]["decision_count"],
                    "final_hp": r["heuristic_choice_arm"]["final_hp"],
                    "termination_reason": r["heuristic_choice_arm"]["termination_reason"],
                    "illegal_action_count": r["heuristic_choice_arm"]["illegal_action_count"],
                    "step_exception": r["heuristic_choice_arm"]["step_exception"],
                },
            }
            for r in results if r["status"] == "ok"
        ],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
