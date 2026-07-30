"""LookaheadSearcher: Phase 3 - samples future draw/reshuffle uncertainty, searches turns ahead.

CombatScenario.ShuffleRngSeed (added upstream) independently varies only the mid-combat
reshuffle RNG stream, leaving Seed/ForcedMove/StateLog (and therefore monster AI and
in-combat RNG) untouched - exactly what's needed to sample K independent "what does my
next reshuffle look like" hypotheses from one otherwise-deterministic position (verified:
same Seed/ForcedMove, different ShuffleRngSeed -> identical turn-1 behavior, different
post-reshuffle hands).

For each candidate root action, K hypotheses are rolled out D full turns deep (each turn
resolved by TurnBeamSearcher, keeping the top M turn-plans alive at each turn boundary),
and the candidate's K resulting scores are averaged - the design doc's
"K個の仮説の平均スコア" step. HeuristicAgent takes just the best-averaging root action,
consistent with this package's state-in/state-out design (the rest of the plan is
implicitly re-derived on the next real decision).

Performance note: cost scales roughly as
  (# root actions) x K x (1 turn-finish + (D-1) x M turn-searches)
each of which is itself a full TurnBeamSearcher call. The design doc's own recommended
K=5-10/D=2/M=3 (full beam_width/max_plays) is therefore quite slow un-optimized; small
values are used for interactive verification here, matching the doc's explicit "K=5-10,
D=2, M=3 initially, K=20-50/D=3-5/M=5-10 only after speeding things up" guidance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from battle_emulator import BattleEmulator, BattleState, dedup_by_state
from beam_search import TurnBeamSearcher
from state_evaluator import StateEvaluator

DEFAULT_K = 5
DEFAULT_D = 2
DEFAULT_M = 3


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


class LookaheadSearcher:
    def __init__(
        self,
        emulator: BattleEmulator,
        evaluator: StateEvaluator,
        turn_searcher: TurnBeamSearcher | None = None,
        K: int = DEFAULT_K,
        D: int = DEFAULT_D,
        M: int = DEFAULT_M,
        rng: random.Random | None = None,
    ):
        self.emulator = emulator
        self.evaluator = evaluator
        self.turn_searcher = turn_searcher or TurnBeamSearcher(emulator, evaluator)
        self.K = K
        self.D = D
        self.M = M
        self.rng = rng or random.Random()

    def sample_future_draw_orders(self, k: int | None = None) -> list[int]:
        k = k or self.K
        return [self.rng.randrange(1, 2**31 - 1) for _ in range(k)]

    def _score(self, state: BattleState, weights: dict) -> float:
        return self.evaluator.evaluate(state.engine_state, weights, outcome=state.outcome)

    def _finish_current_turn(self, state: BattleState, weights: dict) -> BattleState:
        """Completes `state`'s in-progress turn via the single best beam-search
        continuation, returning the state at the next turn boundary (or terminal)."""
        if self.emulator.is_terminal(state):
            return state
        sequence, _, _ = self.turn_searcher.search_best_action_sequence(state, weights)
        for action, target_index in sequence:
            state = self.emulator.apply_action(state, action, target_index)
            if self.emulator.is_terminal(state):
                break
        return state

    def search_with_pattern(self, state: BattleState, weights: dict) -> float:
        """Depth-D, width-M search over full turns from `state` (already at a turn
        boundary), under state's own pinned shuffle_rng_seed. Returns the best resulting
        state's score."""
        beam = [state]
        for _ in range(self.D):
            if all(self.emulator.is_terminal(s) for s in beam):
                break
            candidates: list[BattleState] = []
            for s in beam:
                if self.emulator.is_terminal(s):
                    candidates.append(s)
                    continue
                for result_state, _, _ in self.turn_searcher.search_top_sequences(s, weights, self.M):
                    candidates.append(result_state)
            candidates.sort(key=lambda c: self._score(c, weights), reverse=True)
            if self.turn_searcher.merge_equivalent_states:
                candidates = dedup_by_state(candidates)
            beam = candidates[: self.M]
        return max(self._score(s, weights) for s in beam)

    def choose_best_action_by_average_score(self, battle_state: BattleState, weights: dict) -> ChosenAction:
        legal = self.emulator.enumerate_legal_actions(battle_state)
        if not legal:
            raise RuntimeError("No legal actions available - caller should have checked is_terminal()")
        hypotheses = self.sample_future_draw_orders()

        best: ChosenAction | None = None
        for action in legal:
            for target_index in self.emulator.target_candidates(battle_state, action):
                scores = []
                for seed in hypotheses:
                    seeded_root = self.emulator.with_shuffle_seed(battle_state, seed)
                    after_first = self.emulator.apply_action(seeded_root, action, target_index)
                    turn_start = (
                        after_first
                        if action["action_type"] == "system" or after_first.is_terminal
                        else self._finish_current_turn(after_first, weights)
                    )
                    scores.append(
                        self._score(turn_start, weights)
                        if self.emulator.is_terminal(turn_start)
                        else self.search_with_pattern(turn_start, weights)
                    )
                avg = sum(scores) / len(scores)
                if best is None or avg > best.score:
                    best = ChosenAction(action=action, target_index=target_index, score=avg)
        return best
