"""ChoicePolicyAgent: online, no-lookahead Choice Policy inference adapter, layered on
top of the existing PolicyAgent (policy_agent.py, UNCHANGED - imported and delegated to,
never modified or reimplemented) for this task's "Choice Policy限定オンライン評価".

Scope:
    card / potion / system legal_actions          -> delegates to PolicyAgent.decide()
                                                       UNCHANGED (byte-identical to the
                                                       existing Policy-only baseline - see
                                                       module docstring's non-negotiable
                                                       invariant below)
    choice_card (top-level OR ActionContinuation)  -> Choice Semantics resolve ->
                                                       Choice Policy inference -> top-1
                                                       legal action if all fallback
                                                       conditions are clear, else Heuristic
    choice_skip / choice_confirm                   -> ALWAYS Heuristic (never routed to
                                                       Choice Policy - out of its trained
                                                       scope, see sts2_training.choice_data)

Non-negotiable invariant: the normal Policy path (card/potion/system) is NEVER touched
here - `ChoicePolicyAgent.decide()` delegates straight to the already-validated
`PolicyAgent.decide()` for any decision whose legal_actions are entirely standard-typed,
with zero additional logic in between. This guarantees arm A ("通常Policy + Choice
Policy") and arm B ("通常Policy + Heuristic Choice") share byte-identical behavior for
every non-choice decision - any behavioral divergence between the two arms can only
originate at a choice decision, never at a normal one (see this task's 7節 requirement to
separate "通常Policyの行動が最初に分岐したケース" from "Choiceが原因で分岐したケース" -
by this module's construction, the former category is structurally empty for this
comparison).

Fallback conditions (this task's 3節, ALL of them collapse into "any of these -> route to
Heuristic", checked in `choice_policy_select()`):
    - operationMode == "unknown"                         (checked inside ChoiceDecision)
    - Choice Meaning token unrecognized                   (ChoiceDecision sets
                                                            fallback_reason for this - ANY
                                                            non-null fallback_reason it
                                                            returns is treated as decisive
                                                            here, not merely advisory)
    - 0 choice_card candidates                            (checked here before calling
                                                            ChoiceDecision at all)
    - checkpoint / inference exception                    (try/except around the call)
    - "teacher仕様外" Choice                               (folded into the unknown/
                                                            unrecognized-token cases above -
                                                            ChoiceDecision has no separate
                                                            signal for this; documented here
                                                            rather than inventing new
                                                            detection logic not specified by
                                                            either this task or Training's
                                                            interface)
    - top-1 unmapped to any legal action                  (explicit membership check)
    - non-finite model output                             (math.isfinite on confidence/
                                                            margin - ChoiceDecision computes
                                                            these via softmax over the raw
                                                            logits, so a non-finite logit
                                                            surfaces here too)

Never edits Training/ or Emulator/. Never changes lookup/merge_map. Never overrides an
action based on Value (Value is scored/logged only, exactly as PolicyAgent already does).
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any, Callable

from battle_emulator import BattleState
from choice_semantics import ChoiceSemanticsTable
from policy_agent import PolicyAgent, STANDARD_ACTION_TYPES

TRAINING_ROOT = Path(r"C:\STS2_RL\Training")
DEFAULT_CHOICE_POLICY_CHECKPOINT = TRAINING_ROOT / "checkpoints" / "choice_policy_8token_best" / "best.pt"


def _ensure_sts2_training_importable() -> None:
    if str(TRAINING_ROOT) not in sys.path:
        sys.path.insert(0, str(TRAINING_ROOT))


def build_choice_decision(checkpoint_path: "str | Path" = DEFAULT_CHOICE_POLICY_CHECKPOINT):
    _ensure_sts2_training_importable()
    from sts2_training.choice_inference import ChoiceDecision  # noqa: E402

    return ChoiceDecision(checkpoint_path)


def choice_policy_select(
    battle_state: BattleState,
    legal_actions: list[dict],
    choice_decision,
    choice_table: ChoiceSemanticsTable,
) -> dict[str, Any]:
    """Pure function, no side effects beyond timing: resolves one choice_card-bearing
    decision (top-level OR ActionContinuation-scoped - same shape either way, this
    function doesn't care which) via Choice Policy, or reports why it must fall back.
    Used by BOTH ChoicePolicyAgent.decide() (top-level) and
    make_choice_policy_continuation_resolver() (ActionContinuation micro-steps) - single
    source of truth for the fallback-condition list above."""
    pending = battle_state.engine_state.get("pendingChoice") or {}
    resolution = choice_table.resolve(pending)
    resolved = resolution["resolved"]
    out: dict[str, Any] = {
        "emulator_fact": resolution["emulator_fact"],
        "resolved": resolved,
        "choice_policy_result": None,
        "choice_policy_latency_ms": None,
        "decision_source": None,
        "fallback_reason": None,
        "action": None,
    }
    candidates = [a for a in legal_actions if a.get("action_type") == "choice_card"]
    if not candidates:
        out["fallback_reason"] = "no_choice_card_candidates"
        return out

    try:
        t0 = time.perf_counter()
        result = choice_decision(
            battle_state.engine_state,
            legal_actions,
            resolved["operationMode"],
            resolved["normalizedChoiceOperation"],
            resolved["exceptionEntityKey"],
            pending.get("remainingSelectCount") or 0.0,
        )
        out["choice_policy_latency_ms"] = (time.perf_counter() - t0) * 1000.0
    except Exception as exc:  # noqa: BLE001
        out["fallback_reason"] = f"choice_policy_exception:{type(exc).__name__}: {exc}"
        return out

    out["choice_policy_result"] = result
    if result.get("fallback_reason") is not None:
        out["fallback_reason"] = f"choice_policy:{result['fallback_reason']}"
        return out

    top1_id = result.get("top1_action_id")
    top1_conf = result.get("top1_confidence")
    margin = result.get("confidence_margin")
    if top1_id is None:
        out["fallback_reason"] = "choice_policy_no_top1"
        return out
    if top1_conf is None or margin is None or not (math.isfinite(top1_conf) and math.isfinite(margin)):
        out["fallback_reason"] = "choice_policy_non_finite_output"
        return out

    matched = next((a for a in candidates if a.get("action_id") == top1_id), None)
    if matched is None:
        out["fallback_reason"] = "choice_policy_top1_not_in_legal_actions"
        return out

    out["decision_source"] = "choice_policy"
    out["action"] = matched
    return out


class ChoicePolicyAgent:
    """Top-level decide_fn for arm A ("通常Policy + Choice Policy"). Wraps an existing
    PolicyAgent (constructed elsewhere, passed in - never duplicated) and adds Choice
    Policy routing ONLY for choice_card-bearing decisions; choice_skip/choice_confirm-only
    decisions and every standard decision go through exactly the same paths PolicyAgent
    already uses (`PolicyAgent.decide()` itself for standard, `PolicyAgent._heuristic_
    fallback()` - the same private helper `PolicyAgent.decide()` itself calls - for
    Heuristic routing, so the logged fields/behavior of a fallback are identical to the
    existing baseline's fallback, not a reimplementation)."""

    def __init__(
        self,
        policy_agent: PolicyAgent,
        choice_decision,
        choice_table: ChoiceSemanticsTable,
    ) -> None:
        self.policy_agent = policy_agent
        self.choice_decision = choice_decision
        self.choice_table = choice_table

    def decide(self, battle_state: BattleState, legal_actions: list[dict], deadline: "float | None" = None) -> dict:
        present_types = {a.get("action_type") for a in legal_actions}
        non_standard = present_types - STANDARD_ACTION_TYPES
        if not non_standard:
            return self.policy_agent.decide(battle_state, legal_actions, deadline)

        record: dict[str, Any] = {
            "decision_source": None,
            "fallback_reason": None,
            "chosen_action": None,
            "chosen_target_index": None,
            "chosen_enemy_index": None,
            "heuristic_latency_ms": None,
            "value_output": None,
            "value_latency_ms": None,
            "value_exception": None,
            "choice_semantics": None,
            "choice_policy_result": None,
            "choice_policy_latency_ms": None,
        }
        try:
            t0 = time.perf_counter()
            record["value_output"] = self.policy_agent.value(battle_state.engine_state)
            record["value_latency_ms"] = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:  # noqa: BLE001
            record["value_exception"] = f"{type(exc).__name__}: {exc}"

        if "choice_card" not in present_types:
            # choice_skip / choice_confirm only - ALWAYS Heuristic, per this task's 3節.
            return self.policy_agent._heuristic_fallback(  # noqa: SLF001 - same private helper PolicyAgent.decide() itself uses
                record, battle_state, legal_actions, deadline, "choice_skip_or_confirm_only"
            )

        sel = choice_policy_select(battle_state, legal_actions, self.choice_decision, self.choice_table)
        record["choice_semantics"] = {"emulator_fact": sel["emulator_fact"], "resolved": sel["resolved"]}
        record["choice_policy_result"] = sel["choice_policy_result"]
        record["choice_policy_latency_ms"] = sel["choice_policy_latency_ms"]

        if sel["action"] is None:
            return self.policy_agent._heuristic_fallback(  # noqa: SLF001
                record, battle_state, legal_actions, deadline, sel["fallback_reason"]
            )

        record["decision_source"] = "choice_policy"
        record["chosen_action"] = sel["action"]
        return record


def make_ab_continuation_resolver(
    arm: str,
    choice_decision,
    choice_table: ChoiceSemanticsTable,
    fallback_resolver: Callable,
    shadow_resolver: "Callable | None",
    sink: list,
    ctx: dict,
) -> Callable:
    """Continuation-scope counterpart to ChoicePolicyAgent - env.step()'s
    `continuation_resolver` is invoked once per ActionContinuation micro-step (see
    battle_emulator.py::apply_action()'s loop), completely independent of the outer
    decide_fn. Most Choice decisions in this population are ActionContinuation-scoped
    (per this engagement's own teacher-data generation census - 96%), so this is where
    the actual arm A/B behavioral difference mostly happens.

    arm="choice_policy": tries Choice Policy per micro-step via choice_policy_select(),
        falls back to `fallback_resolver` (emulator._default_choose_action_continuation_
        live / HeuristicAgent._choose_action_continuation_live - functionally identical,
        see this module's docstring) on any fallback condition. Also always runs
        `shadow_resolver` (cheap - these are simple scored heuristics, not multi-ply
        search) purely for the "Heuristic Choice action"/agreement log fields this task's
        4節 requires - never used for selection.
    arm="heuristic_choice": always uses `fallback_resolver` directly (the existing baseline
        behavior, unchanged) - Choice Policy is never invoked, matching arm B's spec.
    """

    def _resolver(game, battle_state, legal_actions, deadline=None):
        pending = battle_state.engine_state.get("pendingChoice") or {}
        resolution = choice_table.resolve(pending)
        step_idx = ctx.get("_continuation_step_index", 0)
        ctx["_continuation_step_index"] = step_idx + 1

        record: dict[str, Any] = {
            "source": "action_continuation",
            "arm": arm,
            "trajectory_id": ctx["trajectory_id"],
            "decision_index": ctx["decision_index"],
            "continuation_step_index": step_idx,
            "battle_state": battle_state.engine_state,
            "legal_actions": legal_actions,
            "emulator_fact": resolution["emulator_fact"],
            "resolved": resolution["resolved"],
        }

        if arm == "choice_policy":
            sel = choice_policy_select(battle_state, legal_actions, choice_decision, choice_table)
            record["choice_policy_result"] = sel["choice_policy_result"]
            record["choice_policy_latency_ms"] = sel["choice_policy_latency_ms"]
            if sel["action"] is not None:
                action = sel["action"]
                record["decision_source"] = "choice_policy"
                record["fallback_reason"] = None
            else:
                t0 = time.perf_counter()
                action = fallback_resolver(game, battle_state, legal_actions, deadline)
                record["heuristic_latency_ms"] = (time.perf_counter() - t0) * 1000.0
                record["decision_source"] = "heuristic_fallback"
                record["fallback_reason"] = sel["fallback_reason"]
        else:
            t0 = time.perf_counter()
            action = fallback_resolver(game, battle_state, legal_actions, deadline)
            record["heuristic_latency_ms"] = (time.perf_counter() - t0) * 1000.0
            record["decision_source"] = "heuristic_choice"
            record["fallback_reason"] = None
            record["choice_policy_result"] = None
            record["choice_policy_latency_ms"] = None

        if shadow_resolver is not None:
            try:
                t0 = time.perf_counter()
                shadow_action = shadow_resolver(game, battle_state, legal_actions, deadline)
                record["heuristic_shadow_latency_ms"] = (time.perf_counter() - t0) * 1000.0
                record["heuristic_shadow_action"] = shadow_action
                record["agrees_with_heuristic"] = (
                    action.get("action_type"), action.get("label"), (action.get("parameters") or {}).get("cardId")
                ) == (
                    shadow_action.get("action_type"), shadow_action.get("label"), (shadow_action.get("parameters") or {}).get("cardId")
                )
            except Exception as exc:  # noqa: BLE001
                record["heuristic_shadow_exception"] = f"{type(exc).__name__}: {exc}"

        record["chosen_action"] = action
        record["chosen_action_in_legal"] = action.get("action_id") in {a.get("action_id") for a in legal_actions}
        sink.append(record)
        return action

    return _resolver
