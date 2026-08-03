"""Entry point / orchestration for the heuristic combat AI.

Phase 0 + Phase 1 only for now (see Combat/README.md for the full phase plan):
  - Phase 0: scenario set, BattleEmulator, StateEvaluator, PotionValueTable, initial weights.
  - Phase 1: HeuristicAgent 1-ply greedy baseline, run against the train scenarios.

Run from this directory: `python main.py`
"""

from __future__ import annotations

from pathlib import Path

from battle_emulator import BattleEmulator
from battle_result import BattleResult
from legacy.beam_search import DEFAULT_BEAM_WIDTH, DEFAULT_MAX_PLAYS, TurnBeamSearcher
from legacy.heuristic_agent import ChosenAction, HeuristicAgent
from lookahead import DEFAULT_D, DEFAULT_K, DEFAULT_M, LookaheadSearcher
from legacy.potion_value_table import PotionValueTable
from scenario_set import ScenarioSet
from legacy.state_evaluator import DEFAULT_WEIGHTS, StateEvaluator


def load_eval_scenarios(train_n: int = 5, validation_n: int = 3, test_n: int = 5) -> ScenarioSet:
    return ScenarioSet.default_ironclad_vs_calcified_cultist(
        train_n=train_n, validation_n=validation_n, test_n=test_n
    )


def load_initial_weights() -> dict:
    return dict(DEFAULT_WEIGHTS)


def load_potion_value_table() -> PotionValueTable:
    return PotionValueTable()


def build_emulator(repo_root: Path | None = None) -> BattleEmulator:
    return BattleEmulator(repo_root=repo_root)


def build_state_evaluator(potion_table: PotionValueTable) -> StateEvaluator:
    return StateEvaluator(potion_table)


def simulate_battle(
    emulator: BattleEmulator,
    agent: HeuristicAgent,
    scenario_spec: dict,
    potion_table: PotionValueTable,
    max_steps: int = 200,
    trace: list | None = None,
) -> BattleResult:
    state = emulator.initialize(scenario_spec)
    steps = 0
    while not emulator.is_terminal(state) and steps < max_steps:
        chosen: ChosenAction = agent.choose_action(state)
        state = emulator.apply_action(state, chosen.action, chosen.target_index)
        if trace is not None:
            trace.append((chosen.label, chosen.target_index, chosen.score, state.engine_state))
        steps += 1
    return emulator.get_result(state, potion_table)


def _run_scenarios(
    emulator: BattleEmulator,
    agent: HeuristicAgent,
    scenarios: list,
    potion_table: PotionValueTable,
    verbose: bool = False,
) -> list[BattleResult]:
    results = []
    for i, scenario in enumerate(scenarios):
        trace = [] if verbose else None
        result = simulate_battle(emulator, agent, scenario, potion_table, trace=trace)
        results.append(result)
        if verbose:
            print(
                f"[scenario {i}] seed={scenario['seed']} win={result.win} "
                f"remaining_hp={result.remaining_hp} turns={result.turn_count} "
                f"enemy_hp_removed_ratio={result.enemy_hp_removed_ratio:.2f}"
            )
            for label, target_index, score, engine_state in trace:
                enemy_hp = sum(e.get("hp", 0) for e in engine_state.get("enemies") or [])
                tgt = f" target={target_index}" if target_index is not None else ""
                print(
                    f"    -> {label:<20}{tgt:<12} score={score:>10.2f} "
                    f"hp={engine_state.get('hp')} block={engine_state.get('block')} "
                    f"enemy_hp={enemy_hp}"
                )
    return results


def run_greedy_baseline(
    scenarios: list,
    weights: dict,
    potion_table: PotionValueTable,
    repo_root: Path | None = None,
    verbose: bool = False,
) -> list[BattleResult]:
    emulator = build_emulator(repo_root)
    evaluator = build_state_evaluator(potion_table)
    agent = HeuristicAgent(emulator, evaluator, weights)
    return _run_scenarios(emulator, agent, scenarios, potion_table, verbose)


def run_turn_beam_search(
    scenarios: list,
    weights: dict,
    potion_table: PotionValueTable,
    repo_root: Path | None = None,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    max_plays: int = DEFAULT_MAX_PLAYS,
    verbose: bool = False,
) -> list[BattleResult]:
    emulator = build_emulator(repo_root)
    evaluator = build_state_evaluator(potion_table)
    searcher = TurnBeamSearcher(emulator, evaluator, beam_width=beam_width, max_plays=max_plays)
    agent = HeuristicAgent(emulator, evaluator, weights, searcher=searcher)
    return _run_scenarios(emulator, agent, scenarios, potion_table, verbose)


def run_sampled_lookahead_search(
    scenarios: list,
    weights: dict,
    potion_table: PotionValueTable,
    repo_root: Path | None = None,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    max_plays: int = DEFAULT_MAX_PLAYS,
    K: int = DEFAULT_K,
    D: int = DEFAULT_D,
    M: int = DEFAULT_M,
    verbose: bool = False,
) -> list[BattleResult]:
    emulator = build_emulator(repo_root)
    evaluator = build_state_evaluator(potion_table)
    turn_searcher = TurnBeamSearcher(emulator, evaluator, beam_width=beam_width, max_plays=max_plays)
    lookahead_searcher = LookaheadSearcher(emulator, evaluator, turn_searcher=turn_searcher, K=K, D=D, M=M)
    agent = HeuristicAgent(emulator, evaluator, weights, lookahead_searcher=lookahead_searcher)
    return _run_scenarios(emulator, agent, scenarios, potion_table, verbose)


def main() -> None:
    scenario_set = load_eval_scenarios(train_n=3, validation_n=2, test_n=2)
    weights = load_initial_weights()
    potion_table = load_potion_value_table()

    print("=== Phase 1: greedy baseline on train scenarios ===")
    greedy_results = run_greedy_baseline(scenario_set.train, weights, potion_table, verbose=True)
    greedy_wins = sum(1 for r in greedy_results if r.win)
    print(f"\nWins: {greedy_wins}/{len(greedy_results)}")

    print("\n=== Phase 2: turn beam search on train scenarios ===")
    beam_results = run_turn_beam_search(scenario_set.train, weights, potion_table, verbose=True)
    beam_wins = sum(1 for r in beam_results if r.win)
    print(f"\nWins: {beam_wins}/{len(beam_results)}")


if __name__ == "__main__":
    main()
