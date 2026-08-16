"""Regression coverage for the Snapshot-Restore -> End Turn Emulator gap
(RL担当指示: End Turn/Snapshot復元 修正) - now FIXED on the Emulator side.

History: ending a turn (`action_type == "system"`) against ANY state reached via
`LiveCombatSession.restore_snapshot()`/`restore_snapshot_json()` used to hang the
underlying C# engine call for ~15s inside `GameInstance.WaitUntilChoiceOrSettled()`, then
surface only a bare `System.TimeoutException` - never the real cause. The real cause was
that `SnapshotRestorer` never re-established a restored enemy's current Move
(`Intent.stateId` stayed the Emulator's own `UNSET_MOVE` sentinel).

Confirmed fixed (empirically, via this file's own two Branch Worker tests, which flipped
from a fast-fail to `BRANCH_STATUS_SUCCESS` the moment the Emulator change landed - no
other RL-side change was needed): the Emulator's `SnapshotRestorer.ApplyEnemyMoves()`
(schema/milestone bumped to `"phase3c.5"`, see `Combat/combat_state_snapshot.py`'s
`KNOWN_SCHEMA_VERSIONS`) now restores each enemy's `Intent.stateId`/`StateLog` via the
same `MonsterModel.SetMoveImmediate()`/`MonsterMoveStateMachine.ForceCurrentState()` the
`ResetFromScenario` path already used for its own `ForcedMove` field - deterministic, no
RNG draw, matching the fix design in
`Outputs/reports/rl_snapshot_restore_enemy_move_gap_design_20260807.md`. A Branch that
Restores a Snapshot and ends the turn now genuinely reproduces root's own continuation:
the enemy performs the SAME move root's timeline had already decided, not a freshly
re-rolled one.

`LiveCombatSession.step()`'s own defensive guard (`SnapshotRestoreMissingMoveError` /
`FAULT_SNAPSHOT_MOVE_MISSING`, raised whenever a living enemy's Move is still unset when
End Turn is attempted) stays in place as defense-in-depth - it no longer fires for the
Emulator's now-fixed Restore path, but it's cheap insurance against any future regression
or an as-yet-unfound edge case (e.g. a state not covered by `ApplyEnemyMoves()`). This
file's last test (`test_step_rejects_end_turn_when_a_living_enemy_still_lacks_a_move`)
exercises that guard directly and synthetically, independent of whatever the Emulator
currently does, so it keeps working even if the Emulator regresses OR improves further.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import copy
import dataclasses
import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from live_combat_session import LiveCombatSession, SnapshotRestoreMissingMoveError  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BOUNDARY_STABLE,
    BRANCH_STATUS_SUCCESS,
    BranchWorkerPool,
    LeaseRegistry,
    WorkItem,
    derive_context_id,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import DecisionContext, DecisionSignature, SemanticAction  # noqa: E402
from search.fault_taxonomy import FAULT_SNAPSHOT_MOVE_MISSING  # noqa: E402
from verify_restore_bootstrap_phase3b import _make_eligible  # noqa: E402


def _spec():
    # A real, non-instant-kill Combat: the CALCIFIED_CULTIST survives the whole first
    # player turn (only Defend cards are played), so it is guaranteed alive - and
    # therefore genuinely due to act - when End Turn is reached. This is the "avoid
    # scenarios where no real enemy turn ever happens" fix RL担当指示 item2 calls for.
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["DEFEND_IRONCLAD", "DEFEND_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _semantic_action_for(action: dict) -> SemanticAction:
    return SemanticAction(action_type=action["action_type"], semantic_key=action.get("semantic_key", ""))


def _root_context(snapshot, state) -> DecisionContext:
    representative = state._cached_legal_actions[0]  # noqa: SLF001
    signature = DecisionSignature.from_battle_state(
        state, semantic_action=_semantic_action_for(representative), resolved_action=representative
    )
    return DecisionContext.from_main_stable_capture(snapshot, state, signature)


def _end_turn_work_item(context: DecisionContext) -> WorkItem:
    pipeline = build_candidate_pipeline_result(context, width=8)
    assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
    end_turn_candidate = next(
        ref
        for ref in [pipeline.continuation_candidate, *pipeline.sub_branch_candidates]
        if ref.semantic_action.action_type == "system"
    )
    return WorkItem.from_candidate_ref(
        context, end_turn_candidate, work_kind="continuation", context_id=derive_context_id(context), work_id="end-turn"
    )


def _assert_enemy_genuinely_acted_and_reached_next_player_decision(result, *, before_turn: int) -> None:
    assert result.status == BRANCH_STATUS_SUCCESS, result.diagnostics
    assert result.result_signature.boundary == BOUNDARY_STABLE, result.result_signature
    assert result.child_snapshot is not None, "expected a Stable child_snapshot after End Turn resolved"

    snapshot = result.child_snapshot
    assert snapshot.TurnNumber == before_turn + 1, snapshot.TurnNumber
    assert snapshot.CurrentSide == "Player", "expected to be back at a player decision after the enemy's turn"

    enemies = list(snapshot.Enemies)
    assert enemies, "expected the CALCIFIED_CULTIST to still be present"
    for enemy in enemies:
        if enemy.IsAlive:
            assert enemy.Intent is not None, enemy
            assert enemy.Intent.get("stateId") != "UNSET_MOVE", enemy.Intent

    move_entries = [e for e in snapshot.CombatHistory.Entries if e.EntryType == "MonsterPerformedMoveEntry"]
    assert move_entries, "expected the restored enemy to have genuinely performed a move, not been skipped"

    action_types = {a["action_type"] for a in (result.next_legal_actions or [])}
    assert "card" in action_types or "system" in action_types, "expected to land back on a real player Decision"


def test_branch_worker_end_turn_immediately_after_restore_enemy_acts_and_reaches_next_player_decision():
    """RL担当指示 item1 (Branch Worker restores a Snapshot, executes End Turn explicitly)
    + item2's "初回Decision直後" case: no card is played first - End Turn is attempted
    directly against the freshly-restored root Decision. Confirms the Emulator fix: the
    restored enemy's already-decided Move is preserved and genuinely performed, and the
    Branch reaches a real next player Decision - not a fast-fail, not a hang."""
    session = LiveCombatSession()
    state = session.start_combat(_spec())
    root_snapshot = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    work_item = _end_turn_work_item(_root_context(root_snapshot, state))

    registry = LeaseRegistry()
    with BranchWorkerPool(worker_count=1, request_timeout_s=60.0) as pool:
        results = pool.dispatch_work_items([work_item], registry)

    _assert_enemy_genuinely_acted_and_reached_next_player_decision(results[0], before_turn=state.turn)


def test_branch_worker_end_turn_after_intermediate_card_play_enemy_acts_and_reaches_next_player_decision():
    """RL担当指示 item2's other required case: End Turn is attempted from an
    INTERMEDIATE Decision reached by playing a card first, not the very first Decision -
    confirms the Emulator fix holds across an intervening Step too, not only for the
    Decision immediately after Restore."""
    session = LiveCombatSession()
    state = session.start_combat(_spec())
    defend_action = next(a for a in state._cached_legal_actions if a["action_type"] == "card")  # noqa: SLF001
    mid_state = session.step(state, defend_action, target_enemy_index=0, stop_at_pending=True)
    mid_snapshot = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    work_item = _end_turn_work_item(_root_context(mid_snapshot, mid_state))

    registry = LeaseRegistry()
    with BranchWorkerPool(worker_count=1, request_timeout_s=60.0) as pool:
        results = pool.dispatch_work_items([work_item], registry)

    _assert_enemy_genuinely_acted_and_reached_next_player_decision(results[0], before_turn=mid_state.turn)


def test_end_turn_without_any_restore_reaches_next_player_decision_normally():
    """RL担当指示 item5: confirm the normal (never-restored) Main Session path is
    unaffected by either the Emulator fix or the RL-side guard - End Turn must still make
    the enemy act and reach the next player Decision exactly as before this change."""
    session = LiveCombatSession()
    state = session.start_combat(_spec())
    end_turn = next(a for a in state._cached_legal_actions if a["action_type"] == "system")  # noqa: SLF001
    next_state = session.step(state, end_turn, stop_at_pending=True)

    assert next_state.turn == state.turn + 1
    enemies = next_state.engine_state.get("enemies", [])
    assert enemies, "expected the CALCIFIED_CULTIST to still be present"
    assert all((e.get("intent") or {}).get("stateId") != "UNSET_MOVE" for e in enemies), enemies
    card_action_types = {a["action_type"] for a in next_state._cached_legal_actions}  # noqa: SLF001
    assert "card" in card_action_types or "system" in card_action_types, "expected to land back on a real player Decision"


def test_end_turn_across_multiple_combats_in_one_process_all_work_normally():
    """RL担当指示 item5: confirm the guard is scoped per-session/per-engine_state, not
    sticky garbage from a previous combat - repeated fresh combats (the "複数戦闘" path)
    in the same OS process must each End Turn normally."""
    for _ in range(2):
        session = LiveCombatSession()
        state = session.start_combat(_spec())
        end_turn = next(a for a in state._cached_legal_actions if a["action_type"] == "system")  # noqa: SLF001
        next_state = session.step(state, end_turn, stop_at_pending=True)
        assert next_state.turn == state.turn + 1


def test_step_rejects_end_turn_when_a_living_enemy_still_lacks_a_move():
    """Defense-in-depth: directly, synthetically exercises `LiveCombatSession.step()`'s
    own `SnapshotRestoreMissingMoveError` guard, independent of whatever the Emulator's
    Restore currently does (the two Branch Worker tests above now exercise the FIXED
    Emulator path and can no longer trigger this guard on their own). A real, valid
    `battle_state` from a normal `start_combat()` is mutated ONLY on the Python-side
    `engine_state` dict (`step()`'s guard reads exclusively from `battle_state.
    engine_state`, before any CLR call - see that method's own docstring) to simulate a
    living enemy whose Move is unset, proving the guard still fires immediately with the
    right `fault_kind`/message rather than ever reaching the CLR `Step()` call."""
    session = LiveCombatSession()
    state = session.start_combat(_spec())
    end_turn = next(a for a in state._cached_legal_actions if a["action_type"] == "system")  # noqa: SLF001

    broken_engine_state = copy.deepcopy(state.engine_state)
    broken_engine_state["enemies"][0]["intent"] = {"stateId": "UNSET_MOVE", "intentTypes": []}
    broken_state = dataclasses.replace(state, engine_state=broken_engine_state)

    try:
        session.step(broken_state, end_turn, stop_at_pending=True)
        raise AssertionError("expected SnapshotRestoreMissingMoveError")
    except SnapshotRestoreMissingMoveError as exc:
        assert exc.fault_kind == FAULT_SNAPSHOT_MOVE_MISSING
        assert exc.missing_enemies and exc.missing_enemies[0]["id"] == "CALCIFIED_CULTIST", exc.missing_enemies

    # The session itself must remain fully usable afterward (see SnapshotRestoreMissingMoveError's
    # own docstring: raised before any CLR call, so it must not fault the session).
    next_state = session.step(state, end_turn, stop_at_pending=True)
    assert next_state.turn == state.turn + 1


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
