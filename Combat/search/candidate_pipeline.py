"""Search Coordinator Candidate Pipeline - Combat execution infrastructure Phase 4.

Implements the candidate-only part of
docs/architecture/combat/mermaid_combat_candidate_pipeline_detail.mermaid: read the
CURRENT Decision Result's own Choice Payload/legal actions, classify by the Emulator's
`action_type` (`choice_kind`), cheap-score without Restore/Step, prune, and split into
one continuation candidate plus sub-branch candidates.

This module deliberately does not implement Branch Workers, RNG hypotheses, DrawPile
belief generation, or a Phase-3 `SearchStrategy` wrapper. It returns only unexecuted
Semantic Actions tied to the Current Context Signature; no Expected Post-Step Signature
exists before a worker actually Steps.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional, Union

from battle_emulator import BattleState, is_action_continuation_pending_choice
from search.decision_context import (
    CHOICE_SCOPE_ACTION_CONTINUATION,
    CHOICE_SCOPE_TOP_LEVEL,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
)


@dataclass(frozen=True)
class Candidate:
    """One unexecuted candidate from the CURRENT Choice Payload.

    `choice_kind` is exactly `LegalAction.action_type`. `choice_scope` is orthogonal
    metadata derived from the current result's own payload; it is carried through but
    never used for evaluator routing.
    """

    semantic_action: SemanticAction
    choice_kind: str
    choice_scope: str
    legal_action_index: int
    label: str
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
    card_cost: "Optional[int]" = None
    potion_slot: "Optional[int]" = None
    card_type: "Optional[str]" = None
    card_rarity: "Optional[str]" = None


@dataclass(frozen=True)
class OrderMaskedObservation:
    """Aggregate evaluator input that cannot carry ordered DrawPile truth.

    Fields are scalar counts and frequency dictionaries only. Evaluators receive this
    object plus one `Candidate`; they never receive `BattleState`, `engine_state`, or a
    pile list, so DrawPile order cannot leak through the scoring function interface.
    """

    hand_size: int
    draw_pile_size: int
    discard_pile_size: int
    exhaust_pile_size: int
    play_pile_size: int
    alive_enemy_count: int
    player_energy: int
    hand_card_id_counts: "dict[str, int]"
    hand_card_type_counts: "dict[str, int]"
    draw_pile_card_id_counts: "dict[str, int]"
    discard_pile_card_id_counts: "dict[str, int]"
    exhaust_pile_card_id_counts: "dict[str, int]"
    pending_choice_type: "Optional[str]"
    pending_min_select: "Optional[int]"
    pending_max_select: "Optional[int]"
    pending_selected_count: int


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    evaluator_name: str


@dataclass(frozen=True)
class PipelineCandidateRef:
    """Candidate shape emitted after split.

    Per the Candidate Pipeline diagram, this carries only the Current Context Signature
    and the unexecuted Semantic Action/target parameters. It intentionally has no
    Expected Post-Step Signature field.
    """

    current_context_signature: DecisionSignature
    semantic_action: SemanticAction
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
    score: float = 0.0
    choice_kind: str = ""
    choice_scope: str = ""


@dataclass(frozen=True)
class CandidatePipelineSuccess:
    continuation_candidate: PipelineCandidateRef
    sub_branch_candidates: "list[PipelineCandidateRef]"
    ranked_candidates: "list[ScoredCandidate]"
    pruned_candidates: "list[ScoredCandidate]"
    observation: OrderMaskedObservation


@dataclass(frozen=True)
class NoViableCandidates:
    detail: str
    ranked_candidates: "list[ScoredCandidate]"
    observation: OrderMaskedObservation


CandidatePipelineResult = Union[CandidatePipelineSuccess, NoViableCandidates]


class NoViableCandidatesError(RuntimeError):
    """Raised by `split_candidates()` if called with an empty pruned list."""


def _legal_actions_from_current_result(current_result: BattleState) -> "list[dict]":
    legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001 - NOTE_NO_REREAD
    return list(legal_actions)


def _int_or_none(value) -> "Optional[int]":
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _card_feature_by_id(engine_state: dict, card_id: "Optional[str]") -> dict:
    if card_id is None:
        return {}
    for card in engine_state.get("hand") or []:
        if card.get("id") == card_id:
            return card
    pending = engine_state.get("pendingChoice") or {}
    for card in pending.get("options") or []:
        if card.get("id") == card_id:
            return card
    return {}


def _choice_scope_from_current_result(current_result: BattleState, legal_actions: "list[dict]") -> str:
    """Derive scope without rereading legal actions from another API boundary.

    Most ActionContinuation states are tagged in pendingChoice and are detected by the
    same predicate Phase 3 uses. Raw C# Step results for target selection, however, can
    expose `choice_target` LegalActions while publishing no pendingChoice metadata; those
    legal actions are still an action-continuation payload, not a new top-level decision.
    """
    if is_action_continuation_pending_choice(current_result.engine_state):
        return CHOICE_SCOPE_ACTION_CONTINUATION
    if any(action.get("action_type") == "choice_target" for action in legal_actions):
        return CHOICE_SCOPE_ACTION_CONTINUATION
    return CHOICE_SCOPE_TOP_LEVEL


def extract_candidates(current_result: BattleState) -> "list[Candidate]":
    """Build Candidates from the Decision Result's own cached legal actions."""
    legal_actions = _legal_actions_from_current_result(current_result)
    choice_scope = _choice_scope_from_current_result(current_result, legal_actions)
    candidates: list[Candidate] = []
    for index, action in enumerate(legal_actions):
        params = action.get("parameters") or {}
        action_type = action.get("action_type")
        card_id = params.get("cardId")
        semantic_action = SemanticAction(
            action_type=action_type,
            semantic_key=action.get("semantic_key", ""),
        )
        card_feature = _card_feature_by_id(current_result.engine_state, card_id)
        candidates.append(
            Candidate(
                semantic_action=semantic_action,
                choice_kind=action_type,
                choice_scope=choice_scope,
                legal_action_index=index,
                label=str(action.get("label") or ""),
                target_index=_int_or_none(params.get("targetIndex")),
                target_enemy_index=_int_or_none(params.get("enemyIndex")),
                card_cost=_int_or_none(params.get("cost") if params.get("cost") is not None else card_feature.get("cost")),
                potion_slot=_int_or_none(params.get("potionSlot")),
                card_type=card_feature.get("type"),
                card_rarity=card_feature.get("rarity"),
            )
        )
    return candidates


