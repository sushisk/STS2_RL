"""Online (no-lookahead) evaluation harness: runs a fixed scenario manifest through the
Policy+choice-Heuristic-fallback path (policy_agent.py) and, independently from the
identical initial state, the existing production Heuristic-only baseline
(HeuristicAgent greedy), logging every field the initial instructions' "必須ログ"
section requires and writing a comparison summary.

Run:
    python online_policy_eval.py --manifest known10 --out <dir>
    python online_policy_eval.py --manifest unused_50 --out <dir> [--measure-agreement]

Manifests: "known10" (fixed_50_scenarios.json[:10], the long-exercised regression set -
previously used to validate Heuristic trajectory generation, NEVER used for teacher2000
training since teacher2000 sampled only floor_states_train.jsonl). "unused_50" /
"unused_200" / "unused_500" (build_unused_manifests.py's output - floor_states from
validation/test/benchmark splits, never sampled for ANY teacher-data generation).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

_COMBAT_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _COMBAT_DIR / "data"
_ENV_DIR = _COMBAT_DIR / "env"
for _p in (_COMBAT_DIR, _DATA_DIR, _ENV_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import battle_state_key  # noqa: E402
from combat_env import CombatEnv  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402
from legacy.policy_agent import build_policy_agent, CHOICE_FALLBACK_ACTION_TYPES  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from generate_heuristic_trajectories import (  # noqa: E402
    unrestorable_forced_moves,
    unsupported_pending_choice,
)

MANIFEST_DIR = Path(__file__).resolve().parent
FIXED50_PATH = _COMBAT_DIR / "evaluation" / "benchmark_states" / "fixed_50_scenarios.json"

MAX_DECISIONS_DEFAULT = 60
MAX_WALL_SECONDS_DEFAULT = 90.0


def harness_code_provenance() -> dict:
    """C:\\STS2_RL is not a git repository (no commit hashes available here), so this
    records the Combat-side bugfix this run depends on by description + verification
    result instead - see Outputs/reports/rl_policy_online_eval_unused200_report_20260724.md
    section 2 for the full writeup."""
    return {
        "fix": "emulator_bridge.py::to_plain() now converts System.Decimal (orb "
        "basePassiveValue/baseEvokeValue) to a plain Python float instead of leaving a "
        "live CLR object in engine_state - BattleEmulator.clone_state()/with_shuffle_seed() "
        "could not copy.deepcopy() such a value ('cannot pickle Decimal object'). Combat-side "
        "only - C:\\STS2_Emulator and Training/ were not modified.",
        "file_changed": "Combat/emulator_bridge.py",
        "fixed_on": "2026-07-24",
        "verification": "Combat/tests/test_scenario_v2.py: 32/32 passed (was 31/32 before the "
        "fix's second iteration - see report for the two-step fix history)",
    }


def manifest_file_sha256(name: str) -> "tuple[str, str] | tuple[None, None]":
    if name == "known10":
        return str(FIXED50_PATH), _sha256_file(FIXED50_PATH)
    path = MANIFEST_DIR / f"{name}_manifest.jsonl"
    if not path.exists():
        return None, None
    return str(path), _sha256_file(path)


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def emulator_git_commit() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", r"C:\STS2_Emulator", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def load_manifest(name: str) -> list[dict]:
    if name == "known10":
        with FIXED50_PATH.open(encoding="utf-8") as f:
            specs = json.load(f)[:10]
        rows = []
        for i, spec in enumerate(specs):
            run_id = (spec.get("source") or {}).get("server_id", f"fixed50-{i}")
            rows.append(
                {"trajectory_id": f"fixed50:{run_id}-{i}", "source_run_id": run_id, "source_combat_index": i, "spec": spec}
            )
        return rows
    path = MANIFEST_DIR / f"{name}_manifest.jsonl"
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


ACTION_SEMANTIC_KEYS = ("action_type", "semantic_key")


def action_semantic_key(action: dict) -> tuple:
    return (action.get("action_type"), action.get("semantic_key", ""))


def encounter_key(spec: dict) -> str:
    """Groups scenarios by monster composition only (character-independent) - matches
    Training/sts2_training/dataset.py::encounter_key()'s convention for the same
    grouping concept, kept consistent so "encounter別" figures line up with how
    Training already talks about encounters."""
    monster_ids = sorted(e.get("monster_id") for e in (spec.get("enemies") or []) if e.get("monster_id"))
    return "+".join(monster_ids) if monster_ids else "__UNKNOWN__"


def make_heuristic_decider(heuristic_agent) -> Callable:
    def _decide(battle_state, legal_actions, deadline):
        t0 = time.perf_counter()
        chosen, action_scores = heuristic_agent.choose_action_with_detail(battle_state, deadline=deadline)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        enemy_index = next(
            (
                c["enemy_index"]
                for c in action_scores
                if c["action_id"] == chosen.action_id and c["target_index"] == chosen.target_index
            ),
            None,
        )
        return {
            "decision_source": "heuristic_baseline",
            "chosen_action": chosen.action,
            "chosen_target_index": chosen.target_index,
            "chosen_enemy_index": enemy_index,
            "heuristic_latency_ms": elapsed_ms,
        }

    return _decide


def make_logging_continuation_resolver(inner_resolver: Callable, choice_table: "ChoiceSemanticsTable", sink: list, decision_index: int) -> Callable:
    """Wraps `inner_resolver` (whatever BattleEmulator.apply_action() would otherwise
    use unchanged - see call site) with a pure logging side-channel. This is the ONLY
    way to observe ActionContinuation-scoped choices (HOLOGRAM/SURVIVOR/BURNING_PACT/
    ARMAMENTS/NIGHTMARE/...): they are auto-resolved INSIDE a single env.step() call
    (see apply_action()'s continuation loop) and never surface as their own top-level
    decision the way a StartOfCombat-scope choice_card does - build_choice_scenarios_
    manifest.py's own module docstring documents this finding in full.

    Does NOT change what gets chosen - `inner_resolver` (HeuristicAgent's or
    BattleEmulator's own existing continuation heuristic, whichever the caller already
    used) makes the actual decision, exactly as it would with no wrapper at all. This
    function only reads `battle_state.engine_state["pendingChoice"]` before delegating,
    and appends one entry to `sink` per continuation step encountered."""

    def _resolver(game, battle_state, legal_actions, deadline=None):
        pending = battle_state.engine_state.get("pendingChoice")
        resolution = choice_table.resolve(pending)
        sink.append(
            {
                "decision_index": decision_index,
                "emulator_fact": resolution["emulator_fact"],
                "resolved": resolution["resolved"],
                "lookup_provenance": {
                    "lookupVersion": choice_table.table_version,
                    "lookupSha256": choice_table.lookup_sha256,
                },
            }
        )
        return inner_resolver(game, battle_state, legal_actions, deadline)

    return _resolver


def run_episode(
    decider_name: str,
    base_state,
    spec: dict,
    emulator,
    decide_fn: Callable,
    trajectory_id: str,
    max_decisions: int,
    max_wall_seconds: float,
    shadow_heuristic_agent=None,
    choice_table: "ChoiceSemanticsTable | None" = None,
) -> dict:
    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(emulator.clone_state(base_state), spec)

    decisions: list[dict] = []
    seen_keys: set[tuple] = set()
    start = time.time()
    deadline = start + max_wall_seconds
    termination_reason = None
    decision_index = 0
    illegal_action_count = 0
    step_exception = None
    shadow_overhead_seconds = 0.0
    first_divergence: "dict | None" = None
    continuation_choices: list = []

    while not env.battle_state.is_terminal and decision_index < max_decisions:
        key = battle_state_key(env.battle_state)
        if key in seen_keys:
            termination_reason = "cycle_detected"
            break
        seen_keys.add(key)

        forced_moves = unrestorable_forced_moves(env.battle_state.engine_state)
        if forced_moves:
            termination_reason = "non_learning_transition:unrestorable_forced_move"
            break
        unsupported_choice = unsupported_pending_choice(env.battle_state.engine_state)
        if unsupported_choice:
            termination_reason = f"unsupported_pending_choice:{unsupported_choice['choice_type']}"
            break
        if time.time() >= deadline:
            termination_reason = f"truncated_at_time_budget:{max_wall_seconds}s"
            break

        legal = env.get_legal_actions()
        if not legal:
            termination_reason = "no_legal_actions_while_non_terminal"
            break

        t0 = time.time()
        try:
            record = decide_fn(env.battle_state, legal, deadline)
        except Exception as exc:  # noqa: BLE001
            termination_reason = f"decider_exception:{type(exc).__name__}"
            step_exception = {"type": type(exc).__name__, "message": str(exc)[:500], "traceback": traceback.format_exc()}
            break
        decide_elapsed_ms = (time.time() - t0) * 1000.0

        # Choice Semantics enrichment - LOGGING ONLY (see choice_semantics.py's module
        # docstring invariant): attached whenever this decision's legal_actions include
        # any choice_card/choice_skip/choice_confirm option, regardless of which arm/
        # decider is running - the existing choice-type routing to Heuristic fallback
        # (policy_agent.py) is completely unaffected by anything computed here.
        if choice_table is not None and any(a.get("action_type") in CHOICE_FALLBACK_ACTION_TYPES for a in legal):
            resolution = choice_table.resolve(env.battle_state.engine_state.get("pendingChoice"))
            record["choice_semantics"] = {
                "emulator_fact": resolution["emulator_fact"],
                "resolved": resolution["resolved"],
                "lookup_provenance": {
                    "lookupVersion": choice_table.table_version,
                    "lookupSha256": choice_table.lookup_sha256,
                },
            }

        if shadow_heuristic_agent is not None:
            try:
                t0s = time.perf_counter()
                shadow_chosen, shadow_scores = shadow_heuristic_agent.choose_action_with_detail(env.battle_state, deadline=None)
                shadow_ms = (time.perf_counter() - t0s) * 1000.0
                shadow_overhead_seconds += shadow_ms / 1000.0
                shadow_enemy_index = next(
                    (
                        c["enemy_index"]
                        for c in shadow_scores
                        if c["action_id"] == shadow_chosen.action_id and c["target_index"] == shadow_chosen.target_index
                    ),
                    None,
                )
                record["heuristic_shadow_action"] = shadow_chosen.action
                record["heuristic_shadow_latency_ms"] = shadow_ms
                chosen_action_for_cmp = record.get("chosen_action")
                record["agrees_with_heuristic"] = (
                    chosen_action_for_cmp is not None
                    and action_semantic_key(chosen_action_for_cmp) == action_semantic_key(shadow_chosen.action)
                )
                # First decision where this episode's actual choice would have differed
                # from Heuristic's counterfactual choice AT THE SAME STATE - i.e. the
                # exact point the two independent trajectories are about to fork. Captured
                # unconditionally here (cheap - one snapshot per episode at most); whether
                # it's actually reported is decided later in run_scenario(), gated on the
                # two arms' FINAL outcomes actually differing (per the task instructions -
                # "結果が異なるScenarioについて").
                if first_divergence is None and record["agrees_with_heuristic"] is False:
                    first_divergence = {
                        "decision_index": decision_index,
                        "observation": env.battle_state.engine_state,
                        "legal_actions": legal,
                        "policy_selected_action_index": record.get("selected_action_index"),
                        "policy_confidence": record.get("confidence"),
                        "policy_ranked_action_indices": record.get("ranked_action_indices"),
                        "policy_chosen_action": chosen_action_for_cmp,
                        "policy_chosen_target_index": record.get("chosen_target_index"),
                        "policy_chosen_enemy_index": record.get("chosen_enemy_index"),
                        "policy_target_ambiguous": record.get("target_ambiguous"),
                        "policy_decision_source": record.get("decision_source"),
                        "heuristic_chosen_action": shadow_chosen.action,
                        "heuristic_chosen_target_index": shadow_chosen.target_index,
                        "heuristic_chosen_enemy_index": shadow_enemy_index,
                        "value_output": record.get("value_output"),
                        "policy_decision_index_at_branch": decision_index,
                    }
            except Exception as exc:  # noqa: BLE001
                record["heuristic_shadow_exception"] = f"{type(exc).__name__}: {exc}"

        chosen_action = record.get("chosen_action")
        if chosen_action is None:
            termination_reason = "decider_returned_no_action"
            break

        legal_ids = {a["action_id"] for a in legal}
        if chosen_action.get("action_id") not in legal_ids:
            illegal_action_count += 1
            termination_reason = "illegal_action_selected"
            record["illegal_action"] = True
            decisions.append(
                {
                    "decision_index": decision_index,
                    "trajectory_id": trajectory_id,
                    **record,
                    "legal_action_count": len(legal),
                    "decide_elapsed_ms": decide_elapsed_ms,
                }
            )
            break

        step_kwargs: dict[str, Any] = {}
        if choice_table is not None:
            # Logs ActionContinuation-scoped choices (HOLOGRAM/SURVIVOR/BURNING_PACT/
            # ARMAMENTS/NIGHTMARE/...) that get auto-resolved INSIDE this single step()
            # call - see make_logging_continuation_resolver()'s docstring. The actual
            # resolution logic is unchanged (BattleEmulator's own existing default),
            # this only adds a logging side-channel.
            step_kwargs["continuation_resolver"] = make_logging_continuation_resolver(
                emulator._default_choose_action_continuation_live,  # noqa: SLF001 - white-box, same pattern as elsewhere in this file
                choice_table, continuation_choices, decision_index,
            )
        try:
            result = env.step(
                chosen_action,
                target_enemy_index=record.get("chosen_enemy_index"),
                target_index=record.get("chosen_target_index"),
                **step_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            termination_reason = f"step_exception:{type(exc).__name__}"
            step_exception = {"type": type(exc).__name__, "message": str(exc)[:500], "traceback": traceback.format_exc()}
            decisions.append(
                {
                    "decision_index": decision_index,
                    "trajectory_id": trajectory_id,
                    **record,
                    "legal_action_count": len(legal),
                    "decide_elapsed_ms": decide_elapsed_ms,
                }
            )
            break

        decisions.append(
            {
                "decision_index": decision_index,
                "trajectory_id": trajectory_id,
                **record,
                "legal_action_count": len(legal),
                "decide_elapsed_ms": decide_elapsed_ms,
                "done": result["done"],
                "outcome": result["info"]["outcome"],
            }
        )
        decision_index += 1

    wall_seconds = time.time() - start
    final_state = env.battle_state
    outcome = final_state.outcome if final_state.is_terminal else "in_progress"
    truncated = not final_state.is_terminal
    fallback_reason_counts = Counter(
        d.get("fallback_reason") for d in decisions if d.get("decision_source") == "heuristic_fallback"
    )
    return {
        "trajectory_id": trajectory_id,
        "decider": decider_name,
        "decisions": decisions,
        "decision_count": len(decisions),
        "final_outcome": outcome,
        "truncated": truncated,
        "termination_reason": termination_reason,
        "illegal_action_count": illegal_action_count,
        "step_exception": step_exception,
        "wall_seconds": wall_seconds,
        "shadow_overhead_seconds": shadow_overhead_seconds,
        "wall_seconds_excluding_shadow": wall_seconds - shadow_overhead_seconds,
        "final_hp": final_state.engine_state.get("hp"),
        "final_max_hp": final_state.engine_state.get("maxHp"),
        "final_potion_count": len([p for p in (final_state.engine_state.get("potions") or []) if p]),
        "fallback_count": sum(1 for d in decisions if d.get("decision_source") == "heuristic_fallback"),
        "fallback_reason_counts": dict(fallback_reason_counts),
        "low_confidence_count": sum(1 for d in decisions if d.get("low_confidence")),
        "policy_exception_count": sum(1 for d in decisions if d.get("policy_exception")),
        "action_mapping_mismatch_count": sum(1 for d in decisions if d.get("action_mapping_mismatch")),
        "target_ambiguous_count": sum(1 for d in decisions if d.get("target_ambiguous")),
        "agreement_count": sum(1 for d in decisions if d.get("agrees_with_heuristic") is True),
        "agreement_eligible_count": sum(1 for d in decisions if "agrees_with_heuristic" in d),
        "first_divergence_from_heuristic": first_divergence,
        "choice_decision_count": sum(1 for d in decisions if "choice_semantics" in d),
        "continuation_choices": continuation_choices,
        "continuation_choice_count": len(continuation_choices),
    }


def classify_branch_point(
    fd: dict, policy_final_outcome: str, heuristic_final_outcome: str, low_confidence_threshold: float
) -> dict:
    """Tags per this task's "分岐点分析" categories. The five content tags
    (end_turn/potion/target/card-to-card/low-confidence) are NOT mutually exclusive - a
    branch can legitimately match more than one (e.g. a low-confidence potion decision) -
    reported as independent booleans rather than forced into one bucket."""
    p_action = fd.get("policy_chosen_action") or {}
    h_action = fd.get("heuristic_chosen_action") or {}
    p_type = p_action.get("action_type")
    h_type = h_action.get("action_type")
    p_params = p_action.get("parameters") or {}
    h_params = h_action.get("parameters") or {}
    same_card = p_type == "card" and h_type == "card" and p_params.get("cardId") == h_params.get("cardId")

    if policy_final_outcome == "victory" and heuristic_final_outcome != "victory":
        outcome_class = "policy_better"
    elif heuristic_final_outcome == "victory" and policy_final_outcome != "victory":
        outcome_class = "policy_worse"
    else:
        outcome_class = "other_non_victory_difference"

    return {
        "outcome_class": outcome_class,
        "end_turn_related": p_type == "system" or h_type == "system",
        "potion_related": p_type == "potion" or h_type == "potion",
        "target_difference": same_card and fd.get("policy_chosen_enemy_index") != fd.get("heuristic_chosen_enemy_index"),
        "card_to_card_difference": p_type == "card" and h_type == "card" and p_params.get("cardId") != h_params.get("cardId"),
        "low_confidence_related": (fd.get("policy_confidence") or 0.0) < low_confidence_threshold,
    }


def run_scenario(
    row: dict,
    emulator,
    heuristic_agent,
    policy_agent,
    max_decisions: int,
    max_wall_seconds: float,
    measure_agreement: bool,
    low_confidence_threshold: float = 0.5,
    choice_table: "ChoiceSemanticsTable | None" = None,
) -> dict:
    spec = row["spec"]
    trajectory_id = row.get("trajectory_id") or f"{row.get('source_run_id')}-{row.get('source_combat_index')}"
    pre = preflight_validate(spec, emulator)
    if pre["status"] != "ok":
        return {"trajectory_id": trajectory_id, "status": "quarantined", "reasons": pre["reasons"], "diffs": pre["diffs"]}

    base_state = pre["battle_state"]
    heuristic_decider = make_heuristic_decider(heuristic_agent)

    policy_result = run_episode(
        "policy", base_state, spec, emulator, policy_agent.decide, trajectory_id, max_decisions, max_wall_seconds,
        shadow_heuristic_agent=heuristic_agent if measure_agreement else None, choice_table=choice_table,
    )
    heuristic_result = run_episode(
        "heuristic_baseline", base_state, spec, emulator, heuristic_decider, trajectory_id, max_decisions, max_wall_seconds,
        choice_table=choice_table,
    )

    branch_point = None
    fd = policy_result.pop("first_divergence_from_heuristic", None)
    if policy_result["final_outcome"] != heuristic_result["final_outcome"]:
        if fd is not None:
            classification = classify_branch_point(
                fd, policy_result["final_outcome"], heuristic_result["final_outcome"], low_confidence_threshold
            )
            branch_point = {
                "trajectory_id": trajectory_id,
                "encounter_key": encounter_key(spec),
                **fd,
                "policy_final_outcome": policy_result["final_outcome"],
                "heuristic_final_outcome": heuristic_result["final_outcome"],
                "policy_final_hp": policy_result["final_hp"],
                "heuristic_final_hp": heuristic_result["final_hp"],
                # Decisions the POLICY arm itself took from the branch point to its own
                # episode end - NOT a step-aligned comparison against Heuristic's path
                # (the two trajectories occupy different states after this point by
                # definition), just a rough "how much of the fight followed this fork"
                # proxy.
                "policy_decisions_after_divergence": policy_result["decision_count"] - fd["decision_index"],
                **classification,
            }

    return {
        "trajectory_id": trajectory_id,
        "status": "ok",
        "encounter_key": encounter_key(spec),
        "policy": policy_result,
        "heuristic": heuristic_result,
        "branch_point": branch_point,
    }


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r["status"] == "ok"]
    quarantined = [r for r in results if r["status"] != "ok"]

    def arm_stats(arm: str) -> dict:
        episodes = [r[arm] for r in ok]
        n = len(episodes)
        if n == 0:
            return {"n": 0}
        wins = sum(1 for e in episodes if e["final_outcome"] == "victory")
        non_terminal = sum(1 for e in episodes if e["truncated"])
        illegal = sum(e["illegal_action_count"] for e in episodes)
        exceptions = sum(1 for e in episodes if e["step_exception"] is not None)
        mismatches = sum(e["action_mapping_mismatch_count"] for e in episodes)
        empty_legal_nonterminal = sum(1 for e in episodes if e["termination_reason"] == "no_legal_actions_while_non_terminal")
        fallback_total = sum(e["fallback_count"] for e in episodes)
        decisions_total = sum(e["decision_count"] for e in episodes)
        agreement_elig = sum(e["agreement_eligible_count"] for e in episodes)
        agreement_hit = sum(e["agreement_count"] for e in episodes)
        target_ambiguous_total = sum(e["target_ambiguous_count"] for e in episodes)
        outcome_breakdown = Counter()
        for e in episodes:
            outcome_breakdown["truncated" if e["truncated"] else e["final_outcome"]] += 1
        fallback_reason_totals = Counter()
        for e in episodes:
            fallback_reason_totals.update(e.get("fallback_reason_counts") or {})
        decision_counts_sorted = sorted(e["decision_count"] for e in episodes)
        mid = len(decision_counts_sorted) // 2
        median_decisions = (
            decision_counts_sorted[mid]
            if len(decision_counts_sorted) % 2
            else (decision_counts_sorted[mid - 1] + decision_counts_sorted[mid]) / 2
        )
        return {
            "n": n,
            "wins": wins,
            "win_rate": wins / n,
            "outcome_breakdown": dict(outcome_breakdown),
            "non_terminal_or_truncated": non_terminal,
            "normal_progress_rate": (n - non_terminal - empty_legal_nonterminal) / n,
            "illegal_action_count": illegal,
            "exception_count": exceptions,
            "action_mapping_mismatch_count": mismatches,
            "empty_legal_actions_nonterminal_count": empty_legal_nonterminal,
            "avg_decision_count": decisions_total / n,
            "median_decision_count": median_decisions,
            "avg_wall_seconds_per_combat": sum(e["wall_seconds"] for e in episodes) / n,
            "avg_wall_seconds_per_combat_excluding_shadow_eval": sum(e["wall_seconds_excluding_shadow"] for e in episodes) / n,
            "avg_final_hp": sum(e["final_hp"] or 0 for e in episodes) / n,
            "avg_final_potion_count": sum(e["final_potion_count"] for e in episodes) / n,
            "fallback_count_total": fallback_total,
            "fallback_rate": (fallback_total / decisions_total) if decisions_total else None,
            "fallback_reason_counts": dict(fallback_reason_totals),
            "low_confidence_count_total": sum(e["low_confidence_count"] for e in episodes),
            "low_confidence_rate": (sum(e["low_confidence_count"] for e in episodes) / decisions_total) if decisions_total else None,
            "policy_exception_count_total": sum(e["policy_exception_count"] for e in episodes),
            "target_ambiguous_count_total": target_ambiguous_total,
            "target_ambiguous_rate": (target_ambiguous_total / decisions_total) if decisions_total else None,
            "agreement_eligible_decisions": agreement_elig,
            "agreement_with_heuristic_rate": (agreement_hit / agreement_elig) if agreement_elig else None,
        }

    policy_decision_latencies = [
        d["policy_latency_ms"]
        for r in ok
        for d in r["policy"]["decisions"]
        if d.get("policy_latency_ms") is not None
    ]
    value_latencies = [
        d["value_latency_ms"] for r in ok for d in r["policy"]["decisions"] if d.get("value_latency_ms") is not None
    ]

    def _pct(values, p):
        if not values:
            return None
        s = sorted(values)
        idx = min(len(s) - 1, int(round(p * (len(s) - 1))))
        return s[idx]

    encounter_stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "policy_wins": 0, "heuristic_wins": 0})
    for r in ok:
        s = encounter_stats[r.get("encounter_key", "__UNKNOWN__")]
        s["n"] += 1
        if r["policy"]["final_outcome"] == "victory":
            s["policy_wins"] += 1
        if r["heuristic"]["final_outcome"] == "victory":
            s["heuristic_wins"] += 1
    for s in encounter_stats.values():
        s["policy_win_rate"] = s["policy_wins"] / s["n"]
        s["heuristic_win_rate"] = s["heuristic_wins"] / s["n"]

    policy_only_defeats = [
        r["trajectory_id"] for r in ok if r["policy"]["final_outcome"] == "defeat" and r["heuristic"]["final_outcome"] != "defeat"
    ]
    heuristic_only_defeats = [
        r["trajectory_id"] for r in ok if r["heuristic"]["final_outcome"] == "defeat" and r["policy"]["final_outcome"] != "defeat"
    ]
    divergent_scenarios = [r["trajectory_id"] for r in ok if r["policy"]["final_outcome"] != r["heuristic"]["final_outcome"]]
    branch_points_captured = sum(1 for r in ok if r.get("branch_point") is not None)

    action_type_agreement: dict[str, dict] = defaultdict(lambda: {"n": 0, "agree": 0})
    for r in ok:
        for d in r["policy"]["decisions"]:
            if "agrees_with_heuristic" not in d:
                continue
            at = (d.get("chosen_action") or {}).get("action_type") or "unknown"
            action_type_agreement[at]["n"] += 1
            if d.get("agrees_with_heuristic") is True:
                action_type_agreement[at]["agree"] += 1
    for v in action_type_agreement.values():
        v["agreement_rate"] = (v["agree"] / v["n"]) if v["n"] else None

    branch_points = [r["branch_point"] for r in ok if r.get("branch_point") is not None]
    branch_point_classification_counts = {
        "policy_better": sum(1 for b in branch_points if b["outcome_class"] == "policy_better"),
        "policy_worse": sum(1 for b in branch_points if b["outcome_class"] == "policy_worse"),
        "other_non_victory_difference": sum(1 for b in branch_points if b["outcome_class"] == "other_non_victory_difference"),
        "end_turn_related": sum(1 for b in branch_points if b["end_turn_related"]),
        "potion_related": sum(1 for b in branch_points if b["potion_related"]),
        "target_difference": sum(1 for b in branch_points if b["target_difference"]),
        "card_to_card_difference": sum(1 for b in branch_points if b["card_to_card_difference"]),
        "low_confidence_related": sum(1 for b in branch_points if b["low_confidence_related"]),
    }

    # Two distinct sources, unified for reporting: `choice_semantics` on a top-level
    # decision (StartOfCombat-scope choice_card, visible as its own decision) and
    # `continuation_choices` (ActionContinuation-scope choices auto-resolved inside a
    # single env.step() call - see make_logging_continuation_resolver()'s docstring).
    choice_decisions = [
        d["choice_semantics"]
        for r in ok
        for arm in ("policy", "heuristic")
        for d in r[arm]["decisions"]
        if "choice_semantics" in d
    ] + [
        cc
        for r in ok
        for arm in ("policy", "heuristic")
        for cc in r[arm].get("continuation_choices") or []
    ]
    n_choice = len(choice_decisions)
    if n_choice:
        pending_choice_present = sum(1 for d in choice_decisions if d["emulator_fact"].get("choiceType") is not None)
        origin_present = sum(
            1
            for d in choice_decisions
            if d["emulator_fact"].get("originEntityType") and d["emulator_fact"].get("originEntityId")
        )
        operation_known = sum(
            1 for d in choice_decisions if d["emulator_fact"].get("choiceOperation") not in (None, "unknown")
        )
        mode_counts = Counter(d["resolved"]["operationMode"] for d in choice_decisions)
        status_counts = Counter(d["resolved"]["lookupStatus"] for d in choice_decisions)
        choice_semantics_stats = {
            "n_choice_decisions": n_choice,
            "pending_choice_rate": pending_choice_present / n_choice,
            "origin_resolved_rate": origin_present / n_choice,
            "operation_known_rate": operation_known / n_choice,
            "normalized_rate": mode_counts.get("normalized", 0) / n_choice,
            "passthrough_rate": mode_counts.get("passthrough", 0) / n_choice,
            "unknown_rate": mode_counts.get("unknown", 0) / n_choice,
            "ambiguous_rate": status_counts.get("ambiguous_match", 0) / n_choice,
            "operation_mode_counts": dict(mode_counts),
            "lookup_status_counts": dict(status_counts),
        }
    else:
        choice_semantics_stats = {"n_choice_decisions": 0}

    return {
        "total_scenarios": len(results),
        "ok": len(ok),
        "quarantined": len(quarantined),
        "quarantine_reasons": {r["trajectory_id"]: r["reasons"] for r in quarantined},
        "encounter_win_rates": dict(encounter_stats),
        "policy_only_defeats": policy_only_defeats,
        "heuristic_only_defeats": heuristic_only_defeats,
        "divergent_final_outcome_scenarios": divergent_scenarios,
        "branch_points_captured": branch_points_captured,
        "branch_point_classification_counts": branch_point_classification_counts,
        "agreement_rate_by_action_type": dict(action_type_agreement),
        "choice_semantics_stats": choice_semantics_stats,
        "policy_arm": arm_stats("policy"),
        "heuristic_arm": arm_stats("heuristic"),
        "policy_decision_latency_ms": {
            "mean": (sum(policy_decision_latencies) / len(policy_decision_latencies)) if policy_decision_latencies else None,
            "p50": _pct(policy_decision_latencies, 0.5),
            "p95": _pct(policy_decision_latencies, 0.95),
            "n": len(policy_decision_latencies),
        },
        "value_latency_ms": {
            "mean": (sum(value_latencies) / len(value_latencies)) if value_latencies else None,
            "p50": _pct(value_latencies, 0.5),
            "p95": _pct(value_latencies, 0.95),
            "n": len(value_latencies),
        },
        "speedup_policy_vs_heuristic_wall_seconds": (
            arm_stats("heuristic").get("avg_wall_seconds_per_combat", 0)
            / arm_stats("policy")["avg_wall_seconds_per_combat_excluding_shadow_eval"]
            if arm_stats("policy").get("n") and arm_stats("policy")["avg_wall_seconds_per_combat_excluding_shadow_eval"]
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="known10 | unused_50 | unused_200 | unused_500")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-decisions", type=int, default=MAX_DECISIONS_DEFAULT)
    parser.add_argument("--max-wall-seconds", type=float, default=MAX_WALL_SECONDS_DEFAULT)
    parser.add_argument("--low-confidence-threshold", type=float, default=0.5)
    parser.add_argument("--measure-agreement", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--choice-semantics", action="store_true", help="attach choice_semantics enrichment to choice_* decision logs")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_manifest(args.manifest)
    if args.limit:
        rows = rows[: args.limit]

    emulator, heuristic_agent, policy_agent = build_policy_agent(low_confidence_threshold=args.low_confidence_threshold)
    choice_table = ChoiceSemanticsTable() if args.choice_semantics else None
    if choice_table is not None and not choice_table.loaded_ok:
        print(f"WARNING: ChoiceSemanticsTable failed to load ({choice_table.load_error}); "
              f"choice decisions will log operationMode=unknown/lookupStatus=load_error only.")

    combats_path = out_dir / "combats.jsonl"
    decisions_policy_path = out_dir / "decisions_policy.jsonl"
    decisions_heuristic_path = out_dir / "decisions_heuristic.jsonl"
    branch_points_path = out_dir / "branch_points.jsonl"
    choice_log_path = out_dir / "choice_log.jsonl"

    results: list[dict] = []
    with combats_path.open("w", encoding="utf-8") as combats_f, \
            decisions_policy_path.open("w", encoding="utf-8") as dp_f, \
            decisions_heuristic_path.open("w", encoding="utf-8") as dh_f, \
            branch_points_path.open("w", encoding="utf-8") as bp_f, \
            choice_log_path.open("w", encoding="utf-8") as cl_f:
        for i, row in enumerate(rows):
            t0 = time.time()
            result = run_scenario(
                row, emulator, heuristic_agent, policy_agent,
                args.max_decisions, args.max_wall_seconds, args.measure_agreement,
                low_confidence_threshold=args.low_confidence_threshold, choice_table=choice_table,
            )
            elapsed = time.time() - t0
            print(
                f"[{i + 1}/{len(rows)}] {result['trajectory_id']} status={result['status']} "
                f"({elapsed:.1f}s)"
                + (
                    f" policy_win={result['policy']['final_outcome']} heuristic_win={result['heuristic']['final_outcome']}"
                    if result["status"] == "ok"
                    else f" reasons={result.get('reasons')}"
                )
            )
            results.append(result)
            combats_f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            combats_f.flush()
            if result["status"] == "ok":
                for d in result["policy"]["decisions"]:
                    dp_f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
                for d in result["heuristic"]["decisions"]:
                    dh_f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
                dp_f.flush()
                dh_f.flush()
                if result.get("branch_point") is not None:
                    bp_f.write(json.dumps(result["branch_point"], ensure_ascii=False, default=str) + "\n")
                    bp_f.flush()
                for arm in ("policy", "heuristic"):
                    for d in result[arm]["decisions"]:
                        if "choice_semantics" in d:
                            cl_f.write(json.dumps(
                                {"trajectory_id": result["trajectory_id"], "arm": arm, "source": "top_level_decision",
                                 "decision_index": d["decision_index"], **d["choice_semantics"]},
                                ensure_ascii=False, default=str,
                            ) + "\n")
                    for cc in result[arm].get("continuation_choices") or []:
                        cl_f.write(json.dumps(
                            {"trajectory_id": result["trajectory_id"], "arm": arm, "source": "action_continuation", **cc},
                            ensure_ascii=False, default=str,
                        ) + "\n")
                cl_f.flush()

    summary = summarize(results)
    summary["manifest"] = args.manifest
    summary["manifest_path"], summary["manifest_sha256"] = manifest_file_sha256(args.manifest)
    summary["policy_checkpoint"] = policy_agent.policy_checkpoint_path
    summary["value_checkpoint"] = policy_agent.value_checkpoint_path
    summary["policy_provenance"] = policy_agent.policy.provenance
    summary["value_provenance"] = policy_agent.value.provenance
    summary["emulator_commit_at_eval_time"] = emulator_git_commit()
    summary["harness_code_provenance"] = harness_code_provenance()
    summary["choice_semantics_provenance"] = choice_table.provenance() if choice_table is not None else None
    summary["measure_agreement"] = args.measure_agreement
    summary["max_decisions"] = args.max_decisions
    summary["max_wall_seconds"] = args.max_wall_seconds
    summary["low_confidence_threshold"] = args.low_confidence_threshold

    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
