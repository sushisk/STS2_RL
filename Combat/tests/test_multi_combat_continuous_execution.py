"""Continuous real-Emulator multi-combat regression for Vertical Slice v1.

This is intentionally a stress/soak-style native test, not a mocked unit test. It runs
multiple full combats back-to-back through the real Main loop, Search Coordinator,
Candidate Pipeline, RNG Hypothesis path where needed, BranchWorkerPool subprocesses,
fault retry taxonomy, and Commit aggregation.
"""

from __future__ import annotations

import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import BRANCH_STATUS_SUCCESS, BranchWorkerPool, LeaseRegistry  # noqa: E402
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    DecisionContext,
    DecisionSignature,
    SemanticAction,
)
from search.main_loop import (  # noqa: E402
    ROUTE_PENDING_STATIC,
    ROUTE_SEARCH,
    CombatTerminalOutcome,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    run_until_terminal_or_fault,
)
from search.multi_round_search import BeamSearchConfig, build_beam_search_strategy  # noqa: E402
from search.search_coordinator import SearchCoordinatorConfig, build_search_strategy  # noqa: E402


@dataclass(frozen=True)
class CombatScenarioCase:
    name: str
    spec: dict
    strategy_kind: str
    expected_outcome: str
    max_iterations: int = 80


@dataclass(frozen=True)
class CombatRunRecord:
    name: str
    strategy_kind: str
    combat_session_id: str
    step_index: int
    outcome: str
    elapsed_s: float


