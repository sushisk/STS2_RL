"""Tests for Combat/search/shadow_adapter.py.

Native assertion runner, matching the surrounding Combat search tests. The main proof
tests use real Restore, real HeuristicAgent, and real BranchWorkerPool execution.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from emulator_bridge import to_plain  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_worker_pool import BranchWorkerPool  # noqa: E402
from search.search_coordinator import SearchCoordinatorConfig  # noqa: E402
from search.shadow_adapter import (  # noqa: E402
    ShadowAction,
    ShadowComparisonResult,
    _actions_agree,
    compare_paths,
    run_new_path,
    run_old_path,
    run_shadow_comparison_over_snapshots,
)


def _simple_spec(hand=None, draw_pile=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": hand if hand is not None else ["WHIRLWIND"],
        "draw_pile": draw_pile if draw_pile is not None else [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _deck_multiset(*card_ids: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card_id in card_ids:
        counts[card_id] = counts.get(card_id, 0) + 1
    return counts


def _snapshot(spec=None):
    session = LiveCombatSession()
    session.start_combat(spec or _simple_spec())
    return session.capture_snapshot()


def _config() -> SearchCoordinatorConfig:
    return SearchCoordinatorConfig(width=1, hypothesis_count=2, max_retries=0, request_timeout_s=120.0)


def _gameplay_projection(state: dict) -> dict:
    return {
        "hp": state.get("hp"),
        "maxHp": state.get("maxHp"),
        "block": state.get("block"),
        "energy": state.get("energy"),
        "stars": state.get("stars"),
        "hand": state.get("hand"),
        "drawPile": state.get("drawPile"),
        "discardPile": state.get("discardPile"),
        "exhaustPile": state.get("exhaustPile"),
        "playPile": state.get("playPile"),
        "playerPowers": state.get("playerPowers"),
        "relics": state.get("relics"),
        "potions": state.get("potions"),
        "enemies": [
            {
                "id": enemy.get("id"),
                "hp": enemy.get("hp"),
                "maxHp": enemy.get("maxHp"),
                "block": enemy.get("block"),
                "isAlive": enemy.get("isAlive"),
                "powers": enemy.get("powers"),
            }
            for enemy in (state.get("enemies") or [])
        ],
        "pendingChoice": state.get("pendingChoice"),
    }


def test_shadow_action_equality_uses_semantics_not_action_id_or_label():
    old = ShadowAction("card", card_id="WHIRLWIND", target_type="AllEnemies", label="Whirlwind")
    same = ShadowAction("card", card_id="WHIRLWIND", target_type="AllEnemies", label="Whirlwind+")
    different = ShadowAction("system", label="End Turn")

    assert _actions_agree(old, same)
    assert not _actions_agree(old, different)
    assert not _actions_agree(old, None)
    assert _actions_agree(None, None)


def test_run_old_path_real_restore_snapshot_produces_chosen_action():
    result = run_old_path(_snapshot(_simple_spec(hand=["WHIRLWIND"], enemy_hp=1)))

    assert result.action is not None
    assert result.action.action_type == "card"
    assert result.action.semantic_key == "0:WHIRLWIND"
    assert result.action_id is not None
    assert result.score is not None
    assert result.candidate_details
    assert result.legal_action_count > 0
    assert result.elapsed_ms >= 0.0
    assert result.restored_combat_session_id


def test_run_new_path_real_restore_snapshot_produces_result():
    snapshot = _snapshot(_simple_spec(hand=["WHIRLWIND"], enemy_hp=1))

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        result = run_new_path(
            snapshot,
            pool=pool,
            config=_config(),
        )

    assert result.status in {"success", "evaluation_failure", "no_decision"}
    assert result.elapsed_ms >= 0.0
    assert result.restored_combat_session_id
    if result.status == "success":
        assert result.action is not None
        assert result.action.action_type == "card"
        assert result.action.semantic_key == "0:WHIRLWIND"
        assert result.planned_sequence_length >= 1
    else:
        assert result.action is None
        assert result.detail


def test_compare_paths_reports_agreement_on_trivial_lethal_board():
    snapshot = _snapshot(_simple_spec(hand=["WHIRLWIND"], enemy_hp=1))

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        result = compare_paths(
            snapshot,
            pool=pool,
            config=_config(),
        )

    assert isinstance(result, ShadowComparisonResult)
    assert result.old.action is not None
    assert result.new.action is not None
    assert result.actions_agree
    assert result.old.action.comparison_key == result.new.action.comparison_key


def test_compare_paths_reports_disagreement_without_misnormalizing_when_actions_differ():
    old = ShadowAction("card", card_id="STRIKE_IRONCLAD", target_type="AnyEnemy", target_enemy_index=0)
    new = ShadowAction("system", label="End Turn")

    assert not _actions_agree(old, new)


def test_batch_shadow_comparison_preserves_input_order_and_uses_distinct_restores():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["WHIRLWIND"], draw_pile=["STRIKE_IRONCLAD"], enemy_hp=50))
    first = session.capture_snapshot()
    whirlwind = next(action for action in state._cached_legal_actions if action["action_type"] == "card")  # noqa: SLF001
    state = session.step(state, whirlwind)
    second = session.capture_snapshot()

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        results = run_shadow_comparison_over_snapshots(
            [first, second],
            pool=pool,
            config=_config(),
        )

    assert len(results) == 2
    assert all(isinstance(result, ShadowComparisonResult) for result in results)
    assert results[0].old.legal_action_count > 0
    assert results[1].old.legal_action_count > 0
    assert results[0].new.restored_combat_session_id != results[1].new.restored_combat_session_id


def test_shadow_comparison_does_not_advance_original_live_session_state():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(hand=["WHIRLWIND"], enemy_hp=1))
    snapshot = session.capture_snapshot()
    before_legal = session.get_legal_actions()
    before_state = _gameplay_projection(to_plain(session.get_observation().State))

    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        for _ in range(2):
            result = compare_paths(
                snapshot,
                pool=pool,
                config=_config(),
            )
            assert result.old.action is not None
            assert result.new.status in {"success", "evaluation_failure", "no_decision"}

    after_legal = session.get_legal_actions()
    after_state = _gameplay_projection(to_plain(session.get_observation().State))

    assert after_legal == before_legal
    assert after_state == before_state


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
