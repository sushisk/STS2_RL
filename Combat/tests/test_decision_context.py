"""Tests for Combat/search/decision_context.py - Combat execution infrastructure Phase 2
(Decision Context / Decision Signature primitives).

Native assertion runner, no pytest dependency - matching this package's OTHER Combat
tests (`test_choice_semantics.py`, `test_action_fault_contract.py`,
`test_restore_snapshot_phase3c1.py`), all of which use a plain `test_*()` + `main()`/
`_run_all()` shape rather than pytest, because the Emulator runtime is process-wide
singleton state (`emulator_bridge.shared_game_instance()` - "only one GameInstance can be
live per OS process"). Run: `cd C:\\STS2_RL\\Combat\\tests && python test_decision_context.py`
"""

from __future__ import annotations

import dataclasses
import sys
import traceback
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import canonical_json, restore_input_eligibility  # noqa: E402
from live_combat_session import LiveCombatSession, SnapshotRestoreRejectedError  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    CHOICE_SCOPE_ACTION_CONTINUATION,
    CHOICE_SCOPE_TOP_LEVEL,
    DecisionContext,
    DecisionSignature,
    PendingSnapshotRestoreViolationError,
    ReplayMismatch,
    ReplayPrefixEntry,
    ReplaySuccess,
    SemanticAction,
    boundary_of_battle_state,
    candidate_multiset,
    replay_decision_context,
)
from verify_restore_bootstrap_phase3b import _make_eligible  # noqa: E402


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _toolbox_pending_spec():
    """TOOLBOX relic presents an immediate StartOfCombat pendingChoice
    (choiceType="ToolboxChooseCard", scope="StartOfCombat") - confirmed not to be an
    ActionContinuation (`is_action_continuation_pending_choice()` returns False for it),
    so it is NOT auto-resolved by `LiveCombatSession.step()`'s continuation loop and
    survives as an externally observable Pending `BattleState`, straight out of
    `start_combat()` - no Step required to reach it. See
    `Combat/tests/test_restore_snapshot_phase3c1.py::test_rejection_categories_via_public_python_api`
    for the same scenario used for a different purpose (restore-rejection testing)."""
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["TOOLBOX"], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _find_action(state: BattleState, action_type: str, card_id=None) -> dict:
    return next(
        a for a in state._cached_legal_actions  # noqa: SLF001
        if a["action_type"] == action_type and (card_id is None or (a.get("parameters") or {}).get("cardId") == card_id)
    )


def _semantic_action_for(action: dict) -> SemanticAction:
    params = action.get("parameters") or {}
    return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))


def _eligible_root_snapshot(session: LiveCombatSession):
    """Return the capture through the shared Phase 3B normalizer used by older tests.

    Fresh Emulator captures are now naturally restore-eligible; the historical
    CardDrawnEntry source_live_state_inconsistency workaround is covered by explicit
    synthetic-invalid tests instead of relying on natural capture corruption.
    """
    return _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001


def _with_synthetic_dangling_draw(snapshot):
    from System import Array  # noqa: PLC0415
    from System import Int32, Object, String  # noqa: PLC0415
    from System.Collections.Generic import Dictionary  # noqa: PLC0415
    from Sts2Emulator.Dto.Snapshot import CombatHistoryEntrySnapshot  # noqa: PLC0415

    dangling_draw = CombatHistoryEntrySnapshot()
    dangling_draw.EntryType = "CardDrawnEntry"
    dangling_draw.RoundNumber = snapshot.RoundNumber
    dangling_draw.CurrentSide = snapshot.CurrentSide
    dangling_draw.PlayerTurnNumbers = Dictionary[String, Int32]()
    fields = Dictionary[String, Object]()
    fields["cardInstanceId"] = "SYNTHETIC_DANGLING_DRAWN_CARD"
    fields["fromHandDraw"] = True
    dangling_draw.Fields = fields
    snapshot.CombatHistory.Entries = Array[CombatHistoryEntrySnapshot]([*snapshot.CombatHistory.Entries, dangling_draw])
    return snapshot


# ---------------------------------------------------------------------------
# DecisionSignature construction from real Stable/Pending boundaries
# ---------------------------------------------------------------------------


