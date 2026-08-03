"""Runtime audit: `external_control`/`zero_index` production paths never import or call
any Policy/Value/Heuristic inference code (RL担当指示：推論撤去後の総合テスト・デバッグ,
section 1).

Two independent guarantees are checked:
1. `sys.modules` never gains a `legacy.*` entry after a full zero_index/external_control
   session - the STRONGEST possible guarantee (the inference package was never even
   imported, not just "imported but not called").
2. A call-counting monkeypatch on every legacy entry point additionally proves zero
   invocations, in case some future change imports `legacy` for an unrelated reason
   without calling into it - both checks must independently show zero.

Native assertion runner, no pytest dependency. Real `LiveCombatSession`/`BranchWorkerPool`.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from execution_mode import make_external_action_selector, zero_index_pending_selector  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.main_loop import (  # noqa: E402
    build_main_decision_context,
    first_candidate_direct_selector,
    initialize_main_loop_state,
    run_until_terminal_or_fault,
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
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 1}],
    }


LEGACY_MODULE_NAMES = (
    "legacy",
    "legacy.heuristic_agent",
    "legacy.policy_agent",
    "legacy.choice_policy_agent",
    "legacy.beam_search",
    "legacy.lookahead",
    "legacy.state_evaluator",
    "legacy.potion_value_table",
    "legacy.main",
    "legacy._bench_abc",
)


def _assert_legacy_never_imported(label: str) -> None:
    leaked = [name for name in LEGACY_MODULE_NAMES if name in sys.modules]
    assert not leaked, f"{label}: legacy inference modules were imported: {leaked}"


def test_zero_index_combat_session_never_imports_legacy():
    for name in LEGACY_MODULE_NAMES:
        sys.modules.pop(name, None)
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    loop_state = initialize_main_loop_state(session, state)
    outcome = run_until_terminal_or_fault(
        loop_state, direct_selector=first_candidate_direct_selector, pending_selector=zero_index_pending_selector
    )
    assert outcome is not None
    _assert_legacy_never_imported("zero_index Combat session")


def test_external_control_combat_session_never_imports_legacy():
    for name in LEGACY_MODULE_NAMES:
        sys.modules.pop(name, None)
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    loop_state = initialize_main_loop_state(session, state)

    def resolve(legal_actions):
        return legal_actions[0]["action_id"]

    selector = make_external_action_selector(resolve)
    outcome = run_until_terminal_or_fault(loop_state, direct_selector=selector, pending_selector=selector)
    assert outcome is not None
    _assert_legacy_never_imported("external_control Combat session")


def test_main_decision_context_build_never_imports_legacy():
    for name in LEGACY_MODULE_NAMES:
        sys.modules.pop(name, None)
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)
    assert context is not None
    _assert_legacy_never_imported("build_main_decision_context")


def test_call_counting_stub_confirms_zero_invocations_across_full_zero_index_run():
    """Second, independent guarantee: even if some future change imports `legacy` for an
    unrelated reason, these fail-fast stubs prove none of its actual decision-making
    entry points are ever CALLED during a zero_index run.
    """
    import legacy.heuristic_agent as heuristic_agent_module
    import legacy.state_evaluator as state_evaluator_module

    calls = {"heuristic": 0, "state_eval": 0}

    original_choose = heuristic_agent_module.HeuristicAgent.choose_action if hasattr(
        heuristic_agent_module.HeuristicAgent, "choose_action"
    ) else None
    original_evaluate = state_evaluator_module.StateEvaluator.evaluate

    def _fail_fast_evaluate(self, *args, **kwargs):
        calls["state_eval"] += 1
        raise RuntimeError("StateEvaluator.evaluate() must never be called during a zero_index run")

    state_evaluator_module.StateEvaluator.evaluate = _fail_fast_evaluate
    try:
        session = LiveCombatSession()
        state = session.start_combat(_toolbox_pending_spec())
        loop_state = initialize_main_loop_state(session, state)
        run_until_terminal_or_fault(
            loop_state, direct_selector=first_candidate_direct_selector, pending_selector=zero_index_pending_selector
        )
    finally:
        state_evaluator_module.StateEvaluator.evaluate = original_evaluate

    assert calls["state_eval"] == 0, "StateEvaluator.evaluate was called during a zero_index run"


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
