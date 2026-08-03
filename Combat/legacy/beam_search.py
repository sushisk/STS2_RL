"""TurnBeamSearcher: Phase 2 - searches card-play order within a single turn.

At each depth, every state on the beam is expanded by every legal action (each
"AnyEnemy" target tried separately - see BattleEmulator.target_candidates). Branches
that end the turn (End Turn played, or the battle went terminal) are moved to the
finished pool rather than expanded further; the rest are re-scored and only the top
`beam_width` survive to the next depth. End Turn is always among the expanded actions
since it's always a legal action while combat is in progress, so it's never excluded by
construction - beam pruning is the only thing that could drop it, same as any other
candidate.

search_best_action_sequence() returns the best *complete* sequence found (one that
either played End Turn or ran out of actions/depth); HeuristicAgent takes just its first
action, since only that one is actually committed to the real state - the rest of the
plan is implicitly re-derived (searched again from scratch) on the next real decision,
consistent with this package's stateless, state-in/state-out design throughout.
"""

from __future__ import annotations

from battle_emulator import BattleEmulator, BattleState, dedup_by_state
from legacy.state_evaluator import StateEvaluator

DEFAULT_BEAM_WIDTH = 8
DEFAULT_MAX_PLAYS = 8


class TurnBeamSearcher:
    def __init__(
        self,
        emulator: BattleEmulator,
        evaluator: StateEvaluator,
        beam_width: int = DEFAULT_BEAM_WIDTH,
        max_plays: int = DEFAULT_MAX_PLAYS,
        merge_equivalent_states: bool = True,
    ):
        self.emulator = emulator
        self.evaluator = evaluator
        self.beam_width = beam_width
        self.max_plays = max_plays
        # Off switch exists only for controlled A/B/C performance comparisons - always
        # leave this True in normal use.
        self.merge_equivalent_states = merge_equivalent_states

    def _dedup(self, entries, state_of):
        if not self.merge_equivalent_states:
            return entries
        return dedup_by_state(entries, state_of=state_of)

    def expand_actions(self, battle_state: BattleState) -> list[dict]:
        return self.emulator.enumerate_legal_actions(battle_state)

    def _score(self, battle_state: BattleState, weights: dict) -> float:
        return self.evaluator.evaluate(
            battle_state.engine_state, weights, outcome=battle_state.outcome
        )

    def _search(
        self, battle_state: BattleState, weights: dict
    ) -> list[tuple[BattleState, list[tuple[dict, int | None]], float]]:
        """Runs the beam search and returns every finished (state, sequence, score)
        candidate, sorted best-first."""
        if self.emulator.is_terminal(battle_state):
            raise RuntimeError("Cannot search from a terminal state")

        beam: list[tuple[BattleState, list[tuple[dict, int | None]]]] = [(battle_state, [])]
        finished: list[tuple[BattleState, list[tuple[dict, int | None]], float]] = []

        for _ in range(self.max_plays):
            if not beam:
                break
            candidates: list[tuple[BattleState, list[tuple[dict, int | None]], float]] = []

            for state, seq in beam:
                actions = self.expand_actions(state)
                if not actions:
                    finished.append((state, seq, self._score(state, weights)))
                    continue
                for action in actions:
                    for target_index in self.emulator.target_candidates(state, action):
                        next_state = self.emulator.apply_action(state, action, target_index)
                        next_seq = seq + [(action, target_index)]
                        score = self._score(next_state, weights)
                        entry = (next_state, next_seq, score)
                        if action["action_type"] == "system" or next_state.is_terminal:
                            finished.append(entry)
                        else:
                            candidates.append(entry)

            candidates.sort(key=lambda c: c[2], reverse=True)
            # Equivalent-state merging: if two+ candidate sequences (however different)
            # land on the exact same next state, only the highest-scoring one can ever
            # matter from here on - the rest would just re-explore identical subtrees.
            candidates = self._dedup(candidates, state_of=lambda c: c[0])
            survivors = candidates[: self.beam_width]
            beam = [(s, seq) for s, seq, _ in survivors]

        # Anything still on the beam after max_plays didn't end its turn in time -
        # score it as-is (mid-turn) so it can still be compared against finished sequences.
        for state, seq in beam:
            finished.append((state, seq, self._score(state, weights)))

        if not finished:
            raise RuntimeError("Beam search produced no candidate sequences")

        finished.sort(key=lambda f: f[2], reverse=True)
        finished = self._dedup(finished, state_of=lambda f: f[0])
        return finished

    def search_best_action_sequence(
        self, battle_state: BattleState, weights: dict
    ) -> tuple[list[tuple[dict, int | None]], float, BattleState]:
        best_state, best_sequence, best_score = self._search(battle_state, weights)[0]
        return best_sequence, best_score, best_state

    def search_top_sequences(
        self, battle_state: BattleState, weights: dict, top_n: int
    ) -> list[tuple[BattleState, list[tuple[dict, int | None]], float]]:
        """Same search as search_best_action_sequence, but returns the top `top_n`
        distinct finished sequences instead of just the single best - used by
        LookaheadSearcher (lookahead.py) to keep multiple plausible turn plans alive
        across a multi-turn beam."""
        return self._search(battle_state, weights)[:top_n]
