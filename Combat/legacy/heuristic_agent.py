"""HeuristicAgent: chooses one action per real decision point.

Phase 1: 1-ply greedy - play out every legal action one step, score the result, keep the
best. Phase 2: pass a `searcher` (TurnBeamSearcher) to delegate to it instead and take
the first action of its best full-turn sequence - see beam_search.py. Phase 3: pass a
`lookahead_searcher` (LookaheadSearcher) to average each candidate root action over K
future-draw hypotheses D turns deep instead - see lookahead.py. At most one of the two
should be set; lookahead_searcher takes priority if both are.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Optional

from battle_emulator import BattleEmulator, BattleState, battle_state_key
from battle_emulator import is_action_continuation_pending_choice
from legacy.state_evaluator import StateEvaluator


@dataclass
class ChosenAction:
    action: dict
    target_index: Optional[int]
    score: float

    @property
    def action_id(self) -> int:
        return self.action["action_id"]

    @property
    def label(self) -> str:
        return self.action["label"]


class HeuristicAgent:
    def __init__(
        self,
        emulator: BattleEmulator,
        evaluator: StateEvaluator,
        weights: dict,
        searcher=None,
        lookahead_searcher=None,
    ):
        self.emulator = emulator
        self.evaluator = evaluator
        self.weights = weights
        self.searcher = searcher
        self.lookahead_searcher = lookahead_searcher

    def choose_action(self, battle_state: BattleState) -> ChosenAction:
        if self.lookahead_searcher is not None:
            return self._choose_via_lookahead(battle_state)
        if self.searcher is not None:
            return self._choose_via_beam_search(battle_state)
        return self._choose_greedy(battle_state)

    def choose_action_with_detail(
        self,
        battle_state: BattleState,
        deadline: "float | None" = None,
        historical_state_keys: "set[tuple] | None" = None,
    ) -> tuple[ChosenAction, list[dict]]:
        """Like choose_action(), but also returns every (action, target) candidate this
        decision actually evaluated, each as {action_id, label, target_index,
        enemy_index, score} - the `action_scores` teacher-data field
        (Common/schemas/... trajectory schema) needs this; choose_action() alone only
        keeps the winning candidate's score.

        Only the greedy path currently returns real per-candidate scores for every
        legal action - it already evaluates every (action, target_index) pair itself.
        The beam/lookahead paths do NOT enumerate flat per-root-action scores the same
        way (beam search shares/reuses partial trees across actions; lookahead only
        keeps a running best) - for those, this returns a single-element list containing
        just the chosen action's own score, rather than fabricating scores for
        candidates that were never actually independently evaluated. Match the caller's
        `searcher`/`lookahead_searcher` configuration against this before assuming
        action_scores is exhaustive.

        `enemy_index` is the STABLE (within this exact battle_state, not across turns -
        see Common/schemas/combat_state_schema.json's caveat on this) `state["enemies"][i]["index"]`
        value that `target_index` (a position into `target_candidates()`'s alive-enemy
        list) resolves to - None for actions that don't target an enemy.

        `deadline` (time.time()-comparable, optional): checked BEFORE each individual
        candidate's apply_action() call, not just once per decision - a single decision
        can itself hold an unbounded number of (action, target) pairs (hand size x alive
        enemy count, each a full restore+Step), so a caller-side "time budget per
        decision" check that only runs between decisions (like
        generate_heuristic_trajectories.py's max_wall_seconds) does not actually bound a
        single pathological decision - found in practice (a 2-enemy, 20-card Necrobinder
        deck ran past 8 minutes on ONE decision). Once past the deadline, remaining
        candidates are recorded as skipped (reason "deadline_exceeded") rather than
        evaluated, and whatever best candidate was found so far (or the first remaining
        legal action, if none scored yet) is returned - never raises just for running
        long.
        """
        if self.lookahead_searcher is not None or self.searcher is not None:
            chosen = self.choose_action(battle_state)
            return chosen, [self._candidate_detail(battle_state, chosen.action, chosen.target_index, chosen.score)]

        legal = self.emulator.enumerate_legal_actions(battle_state)
        if not legal:
            raise RuntimeError("No legal actions available - caller should have checked is_terminal()")

        candidates: list[dict] = []
        skipped: list[dict] = []
        best: ChosenAction | None = None
        fallback: ChosenAction | None = None
        deadline_hit = False
        current_key = battle_state_key(battle_state)
        for action in legal:
            for target_index in self.emulator.target_candidates(battle_state, action):
                if fallback is None:
                    fallback = ChosenAction(action=action, target_index=target_index, score=0.0)
                if deadline is not None and time.time() >= deadline:
                    deadline_hit = True
                    skipped.append(
                        {**self._candidate_detail(battle_state, action, target_index, score=None), "skipped_reason": "deadline_exceeded"}
                    )
                    continue
                # Defensive per-candidate try/except: apply_action() restores
                # battle_state fresh via build_scenario_from_state. Stars is now carried
                # by that path, but future Emulator fields may repeat the same shape of
                # "legal before restore, illegal after restore" mismatch. A candidate
                # that fails this way is un-evaluable, not a reason to abort the whole
                # decision - skip it and continue scoring the rest, recording why in
                # `skipped` rather than silently dropping it or crashing.
                try:
                    resulting = self.emulator.apply_action(
                        battle_state,
                        action,
                        target_index,
                        continuation_resolver=self._choose_action_continuation_live,
                        continuation_deadline=deadline,
                    )
                except Exception as exc:  # noqa: BLE001
                    skipped.append(
                        {
                            **self._candidate_detail(battle_state, action, target_index, score=None),
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc)[:500],
                            "exception_traceback": traceback.format_exc(),
                            "skipped_reason": f"{type(exc).__name__}: {str(exc)[:150]}",
                        }
                    )
                    continue
                score = self.evaluator.evaluate(
                    resulting.engine_state, self.weights, outcome=resulting.outcome
                )
                same_state_loop = is_non_terminal_self_loop(battle_state, resulting, current_key=current_key)
                if same_state_loop:
                    score -= 1_000_000.0
                historical_loop = False
                if historical_state_keys:
                    resulting_key = battle_state_key(resulting)
                    historical_loop = (not resulting.is_terminal) and (resulting_key in historical_state_keys)
                    if historical_loop:
                        score -= 1_000_000.0
                detail = self._candidate_detail(battle_state, action, target_index, score)
                if same_state_loop:
                    detail["same_state_loop_penalty"] = True
                if historical_loop:
                    detail["historical_state_loop_penalty"] = True
                candidates.append(detail)
                if best is None or score > best.score or (
                    score == best.score and self._prefer_candidate_over_best(action, best.action)
                ):
                    best = ChosenAction(action=action, target_index=target_index, score=score)
        if best is None:
            if deadline_hit and fallback is not None:
                return fallback, candidates + skipped
            error = RuntimeError(
                f"Every legal-action candidate failed to evaluate (skipped={len(skipped)}) - "
                f"no action could be scored. First failure: {skipped[0]['skipped_reason'] if skipped else 'n/a'}"
            )
            error.skipped_candidates = skipped
            raise error
        return best, candidates + skipped

    def _candidate_detail(self, battle_state: BattleState, action: dict, target_index: "int | None", score: "float | None") -> dict:
        enemy_index = None
        if target_index is not None:
            alive = [e for e in battle_state.engine_state.get("enemies") or [] if e.get("isAlive", True)]
            if 0 <= target_index < len(alive):
                enemy_index = alive[target_index].get("index")
        return {
            "action_id": action["action_id"],
            "label": action["label"],
            "target_index": target_index,
            "enemy_index": enemy_index,
            "score": score,
        }

    def _prefer_candidate_over_best(self, candidate_action: dict, best_action: dict) -> bool:
        candidate_type = candidate_action.get("action_type")
        best_type = best_action.get("action_type")
        if candidate_type != "system" and best_type == "system":
            return True
        if candidate_type == "system" and best_type != "system":
            return False
        return False

    def _resolve_action_continuations_live(
        self,
        game,
        battle_state: BattleState,
        deadline: "float | None" = None,
    ) -> BattleState:
        current = battle_state
        continuation_steps = 0
        while is_action_continuation_pending_choice(current.engine_state):
            continuation_steps += 1
            if continuation_steps > 50:
                raise RuntimeError("ActionContinuation did not resolve within 50 continuation steps.")
            legal = self.emulator.enumerate_legal_actions(current)
            action = self._choose_action_continuation_live(game, current, legal, deadline)
            current = self.emulator.step_live_action(game, current, action)
        return current

    def _choose_action_continuation_live(
        self,
        game,
        battle_state: BattleState,
        legal_actions: list[dict],
        deadline: "float | None" = None,
    ) -> dict:
        if not legal_actions:
            raise RuntimeError("ActionContinuation exposed no legal actions.")
        pending = battle_state.engine_state.get("pendingChoice") or {}
        selected_count = int(pending.get("selectedCount") or 0)
        min_select = int(pending.get("minSelect") or 0)
        max_select = pending.get("maxSelect")
        option_cards = list(pending.get("options") or [])

        confirm = next((a for a in legal_actions if a["action_type"] == "choice_confirm"), None)
        skip = next((a for a in legal_actions if a["action_type"] == "choice_skip"), None)
        cards = [a for a in legal_actions if a["action_type"] == "choice_card"]

        if not cards:
            return confirm or skip or legal_actions[0]

        if selected_count < min_select:
            return self._best_continuation_card_action(battle_state, pending, cards, option_cards)

        if max_select is not None and selected_count >= int(max_select):
            return confirm or skip or cards[0]

        if pending.get("choiceType") == "WishDrawToHand":
            return self._best_continuation_card_action(battle_state, pending, cards, option_cards)

        if cards:
            return self._best_continuation_card_action(battle_state, pending, cards, option_cards)
        return skip or confirm or legal_actions[0]

    def _best_continuation_card_action(
        self,
        battle_state: BattleState,
        pending_choice: dict,
        card_actions: list[dict],
        option_cards: list[dict],
    ) -> dict:
        play_pile_ids = {c.get("id") for c in (battle_state.engine_state.get("playPile") or [])}
        best_action = card_actions[0]
        best_score = None
        for idx, action in enumerate(card_actions):
            option = option_cards[idx] if idx < len(option_cards) else {"id": action["label"]}
            score = 0.0
            rarity = option.get("rarity")
            card_type = option.get("type")
            cost = option.get("cost")
            card_id = option.get("id")
            if card_id in play_pile_ids:
                score -= 50.0
            if rarity == "Rare":
                score += 4.0
            elif rarity == "Uncommon":
                score += 2.0
            elif rarity == "Common":
                score += 1.0
            if card_type == "Curse":
                score -= 100.0
            elif card_type == "Status":
                score -= 50.0
            elif card_type == "Attack":
                score += 1.5
            elif card_type == "Skill":
                score += 1.0
            elif card_type == "Power":
                score += 0.5
            if isinstance(cost, int):
                score += max(0.0, 3.0 - float(cost))
            if best_score is None or score > best_score:
                best_score = score
                best_action = action
        return best_action

    def _choose_greedy(self, battle_state: BattleState) -> ChosenAction:
        best, _ = self.choose_action_with_detail(battle_state)
        return best

    def _choose_via_beam_search(self, battle_state: BattleState) -> ChosenAction:
        sequence, score, _ = self.searcher.search_best_action_sequence(battle_state, self.weights)
        if not sequence:
            raise RuntimeError("No legal actions available - caller should have checked is_terminal()")
        action, target_index = sequence[0]
        return ChosenAction(action=action, target_index=target_index, score=score)

    def _choose_via_lookahead(self, battle_state: BattleState) -> ChosenAction:
        chosen = self.lookahead_searcher.choose_best_action_by_average_score(battle_state, self.weights)
        return ChosenAction(action=chosen.action, target_index=chosen.target_index, score=chosen.score)


def is_non_terminal_self_loop(
    before: BattleState,
    after: BattleState,
    *,
    current_key: "tuple | None" = None,
) -> bool:
    if after.is_terminal:
        return False
    before_key = current_key if current_key is not None else battle_state_key(before)
    return before_key == battle_state_key(after)
