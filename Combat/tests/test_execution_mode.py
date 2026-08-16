"""Tests for execution_mode.py and the RL-owns-no-inference refactor.

Native assertion runner, no pytest dependency. Real `LiveCombatSession`/`BranchWorkerPool`
where needed - no mocking of the decision surface itself.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from execution_mode import (  # noqa: E402
    EXECUTION_MODES,
    MODE_EXTERNAL_CONTROL,
    MODE_ZERO_INDEX,
    make_external_action_selector,
    zero_index_pending_selector,
)
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import BranchWorkerPool, LeaseRegistry  # noqa: E402
from search.main_loop import (  # noqa: E402
    build_main_decision_context,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    run_until_terminal_or_fault,
)
from search.candidate_pipeline import (  # noqa: E402
    build_candidate_pipeline_result,
    build_candidate_pipeline_result_for_explicit_candidates,
    extract_candidates,
)
from search.search_coordinator import (  # noqa: E402
    SearchCoordinatorConfig,
    dispatch_explicit_candidates,
)


def _toolbox_pending_spec():
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": ["TOOLBOX"],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def test_execution_modes_are_exactly_zero_index_and_external_control():
    assert EXECUTION_MODES == {MODE_ZERO_INDEX, MODE_EXTERNAL_CONTROL}


def test_zero_index_pending_selector_never_reorders_never_scores():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    step = zero_index_pending_selector(state)
    legal = state._cached_legal_actions  # noqa: SLF001
    first = legal[0]
    assert step.semantic_action.action_type == first.get("action_type")
    assert step.semantic_action.semantic_key == first.get("semantic_key", "")
    assert step.expected_signature is None


def test_first_candidate_direct_selector_is_the_zero_index_direct_implementation():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    step = first_candidate_direct_selector(state)
    legal = state._cached_legal_actions  # noqa: SLF001
    assert step.semantic_action.action_type == legal[0]["action_type"]


def test_external_control_selector_resolves_exactly_the_given_action_id_never_others():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    legal = state._cached_legal_actions  # noqa: SLF001
    target = legal[-1]  # deliberately NOT index 0 - proves this is not zero_index
    selector = make_external_action_selector(lambda _legal: target["action_id"])
    step = selector(state)
    assert step.semantic_action.action_type == target["action_type"]
    assert step.semantic_action.semantic_key == target.get("semantic_key", "")


def test_external_control_selector_rejects_unresolvable_action_id():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    selector = make_external_action_selector(lambda _legal: 999999)
    raised = False
    try:
        selector(state)
    except ValueError:
        raised = True
    assert raised


def test_zero_index_direct_and_pending_never_call_any_heuristic_or_model():
    """Static/behavioral check: neither selector imports or touches anything under
    Combat/legacy (the isolated Policy/Value/Heuristic inference package)."""
    import inspect

    from search.main_loop import first_candidate_direct_selector as direct_sel

    for fn in (direct_sel, zero_index_pending_selector):
        src = inspect.getsource(fn)
        assert "legacy" not in src
        assert "score" not in src.lower()
        assert "heuristic" not in src.lower()


def test_explicit_candidate_pipeline_never_implicitly_expands_all_legal_actions():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)

    all_candidates = extract_candidates(state)
    assert len(all_candidates) > 1

    raised_empty = False
    try:
        build_candidate_pipeline_result_for_explicit_candidates(context, [])
    except ValueError:
        raised_empty = True
    assert raised_empty, "explicit builder must not silently fall back to all legal actions"

    only_index_0 = build_candidate_pipeline_result_for_explicit_candidates(context, [0])
    assert only_index_0.sub_branch_candidates == []
    assert only_index_0.continuation_candidate.score == 0.0


def test_dispatch_explicit_candidates_never_selects_a_winner():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)

    with BranchWorkerPool(worker_count=2) as pool:
        registry = LeaseRegistry()
        deck_multiset = {"STRIKE_IRONCLAD": 1, "DEFEND_IRONCLAD": 1, "BASH": 1}
        results = dispatch_explicit_candidates(
            context, [0, 2], pool=pool, config=SearchCoordinatorConfig(), lease_registry=registry,
        )
        assert len(results) >= 2
        distinct = {r.work_item.candidate.semantic_action for r in results}
        assert len(distinct) == 2
        for r in results:
            assert not hasattr(r, "aggregate_score")
            assert not hasattr(r, "best_action")
            assert not hasattr(r.branch_result, "aggregate_score")


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
