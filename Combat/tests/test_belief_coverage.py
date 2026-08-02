"""Tests for Combat/search/belief_coverage.py - Task 6 PUBLIC_MULTISET coverage.

Native assertion runner, no pytest dependency. Run:
cd C:\\STS2_RL\\Combat\\tests && python test_belief_coverage.py
"""

from __future__ import annotations

import sys
import traceback
from collections import Counter
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import search.search_coordinator as coordinator_module  # noqa: E402
from battle_emulator import BattleState  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.belief_coverage import (  # noqa: E402
    assess_public_multiset_coverage,
    assess_public_multiset_coverage_for_combat_start,
    compute_public_multiset_with_coverage,
)
from search.branch_worker_pool import BranchWorkerPool, LeaseRegistry  # noqa: E402
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import DecisionContext, DecisionSignature, SemanticAction  # noqa: E402
from search.main_loop import SearchSuccess  # noqa: E402
from search.rng_hypothesis import compute_public_multiset  # noqa: E402
from search.search_coordinator import SearchCoordinatorConfig, build_search_strategy  # noqa: E402


def _simple_spec(hand=None, draw_pile=None, relics=None, player_powers=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": draw_pile if draw_pile is not None else ["BASH"],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": player_powers if player_powers is not None else [],
        "relics": relics if relics is not None else [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _semantic_action_for(action: dict) -> SemanticAction:
    params = action.get("parameters") or {}
    return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))


def _representative_signature(state: BattleState) -> DecisionSignature:
    action = state._cached_legal_actions[0]  # noqa: SLF001
    return DecisionSignature.from_battle_state(state, semantic_action=_semantic_action_for(action), resolved_action=action)


def _context(spec: dict) -> DecisionContext:
    session = LiveCombatSession()
    state = session.start_combat(spec)
    return DecisionContext.from_main_stable_capture(session.capture_snapshot(), state, _representative_signature(state))


def _deck_multiset(*card_ids: str) -> dict[str, int]:
    return dict(Counter(card_ids))


def test_assess_public_multiset_coverage_no_generation_relevant_sources_is_complete():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    assessment = assess_public_multiset_coverage(session.capture_snapshot())

    assert assessment.is_complete is True
    assert assessment.uncertain_sources == []
    assert "no known card-generation-capable" in assessment.reason


def test_assess_public_multiset_coverage_confirmed_active_relic_is_complete():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(relics=["BIIIG_HUG"]))
    assessment = assess_public_multiset_coverage(session.capture_snapshot())

    assert assessment.is_complete is True
    assert assessment.uncertain_sources == []
    assert "Relic:BIIIG_HUG" in assessment.reason


def test_assess_public_multiset_coverage_for_combat_start_toolbox_is_complete():
    spec = _simple_spec(relics=["TOOLBOX"])

    assessment = assess_public_multiset_coverage_for_combat_start(spec)

    assert assessment.is_complete is True
    assert assessment.uncertain_sources == []
    assert "Relic:TOOLBOX" in assessment.reason


def test_compute_public_multiset_with_coverage_delegates_to_rng_hypothesis():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(relics=["BIIIG_HUG"]))
    snapshot = session.capture_snapshot()
    combat_start = _deck_multiset("STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH")

    wrapped_multiset, assessment = compute_public_multiset_with_coverage(
        snapshot,
        combat_start_deck_multiset=combat_start,
    )
    direct_multiset = compute_public_multiset(snapshot, combat_start_deck_multiset=combat_start)

    assert wrapped_multiset == direct_multiset
    assert assessment.is_complete is True
    assert "Relic:BIIIG_HUG" in assessment.reason


def test_live_collision_course_generation_emits_resolvable_card_generated_entry():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["COLLISION_COURSE"], draw_pile=[], enemy_hp=999))
    action = next(a for a in state._cached_legal_actions if a["action_type"] == "card")  # noqa: SLF001
    session.step(state, action)
    snapshot = session.capture_snapshot()

    generated = [entry for entry in snapshot.CombatHistory.Entries if entry.EntryType == "CardGeneratedEntry"]
    assert len(generated) == 1
    assert generated[0].Fields.get("cardInstanceId") in {card.InstanceId for card in snapshot.Player.Hand}
    public_multiset = compute_public_multiset(snapshot, combat_start_deck_multiset={"COLLISION_COURSE": 1})
    assert public_multiset == {}


def test_search_coordinator_hypothesis_entries_include_public_multiset_coverage_diagnostics():
    context = _context(_simple_spec(hand=["STRIKE_IRONCLAD"], draw_pile=["BASH"], relics=["BIIIG_HUG"], enemy_hp=999))
    captured_entries = {}
    original_aggregate = coordinator_module.aggregate_hypothesis_results

    def _capturing_hypothesis_aggregate(entries, *, min_coverage_fraction):
        captured_entries["entries"] = list(entries)
        return original_aggregate(entries, min_coverage_fraction=min_coverage_fraction)

    coordinator_module.aggregate_hypothesis_results = _capturing_hypothesis_aggregate
    try:
        with BranchWorkerPool(worker_count=2, request_timeout_s=120.0) as pool:
            strategy = build_search_strategy(
                pool,
                config=SearchCoordinatorConfig(width=1, hypothesis_count=2),
                combat_start_deck_multiset=_deck_multiset("STRIKE_IRONCLAD", "BASH"),
                lease_registry=LeaseRegistry(),
            )
            result = strategy(context)
    finally:
        coordinator_module.aggregate_hypothesis_results = original_aggregate

    assert isinstance(result, SearchSuccess), result
    diagnostics = [entry.diagnostics["public_multiset_coverage"] for entry in captured_entries["entries"]]
    assert diagnostics
    assert all(item["is_complete"] is True for item in diagnostics)
    assert all(item["uncertain_sources"] == [] for item in diagnostics)
    assert all("Relic:BIIIG_HUG" in item["reason"] for item in diagnostics)


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
