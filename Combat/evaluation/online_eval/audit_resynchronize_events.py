"""Phase 1 resynchronize audit (read-only instrumentation via monkey-patching - does NOT
modify live_combat_session.py/battle_emulator.py/choice_policy_online_eval.py on disk).

For every LiveCombatSession._resynchronize() call across a fixed 10-scenario x 2-arm
run (the same population used for the Phase 1 completion report's performance
measurement), records:
    - scenario_id, arm
    - the BattleState's own DecisionFrame (what SHOULD have been live)
    - the shared GameInstance's ACTUAL (drifted) frame/state at the moment of detection
    - a call-stack sample identifying what touched the shared GameInstance in between
      (via a parallel instrumentation of BattleEmulator._restore(), the sole mechanism
      HeuristicAgent's candidate scoring / Choice Policy fallback / shadow evaluation
      use to restore)
    - before/after HP, enemy state, turn, legal-action count, terminal flag
    - the resulting resynchronize's restore input (always battle_state.engine_state - a
      mid-combat Observation, never the original scenario spec - confirmed by reading
      live_combat_session.py's own source, not re-derived here)

Run twice (--repeat 2, default) to test reproducibility under identical conditions.

This script is NOT part of the permanent pipeline - it monkey-patches LiveCombatSession/
BattleEmulator methods at runtime and restores the originals in a `finally` block; no
file on disk is modified by running it.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import BattleEmulator, to_plain  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
import choice_policy_online_eval as cpoe  # noqa: E402
from choice_policy_online_eval import run_scenario_ab, load_jsonl  # noqa: E402
from choice_policy_agent import ChoicePolicyAgent, build_choice_decision, DEFAULT_CHOICE_POLICY_CHECKPOINT  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from policy_agent import build_policy_agent  # noqa: E402


def _state_summary(engine_state: dict) -> dict:
    return {
        "hp": engine_state.get("hp"),
        "maxHp": engine_state.get("maxHp"),
        "turnNumber": engine_state.get("turnNumber"),
        "combatRoundNumber": engine_state.get("combatRoundNumber"),
        "stepIndex": engine_state.get("stepIndex"),
        "enemies": [
            {"id": e.get("id"), "hp": e.get("hp"), "isAlive": e.get("isAlive")}
            for e in (engine_state.get("enemies") or [])
        ],
    }


def run_audit(manifest_rows, repeat: int) -> list[dict]:
    all_runs = []

    restore_trace: list[dict] = []
    resync_events: list[dict] = []
    current_context = {"scenario_id": None, "arm": None}

    original_restore = BattleEmulator._restore
    original_resynchronize = LiveCombatSession._resynchronize
    original_is_still_current = LiveCombatSession._is_still_current
    original_run_episode_ab = cpoe.run_episode_ab

    def instrumented_restore(self, battle_state):
        # Identify the calling chain (skip this frame + the direct wrapper).
        stack = traceback.extract_stack()[:-1]
        caller_frames = [f"{f.filename.split(chr(92))[-1]}:{f.lineno} in {f.name}" for f in stack[-6:]]
        restore_trace.append(
            {
                "scenario_id": current_context["scenario_id"],
                "arm": current_context["arm"],
                "caller_frames": caller_frames,
                "engine_state_summary": _state_summary(battle_state.engine_state),
            }
        )
        return original_restore(self, battle_state)

    def instrumented_is_still_current(self) -> bool:
        obs = self._game.GetObservation()
        live_frame = (getattr(obs, "CombatSessionId", None), int(getattr(obs, "StepIndex", -1)))
        current_frame = (self._current_frame.combat_session_id, self._current_frame.step_index)
        self._audit_last_live_frame = live_frame
        self._audit_last_live_state_summary = _state_summary(to_plain(obs.State))
        return live_frame == current_frame

    def instrumented_resynchronize(self, battle_state) -> None:
        drifted_frame = getattr(self, "_audit_last_live_frame", None)
        drifted_state = getattr(self, "_audit_last_live_state_summary", None)
        expected_frame = (battle_state.decision_frame.combat_session_id, battle_state.decision_frame.step_index)
        stack = traceback.extract_stack()[:-1]
        caller_frames = [f"{f.filename.split(chr(92))[-1]}:{f.lineno} in {f.name}" for f in stack[-6:]]

        # Which restore_trace entries happened since the last resync/step? Correlate by
        # matching this scenario/arm context - the most recent matching entries are the
        # interference source for this resync.
        matching_restores = [
            r for r in restore_trace if r["scenario_id"] == current_context["scenario_id"] and r["arm"] == current_context["arm"]
        ]
        interference_sample = matching_restores[-3:] if matching_restores else []

        original_resynchronize(self, battle_state)

        after_obs = self._game.GetObservation()
        after_state_summary = _state_summary(to_plain(after_obs.State))

        resync_events.append(
            {
                "scenario_id": current_context["scenario_id"],
                "arm": current_context["arm"],
                "decision_frame_expected": {"combat_session_id": expected_frame[0], "step_index": expected_frame[1]},
                "live_frame_actually_found_drifted_to": {"combat_session_id": drifted_frame[0] if drifted_frame else None, "step_index": drifted_frame[1] if drifted_frame else None},
                "caller_frames_of_resynchronize": caller_frames,
                "interference_source_sample": interference_sample,
                "restore_input_source": "mid_combat_observation (battle_state.engine_state, via build_scenario_from_state - NOT the original scenario spec)",
                "drifted_state_summary": drifted_state,
                "restored_state_summary_battle_state_own_data": _state_summary(battle_state.engine_state),
                "live_state_after_resync": after_state_summary,
            }
        )

    def instrumented_run_episode_ab(arm, base_state, spec, emulator_, decide_fn, trajectory_id, *rest, **kw):
        current_context["scenario_id"] = trajectory_id
        current_context["arm"] = arm
        return original_run_episode_ab(arm, base_state, spec, emulator_, decide_fn, trajectory_id, *rest, **kw)

    BattleEmulator._restore = instrumented_restore
    LiveCombatSession._is_still_current = instrumented_is_still_current
    LiveCombatSession._resynchronize = instrumented_resynchronize
    cpoe.run_episode_ab = instrumented_run_episode_ab

    try:
        for run_idx in range(repeat):
            restore_trace.clear()
            resync_events.clear()

            emulator, heuristic_agent, policy_agent = build_policy_agent()
            choice_decision = build_choice_decision(DEFAULT_CHOICE_POLICY_CHECKPOINT)
            choice_table = ChoiceSemanticsTable()
            cpa = ChoicePolicyAgent(policy_agent, choice_decision, choice_table)

            results_summary = []
            for row in manifest_rows:
                result = run_scenario_ab(
                    row, emulator, heuristic_agent, policy_agent, cpa, choice_decision, choice_table, 60, 90.0, True,
                )
                if result["status"] == "ok":
                    results_summary.append(
                        {
                            "scenario_id": row["trajectory_id"],
                            "cp_outcome": result["choice_policy_arm"]["final_outcome"],
                            "hc_outcome": result["heuristic_choice_arm"]["final_outcome"],
                        }
                    )

            all_runs.append(
                {
                    "run_index": run_idx,
                    "resync_events": [dict(e) for e in resync_events],
                    "resync_count": len(resync_events),
                    "scenario_results": results_summary,
                }
            )
    finally:
        BattleEmulator._restore = original_restore
        LiveCombatSession._is_still_current = original_is_still_current
        LiveCombatSession._resynchronize = original_resynchronize
        cpoe.run_episode_ab = original_run_episode_ab

    return all_runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--out", type=Path, default=_HERE / "resynchronize_audit_raw.json")
    args = parser.parse_args()

    rows = load_jsonl(_HERE / "choice_policy_online_eval_manifest.jsonl")[: args.n]
    runs = run_audit(rows, args.repeat)

    args.out.write_text(json.dumps(runs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for run in runs:
        print(f"run {run['run_index']}: resync_count={run['resync_count']}")
        for e in run["resync_events"]:
            print(f"  scenario={e['scenario_id']} expected_frame={e['decision_frame_expected']} drifted_to={e['live_frame_actually_found_drifted_to']}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