def _card_id_counts(cards) -> "dict[str, int]":
    return dict(Counter(c.get("id") for c in (cards or []) if c and c.get("id") is not None))


def _card_type_counts(cards) -> "dict[str, int]":
    return dict(Counter(c.get("type") for c in (cards or []) if c and c.get("type") is not None))


def build_order_masked_observation(current_result: BattleState) -> OrderMaskedObservation:
    """Extract only count/frequency features; never expose ordered pile lists."""
    state = current_result.engine_state
    hand = state.get("hand") or []
    draw_pile = state.get("drawPile") or []
    discard_pile = state.get("discardPile") or []
    exhaust_pile = state.get("exhaustPile") or []
    play_pile = state.get("playPile") or []
    pending = state.get("pendingChoice") or {}
    enemies = [e for e in (state.get("enemies") or []) if e.get("isAlive", True)]
    return OrderMaskedObservation(
        hand_size=len(hand),
        draw_pile_size=len(draw_pile),
        discard_pile_size=len(discard_pile),
        exhaust_pile_size=len(exhaust_pile),
        play_pile_size=len(play_pile),
        alive_enemy_count=len(enemies),
        player_energy=int(state.get("energy") or 0),
        hand_card_id_counts=_card_id_counts(hand),
        hand_card_type_counts=_card_type_counts(hand),
        draw_pile_card_id_counts=_card_id_counts(draw_pile),
        discard_pile_card_id_counts=_card_id_counts(discard_pile),
        exhaust_pile_card_id_counts=_card_id_counts(exhaust_pile),
        pending_choice_type=pending.get("choiceType"),
        pending_min_select=_int_or_none(pending.get("minSelect")),
        pending_max_select=_int_or_none(pending.get("maxSelect")),
        pending_selected_count=int(pending.get("selectedCount") or 0),
    )


def _require_order_masked(observation: OrderMaskedObservation) -> None:
    if not isinstance(observation, OrderMaskedObservation):
        raise TypeError("candidate evaluators accept only OrderMaskedObservation plus Candidate")