def test_signature_from_stable_boundary_has_no_pending_fields():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    semantic_action = _semantic_action_for(strike)

    next_state = session.step(state, strike, target_enemy_index=0)
    assert boundary_of_battle_state(next_state) == BOUNDARY_STABLE, next_state.engine_state.get("pendingChoice")

    sig = DecisionSignature.from_battle_state(next_state, semantic_action=semantic_action, resolved_action=strike, target_enemy_index=0)
    assert sig.boundary == BOUNDARY_STABLE
    assert sig.resolved_card_id == "STRIKE_IRONCLAD"
    assert sig.resolved_action_id == strike["action_id"]
    assert sig.combat_session_id == next_state.decision_frame.combat_session_id
    assert sig.step_index == next_state.decision_frame.step_index
    # Stable => the three Pending-only fields must stay None.
    assert sig.choice_scope is None
    assert sig.choice_kind is None
    assert sig.candidate_semantic_keys is None


def test_signature_from_pending_boundary_populates_pending_only_fields():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING, state.engine_state.get("pendingChoice")

    chosen = _find_action(state, "choice_card", "EQUILIBRIUM")
    semantic_action = _semantic_action_for(chosen)
    sig = DecisionSignature.from_battle_state(state, semantic_action=semantic_action, resolved_action=chosen)

    assert sig.boundary == BOUNDARY_PENDING
    assert sig.choice_scope == CHOICE_SCOPE_TOP_LEVEL  # StartOfCombat, not ActionContinuation
    assert sig.choice_kind == "ToolboxChooseCard"
    assert sig.candidate_semantic_keys is not None
    candidate_keys_dict = dict(sig.candidate_semantic_keys)
    assert candidate_keys_dict[("choice_card", "EQUILIBRIUM", None)] == 1
    assert sum(candidate_keys_dict.values()) == len(state._cached_legal_actions)  # noqa: SLF001


def test_restore_input_eligibility_rejects_pending_capture_but_accepts_stable_capture():
    stable_session = LiveCombatSession()
    stable_session.start_combat(_simple_spec())
    stable_snapshot = stable_session.capture_snapshot()
    stable_eligible, stable_reasons = restore_input_eligibility(stable_snapshot)

    assert stable_snapshot.Metadata.CaptureBoundary == "normal_player_decision"
    assert stable_eligible, stable_reasons

    pending_session = LiveCombatSession()
    pending_state = pending_session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(pending_state) == BOUNDARY_PENDING
    pending_snapshot = pending_session.capture_snapshot()
    pending_eligible, pending_reasons = restore_input_eligibility(pending_snapshot)

    assert pending_snapshot.Metadata.CaptureBoundary == "published_choice"
    assert pending_eligible is False
    assert "capture_boundary='published_choice' is not Restore-eligible" in pending_reasons


def test_boundary_construction_rejects_pending_fields_when_not_pending():
    strike_semantic = SemanticAction("card", "STRIKE_IRONCLAD", None)
    try:
        DecisionSignature(
            combat_session_id="s", step_index=0, continuation_step_index=None,
            semantic_action=strike_semantic, resolved_action_id=1,
            resolved_card_id="STRIKE_IRONCLAD", resolved_target_type=None,
            resolved_target_index=None, resolved_target_slot_index=0,
            boundary=BOUNDARY_STABLE, choice_scope=CHOICE_SCOPE_TOP_LEVEL,
        )
        raise AssertionError("expected ValueError for Pending-only field set on a Stable signature")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Candidate multiset - duplicates preserved, never collapsed to a set
# ---------------------------------------------------------------------------


def test_candidate_multiset_preserves_duplicates():
    legal_actions = [
        {"action_id": 0, "action_type": "card", "parameters": {"cardId": "STRIKE_IRONCLAD", "targetType": "SingleEnemy"}},
        {"action_id": 1, "action_type": "card", "parameters": {"cardId": "STRIKE_IRONCLAD", "targetType": "SingleEnemy"}},
        {"action_id": 2, "action_type": "card", "parameters": {"cardId": "DEFEND_IRONCLAD", "targetType": None}},
    ]
    multiset = candidate_multiset(legal_actions)
    as_dict = dict(multiset)
    assert as_dict[("card", "STRIKE_IRONCLAD", "SingleEnemy")] == 2, as_dict
    assert as_dict[("card", "DEFEND_IRONCLAD", None)] == 1, as_dict
    assert sum(as_dict.values()) == 3
    # A naive `set()` over the same keys would have collapsed the duplicate away - assert
    # the multiset actually carries MORE total count than the deduplicated key set would.
    assert sum(as_dict.values()) > len(set(k for k, _ in multiset))


