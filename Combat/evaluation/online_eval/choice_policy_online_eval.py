"""Choice Policy限定オンライン評価: runs the fixed 30-scenario manifest (+1 synthetic,
separately) through two arms that share the EXACT SAME normal Policy for card/potion/
system decisions (Combat/policy_agent.py's PolicyAgent, unmodified):

    A: choice_policy_arm    - choice_card decisions routed through Choice Policy
                               (Combat/choice_policy_agent.py::ChoicePolicyAgent),
                               falling back to Heuristic per its documented conditions.
                               choice_skip/choice_confirm always Heuristic.
    B: heuristic_choice_arm - PolicyAgent.decide() used completely unmodified (the
                               already-validated baseline - every choice_* decision goes
                               to Heuristic, exactly as it always has).

Because both arms call the identical PolicyAgent.decide() for every standard decision,
any behavioral divergence between A and B can only originate at a choice decision - see
choice_policy_agent.py's module docstring for why this is true by construction, not just
empirically likely.

Run:
    python choice_policy_online_eval.py --stage a
    python choice_policy_online_eval.py --stage b --out <dir>
    python choice_policy_online_eval.py --stage c --out <dir> [--measure-agreement]
    python choice_policy_online_eval.py --stage synthetic --out <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
_DATA_DIR = _COMBAT_DIR / "data"
_ENV_DIR = _COMBAT_DIR / "env"
for _p in (_COMBAT_DIR, _DATA_DIR, _ENV_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import battle_state_key  # noqa: E402
from combat_env import CombatEnv  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402
from policy_agent import build_policy_agent, CHOICE_FALLBACK_ACTION_TYPES  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from choice_policy_agent import (  # noqa: E402
    ChoicePolicyAgent, build_choice_decision, make_ab_continuation_resolver,
    DEFAULT_CHOICE_POLICY_CHECKPOINT,
)
from generate_heuristic_trajectories import unrestorable_forced_moves, unsupported_pending_choice  # noqa: E402

MAX_DECISIONS_DEFAULT = 60
MAX_WALL_SECONDS_DEFAULT = 90.0
STAGE_B_N = 10
STAGE_C_N = 30

BASELINE_722B019_PATH = _COMBAT_DIR / "policy_baseline" / "combat_state_contract_phase1_emulator_baseline_v1_20260726.json"


def _sha256_file(path: Path) -> "str | None":
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def emulator_git_commit() -> "str | None":
    try:
        out = subprocess.run(["git", "-C", r"C:\STS2_Emulator", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def verify_emulator_baseline() -> dict:
    baseline = json.loads(BASELINE_722B019_PATH.read_text(encoding="utf-8"))
    expected_commit = baseline["emulator"]["commit"]
    expected_dll = baseline["emulator"]["sts2emulator_dll_sha256"]
    from generate_heuristic_trajectories import EMULATOR_DLL_PATH, emulator_dll_sha256
    stage1_path = EMULATOR_DLL_PATH.parent / "Sts2Imported.Stage1.dll"
    actual_commit = emulator_git_commit()
    actual_dll = emulator_dll_sha256()
    actual_stage1 = _sha256_file(stage1_path)
    expected_stage1 = baseline["emulator"]["sts2imported_stage1_dll_sha256"]
    ok = (
        actual_commit == expected_commit
        and actual_dll.lower() == expected_dll.lower()
        and (actual_stage1 or "").lower() == expected_stage1.lower()
    )
    result = {"actual_commit": actual_commit, "actual_dll_sha256": actual_dll, "actual_stage1_sha256": actual_stage1, "match": ok}
    if not ok:
        raise RuntimeError(f"Live Emulator does not match 722b019 baseline: {json.dumps(result, indent=2)}")
    return result


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def action_semantic_key(action: "dict | None") -> tuple:
    if action is None:
        return (None, None, None, None)
    params = action.get("parameters") or {}
    return (action.get("action_type"), action.get("label"), params.get("cardId"), params.get("potionId"))


def run_episode_ab(
    arm: str,
    base_state,
    spec: dict,
    emulator,
    decide_fn: Callable,
    trajectory_id: str,
    max_decisions: int,
    max_wall_seconds: float,
    choice_table: ChoiceSemanticsTable,
    choice_decision,
    heuristic_agent,
    shadow_top_level: bool,
) -> dict:
    """Faithful adaptation of online_policy_eval.py::run_episode() for this task - same
    loop shape/logging fields, but with a per-arm continuation_resolver (this task's
    whole reason for existing; online_policy_eval.py's version hardcodes the Emulator's
    own default there, which would silently make BOTH arms identical for the 96% of
    Choice decisions that are ActionContinuation-scoped)."""
    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(emulator.clone_state(base_state), spec)

    decisions: list[dict] = []
    continuation_choices: list = []
    seen_keys: set[tuple] = set()
    start = time.time()
    deadline = start + max_wall_seconds
    termination_reason = None
    decision_index = 0
    illegal_action_count = 0
    step_exception = None
    first_choice_divergence: "dict | None" = None
    ctx: dict = {"trajectory_id": trajectory_id}

    fallback_resolver = emulator._default_choose_action_continuation_live  # noqa: SLF001 - same pattern used throughout this engagement
    shadow_resolver = heuristic_agent._choose_action_continuation_live if arm == "choice_policy" else None  # noqa: SLF001

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

        legal_types = {a.get("action_type") for a in legal}
        is_top_level_choice = bool(legal_types & CHOICE_FALLBACK_ACTION_TYPES)
        if is_top_level_choice:
            pending = env.battle_state.engine_state.get("pendingChoice") or {}
            resolution = choice_table.resolve(pending)
            record["choice_semantics"] = {
                "emulator_fact": resolution["emulator_fact"], "resolved": resolution["resolved"],
            }
            if shadow_top_level and "choice_card" in legal_types:
                try:
                    t0s = time.perf_counter()
                    shadow_chosen, _ = heuristic_agent.choose_action_with_detail(env.battle_state, deadline=None)
                    record["heuristic_shadow_latency_ms"] = (time.perf_counter() - t0s) * 1000.0
                    record["heuristic_shadow_action"] = shadow_chosen.action
                    record["agrees_with_heuristic"] = action_semantic_key(record.get("chosen_action")) == action_semantic_key(shadow_chosen.action)
                    if first_choice_divergence is None and record["agrees_with_heuristic"] is False:
                        first_choice_divergence = {
                            "decision_index": decision_index, "source": "top_level_decision",
                            "battle_state": env.battle_state.engine_state, "legal_actions": legal,
                            "choice_semantics": record["choice_semantics"],
                            "arm_action": record.get("chosen_action"), "heuristic_action": shadow_chosen.action,
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
            decisions.append({"decision_index": decision_index, "trajectory_id": trajectory_id, **record, "legal_action_count": len(legal), "decide_elapsed_ms": decide_elapsed_ms})
            break

        ctx["decision_index"] = decision_index
        ctx["_continuation_step_index"] = 0
        continuation_resolver = make_ab_continuation_resolver(
            arm, choice_decision, choice_table, fallback_resolver, shadow_resolver, continuation_choices, ctx,
        )
        try:
            result = env.step(
                chosen_action, target_enemy_index=record.get("chosen_enemy_index"),
                target_index=record.get("chosen_target_index"), continuation_resolver=continuation_resolver,
            )
        except Exception as exc:  # noqa: BLE001
            termination_reason = f"step_exception:{type(exc).__name__}"
            step_exception = {"type": type(exc).__name__, "message": str(exc)[:500], "traceback": traceback.format_exc()}
            decisions.append({"decision_index": decision_index, "trajectory_id": trajectory_id, **record, "legal_action_count": len(legal), "decide_elapsed_ms": decide_elapsed_ms})
            break

        # First-divergence detection for ActionContinuation-scoped choices this decision produced
        if first_choice_divergence is None:
            for cc in continuation_choices:
                if cc["decision_index"] != decision_index:
                    continue
                if cc.get("agrees_with_heuristic") is False:
                    first_choice_divergence = {
                        "decision_index": decision_index, "source": "action_continuation",
                        "continuation_step_index": cc["continuation_step_index"],
                        "battle_state": cc["battle_state"], "legal_actions": cc["legal_actions"],
                        "choice_semantics": {"emulator_fact": cc["emulator_fact"], "resolved": cc["resolved"]},
                        "arm_action": cc["chosen_action"], "heuristic_action": cc.get("heuristic_shadow_action"),
                    }
                    break

        decisions.append({
            "decision_index": decision_index, "trajectory_id": trajectory_id, **record,
            "legal_action_count": len(legal), "decide_elapsed_ms": decide_elapsed_ms,
            "done": result["done"], "outcome": result["info"]["outcome"],
        })
        decision_index += 1
        if result["done"]:
            break

    wall_seconds = time.time() - start
    final_state = env.battle_state
    outcome = final_state.outcome if final_state.is_terminal else "in_progress"
    truncated = not final_state.is_terminal
    return {
        "trajectory_id": trajectory_id, "arm": arm, "decisions": decisions,
        "decision_count": len(decisions), "final_outcome": outcome, "truncated": truncated,
        "termination_reason": termination_reason, "illegal_action_count": illegal_action_count,
        "step_exception": step_exception, "wall_seconds": wall_seconds,
        "final_hp": final_state.engine_state.get("hp"), "final_max_hp": final_state.engine_state.get("maxHp"),
        "final_potion_count": len([p for p in (final_state.engine_state.get("potions") or []) if p]),
        "choice_decision_count": sum(1 for d in decisions if "choice_semantics" in d) + len(continuation_choices),
        "continuation_choices": continuation_choices,
        "top_level_choice_fallback_count": sum(1 for d in decisions if "choice_semantics" in d and d.get("decision_source") in ("heuristic_fallback",)),
        "top_level_choice_policy_count": sum(1 for d in decisions if "choice_semantics" in d and d.get("decision_source") == "choice_policy"),
        "first_choice_divergence": first_choice_divergence,
    }


def run_scenario_ab(
    row: dict, emulator, heuristic_agent, policy_agent, choice_policy_agent, choice_decision, choice_table,
    max_decisions: int, max_wall_seconds: float, shadow_top_level: bool,
) -> dict:
    spec = row["spec"]
    trajectory_id = row["trajectory_id"]
    pre = preflight_validate(spec, emulator)
    if pre["status"] != "ok":
        return {"trajectory_id": trajectory_id, "status": "quarantined", "reasons": pre["reasons"]}

    base_state = pre["battle_state"]
    a_result = run_episode_ab(
        "choice_policy", base_state, spec, emulator, choice_policy_agent.decide, trajectory_id,
        max_decisions, max_wall_seconds, choice_table, choice_decision, heuristic_agent, shadow_top_level,
    )
    b_result = run_episode_ab(
        "heuristic_choice", base_state, spec, emulator, policy_agent.decide, trajectory_id,
        max_decisions, max_wall_seconds, choice_table, choice_decision, heuristic_agent, False,
    )

    outcomes_differ = a_result["final_outcome"] != b_result["final_outcome"]
    return {
        "trajectory_id": trajectory_id, "status": "ok",
        "choice_policy_arm": a_result, "heuristic_choice_arm": b_result,
        "outcomes_differ": outcomes_differ,
        "categories": row.get("categories"),
    }


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r["status"] == "ok"]
    quarantined = [r for r in results if r["status"] != "ok"]

    def arm_stats(arm_key: str) -> dict:
        episodes = [r[arm_key] for r in ok]
        n = len(episodes)
        if n == 0:
            return {"n": 0}
        wins = sum(1 for e in episodes if e["final_outcome"] == "victory")
        non_terminal = sum(1 for e in episodes if e["truncated"])
        illegal = sum(e["illegal_action_count"] for e in episodes)
        exceptions = sum(1 for e in episodes if e["step_exception"] is not None)
        choice_decisions_total = sum(e["choice_decision_count"] for e in episodes)
        cp_used = sum(
            e["top_level_choice_policy_count"] + sum(1 for cc in e["continuation_choices"] if cc.get("decision_source") == "choice_policy")
            for e in episodes
        )
        cp_fallback = sum(
            e["top_level_choice_fallback_count"] + sum(1 for cc in e["continuation_choices"] if cc.get("decision_source") == "heuristic_fallback")
            for e in episodes
        )
        agreement_rows = [
            d for e in episodes for d in e["decisions"] if "agrees_with_heuristic" in d
        ] + [cc for e in episodes for cc in e["continuation_choices"] if "agrees_with_heuristic" in cc]
        agree_n = sum(1 for r in agreement_rows if r["agrees_with_heuristic"] is True)
        return {
            "n": n, "wins": wins, "win_rate": wins / n,
            "non_terminal_or_truncated": non_terminal,
            "normal_completion_rate": (n - non_terminal) / n,
            "illegal_action_count": illegal, "exception_count": exceptions,
            "avg_final_hp": sum(e["final_hp"] or 0 for e in episodes) / n,
            "avg_final_potion_count": sum(e["final_potion_count"] for e in episodes) / n,
            "avg_wall_seconds_per_combat": sum(e["wall_seconds"] for e in episodes) / n,
            "total_wall_seconds": sum(e["wall_seconds"] for e in episodes),
            "choice_decision_count_total": choice_decisions_total,
            "choice_policy_used_count": cp_used,
            "choice_policy_usage_rate": (cp_used / choice_decisions_total) if choice_decisions_total else None,
            "choice_fallback_count": cp_fallback,
            "choice_fallback_rate": (cp_fallback / choice_decisions_total) if choice_decisions_total else None,
            "choice_agreement_eligible": len(agreement_rows),
            "choice_agreement_rate": (agree_n / len(agreement_rows)) if agreement_rows else None,
        }

    win_rate_ratio = None
    a_stats = arm_stats("choice_policy_arm")
    b_stats = arm_stats("heuristic_choice_arm")
    if a_stats.get("n") and b_stats.get("win_rate"):
        win_rate_ratio = (a_stats["win_rate"] / b_stats["win_rate"]) if b_stats["win_rate"] else None

    fallback_reason_counts = Counter()
    op_mode_usage = defaultdict(lambda: {"choice_policy": 0, "fallback": 0})
    exception_entity_usage = defaultdict(lambda: {"choice_policy": 0, "fallback": 0})
    candidate_count_bucket_usage = defaultdict(lambda: {"choice_policy": 0, "fallback": 0})
    confidence_margin_bucket = defaultdict(lambda: {"choice_policy": 0, "fallback": 0})

    def bucket_candidates(n):
        if n <= 3:
            return "1-3"
        if n <= 7:
            return "4-7"
        return "8+"

    def bucket_margin(m):
        if m is None:
            return "n/a"
        if m < 0.2:
            return "0-0.2"
        if m < 0.5:
            return "0.2-0.5"
        return "0.5-1.0"

    latencies = []
    for r in ok:
        for d in r["choice_policy_arm"]["decisions"]:
            if "choice_semantics" in d:
                _tally_choice_row(d, fallback_reason_counts, op_mode_usage, exception_entity_usage, candidate_count_bucket_usage, confidence_margin_bucket, bucket_candidates, bucket_margin, latencies)
        for cc in r["choice_policy_arm"]["continuation_choices"]:
            _tally_choice_row(cc, fallback_reason_counts, op_mode_usage, exception_entity_usage, candidate_count_bucket_usage, confidence_margin_bucket, bucket_candidates, bucket_margin, latencies)

    divergent = [r for r in ok if r["outcomes_differ"]]
    cp_only_wins = sum(1 for r in ok if r["choice_policy_arm"]["final_outcome"] == "victory" and r["heuristic_choice_arm"]["final_outcome"] != "victory")
    hc_only_wins = sum(1 for r in ok if r["heuristic_choice_arm"]["final_outcome"] == "victory" and r["choice_policy_arm"]["final_outcome"] != "victory")

    return {
        "scenario_count": len(results), "ok_count": len(ok), "quarantined_count": len(quarantined),
        "choice_policy_arm": a_stats, "heuristic_choice_arm": b_stats, "win_rate_ratio_cp_over_hc": win_rate_ratio,
        "outcomes_differ_count": len(divergent), "outcomes_differ_trajectory_ids": [r["trajectory_id"] for r in divergent],
        "choice_policy_only_wins": cp_only_wins, "heuristic_choice_only_wins": hc_only_wins,
        "choice_fallback_reason_counts": dict(fallback_reason_counts),
        "operation_mode_usage": {k: dict(v) for k, v in op_mode_usage.items()},
        "exception_entity_usage": {k: dict(v) for k, v in exception_entity_usage.items()},
        "candidate_count_bucket_usage": {k: dict(v) for k, v in candidate_count_bucket_usage.items()},
        "confidence_margin_bucket_usage": {k: dict(v) for k, v in confidence_margin_bucket.items()},
        "choice_policy_latency_ms_avg": (sum(latencies) / len(latencies)) if latencies else None,
        "choice_policy_latency_ms_max": max(latencies) if latencies else None,
    }


def _tally_choice_row(d, fallback_reason_counts, op_mode_usage, exception_entity_usage, candidate_count_bucket_usage, confidence_margin_bucket, bucket_candidates, bucket_margin, latencies):
    source = d.get("decision_source")
    resolved = (d.get("choice_semantics") or {}).get("resolved") or d.get("resolved") or {}
    op_mode = resolved.get("operationMode", "n/a")
    exc_key = resolved.get("exceptionEntityKey")
    n_candidates = len([a for a in (d.get("legal_actions") or []) if a.get("action_type") == "choice_card"])
    cpr = d.get("choice_policy_result")
    margin = cpr.get("confidence_margin") if cpr else None
    lat = d.get("choice_policy_latency_ms")
    if lat is not None:
        latencies.append(lat)

    if source == "choice_policy":
        op_mode_usage[op_mode]["choice_policy"] += 1
        if exc_key:
            exception_entity_usage[exc_key]["choice_policy"] += 1
        candidate_count_bucket_usage[bucket_candidates(n_candidates)]["choice_policy"] += 1
        confidence_margin_bucket[bucket_margin(margin)]["choice_policy"] += 1
    elif source == "heuristic_fallback":
        fallback_reason_counts[d.get("fallback_reason") or "unknown"] += 1
        op_mode_usage[op_mode]["fallback"] += 1
        if exc_key:
            exception_entity_usage[exc_key]["fallback"] += 1
        candidate_count_bucket_usage[bucket_candidates(n_candidates)]["fallback"] += 1
        confidence_margin_bucket[bucket_margin(margin)]["fallback"] += 1


def stage_a(args) -> None:
    print("=== Stage A: direct integration test ===")
    baseline_check = verify_emulator_baseline()
    print(f"Emulator baseline check: {baseline_check['match']}")

    choice_decision = build_choice_decision(DEFAULT_CHOICE_POLICY_CHECKPOINT)
    print(f"Choice Policy checkpoint loaded: provenance={json.dumps(choice_decision.provenance, default=str)}")
    print(f"choice_meaning_vocab size={choice_decision.choice_meaning_vocab.size}, merge_map size={len(choice_decision.merge_map)}")

    choice_table = ChoiceSemanticsTable()
    assert choice_table.loaded_ok, choice_table.load_error
    print("ChoiceSemanticsTable loaded ok")

    emulator, heuristic_agent, policy_agent = build_policy_agent()
    choice_policy_agent = ChoicePolicyAgent(policy_agent, choice_decision, choice_table)

    # fallback condition unit checks (synthetic legal_actions, no live Emulator call needed)
    from choice_policy_agent import choice_policy_select

    class _FakeBS:
        def __init__(self, engine_state):
            self.engine_state = engine_state

    no_candidates_bs = _FakeBS({"pendingChoice": {"choiceType": "X"}})
    r = choice_policy_select(no_candidates_bs, [{"action_type": "choice_skip", "action_id": 1}], choice_decision, choice_table)
    assert r["fallback_reason"] == "no_choice_card_candidates", r
    print("fallback: 0 choice_card candidates -> OK")

    unknown_op_bs = _FakeBS({"pendingChoice": {"choiceType": "Unsupported", "originEntityType": None, "originEntityId": None}})
    cand = [{"action_type": "choice_card", "action_id": 1, "label": "X", "parameters": {"cardId": "STRIKE_REGENT"}}]
    r = choice_policy_select(unknown_op_bs, cand, choice_decision, choice_table)
    assert r["fallback_reason"] is not None and "choice_policy:" in r["fallback_reason"], r
    print(f"fallback: operationMode unknown -> OK ({r['fallback_reason']})")

    # 8-token merge map conversion check
    merged = choice_decision.merge_map.get("retrieve_to_hand")
    assert merged == "retrieve", f"expected retrieve_to_hand -> retrieve, got {merged}"
    meaning_id = choice_decision.choice_meaning_vocab.encode("retrieve")
    assert meaning_id != 0, "merged token 'retrieve' should be a known vocab entry"
    unmerged_id = choice_decision.choice_meaning_vocab.encode("relic:GAMBLING_CHIP")
    assert unmerged_id != 0, "unmerged passthrough token 'relic:GAMBLING_CHIP' should be a known vocab entry"
    print(f"8-token conversion: retrieve_to_hand->retrieve (id={meaning_id}), relic:GAMBLING_CHIP passthrough (id={unmerged_id}) -> OK")

    # determinism + action_id mapping: same input twice
    known_bs = _FakeBS({"pendingChoice": {"choiceType": "Unsupported", "originEntityType": "card", "originEntityId": "BURNING_PACT", "choiceOperation": "exhaust", "sourceZone": "hand", "destinationZone": "exhaust_pile", "remainingSelectCount": 2}})
    cand2 = [
        {"action_type": "choice_card", "action_id": 1, "label": "A", "parameters": {"cardId": "STRIKE_REGENT"}},
        {"action_type": "choice_card", "action_id": 2, "label": "B", "parameters": {"cardId": "DEFEND_REGENT"}},
        {"action_type": "choice_confirm", "action_id": 3, "label": "Confirm"},
    ]
    r_map = choice_policy_select(known_bs, cand2, choice_decision, choice_table)
    if r_map["action"] is not None:
        assert r_map["action"]["action_id"] == r_map["choice_policy_result"]["top1_action_id"]
        assert r_map["action"] in cand2
        print(f"action_id mapping: top1_action_id={r_map['choice_policy_result']['top1_action_id']} -> matched candidate action_id={r_map['action']['action_id']} -> OK")
    else:
        print(f"action_id mapping: fell back ({r_map['fallback_reason']}) - no top1 to map, consistent with fallback-first-then-map order")

    r1 = choice_policy_select(known_bs, cand2, choice_decision, choice_table)
    r2 = choice_policy_select(known_bs, cand2, choice_decision, choice_table)
    assert r1["decision_source"] == r2["decision_source"], (r1, r2)
    if r1["action"] is not None:
        assert r1["action"]["action_id"] == r2["action"]["action_id"]
        assert r1["choice_policy_result"]["top1_confidence"] == r2["choice_policy_result"]["top1_confidence"]
    print(f"determinism (same input twice): decision_source={r1['decision_source']} matches -> OK")

    # choice_skip/choice_confirm always routed to Heuristic - verified via
    # ChoicePolicyAgent.decide()'s routing branch only (the actual Heuristic call needs a
    # live GameInstance-backed BattleState, not this synthetic _FakeBS stub - stubbed out
    # here to isolate the routing decision itself, same scope as the checks above).
    captured_reason = {}
    original_fallback = policy_agent._heuristic_fallback
    policy_agent._heuristic_fallback = lambda record, bs, legal, dl, reason: (captured_reason.__setitem__("reason", reason) or {**record, "decision_source": "heuristic_fallback", "fallback_reason": reason})
    try:
        record = choice_policy_agent.decide(known_bs, [{"action_type": "choice_skip", "action_id": 9, "label": "Skip"}, {"action_type": "choice_confirm", "action_id": 10, "label": "Confirm"}], None)
    finally:
        policy_agent._heuristic_fallback = original_fallback
    assert record["decision_source"] == "heuristic_fallback" and record["fallback_reason"] == "choice_skip_or_confirm_only", record
    print("choice_skip/choice_confirm-only routing -> Heuristic OK")

    print("=== Stage A direct integration checks: ALL PASSED ===")


def stage_run(args, stage_name: str, manifest_path: Path, out_dir: Path) -> None:
    print(f"=== Stage {stage_name}: {manifest_path.name} -> {out_dir} ===")
    baseline_check = verify_emulator_baseline()
    print(f"Emulator baseline check: {baseline_check['match']}")

    choice_decision = build_choice_decision(DEFAULT_CHOICE_POLICY_CHECKPOINT)
    choice_table = ChoiceSemanticsTable()
    assert choice_table.loaded_ok, choice_table.load_error
    emulator, heuristic_agent, policy_agent = build_policy_agent()
    choice_policy_agent = ChoicePolicyAgent(policy_agent, choice_decision, choice_table)

    rows = load_jsonl(manifest_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with (out_dir / "combats.jsonl").open("w", encoding="utf-8") as cf:
        for i, row in enumerate(rows):
            t0 = time.time()
            result = run_scenario_ab(
                row, emulator, heuristic_agent, policy_agent, choice_policy_agent, choice_decision, choice_table,
                args.max_decisions, args.max_wall_seconds, args.measure_agreement,
            )
            elapsed = time.time() - t0
            if result["status"] == "ok":
                print(f"[{i + 1}/{len(rows)}] {result['trajectory_id']} cp={result['choice_policy_arm']['final_outcome']} hc={result['heuristic_choice_arm']['final_outcome']} differ={result['outcomes_differ']} ({elapsed:.1f}s)")
            else:
                print(f"[{i + 1}/{len(rows)}] {result['trajectory_id']} status={result['status']} ({elapsed:.1f}s)")
            results.append(result)
            cf.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            cf.flush()

    summary = summarize(results)
    summary["manifest"] = str(manifest_path)
    summary["manifest_sha256"] = _sha256_file(manifest_path)
    summary["emulator_commit"] = baseline_check["actual_commit"]
    summary["choice_policy_checkpoint"] = str(DEFAULT_CHOICE_POLICY_CHECKPOINT)
    summary["choice_policy_checkpoint_sha256"] = _sha256_file(Path(DEFAULT_CHOICE_POLICY_CHECKPOINT))
    summary["choice_semantics_provenance"] = choice_table.provenance()
    summary["choice_decision_provenance"] = choice_decision.provenance
    summary["max_decisions"] = args.max_decisions
    summary["max_wall_seconds"] = args.max_wall_seconds
    summary["measure_agreement"] = args.measure_agreement
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    stop_conditions = {
        "illegal_action": summary["choice_policy_arm"].get("illegal_action_count", 0) + summary["heuristic_choice_arm"].get("illegal_action_count", 0),
        "exception": summary["choice_policy_arm"].get("exception_count", 0) + summary["heuristic_choice_arm"].get("exception_count", 0),
        "quarantined": summary["quarantined_count"],
    }
    print(f"Stop-condition check: {stop_conditions}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["a", "b", "c", "synthetic"])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-decisions", type=int, default=MAX_DECISIONS_DEFAULT)
    parser.add_argument("--max-wall-seconds", type=float, default=MAX_WALL_SECONDS_DEFAULT)
    parser.add_argument("--measure-agreement", action="store_true", default=True)
    args = parser.parse_args()

    if args.stage == "a":
        stage_a(args)
        return

    manifest_map = {
        "b": _HERE / "choice_policy_online_eval_manifest.jsonl",  # first STAGE_B_N rows used
        "c": _HERE / "choice_policy_online_eval_manifest.jsonl",
        "synthetic": _HERE / "choice_policy_online_eval_synthetic_manifest.jsonl",
    }
    manifest_path = manifest_map[args.stage]
    if args.stage == "b":
        rows = load_jsonl(manifest_path)[:STAGE_B_N]
        tmp_path = _HERE / "choice_policy_online_eval_stage_b_manifest.jsonl"
        with tmp_path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        manifest_path = tmp_path

    out_dir = args.out or (_COMBAT_DIR / "evaluation" / "reports" / f"choice_policy_online_eval_stage_{args.stage}")
    stage_run(args, args.stage.upper(), manifest_path, out_dir)


if __name__ == "__main__":
    main()