def _spec(
    *,
    character_id: str = "IRONCLAD",
    hand: list[str] | None = None,
    draw_pile: list[str] | None = None,
    discard_pile: list[str] | None = None,
    relics: list[str] | None = None,
    potions: list[dict] | None = None,
    enemies: list[dict] | None = None,
    player_hp: int | None = None,
    player_max_hp: int | None = None,
    seed: int = 1,
) -> dict:
    return {
        "character_id": character_id,
        "player_hp": player_hp,
        "player_max_hp": player_max_hp,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD"],
        "draw_pile": draw_pile if draw_pile is not None else [],
        "discard_pile": discard_pile if discard_pile is not None else [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": relics if relics is not None else [],
        "potions": potions if potions is not None else [],
        "seed": seed,
        "enemies": enemies if enemies is not None else [{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
    }


def _combat_start_deck_multiset(spec: dict) -> dict[str, int]:
    cards: list[str] = []
    for pile_name in ("hand", "draw_pile", "discard_pile", "exhaust_pile"):
        cards.extend(spec.get(pile_name) or [])
    for pile_name in ("hand_cards", "draw_pile_cards", "discard_pile_cards", "exhaust_pile_cards"):
        cards.extend((card.get("card_id") for card in spec.get(pile_name) or [] if card.get("card_id")))
    return dict(Counter(cards))


def _routing_policy(boundary: str) -> str:
    if boundary == BOUNDARY_STABLE:
        return ROUTE_SEARCH
    if boundary == BOUNDARY_PENDING:
        return ROUTE_PENDING_STATIC
    raise AssertionError(f"unexpected non-terminal boundary routed for decision: {boundary!r}")


def _build_strategy(kind: str, pool: BranchWorkerPool, registry: LeaseRegistry, loop_state, deck_multiset: dict[str, int]):
    # Strategy instances are combat-scoped because Main invariant checking closes over
    # this combat's loop_state and RNG hypotheses need this combat's starting deck.
    # The worker pool and registry stay shared to stress repeated service-style reuse.
    coordinator_config = SearchCoordinatorConfig(
        width=1,
        hypothesis_count=2,
        max_retries=1,
        request_timeout_s=120.0,
    )
    if kind == "single_round":
        return build_search_strategy(
            pool,
            config=coordinator_config,
            combat_start_deck_multiset=deck_multiset,
            lease_registry=registry,
            main_state_provider=lambda: loop_state,
        )
    if kind == "beam":
        return build_beam_search_strategy(
            pool,
            config=BeamSearchConfig(
                coordinator=coordinator_config,
                beam_width=2,
                max_rounds=2,
            ),
            combat_start_deck_multiset=deck_multiset,
            lease_registry=registry,
            main_state_provider=lambda: loop_state,
        )
    raise AssertionError(f"unknown strategy kind {kind!r}")


def _run_one_combat(case: CombatScenarioCase, pool: BranchWorkerPool, registry: LeaseRegistry) -> CombatRunRecord:
    session = LiveCombatSession()
    state = session.start_combat(case.spec)
    loop_state = initialize_main_loop_state(session, state)
    strategy = _build_strategy(
        case.strategy_kind,
        pool,
        registry,
        loop_state,
        _combat_start_deck_multiset(case.spec),
    )

    started = time.perf_counter()
    outcome = run_until_terminal_or_fault(
        loop_state,
        direct_selector=first_candidate_direct_selector,
        search_strategy=strategy,
        routing_policy=_routing_policy,
        max_iterations=case.max_iterations,
    )
    elapsed = time.perf_counter() - started

    assert isinstance(outcome, CombatTerminalOutcome), outcome
    assert outcome.final_state.is_terminal
    assert outcome.final_state.outcome == case.expected_outcome, outcome.final_state.outcome
    frame = outcome.final_state.decision_frame
    assert frame is not None
    assert frame.combat_session_id == loop_state.current_result.decision_frame.combat_session_id

    record = CombatRunRecord(
        name=case.name,
        strategy_kind=case.strategy_kind,
        combat_session_id=frame.combat_session_id,
        step_index=frame.step_index,
        outcome=outcome.final_state.outcome,
        elapsed_s=elapsed,
    )
    print(
        "COMBAT "
        f"name={record.name} strategy={record.strategy_kind} "
        f"session={record.combat_session_id} step={record.step_index} "
        f"outcome={record.outcome} elapsed_s={record.elapsed_s:.3f}"
    )
    return record


def _assert_pool_responsive_after_continuous_run(pool: BranchWorkerPool, registry: LeaseRegistry) -> None:
    session = LiveCombatSession()
    state = session.start_combat(
        _spec(hand=["DEFEND_IRONCLAD"], enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 999}])
    )
    action = state._cached_legal_actions[0]  # noqa: SLF001
    params = action.get("parameters") or {}
    signature = DecisionSignature.from_battle_state(
        state,
        semantic_action=SemanticAction(
            action_type=action["action_type"],
            card_id=params.get("cardId"),
            target_type=params.get("targetType"),
        ),
        resolved_action=action,
    )
    context = DecisionContext.from_main_stable_capture(session.capture_snapshot(), state, signature)
    pipeline = build_candidate_pipeline_result(context, width=1)
    assert isinstance(pipeline, CandidatePipelineSuccess), pipeline

    from search.branch_worker_pool import WORK_KIND_CONTINUATION, WorkItem, derive_context_id  # noqa: PLC0415

    item = WorkItem.from_candidate_ref(
        context,
        pipeline.continuation_candidate,
        work_kind=WORK_KIND_CONTINUATION,
        context_id=derive_context_id(context),
        work_id="post-continuous-health-check",
    )
    result = pool.dispatch_work_items([item], registry)[0]
    assert result.status == BRANCH_STATUS_SUCCESS, result.diagnostics
    assert result.worker_id in set(pool.worker_ids)
    assert all(pool._workers[worker_id].process.is_alive() for worker_id in pool.worker_ids)  # noqa: SLF001
    print(f"POOL_HEALTH worker_ids={pool.worker_ids} generations={pool.worker_generations}")


def _continuous_cases() -> list[CombatScenarioCase]:
    return [
        CombatScenarioCase(
            name="ironclad_whirlwind_lethal_single",
            strategy_kind="single_round",
            expected_outcome="victory",
            spec=_spec(hand=["WHIRLWIND"], enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 1}]),
        ),
        CombatScenarioCase(
            name="ironclad_strike_single_target_beam",
            strategy_kind="beam",
            expected_outcome="victory",
            spec=_spec(hand=["STRIKE_IRONCLAD"], enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 5}]),
        ),
        CombatScenarioCase(
            name="ironclad_anchor_whirlwind_single",
            strategy_kind="single_round",
            expected_outcome="victory",
            spec=_spec(
                hand=["WHIRLWIND"],
                relics=["ANCHOR"],
                enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
            ),
        ),
        CombatScenarioCase(
            name="ironclad_potion_belt_whirlwind_beam",
            strategy_kind="beam",
            expected_outcome="victory",
            spec=_spec(
                hand=["WHIRLWIND"],
                relics=["POTION_BELT"],
                potions=[{"slot": 3, "potion_id": "FIRE_POTION"}],
                enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
            ),
        ),
        CombatScenarioCase(
            name="ironclad_hypothesis_drawpile_single",
            strategy_kind="single_round",
            expected_outcome="victory",
            spec=_spec(
                hand=["STRIKE_IRONCLAD"],
                draw_pile=["BASH"],
                relics=["BIIIG_HUG"],
                enemies=[{"monster_id": "CALCIFIED_CULTIST", "hp": 6}],
            ),
        ),
    ]


def test_shared_pool_runs_multiple_real_combats_back_to_back_to_terminal():
    started = time.perf_counter()
    records: list[CombatRunRecord] = []
    registry = LeaseRegistry()

    with BranchWorkerPool(worker_count=3, request_timeout_s=120.0) as pool:
        for case in _continuous_cases():
            records.append(_run_one_combat(case, pool, registry))

        assert len(records) == 5
        assert len({record.combat_session_id for record in records}) == len(records), records
        assert all(record.outcome == "victory" for record in records)
        assert all(record.step_index >= 1 for record in records)
        _assert_pool_responsive_after_continuous_run(pool, registry)

    total_elapsed = time.perf_counter() - started
    print(f"TOTAL combats={len(records)} elapsed_s={total_elapsed:.3f}")
    assert total_elapsed < 240.0, f"continuous real-combat run took {total_elapsed:.3f}s"


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(_run_all())
