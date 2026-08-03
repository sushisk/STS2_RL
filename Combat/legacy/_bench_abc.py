"""Scratch benchmark script - A/B/C optimization comparison on the Necrobinder vs
AEONGLASS benchmark scenario. Not part of the package; deleted after use.
"""
import random
import time

from battle_emulator import BattleEmulator
from legacy.state_evaluator import StateEvaluator, DEFAULT_WEIGHTS
from legacy.potion_value_table import PotionValueTable
from scenario_set import ScenarioSet
from legacy.beam_search import TurnBeamSearcher, DEFAULT_BEAM_WIDTH, DEFAULT_MAX_PLAYS
from lookahead import LookaheadSearcher, DEFAULT_K, DEFAULT_D, DEFAULT_M
from legacy.heuristic_agent import HeuristicAgent

RNG_SEED = 4242


def run_condition(name, use_legal_action_cache, merge_equivalent_states):
    print(f"=== Condition {name}: cache={use_legal_action_cache} merge={merge_equivalent_states} ===", flush=True)
    emulator = BattleEmulator(use_legal_action_cache=use_legal_action_cache)
    evaluator = StateEvaluator(PotionValueTable())
    weights = dict(DEFAULT_WEIGHTS)
    scenarios = ScenarioSet.necrobinder_vs_aeonglass_benchmark(seed=1)
    state = emulator.initialize(scenarios.train[0])

    turn_searcher = TurnBeamSearcher(
        emulator, evaluator,
        beam_width=DEFAULT_BEAM_WIDTH, max_plays=DEFAULT_MAX_PLAYS,
        merge_equivalent_states=merge_equivalent_states,
    )
    lookahead_searcher = LookaheadSearcher(
        emulator, evaluator, turn_searcher=turn_searcher,
        K=DEFAULT_K, D=DEFAULT_D, M=DEFAULT_M, rng=random.Random(RNG_SEED),
    )
    agent = HeuristicAgent(emulator, evaluator, weights, lookahead_searcher=lookahead_searcher)

    import cProfile
    profiler = cProfile.Profile()
    t0 = time.time()
    profiler.enable()
    chosen = agent.choose_action(state)
    profiler.disable()
    elapsed = time.time() - t0

    import pstats
    stats = pstats.Stats(profiler)
    reset_calls = 0
    reset_time = 0.0
    apply_calls = 0
    apply_time = 0.0
    for func, stat in stats.stats.items():
        if func[2] == "_restore":
            cc, nc, tt, ct, callers = stat
            reset_calls = nc
            reset_time = ct
        if func[2] == "apply_action":
            cc, nc, tt, ct, callers = stat
            apply_calls = nc
            apply_time = tt

    print(f"  elapsed: {elapsed:.3f}s")
    print(f"  ResetFromScenario calls (via _restore): {reset_calls}  cumtime={reset_time:.3f}s")
    print(f"  Step calls (via apply_action, ncalls): {apply_calls}  apply_action_tottime={apply_time:.3f}s")
    print(f"  chosen: action_id={chosen.action_id} label={chosen.label!r} target={chosen.target_index} score={chosen.score!r}")
    return {
        "elapsed": elapsed,
        "reset_calls": reset_calls,
        "apply_calls": apply_calls,
        "chosen": (chosen.action_id, chosen.label, chosen.target_index, chosen.score),
    }


results = {}
results["A"] = run_condition("A (no optimization)", use_legal_action_cache=False, merge_equivalent_states=False)
results["B"] = run_condition("B (legal-action cache only)", use_legal_action_cache=True, merge_equivalent_states=False)
results["C"] = run_condition("C (both optimizations)", use_legal_action_cache=True, merge_equivalent_states=True)

print()
print("=== SUMMARY ===")
for k, v in results.items():
    print(k, v)

print()
print("A vs C action match:", results["A"]["chosen"][:3] == results["C"]["chosen"][:3])