def _card_score(observation: OrderMaskedObservation, candidate: Candidate) -> float:
    _require_order_masked(observation)
    score = 20.0
    if candidate.card_cost is not None:
        score += max(0.0, 3.0 - float(candidate.card_cost))
        if candidate.card_cost > observation.player_energy:
            score -= 4.0
    if candidate.card_type == "Attack" and observation.alive_enemy_count:
        score += 2.0
    elif candidate.card_type == "Skill":
        score += 1.0
    elif candidate.card_type == "Power":
        score += 0.5
    elif candidate.card_type in ("Curse", "Status"):
        score -= 20.0
    return score


def _target_score(observation: OrderMaskedObservation, candidate: Candidate) -> float:
    _require_order_masked(observation)
    score = 15.0
    if candidate.target_enemy_index is not None:
        score += 1.0
    return score


def _hand_choice_score(observation: OrderMaskedObservation, candidate: Candidate) -> float:
    _require_order_masked(observation)
    score = 12.0
    if candidate.card_type in ("Curse", "Status"):
        score -= 10.0
    if candidate.card_rarity == "Rare":
        score += 3.0
    elif candidate.card_rarity == "Uncommon":
        score += 2.0
    elif candidate.card_rarity == "Common":
        score += 1.0
    if candidate.card_cost is not None:
        score += max(0.0, 3.0 - float(candidate.card_cost))
    return score


def _confirm_score(observation: OrderMaskedObservation, candidate: Candidate) -> float:
    _require_order_masked(observation)
    if candidate.choice_kind == "choice_skip":
        return -5.0
    if observation.pending_min_select is not None and observation.pending_selected_count < observation.pending_min_select:
        return -20.0
    return 8.0


def _other_score(observation: OrderMaskedObservation, candidate: Candidate) -> float:
    _require_order_masked(observation)
    if candidate.choice_kind == "system":
        return 0.0
    if candidate.choice_kind == "potion":
        return 10.0
    return 1.0


Evaluator = Callable[[OrderMaskedObservation, Candidate], float]

EVALUATORS_BY_CHOICE_KIND: "dict[str, tuple[str, Evaluator]]" = {
    "card": ("card", _card_score),
    "choice_target": ("target", _target_score),
    "choice_card": ("hand", _hand_choice_score),
    "choice_confirm": ("confirm", _confirm_score),
    "choice_skip": ("confirm", _confirm_score),
    "potion": ("other", _other_score),
    "system": ("other", _other_score),
}


def score_candidate(observation: OrderMaskedObservation, candidate: Candidate) -> ScoredCandidate:
    _require_order_masked(observation)
    evaluator_name, evaluator = EVALUATORS_BY_CHOICE_KIND.get(candidate.choice_kind, ("other", _other_score))
    return ScoredCandidate(candidate=candidate, score=float(evaluator(observation, candidate)), evaluator_name=evaluator_name)


def rank_candidates(observation: OrderMaskedObservation, candidates: "list[Candidate]") -> "list[ScoredCandidate]":
    """Score and sort best-first, using legal-action order as deterministic tie-break."""
    scored = [score_candidate(observation, candidate) for candidate in candidates]
    return sorted(scored, key=lambda item: (-item.score, item.candidate.legal_action_index))


def prune_candidates(
    candidates: "list[ScoredCandidate]", *, width: int, score_threshold: "Optional[float]" = None
) -> "list[ScoredCandidate]":
    """Keep top `width` candidates, optionally filtering below a minimum score."""
    if width <= 0:
        return []
    survivors = [c for c in candidates if score_threshold is None or c.score >= score_threshold]
    return survivors[:width]


def _to_pipeline_ref(signature: DecisionSignature, scored: ScoredCandidate) -> PipelineCandidateRef:
    candidate = scored.candidate
    return PipelineCandidateRef(
        current_context_signature=signature,
        semantic_action=candidate.semantic_action,
        target_index=candidate.target_index,
        target_enemy_index=candidate.target_enemy_index,
        score=scored.score,
        choice_kind=candidate.choice_kind,
        choice_scope=candidate.choice_scope,
    )


def split_candidates(
    pruned: "list[ScoredCandidate]", *, current_context_signature: DecisionSignature
) -> "tuple[PipelineCandidateRef, list[PipelineCandidateRef]]":
    if not pruned:
        raise NoViableCandidatesError("cannot split zero pruned candidates")
    continuation = _to_pipeline_ref(current_context_signature, pruned[0])
    sub_branches = [_to_pipeline_ref(current_context_signature, scored) for scored in pruned[1:]]
    return continuation, sub_branches