# ---------------------------------------------------------------------------
# DecisionSignature equality/diff detects divergence in each required field
# ---------------------------------------------------------------------------


def _base_signature(**overrides):
    fields = dict(
        combat_session_id="session-a", step_index=3, continuation_step_index=None,
        semantic_action=SemanticAction("card", "STRIKE_IRONCLAD", "SingleEnemy"),
        resolved_action_id=7, resolved_card_id="STRIKE_IRONCLAD", resolved_target_type="SingleEnemy",
        resolved_target_index=None, resolved_target_slot_index=0, boundary=BOUNDARY_STABLE,
    )
    fields.update(overrides)
    return DecisionSignature(**fields)


def _base_pending_signature(**overrides):
    fields = dict(
        combat_session_id="session-a", step_index=3, continuation_step_index=None,
        semantic_action=SemanticAction("choice_card", "EQUILIBRIUM", None),
        resolved_action_id=0, resolved_card_id="EQUILIBRIUM", resolved_target_type=None,
        resolved_target_index=None, resolved_target_slot_index=None, boundary=BOUNDARY_PENDING,
        choice_scope=CHOICE_SCOPE_TOP_LEVEL, choice_kind="ToolboxChooseCard",
        candidate_semantic_keys=(( ("choice_card", "EQUILIBRIUM", None), 1), (("choice_skip", None, None), 1)),
    )
    fields.update(overrides)
    return DecisionSignature(**fields)


def test_diff_detects_divergence_in_each_stable_field():
    base = _base_signature()
    cases = {
        "combat_session_id": _base_signature(combat_session_id="session-b"),
        "step_index": _base_signature(step_index=4),
        "semantic_action": _base_signature(semantic_action=SemanticAction("card", "DEFEND_IRONCLAD", None)),
        "resolved_action_id": _base_signature(resolved_action_id=8),
        "resolved_card_id": _base_signature(resolved_card_id="DEFEND_IRONCLAD"),
        "resolved_target_type": _base_signature(resolved_target_type="AllEnemies"),
        "resolved_target_slot_index": _base_signature(resolved_target_slot_index=1),
        "boundary": _base_signature(boundary="terminal"),
    }
    for field_name, mutated in cases.items():
        assert base.diff(mutated) == [field_name], (field_name, base.diff(mutated))
        assert base != mutated


def test_diff_detects_divergence_in_each_pending_only_field():
    base = _base_pending_signature()
    cases = {
        "choice_scope": _base_pending_signature(choice_scope=CHOICE_SCOPE_ACTION_CONTINUATION),
        "choice_kind": _base_pending_signature(choice_kind="OtherChoice"),
        "candidate_semantic_keys": _base_pending_signature(
            candidate_semantic_keys=(( ("choice_card", "EQUILIBRIUM", None), 2), (("choice_skip", None, None), 1))
        ),
    }
    for field_name, mutated in cases.items():
        assert base.diff(mutated) == [field_name], (field_name, base.diff(mutated))


def test_matches_for_replay_ignores_non_portable_identity_fields():
    base = _base_signature()
    same_content_different_session = _base_signature(combat_session_id="a-totally-different-session", resolved_action_id=999)
    assert base != same_content_different_session  # strict eq still sees them as different
    assert base.matches_for_replay(same_content_different_session)  # replay-relevant eq does not
    assert base.diff_for_replay(same_content_different_session) == []

    genuinely_different = _base_signature(resolved_card_id="DEFEND_IRONCLAD")
    assert not base.matches_for_replay(genuinely_different)
    assert base.diff_for_replay(genuinely_different) == ["resolved_card_id"]


# ---------------------------------------------------------------------------
# Structural constraint: no board-state field can exist on DecisionSignature
# ---------------------------------------------------------------------------

_FORBIDDEN_SUBSTRINGS = ("hp", "block", "energy", "pile", "power", "relic", "enemy")


def test_signature_has_no_board_state_fields():
    field_names = [f.name.lower() for f in dataclasses.fields(DecisionSignature)]
    for name in field_names:
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in name, f"forbidden board-state substring {forbidden!r} found in field {name!r}"
    # And no generic escape hatch that could smuggle board state back in later.
    assert "extra" not in field_names
    assert "metadata" not in field_names


# ---------------------------------------------------------------------------
# DecisionContext: MAIN_CAP vs PREV_CHILD constructors
# ---------------------------------------------------------------------------


