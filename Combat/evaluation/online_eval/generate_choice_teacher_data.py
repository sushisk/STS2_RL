"""Generates Choice Policy teacher data: for each scenario in a
build_choice_teacher_data_manifest.py manifest, replays it with the SAME greedy
HeuristicAgent that produced teacher2000 (generate_heuristic_trajectories.py's
build_default_agent(), HEURISTIC_VERSION="greedy_v1_default_weights" - unchanged), and
records one row per Choice decision - both the 43-per-teacher2000-census top-level
choice_card/choice_skip/choice_confirm decisions AND the ActionContinuation-scoped
choices (HOLOGRAM/SURVIVOR/BURNING_PACT/ARMAMENTS/NIGHTMARE/GAMBLING_CHIP/...) that are
normally auto-resolved INSIDE a single env.step() call and invisible to teacher2000's own
trajectories.jsonl - see build_choice_scenarios_manifest.py's module docstring for the
original finding, and make_logging_continuation_resolver_full() below for how they're
observed here without changing what gets chosen.

This module is LOGGING/ANALYSIS/DATA-GENERATION ONLY, same invariant as choice_semantics.
py: nothing here changes which action HeuristicAgent selects - _choose_action_continuation_
live and choose_action_with_detail are called completely unwrapped for the actual decision;
the wrapper only observes and records around them.

Emulator baseline: 722b019 (Combat/policy_baseline/choice_semantics_baseline_722b019_v1_
20260725.json) - verify_emulator_baseline() checks the live DLL hashes/git commit match
that record before any scenario is run, exactly as prior 722b019 reverification did.

Run:
    python generate_choice_teacher_data.py --manifest <manifest.jsonl> --out <out_dir>
        [--max-decisions 60] [--max-wall-seconds 120]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

_COMBAT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_COMBAT_DIR))
sys.path.insert(0, str(_COMBAT_DIR / "env"))
sys.path.insert(0, str(_COMBAT_DIR / "data"))

from battle_emulator import battle_state_key, is_action_continuation_pending_choice  # noqa: E402
from combat_env import CombatEnv  # noqa: E402
from heuristic_agent import HeuristicAgent  # noqa: E402
from state_evaluator import DEFAULT_WEIGHTS, StateEvaluator  # noqa: E402
from potion_value_table import PotionValueTable  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from policy_agent import CHOICE_FALLBACK_ACTION_TYPES  # noqa: E402
from generate_heuristic_trajectories import (  # noqa: E402
    HEURISTIC_VERSION, EMULATOR_DLL_PATH, EMULATOR_COMMIT, emulator_dll_sha256,
    emulator_version, scenario_hash, unrestorable_forced_moves, unsupported_pending_choice,
    classify_quarantine, build_default_agent,
)

BASELINE_722B019_PATH = _COMBAT_DIR / "policy_baseline" / "combat_state_contract_phase1_emulator_baseline_v1_20260726.json"
STAGE1_DLL_PATH = EMULATOR_DLL_PATH.parent / "Sts2Imported.Stage1.dll"


def emulator_git_commit() -> "str | None":
    try:
        out = subprocess.run(
            ["git", "-C", r"C:\STS2_Emulator", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _sha256_file(path: Path) -> "str | None":
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()
    except OSError:
        return None


def verify_emulator_baseline() -> dict:
    """Fails loudly (raises) rather than silently generating data against the wrong
    Emulator build - same check performed before the 722b019 AFTER re-verification run."""
    baseline = json.loads(BASELINE_722B019_PATH.read_text(encoding="utf-8"))
    expected_commit = baseline["emulator"]["commit"]
    expected_dll = baseline["emulator"]["sts2emulator_dll_sha256"]
    expected_stage1 = baseline["emulator"]["sts2imported_stage1_dll_sha256"]

    actual_commit = emulator_git_commit()
    actual_dll = emulator_dll_sha256()
    actual_stage1 = _sha256_file(STAGE1_DLL_PATH)

    ok = (
        actual_commit == expected_commit
        and actual_dll.lower() == expected_dll.lower()
        and (actual_stage1 or "").lower() == expected_stage1.lower()
    )
    result = {
        "expected_commit": expected_commit, "actual_commit": actual_commit,
        "expected_dll_sha256": expected_dll, "actual_dll_sha256": actual_dll,
        "expected_stage1_sha256": expected_stage1, "actual_stage1_sha256": actual_stage1,
        "match": ok,
    }
    if not ok:
        raise RuntimeError(f"Live Emulator does not match 722b019 baseline: {json.dumps(result, indent=2)}")
    return result


def make_logging_continuation_resolver_full(
    inner_resolver: Callable,
    choice_table: ChoiceSemanticsTable,
    sink: list,
    ctx: dict,
) -> Callable:
    """Like online_policy_eval.py's make_logging_continuation_resolver, but additionally
    records the actual action returned by `inner_resolver` (the "teacher action" for this
    micro-step) plus legal_actions/battle-state/candidate-card fields - online_policy_eval.
    py's version only needed the pendingChoice fact (Policy/Heuristic online routing
    already sends these decisions to Heuristic fallback unconditionally, so it never
    needed the chosen action itself). Teacher-data generation needs the actual selection,
    so this is a separate, slightly larger sink record - not a modification of the
    existing online-eval wrapper.

    `ctx` carries {trajectory_id, source_run_id, source_combat_index, decision_index}
    for the CURRENT outer real-decision (mutated by the caller between decisions, read
    here at call time) plus a private "_continuation_step_index" counter this function
    owns to number consecutive micro-steps within one outer decision (this task's
    "1 Step内の連続Choice" requirement)."""

    def _resolver(game, battle_state, legal_actions, deadline=None):
        pending = battle_state.engine_state.get("pendingChoice") or {}
        resolution = choice_table.resolve(pending)
        action = inner_resolver(game, battle_state, legal_actions, deadline)

        step_idx = ctx.get("_continuation_step_index", 0)
        ctx["_continuation_step_index"] = step_idx + 1

        candidate_card_id = None
        if action.get("action_type") == "choice_card":
            candidate_card_id = (action.get("parameters") or {}).get("cardId")
        candidate_identifiable = (action.get("action_type") != "choice_card") or (candidate_card_id is not None)

        sink.append(
            {
                "source": "action_continuation",
                "trajectory_id": ctx["trajectory_id"],
                "source_run_id": ctx["source_run_id"],
                "source_combat_index": ctx["source_combat_index"],
                "decision_index": ctx["decision_index"],
                "continuation_step_index": step_idx,
                "battle_state": battle_state.engine_state,
                "legal_actions": legal_actions,
                "teacher_action": action,
                "candidate_card_id": candidate_card_id,
                "candidate_identifiable": candidate_identifiable,
                "remaining_select_count": pending.get("remainingSelectCount"),
                "raw_pending_choice": pending,
                "emulator_fact": resolution["emulator_fact"],
                "resolved": resolution["resolved"],
                "lookup_provenance": {
                    "lookupVersion": choice_table.table_version,
                    "lookupSha256": choice_table.lookup_sha256,
                },
                "teacher_action_in_legal": action.get("action_id") in {a.get("action_id") for a in legal_actions},
            }
        )
        return action

    return _resolver


def generate_choice_scenario(
    spec: dict,
    emulator,
    agent: HeuristicAgent,
    choice_table: ChoiceSemanticsTable,
    trajectory_id: str,
    source_run_id: Any,
    source_combat_index: Any,
    max_decisions: int,
    max_wall_seconds: "float | None",
) -> dict:
    """One scenario's full replay. Mirrors generate_heuristic_trajectories.generate_
    trajectory()'s loop (same preflight/env/HeuristicAgent machinery, same quarantine
    handling) but adds: (a) choice_semantics enrichment on top-level choice_card/
    choice_skip/choice_confirm decisions, (b) the full-logging continuation resolver
    above wired into env.step() so ActionContinuation-scoped choices are captured too."""
    pre = preflight_validate(spec, emulator)
    if pre["status"] != "ok":
        return {
            "trajectory_id": trajectory_id, "source_run_id": source_run_id,
            "source_combat_index": source_combat_index, "status": "quarantined",
            "reasons": pre["reasons"], "quarantine_classification": classify_quarantine(pre["reasons"]),
            "choice_decisions": [], "exception": None,
        }

    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(pre["battle_state"], spec)
    s_hash = scenario_hash(spec)

    choice_decisions: list[dict] = []
    warnings: list[str] = []
    decision_index = 0
    illegal_action_count = 0
    exception_info = None
    start_time = time.time()
    deadline = (start_time + max_wall_seconds) if max_wall_seconds is not None else None
    seen_state_keys: set[tuple] = set()
    termination_reason = None
    ctx: dict = {
        "trajectory_id": trajectory_id, "source_run_id": source_run_id,
        "source_combat_index": source_combat_index,
    }

    while not env.battle_state.is_terminal and decision_index < max_decisions:
        key = battle_state_key(env.battle_state)
        if key in seen_state_keys:
            termination_reason = "cycle_detected"
            warnings.append("cycle_detected")
            break
        seen_state_keys.add(key)

        forced_moves = unrestorable_forced_moves(env.battle_state.engine_state)
        if forced_moves:
            termination_reason = "non_learning_transition:unrestorable_forced_move"
            break
        unsupported_choice = unsupported_pending_choice(env.battle_state.engine_state)
        if unsupported_choice:
            termination_reason = f"unsupported_pending_choice:{unsupported_choice['choice_type']}"
            break
        if deadline is not None and time.time() >= deadline:
            termination_reason = f"truncated_at_time_budget:{max_wall_seconds}s"
            break

        legal = env.get_legal_actions()
        if not legal:
            warnings.append("no_legal_actions_while_non_terminal")
            break

        state_before = env.battle_state.engine_state
        legal_types = {a.get("action_type") for a in legal}
        is_top_level_choice = bool(legal_types & CHOICE_FALLBACK_ACTION_TYPES)

        try:
            chosen, action_scores = agent.choose_action_with_detail(
                env.battle_state, deadline=deadline, historical_state_keys=seen_state_keys,
            )
        except Exception as exc:  # noqa: BLE001
            termination_reason = f"heuristic_exception:{type(exc).__name__}"
            warnings.append(f"heuristic_exception:{type(exc).__name__}:{str(exc)[:150]}")
            exception_info = {"type": type(exc).__name__, "message": str(exc)[:500], "traceback": traceback.format_exc()}
            break

        chosen_enemy_index = next(
            (c["enemy_index"] for c in action_scores if c["action_id"] == chosen.action_id and c["target_index"] == chosen.target_index),
            None,
        )

        if is_top_level_choice:
            pending = state_before.get("pendingChoice") or {}
            resolution = choice_table.resolve(pending)
            candidate_card_id = None
            if chosen.action.get("action_type") == "choice_card":
                candidate_card_id = (chosen.action.get("parameters") or {}).get("cardId")
            candidate_identifiable = (chosen.action.get("action_type") != "choice_card") or (candidate_card_id is not None)
            choice_decisions.append(
                {
                    "source": "top_level_decision",
                    "trajectory_id": trajectory_id, "source_run_id": source_run_id,
                    "source_combat_index": source_combat_index, "decision_index": decision_index,
                    "continuation_step_index": None,
                    "battle_state": state_before, "legal_actions": legal,
                    "teacher_action": chosen.action, "candidate_card_id": candidate_card_id,
                    "candidate_identifiable": candidate_identifiable,
                    "remaining_select_count": pending.get("remainingSelectCount"),
                    "raw_pending_choice": pending,
                    "emulator_fact": resolution["emulator_fact"], "resolved": resolution["resolved"],
                    "lookup_provenance": {"lookupVersion": choice_table.table_version, "lookupSha256": choice_table.lookup_sha256},
                    "teacher_action_in_legal": chosen.action.get("action_id") in {a.get("action_id") for a in legal},
                }
            )

        ctx["decision_index"] = decision_index
        ctx["_continuation_step_index"] = 0
        step_kwargs = {
            "continuation_resolver": make_logging_continuation_resolver_full(
                agent._choose_action_continuation_live, choice_table, choice_decisions, ctx,  # noqa: SLF001
            ),
            "continuation_deadline": deadline,
        }
        try:
            result = env.step(
                chosen.action, target_enemy_index=chosen_enemy_index,
                target_index=chosen.target_index, **step_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            termination_reason = f"step_exception:{type(exc).__name__}"
            warnings.append(f"step_exception:{type(exc).__name__}:{str(exc)[:150]}")
            exception_info = {"type": type(exc).__name__, "message": str(exc)[:500], "traceback": traceback.format_exc()}
            break

        decision_index += 1
        if result["done"]:
            break

    time_truncated = any(w.startswith("truncated_at_time_budget") for w in warnings)
    truncated = (not env.battle_state.is_terminal) and (decision_index >= max_decisions or time_truncated)
    if env.battle_state.is_terminal:
        termination_reason = termination_reason or "terminal"
    elif truncated and termination_reason is None:
        termination_reason = f"truncated_at_max_decisions:{max_decisions}"

    for d in choice_decisions:
        d["scenario_final_outcome"] = env.battle_state.outcome if env.battle_state.is_terminal else "in_progress"
        d["scenario_truncated"] = truncated
        d["scenario_termination_reason"] = termination_reason
        d["scenario_hash"] = s_hash
        d["heuristic_version"] = HEURISTIC_VERSION

    return {
        "trajectory_id": trajectory_id, "source_run_id": source_run_id,
        "source_combat_index": source_combat_index, "status": "ok",
        "decision_count": decision_index, "choice_decision_count": len(choice_decisions),
        "final_outcome": env.battle_state.outcome if env.battle_state.is_terminal else "in_progress",
        "truncated": truncated, "termination_reason": termination_reason,
        "warnings": warnings, "illegal_action_count": illegal_action_count,
        "exception": exception_info, "choice_decisions": choice_decisions,
    }


def load_manifest(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def harness_code_provenance() -> dict:
    files = [Path(__file__), _COMBAT_DIR / "choice_semantics.py", _COMBAT_DIR / "heuristic_agent.py"]
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-decisions", type=int, default=60)
    parser.add_argument("--max-wall-seconds", type=float, default=120.0)
    args = parser.parse_args()

    baseline_check = verify_emulator_baseline()
    print(f"Emulator baseline check: {baseline_check['match']} (commit={baseline_check['actual_commit']})")

    rows = load_manifest(args.manifest)
    choice_table = ChoiceSemanticsTable()
    if not choice_table.loaded_ok:
        raise RuntimeError(f"ChoiceSemanticsTable failed to load: {choice_table.load_error}")

    emulator, agent = build_default_agent()
    args.out.mkdir(parents=True, exist_ok=True)
    scenarios_path = args.out / "scenarios.jsonl"
    choices_path = args.out / "choice_teacher_data.jsonl"

    scenario_results = []
    with scenarios_path.open("w", encoding="utf-8") as sf, choices_path.open("w", encoding="utf-8") as cf:
        for i, row in enumerate(rows):
            t0 = time.time()
            try:
                result = generate_choice_scenario(
                    row["spec"], emulator, agent, choice_table,
                    row["trajectory_id"], row.get("source_run_id"), row.get("source_combat_index"),
                    args.max_decisions, args.max_wall_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "trajectory_id": row["trajectory_id"], "source_run_id": row.get("source_run_id"),
                    "source_combat_index": row.get("source_combat_index"), "status": "scenario_exception",
                    "exception": {"type": type(exc).__name__, "message": str(exc)[:500], "traceback": traceback.format_exc()},
                    "choice_decisions": [],
                }
            elapsed = time.time() - t0
            n_choices = len(result.get("choice_decisions") or [])
            print(f"[{i + 1}/{len(rows)}] {result['trajectory_id']} status={result.get('status')} choices={n_choices} ({elapsed:.1f}s)")

            meta = {k: v for k, v in result.items() if k != "choice_decisions"}
            meta["categories"] = row.get("categories")
            meta["synthetic"] = row.get("synthetic", False)
            sf.write(json.dumps(meta, ensure_ascii=False, default=str) + "\n")
            sf.flush()
            scenario_results.append(meta)
            for d in result.get("choice_decisions") or []:
                cf.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
            cf.flush()

    summary = {
        "manifest": str(args.manifest), "manifest_sha256": manifest_sha256(args.manifest),
        "scenario_count": len(rows),
        "ok_count": sum(1 for r in scenario_results if r["status"] == "ok"),
        "quarantined_count": sum(1 for r in scenario_results if r["status"] == "quarantined"),
        "exception_count": sum(1 for r in scenario_results if r["status"] not in ("ok", "quarantined")),
        "total_choice_decisions": sum(r.get("choice_decision_count", 0) for r in scenario_results),
        "emulator_commit": baseline_check["actual_commit"],
        "emulator_dll_sha256": baseline_check["actual_dll_sha256"],
        "emulator_stage1_dll_sha256": baseline_check["actual_stage1_sha256"],
        "choice_semantics_provenance": choice_table.provenance(),
        "harness_code_provenance": harness_code_provenance(),
        "heuristic_version": HEURISTIC_VERSION,
        "max_decisions": args.max_decisions, "max_wall_seconds": args.max_wall_seconds,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