def build_candidate_pipeline_result(
    decision_context: DecisionContext,
    *,
    width: int = 8,
    score_threshold: "Optional[float]" = None,
) -> CandidatePipelineResult:
    """End-to-end Candidate Pipeline entry point for a future SearchStrategy wrapper.

    Uses this module's own cheap built-in heuristic scoring (`_card_score`/
    `_hand_choice_score`/etc.) to implicitly expand and rank EVERY current legal action,
    then keep only the top `width`. Per the "RL担当指示：推論処理撤去と受動実行基盤への整理"
    division of responsibility, this implicit-scoring path is kept only for existing
    tests/callers that predate Training owning candidate selection - new callers that
    know exactly which candidates to branch on should use
    `build_candidate_pipeline_result_for_explicit_candidates()` instead, which performs
    no scoring and no implicit "all legal actions" expansion.
    """
    observation = build_order_masked_observation(decision_context.current_decision_result)
    candidates = extract_candidates(decision_context.current_decision_result)
    ranked = rank_candidates(observation, candidates)
    pruned = prune_candidates(ranked, width=width, score_threshold=score_threshold)
    if not pruned:
        return NoViableCandidates(
            detail=f"no viable candidates after pruning (width={width}, score_threshold={score_threshold!r})",
            ranked_candidates=ranked,
            observation=observation,
        )
    continuation, sub_branches = split_candidates(
        pruned,
        current_context_signature=decision_context.current_context_signature,
    )
    return CandidatePipelineSuccess(
        continuation_candidate=continuation,
        sub_branch_candidates=sub_branches,
        ranked_candidates=ranked,
        pruned_candidates=pruned,
        observation=observation,
    )


def build_candidate_pipeline_result_for_explicit_candidates(
    decision_context: DecisionContext,
    candidate_legal_action_indices: "list[int]",
) -> CandidatePipelineResult:
    """Training-facing Candidate Pipeline entry point: NO scoring, NO automatic expansion
    of every legal action, NO score-based pruning - per the "RL担当指示：推論処理撤去と
    受動実行基盤への整理" division of responsibility (Training decides which candidates to
    branch on; RL only builds the Branch-ready shape for the exact indices it's given).

    `candidate_legal_action_indices` must be a non-empty list of indices into the CURRENT
    Decision Result's own `legal_actions` (the same order Emulator/RL already publishes,
    never reordered here). The FIRST index becomes the continuation candidate (Holder);
    the rest become sub-branch candidates (siblings) - caller-controlled via list order,
    not by any RL-side ranking. Every returned `ScoredCandidate.score` is `0.0` and
    `evaluator_name="explicit"` - these fields are kept only for shape-compatibility with
    `CandidatePipelineResult`'s existing consumers (`search_coordinator.py`,
    `branch_worker_pool.py`), never read as a real score by this function's own caller.

    Raises `ValueError` if any requested index is not present among the current legal
    actions - this function never silently substitutes or falls back to "all actions".
    """
    if not candidate_legal_action_indices:
        raise ValueError(
            "build_candidate_pipeline_result_for_explicit_candidates requires at least one "
            "candidate index - it never implicitly expands all Legal Actions"
        )
    observation = build_order_masked_observation(decision_context.current_decision_result)
    all_candidates = extract_candidates(decision_context.current_decision_result)
    by_index = {c.legal_action_index: c for c in all_candidates}
    selected: "list[ScoredCandidate]" = []
    for idx in candidate_legal_action_indices:
        if idx not in by_index:
            raise ValueError(
                f"candidate index {idx!r} not present among current legal actions "
                f"(valid indices: {sorted(by_index)!r})"
            )
        selected.append(ScoredCandidate(candidate=by_index[idx], score=0.0, evaluator_name="explicit"))
    continuation, sub_branches = split_candidates(
        selected, current_context_signature=decision_context.current_context_signature
    )
    return CandidatePipelineSuccess(
        continuation_candidate=continuation,
        sub_branch_candidates=sub_branches,
        ranked_candidates=selected,
        pruned_candidates=selected,
        observation=observation,
    )
