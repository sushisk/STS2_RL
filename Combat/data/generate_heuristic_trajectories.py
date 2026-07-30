"""Generates audited, imitation-learning-ready trajectories: for each input scenario
(a reconstructed pre-combat state or a hand-authored one), pre-flight-validates it
(preflight_validate.py), then drives a full combat via CombatEnv.reset()/step() with the
Phase-1 greedy HeuristicAgent choosing each action - one JSONL record per real decision,
per Common/schemas/... trajectory schema (see this module's TRAJECTORY_FIELDS).

Architecture note (see Outputs/reports/heuristic_code_audit.md): the OUTER real-decision
loop below goes through CombatEnv exclusively (reset/get_legal_actions/step) - that IS
the "本番教師生成経路をCombatEnvへ統一" requirement. HeuristicAgent's OWN internal
search (currently just the greedy 1-ply path) still calls BattleEmulator directly to
explore hypothetical, uncommitted branches - CombatEnv intentionally does not
re-implement that branching capability; only the actually-committed trajectory needs to
go through CombatEnv. See CombatEnv.battle_state's docstring for the same point from the
other side.

enemyIndex handling: each committed decision's target (if any) is stored using the
`enemy_index` resolved AT THAT DECISION's state (matches state["enemies"][i]["index"] -
see Common/schemas/combat_state_schema.json's stability caveat) - never treated as a
cross-decision identifier. CombatEnv.step() is called with target_enemy_index (not the
positional target_index) once available, so the actually-applied action re-resolves
against the live state's enemyIndex rather than a cached position.

Run standalone for a quick smoke test: `python generate_heuristic_trajectories.py`
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "env"))

from battle_emulator import BattleEmulator, battle_state_key  # noqa: E402
from battle_emulator import is_action_continuation_pending_choice  # noqa: E402
from combat_env import CombatEnv  # noqa: E402
from heuristic_agent import HeuristicAgent  # noqa: E402
from state_evaluator import DEFAULT_WEIGHTS, StateEvaluator  # noqa: E402
from potion_value_table import PotionValueTable  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402

HEURISTIC_VERSION = "greedy_v1_default_weights"
EMULATOR_DLL_PATH = Path(r"C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll")
EMULATOR_COMMIT = "163bf040027abca2754393a949e612e42f46a3e7"
UNRESTORABLE_FORCED_MOVES = set()
NO_PROGRESS_WINDOW = 10


def emulator_dll_sha256() -> str:
    try:
        return hashlib.sha256(EMULATOR_DLL_PATH.read_bytes()).hexdigest().upper()
    except OSError:
        return "unknown"


def emulator_version() -> str:
    """No formal version string exists on the Emulator side - the DLL's own build
    timestamp is the closest available identifier (matches the convention already used
    in Outputs/reports/emulator_fix_revalidation_report.md)."""
    try:
        mtime = EMULATOR_DLL_PATH.stat().st_mtime
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
    except OSError:
        return "unknown"


def scenario_hash(spec: dict) -> str:
    payload = json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def unrestorable_forced_moves(state: dict) -> list[dict]:
    """Enemy intent states that Observation exposes but CombatScenario.ForcedMove cannot
    faithfully restore. Keep this list empty unless a live Emulator build demonstrates
    a concrete Observation-stateId that ResetFromScenario still cannot accept."""
    hits = []
    for enemy in state.get("enemies") or []:
        move_id = (enemy.get("intent") or {}).get("stateId")
        if move_id in UNRESTORABLE_FORCED_MOVES:
            hits.append(
                {
                    "enemy_index": enemy.get("index"),
                    "enemy_id": enemy.get("id"),
                    "move_id": move_id,
                    "state_log": enemy.get("stateLog") or [],
                }
            )
    return hits


def unsupported_pending_choice(state: dict) -> dict | None:
    pending = state.get("pendingChoice") or {}
    choice_type = pending.get("choiceType")
    if not choice_type or not is_action_continuation_pending_choice(state):
        return None
    return {
        "choice_type": str(choice_type),
        "scope": pending.get("scope"),
        "scenario_restorable": pending.get("scenarioRestorable"),
        "min_select": pending.get("minSelect"),
        "max_select": pending.get("maxSelect"),
        "selected_count": pending.get("selectedCount"),
        "option_count": len(pending.get("options") or []),
        "option_ids": [c.get("id") for c in (pending.get("options") or [])],
    }


def classify_quarantine(reasons: list[str]) -> str:
    if any(reason.startswith("init_exception") for reason in reasons):
        return "unsupported_state"
    if "no_legal_actions" in reasons:
        return "unsupported_state"
    if "relic_mismatch" in reasons:
        return "relic_after_obtained_side_effect"
    if any(reason.endswith("_mismatch") for reason in reasons):
        return "emulator_state_mismatch"
    return "other"


def generate_trajectory(
    spec: dict,
    emulator: BattleEmulator,
    agent: HeuristicAgent,
    trajectory_id: str,
    source_run_id: Any,
    combat_index: int,
    max_decisions: int = 50,
    max_wall_seconds: "float | None" = 120.0,
) -> dict:
    """Returns {trajectory_id, status: "ok"|"quarantined", decisions: [...], ...}.

    `max_wall_seconds` bounds real elapsed time, independent of `max_decisions` - found
    necessary in practice: HeuristicAgent's greedy path evaluates every (legal action x
    target) candidate via a full apply_action() restore+Step each, so a single DECISION's
    cost scales with hand size x alive-enemy count; an add-spawning elite (e.g.
    ENTOMANCER) combined with a large deck can make individual decisions - not just the
    whole combat - expensive enough that max_decisions alone doesn't bound wall time.
    None disables the check (unbounded, `max_decisions` is the only cap)."""
    pre = preflight_validate(spec, emulator)
    if pre["status"] != "ok":
        classification = classify_quarantine(pre["reasons"])
        return {
            "trajectory_id": trajectory_id,
            "source_run_id": source_run_id,
            "source_combat_index": combat_index,
            "status": "quarantined",
            "reasons": pre["reasons"],
            "quarantine_classification": classification,
            "input_state": spec,
            "initialized_state": pre["battle_state"].engine_state if pre.get("battle_state") is not None else None,
            "diffs": pre["diffs"],
            "state_diff": pre["diffs"],
            "first_failing_decision": None,
            "exception": None,
            "recommended_action": (
                "exclude_from_teacher_data; audit relic AfterObtained or replacement behavior"
                if classification == "relic_after_obtained_side_effect"
                else "exclude_from_teacher_data unless the unsupported state is made restorable"
            ),
        }

    env = CombatEnv(reward_fn=lambda *a: 0.0)  # reward left to downstream consumers, not baked in here
    env.adopt_state(pre["battle_state"], spec)
    s_hash = scenario_hash(spec)
    e_version = emulator_version()

    decisions: list[dict] = []
    warnings: list[str] = []
    decision_index = 0
    start_time = time.time()
    deadline = (start_time + max_wall_seconds) if max_wall_seconds is not None else None
    seen_state_keys: set[tuple] = set()
    cycle_detected = False
    no_progress_detected = False
    termination_reason: str | None = None
    non_learning_transition: dict | None = None
    heuristic_exception: dict | None = None
    recent_progress_window: deque[dict] = deque(maxlen=NO_PROGRESS_WINDOW)
    while not env.battle_state.is_terminal and decision_index < max_decisions:
        key = battle_state_key(env.battle_state)
        if key in seen_state_keys:
            cycle_detected = True
            termination_reason = "cycle_detected"
            warnings.append("cycle_detected")
            break
        seen_state_keys.add(key)

        forced_moves = unrestorable_forced_moves(env.battle_state.engine_state)
        if forced_moves:
            termination_reason = "non_learning_transition:unrestorable_forced_move"
            non_learning_transition = {"classification": "non_learning_transition", "forced_moves": forced_moves}
            warnings.append(f"non_learning_transition:unrestorable_forced_move:{forced_moves[0]['move_id']}")
            break

        unsupported_choice = unsupported_pending_choice(env.battle_state.engine_state)
        if unsupported_choice:
            termination_reason = f"unsupported_pending_choice:{unsupported_choice['choice_type']}"
            non_learning_transition = {
                "classification": "api_gap_pending_choice",
                "pending_choice": unsupported_choice,
            }
            warnings.append(f"unsupported_pending_choice:{unsupported_choice['choice_type']}")
            break

        if deadline is not None and time.time() >= deadline:
            termination_reason = f"truncated_at_time_budget:{max_wall_seconds}s"
            warnings.append(f"truncated_at_time_budget:{max_wall_seconds}s")
            break
        legal = env.get_legal_actions()
        if not legal:
            warnings.append("no_legal_actions_while_non_terminal")
            break
        decision_started = time.time()
        try:
            # `deadline` is checked INSIDE choose_action_with_detail too, per-candidate -
            # a single decision's own candidate space (hand size x alive enemies) can by
            # itself run well past a between-decision check alone (found in practice: an
            # 8+ minute single decision) - see that method's own docstring.
            chosen, action_scores = agent.choose_action_with_detail(
                env.battle_state,
                deadline=deadline,
                historical_state_keys=seen_state_keys,
            )
        except Exception as exc:  # noqa: BLE001
            skipped_candidates = getattr(exc, "skipped_candidates", None) or []
            skipped_types = {c.get("exception_type") for c in skipped_candidates if c.get("exception_type")}
            if skipped_candidates and skipped_types == {"TimeoutException"}:
                termination_reason = "step_exception:TimeoutException"
                warnings.append("step_exception:TimeoutException:candidate_evaluation")
                non_learning_transition = {
                    "classification": "emulator_step_timeout",
                    "candidate_failures": skipped_candidates,
                }
                break
            termination_reason = f"heuristic_exception:{type(exc).__name__}"
            warnings.append(f"heuristic_exception:{type(exc).__name__}:{str(exc)[:150]}")
            heuristic_exception = {
                "decision_index": decision_index,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "exception_traceback": traceback.format_exc(),
                "observation": env.battle_state.engine_state,
                "legal_actions": legal,
                "evaluated_action": None,
                "candidate_failures": skipped_candidates,
                "forced_move_context": unrestorable_forced_moves(env.battle_state.engine_state),
                "selection_context": (
                    "forced_move"
                    if unrestorable_forced_moves(env.battle_state.engine_state)
                    else "normal_selection"
                ),
            }
            break
        elapsed_ms = round((time.time() - decision_started) * 1000.0, 3)
        evaluated_action_count = sum(1 for c in action_scores if c.get("score") is not None)
        total_candidate_count = len(action_scores)
        decision_budget_exceeded = any(c.get("skipped_reason") == "deadline_exceeded" for c in action_scores)

        state_before = env.battle_state.engine_state
        chosen_enemy_index = next(
            (c["enemy_index"] for c in action_scores if c["action_id"] == chosen.action_id and c["target_index"] == chosen.target_index),
            None,
        )
        selected_action_index = next((i for i, a in enumerate(legal) if a["action_id"] == chosen.action_id), None)
        selected_score_entry = next(
            (c for c in action_scores if c["action_id"] == chosen.action_id and c["target_index"] == chosen.target_index),
            None,
        )
        fallback_used = bool(selected_score_entry and selected_score_entry.get("score") is None)
        fallback_reason = selected_score_entry.get("skipped_reason") if fallback_used else None

        try:
            result = env.step(
                chosen.action,
                target_enemy_index=chosen_enemy_index,
                target_index=chosen.target_index,
                continuation_resolver=agent._choose_action_continuation_live,
                continuation_deadline=deadline,
            )
        except Exception as exc:  # noqa: BLE001
            termination_reason = f"step_exception:{type(exc).__name__}"
            warnings.append(f"step_exception:{type(exc).__name__}:{str(exc)[:150]}")
            break

        decisions.append(
            {
                "trajectory_id": trajectory_id,
                "source_run_id": source_run_id,
                "source_combat_index": combat_index,
                "decision_index": decision_index,
                "emulator_version": e_version,
                "scenario_hash": s_hash,
                "state": state_before,
                "legal_actions": legal,
                "selected_action": chosen.action,
                "selected_action_index": selected_action_index,
                "selected_enemy_index": chosen_enemy_index,
                "action_scores": action_scores,
                "decision_budget_exceeded": decision_budget_exceeded,
                "elapsed_ms": elapsed_ms,
                "evaluated_action_count": evaluated_action_count,
                "total_legal_action_count": len(legal),
                "total_candidate_count": total_candidate_count,
                "search_depth_reached": 1,
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason,
                "reward": result["reward"],
                "next_state": result["observation"]["state"],
                "done": result["done"],
                "outcome": result["observation"]["outcome"],
                "heuristic_version": HEURISTIC_VERSION,
                "random_seed": spec.get("seed"),
                "warnings": [],
            }
        )
        recent_progress_window.append(
            {
                "selected_label": chosen.action["label"],
                "had_non_end_turn_option": any(a.get("label") != "End Turn" for a in legal),
                "enemy_hp_before": sum(max(0, e.get("hp", 0) or 0) for e in state_before.get("enemies") or []),
                "enemy_hp_after": sum(max(0, e.get("hp", 0) or 0) for e in result["observation"]["state"].get("enemies") or []),
                "player_hp_before": state_before.get("hp"),
                "player_hp_after": result["observation"]["state"].get("hp"),
            }
        )
        decision_index += 1
        if len(recent_progress_window) == NO_PROGRESS_WINDOW:
            all_end_turn = all(row["selected_label"] == "End Turn" for row in recent_progress_window)
            always_had_other_options = all(row["had_non_end_turn_option"] for row in recent_progress_window)
            no_enemy_change = all(row["enemy_hp_before"] == row["enemy_hp_after"] for row in recent_progress_window)
            no_player_change = all(row["player_hp_before"] == row["player_hp_after"] for row in recent_progress_window)
            if all_end_turn and always_had_other_options and no_enemy_change and no_player_change:
                no_progress_detected = True
                termination_reason = f"no_progress_detected:{NO_PROGRESS_WINDOW}_end_turns"
                warnings.append(f"no_progress_detected:{NO_PROGRESS_WINDOW}_end_turns")
                break
        if result["done"]:
            break

    time_truncated = any(w.startswith("truncated_at_time_budget") for w in warnings)
    truncated = (not env.battle_state.is_terminal) and (decision_index >= max_decisions or time_truncated)
    if truncated and not time_truncated:
        termination_reason = f"truncated_at_max_decisions:{max_decisions}"
        warnings.append(f"truncated_at_max_decisions:{max_decisions}")
    if env.battle_state.is_terminal:
        termination_reason = "terminal"

    return {
        "trajectory_id": trajectory_id,
        "source_run_id": source_run_id,
        "source_combat_index": combat_index,
        "status": "ok",
        "decision_count": len(decisions),
        "truncated": truncated,
        "final_outcome": env.battle_state.outcome,
        "final_is_terminal": env.battle_state.is_terminal,
        "warnings": warnings,
        "termination_reason": termination_reason,
        "max_decisions_reached": decision_index >= max_decisions and not env.battle_state.is_terminal,
        "cycle_detected": cycle_detected,
        "no_progress_detected": no_progress_detected,
        "termination_limit": max_decisions,
        "non_learning_transition": non_learning_transition,
        "heuristic_exception": heuristic_exception,
        "decisions": decisions,
    }


def build_default_agent() -> tuple[BattleEmulator, HeuristicAgent]:
    emulator = BattleEmulator()
    evaluator = StateEvaluator(PotionValueTable())
    agent = HeuristicAgent(emulator, evaluator, dict(DEFAULT_WEIGHTS))
    return emulator, agent


if __name__ == "__main__":
    from scenario_set import ScenarioSet

    emulator, agent = build_default_agent()
    scenario = ScenarioSet.default_ironclad_vs_calcified_cultist(train_n=1, validation_n=0, test_n=0).train[0]
    result = generate_trajectory(scenario, emulator, agent, trajectory_id="smoke-test-0", source_run_id="smoke", combat_index=0)
    print(f"status={result['status']} decisions={result.get('decision_count')} "
          f"outcome={result.get('final_outcome')} truncated={result.get('truncated')} warnings={result.get('warnings')}")
    for d in result.get("decisions", [])[:5]:
        print(f"  decision {d['decision_index']}: {d['selected_action']['label']} "
              f"(enemy_index={d['selected_enemy_index']}) candidates={len(d['action_scores'])} reward={d['reward']}")