def test_decision_context_main_cap_starts_both_lists_empty():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    root_snapshot = _eligible_root_snapshot(session)
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    sig = DecisionSignature.from_battle_state(
        state, semantic_action=_semantic_action_for(strike), resolved_action=strike, target_enemy_index=0
    )

    ctx = DecisionContext.from_main_stable_capture(root_snapshot, state, sig)
    assert ctx.replay_prefix == []
    assert ctx.plan_path == []
    assert ctx.search_hypothesis_id is None


def test_decision_context_prev_child_inherits_plan_path_but_resets_replay_prefix():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    root_snapshot = _eligible_root_snapshot(session)
    strike = _find_action(state, "card", "STRIKE_IRONCLAD")
    semantic_action = _semantic_action_for(strike)
    sig = DecisionSignature.from_battle_state(state, semantic_action=semantic_action, resolved_action=strike, target_enemy_index=0)
    entry = ReplayPrefixEntry(semantic_action=semantic_action, expected_signature=sig, target_enemy_index=0)

    parent_plan_path = [entry, entry]  # non-empty, as if this parent had already taken 2 steps
    ctx = DecisionContext.from_prev_child(root_snapshot, state, sig, parent_plan_path)

    assert ctx.replay_prefix == []  # always resets at a Stable capture, same as MAIN_CAP
    assert ctx.plan_path == parent_plan_path  # inherited verbatim, unlike MAIN_CAP
    assert ctx.plan_path is not parent_plan_path  # copied, not aliased
    assert ctx.search_hypothesis_id is None


# ---------------------------------------------------------------------------
# replay_decision_context: clean replay, tampered-entry mismatch, real fault propagation
# ---------------------------------------------------------------------------


def test_replay_decision_context_clean_replay_reaches_recorded_signature():
    record_session = LiveCombatSession()
    state0 = record_session.start_combat(_simple_spec())
    root_snapshot = _eligible_root_snapshot(record_session)

    strike = _find_action(state0, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)
    state1 = record_session.step(state0, strike, target_enemy_index=0)
    sig1 = DecisionSignature.from_battle_state(state1, semantic_action=strike_semantic, resolved_action=strike, target_enemy_index=0)
    entry1 = ReplayPrefixEntry(semantic_action=strike_semantic, expected_signature=sig1, target_enemy_index=0)

    defend = _find_action(state1, "card", "DEFEND_IRONCLAD")
    defend_semantic = _semantic_action_for(defend)
    state2 = record_session.step(state1, defend)
    sig2 = DecisionSignature.from_battle_state(state2, semantic_action=defend_semantic, resolved_action=defend)
    entry2 = ReplayPrefixEntry(semantic_action=defend_semantic, expected_signature=sig2)

    context = DecisionContext.from_main_stable_capture(root_snapshot, state0, sig1)
    context = dataclasses.replace(context, replay_prefix=[entry1, entry2], current_context_signature=sig2)

    replay_session = LiveCombatSession()
    outcome = replay_decision_context(replay_session, context)
    assert isinstance(outcome, ReplaySuccess), outcome
    # NOT a raw `engine_state ==` comparison: `Intent` is a documented, confirmed gap
    # that Restore never reproduces (see test_restore_snapshot_phase3c1.py's own
    # `_strip_known_restore_boundary_diffs()` docstring) - a benign, expected difference
    # unrelated to whether the REPLAY itself succeeded. `ReplaySuccess` already means
    # REPLAY_SIG_CHECK/CTX_SIG_CHECK both passed (the DC_SIGNATURE-scoped fields, which
    # deliberately exclude board state entirely) - assert against those instead.
    assert boundary_of_battle_state(outcome.final_state) == BOUNDARY_STABLE
    final_sig = DecisionSignature.from_battle_state(
        outcome.final_state, semantic_action=defend_semantic, resolved_action=defend
    )
    assert final_sig.matches_for_replay(sig2)


def test_replay_decision_context_detects_tampered_action_as_mismatch():
    record_session = LiveCombatSession()
    state0 = record_session.start_combat(_simple_spec())
    root_snapshot = _eligible_root_snapshot(record_session)

    strike = _find_action(state0, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)
    state1 = record_session.step(state0, strike, target_enemy_index=0)
    sig1 = DecisionSignature.from_battle_state(state1, semantic_action=strike_semantic, resolved_action=strike, target_enemy_index=0)

    tampered_action = SemanticAction("card", "A_CARD_THAT_DOES_NOT_EXIST", None)
    tampered_entry = ReplayPrefixEntry(semantic_action=tampered_action, expected_signature=sig1, target_enemy_index=0)
    context = DecisionContext.from_main_stable_capture(root_snapshot, state0, sig1)
    context = dataclasses.replace(context, replay_prefix=[tampered_entry], current_context_signature=sig1)

    outcome = replay_decision_context(LiveCombatSession(), context)
    assert isinstance(outcome, ReplayMismatch), outcome
    assert outcome.stage == "resolve"
    assert outcome.step_index == 0


