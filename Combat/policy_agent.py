"""PolicyAgent: online, no-lookahead Policy/Value inference adapter over CombatEnv.

Scope (see this task's initial instructions - "Policy / Valueオンライン評価"): connects
the already-trained CandidatePolicyNet/ValueNet checkpoints (Training/checkpoints/
policy_teacher2000_seed_20260724, value_teacher2000_seed_20260724) to live Combat
decisions, with NO multi-ply search in the normal path - search stays reserved for
low-confidence monitoring (recorded, not acted on here) and teacher-data generation
(generate_heuristic_trajectories.py, unaffected by this file).

Routing (per the initial instructions):
    card / potion / system legal_actions   -> PolicyDecision
    choice_card / choice_skip / choice_confirm
        (or ANY other non-{system,card,potion} action_type)
                                            -> Heuristic fallback (HeuristicAgent,
                                               already production code - not reimplemented
                                               here)
    Policy low confidence                  -> action UNCHANGED, flagged in the log only
    ValueDetermination                     -> scored and logged every decision, never
                                               used to pick or override the action here

Action-mapping contract (this is the "Emulatorとの入出力変換" / "choice semantics正規化"
part of this task): `normalize_legal_action()` below reshapes a raw CombatEnv/
BattleEmulator legal_action dict ({action_id, action_type, label, is_available,
parameters}) into the flattened shape Training/sts2_training/encoding.py's
ExportEncoder.encode_action() expects ({card_id, potion_id, target_type,
target_enemy_index, raw_parameters, ...}). This MUST stay structurally identical to
Training/export_training_dataset.py::normalize_action() - that function defines the
actual training-time contract the checkpoint was fit against. Training/ is off-limits to
edit or import from operationally (see initial instructions' prohibitions), so this is a
deliberate, minimal reimplementation of that same small mapping, not a shortcut - if the
two ever diverge it is a silent train/serve skew ("action mapping mismatch"), not
something a stack trace will surface, so keep this function reviewed against Training's
copy whenever either side changes.

Target selection for "AnyEnemy" card/potion plays with 2+ alive enemies: Policy's
legal_actions never carry a resolved enemy target for these (verified against the
training export - every candidate's target_enemy_index is left unresolved/None there,
teacher action included; only the ACTUAL chosen target on the teacher's own selected
action was ever known, and that information is not part of what the model conditions
on). Picking a target is therefore a structural adapter responsibility, not a Policy
prediction gap. Deliberately resolved WITHOUT any extra apply_action-based scoring here
(that would itself be a small search) - defaults to the first currently-alive candidate
(target_index=0), flagged in the decision log via `target_ambiguous`/
`alive_enemy_count` for visibility. Revisit before scaling past small evaluation batches
if multi-enemy AnyEnemy decisions turn out to be frequent enough to matter.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from battle_emulator import BattleEmulator, BattleState
from heuristic_agent import HeuristicAgent
from state_evaluator import StateEvaluator

TRAINING_ROOT = Path(r"C:\STS2_RL\Training")
DEFAULT_POLICY_CHECKPOINT = TRAINING_ROOT / "checkpoints" / "policy_teacher2000_seed_20260724" / "best.pt"
DEFAULT_VALUE_CHECKPOINT = TRAINING_ROOT / "checkpoints" / "value_teacher2000_seed_20260724" / "best.pt"

STANDARD_ACTION_TYPES = {"system", "card", "potion"}
CHOICE_FALLBACK_ACTION_TYPES = {"choice_card", "choice_skip", "choice_confirm"}


def normalize_legal_action(action: dict, selected_enemy_index: "int | None" = None) -> dict:
    """Mirrors Training/export_training_dataset.py::normalize_action() exactly - see
    module docstring for why this cannot simply import that function."""
    parameters = action.get("parameters") or {}
    return {
        "action_id": action.get("action_id"),
        "action_type": action.get("action_type"),
        "label": action.get("label"),
        "is_available": action.get("is_available"),
        "card_id": parameters.get("cardId"),
        "potion_id": parameters.get("potionId"),
        "target_type": parameters.get("targetType"),
        "target_enemy_index": selected_enemy_index if selected_enemy_index is not None else parameters.get("enemyIndex"),
        "raw_parameters": parameters,
    }


def _ensure_sts2_training_importable() -> None:
    if str(TRAINING_ROOT) not in sys.path:
        sys.path.insert(0, str(TRAINING_ROOT))


class PolicyAgent:
    """Loads both checkpoints exactly ONCE (in __init__) - construct a single instance
    per process at startup and reuse it for every decision/episode, per the initial
    instructions ("モデルはprocess起動時に1回だけロードする")."""

    def __init__(
        self,
        policy_checkpoint: "str | Path" = DEFAULT_POLICY_CHECKPOINT,
        value_checkpoint: "str | Path" = DEFAULT_VALUE_CHECKPOINT,
        *,
        heuristic_agent: HeuristicAgent,
        emulator: BattleEmulator,
        state_evaluator: StateEvaluator,
        low_confidence_threshold: float = 0.5,
    ) -> None:
        _ensure_sts2_training_importable()
        from sts2_training.inference import PolicyDecision, ValueDetermination  # noqa: E402

        self.policy_checkpoint_path = str(policy_checkpoint)
        self.value_checkpoint_path = str(value_checkpoint)
        self.policy = PolicyDecision(policy_checkpoint)
        self.value = ValueDetermination(value_checkpoint)
        self.heuristic_agent = heuristic_agent
        self.emulator = emulator
        self.state_evaluator = state_evaluator
        self.low_confidence_threshold = low_confidence_threshold

    def decide(
        self,
        battle_state: BattleState,
        legal_actions: list[dict],
        deadline: "float | None" = None,
    ) -> dict:
        """Returns one decision record (see module + RL初期指示's "必須ログ") including
        `chosen_action` / `chosen_target_index` / `chosen_enemy_index` for the caller to
        actually pass to CombatEnv.step()."""
        record: dict[str, Any] = {
            "decision_source": None,  # "policy" | "heuristic_fallback"
            "fallback_reason": None,
            "selected_action_index": None,
            "confidence": None,
            "ranked_action_indices": None,
            "low_confidence": False,
            "policy_latency_ms": None,
            "policy_exception": None,
            "value_output": None,
            "value_latency_ms": None,
            "value_exception": None,
            "action_mapping_mismatch": None,
            "target_ambiguous": False,
            "alive_enemy_count": None,
            "target_resolution_method": None,
            "chosen_action": None,
            "chosen_target_index": None,
            "chosen_enemy_index": None,
            "heuristic_latency_ms": None,
        }

        # Value: scored and logged for every decision, independent of the routing below.
        # Never feeds back into action selection here - see module docstring.
        try:
            t0 = time.perf_counter()
            record["value_output"] = self.value(battle_state.engine_state)
            record["value_latency_ms"] = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:  # noqa: BLE001
            record["value_exception"] = f"{type(exc).__name__}: {exc}"

        present_types = {a.get("action_type") for a in legal_actions}
        non_standard = present_types - STANDARD_ACTION_TYPES
        if non_standard:
            if non_standard <= CHOICE_FALLBACK_ACTION_TYPES:
                reason = f"choice_action_type:{sorted(non_standard)}"
            else:
                reason = f"non_standard_action_type:{sorted(non_standard)}"
            return self._heuristic_fallback(record, battle_state, legal_actions, deadline, reason)

        normalized = [normalize_legal_action(a) for a in legal_actions]
        try:
            t0 = time.perf_counter()
            result = self.policy(battle_state.engine_state, normalized)
            record["policy_latency_ms"] = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:  # noqa: BLE001
            record["policy_exception"] = f"{type(exc).__name__}: {exc}"
            return self._heuristic_fallback(
                record, battle_state, legal_actions, deadline, f"policy_exception:{type(exc).__name__}"
            )

        if result.get("recommend_heuristic_fallback"):
            return self._heuristic_fallback(
                record, battle_state, legal_actions, deadline, "policy_recommend_fallback_choice_card"
            )

        selected_index = result["selected_action_index"]
        record["selected_action_index"] = selected_index
        record["confidence"] = result["confidence"]
        record["ranked_action_indices"] = result["ranked_action_indices"]

        # Action-mapping-mismatch guard: this index must resolve to a real, currently
        # available legal action. Anything else is an adapter/index-alignment bug, not a
        # Policy quality issue - recorded distinctly and hard-routed to Heuristic rather
        # than coerced into something plausible-looking.
        if not (isinstance(selected_index, int) and 0 <= selected_index < len(legal_actions)):
            record["action_mapping_mismatch"] = (
                f"selected_action_index={selected_index} out of range for {len(legal_actions)} legal_actions"
            )
            return self._heuristic_fallback(record, battle_state, legal_actions, deadline, "action_mapping_mismatch")
        selected_action = legal_actions[selected_index]
        if selected_action.get("is_available") is not True:
            record["action_mapping_mismatch"] = (
                f"selected action_id={selected_action.get('action_id')} label={selected_action.get('label')!r} "
                "is_available=False"
            )
            return self._heuristic_fallback(record, battle_state, legal_actions, deadline, "action_mapping_mismatch")

        record["decision_source"] = "policy"
        record["low_confidence"] = result["confidence"] < self.low_confidence_threshold
        # Low confidence: per the initial instructions ("Policy低信頼 -> 行動は変更せず
        # 記録のみ") the action selection below proceeds unchanged regardless of this flag.

        target_index, enemy_index, method, ambiguous, alive_count = self._resolve_target(battle_state, selected_action)
        record["chosen_action"] = selected_action
        record["chosen_target_index"] = target_index
        record["chosen_enemy_index"] = enemy_index
        record["target_resolution_method"] = method
        record["target_ambiguous"] = ambiguous
        record["alive_enemy_count"] = alive_count
        return record

    def _resolve_target(self, battle_state: BattleState, action: dict):
        candidates = self.emulator.target_candidates(battle_state, action)
        if candidates == [None]:
            return None, None, "no_target_needed", False, None
        alive = [e for e in battle_state.engine_state.get("enemies") or [] if e.get("isAlive", True)]
        if len(candidates) == 1:
            target_index = candidates[0]
            enemy_index = alive[target_index].get("index") if 0 <= target_index < len(alive) else None
            return target_index, enemy_index, "single_candidate", False, len(alive)
        # 2+ alive AnyEnemy candidates and Policy gives no target signal (see module
        # docstring) - default to the first alive candidate, no extra simulation.
        target_index = 0
        enemy_index = alive[0].get("index") if alive else None
        return target_index, enemy_index, "default_first_alive_no_search", True, len(alive)

    def _heuristic_fallback(
        self,
        record: dict,
        battle_state: BattleState,
        legal_actions: list[dict],
        deadline: "float | None",
        reason: str,
    ) -> dict:
        record["decision_source"] = "heuristic_fallback"
        record["fallback_reason"] = reason
        t0 = time.perf_counter()
        chosen, action_scores = self.heuristic_agent.choose_action_with_detail(battle_state, deadline=deadline)
        record["heuristic_latency_ms"] = (time.perf_counter() - t0) * 1000.0
        enemy_index = next(
            (
                c["enemy_index"]
                for c in action_scores
                if c["action_id"] == chosen.action_id and c["target_index"] == chosen.target_index
            ),
            None,
        )
        record["chosen_action"] = chosen.action
        record["chosen_target_index"] = chosen.target_index
        record["chosen_enemy_index"] = enemy_index
        record["target_resolution_method"] = "heuristic"
        return record


def build_policy_agent(
    repo_root: "Path | None" = None,
    policy_checkpoint: "str | Path" = DEFAULT_POLICY_CHECKPOINT,
    value_checkpoint: "str | Path" = DEFAULT_VALUE_CHECKPOINT,
    low_confidence_threshold: float = 0.5,
) -> tuple[BattleEmulator, HeuristicAgent, "PolicyAgent"]:
    """Convenience factory mirroring generate_heuristic_trajectories.py::
    build_default_agent() - one BattleEmulator (one GameInstance per process, per its own
    module docstring), one HeuristicAgent (used both as the choice-fallback path and as
    the Heuristic-baseline comparison arm), one PolicyAgent wrapping both checkpoints."""
    from potion_value_table import PotionValueTable
    from state_evaluator import DEFAULT_WEIGHTS

    emulator = BattleEmulator(repo_root=repo_root)
    evaluator = StateEvaluator(PotionValueTable())
    heuristic_agent = HeuristicAgent(emulator, evaluator, dict(DEFAULT_WEIGHTS))
    policy_agent = PolicyAgent(
        policy_checkpoint,
        value_checkpoint,
        heuristic_agent=heuristic_agent,
        emulator=emulator,
        state_evaluator=evaluator,
        low_confidence_threshold=low_confidence_threshold,
    )
    return emulator, heuristic_agent, policy_agent