def test_replay_decision_context_detects_tampered_expected_signature_as_mismatch():
    record_session = LiveCombatSession()
    state0 = record_session.start_combat(_simple_spec())
    root_snapshot = _eligible_root_snapshot(record_session)

    strike = _find_action(state0, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)
    state1 = record_session.step(state0, strike, target_enemy_index=0)
    sig1 = DecisionSignature.from_battle_state(state1, semantic_action=strike_semantic, resolved_action=strike, target_enemy_index=0)

    tampered_sig1 = dataclasses.replace(sig1, resolved_card_id="DEFEND_IRONCLAD")
    tampered_entry = ReplayPrefixEntry(semantic_action=strike_semantic, expected_signature=tampered_sig1, target_enemy_index=0)
    context = DecisionContext.from_main_stable_capture(root_snapshot, state0, sig1)
    context = dataclasses.replace(context, replay_prefix=[tampered_entry], current_context_signature=tampered_sig1)

    outcome = replay_decision_context(LiveCombatSession(), context)
    assert isinstance(outcome, ReplayMismatch), outcome
    assert outcome.stage == "signature"
    assert outcome.step_index == 0
    assert "resolved_card_id" in outcome.diverged_fields


def test_replay_decision_context_surfaces_real_restore_rejection_uncaught():
    """SnapshotRestoreRejectedError is a genuine `live_combat_session.py` Emulator-level
    exception, not a controlled replay-mismatch outcome - `replay_decision_context()`
    must let it propagate, never swallow or convert it to a `ReplayMismatch`."""
    session = LiveCombatSession()
    state0 = session.start_combat(_simple_spec())
    # The old natural fresh-capture CardDrawnEntry corruption is fixed in the Emulator.
    # Inject the same broken reference shape explicitly so this test still verifies that
    # Emulator-level restore rejection propagates uncaught.
    ineligible_snapshot = _with_synthetic_dangling_draw(_make_eligible(session._game.CaptureSnapshot()))  # noqa: SLF001
    validation = LiveCombatSession().validate_restore_snapshot(ineligible_snapshot)
    assert validation.eligible is False, validation
    assert any("reference_integrity" in code for code in validation.rejection_codes), validation.rejection_codes

    strike = _find_action(state0, "card", "STRIKE_IRONCLAD")
    strike_semantic = _semantic_action_for(strike)
    placeholder_sig = DecisionSignature.from_battle_state(
        state0, semantic_action=strike_semantic, resolved_action=strike, target_enemy_index=0
    )
    context = DecisionContext.from_main_stable_capture(ineligible_snapshot, state0, placeholder_sig)

    try:
        replay_decision_context(LiveCombatSession(), context)
        raise AssertionError("expected SnapshotRestoreRejectedError to propagate uncaught")
    except SnapshotRestoreRejectedError:
        pass


def test_replay_decision_context_rejects_pending_root_as_design_violation():
    session = LiveCombatSession()
    state = session.start_combat(_toolbox_pending_spec())
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING
    pending_snapshot = session.capture_snapshot()

    chosen = _find_action(state, "choice_card", "EQUILIBRIUM")
    signature = DecisionSignature.from_battle_state(
        state,
        semantic_action=_semantic_action_for(chosen),
        resolved_action=chosen,
    )

    for root_snapshot in (
        pending_snapshot,
        canonical_json(dataclasses.asdict(pending_snapshot), exclude_volatile=False),
    ):
        context = DecisionContext.from_main_stable_capture(root_snapshot, state, signature)
        try:
            replay_decision_context(LiveCombatSession(), context)
            raise AssertionError("expected PendingSnapshotRestoreViolationError")
        except PendingSnapshotRestoreViolationError as exc:
            assert "NOTE_NO_REGEN" in str(exc)
            assert "published_choice" in str(exc)
            assert "prior Stable root snapshot" in str(exc)


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
    sys.exit(_run_all())
