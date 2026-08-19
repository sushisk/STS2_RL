"""Restore rejection/failure tests expressed through typed snapshot DTOs.

Malformed-wire tests belong in ``test_snapshot_wire_contract.py``. This module mutates
production DTO objects; the only CLR reflection here is the test-only failure injection
hook, which is runtime behavior rather than wire-schema knowledge.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
for _path in (_HERE, _COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from combat_state_snapshot import (  # noqa: E402
    CombatHistoryEntrySnapshot,
    CombatStateSnapshot,
    UnsupportedSnapshotField,
)
from emulator_bridge import ensure_loaded, shared_game_instance  # noqa: E402
from live_combat_session import (  # noqa: E402
    FaultedCombatSessionError,
    LiveCombatSession,
    SnapshotRestoreFailedError,
    SnapshotRestoreRejectedError,
)
from snapshot_testkit import to_emulator_json  # noqa: E402


def _simple_spec(*, relics: list[str] | None = None) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": list(relics or []),
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _restorable_capture(session: LiveCombatSession) -> CombatStateSnapshot:
    snapshot = session.capture_snapshot()
    snapshot.Metadata.Completeness = "complete"
    snapshot.Metadata.UnsupportedFields = []
    snapshot.CombatHistory.Entries = []
    return snapshot


def _fresh_snapshot() -> CombatStateSnapshot:
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    return _restorable_capture(session)


def _strike_action(state) -> dict:
    return next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001 - test-only action lookup
        if action["action_type"] == "card"
        and (action.get("parameters") or {}).get("cardId") == "STRIKE_IRONCLAD"
    )


def _assert_rejected(snapshot: CombatStateSnapshot, expected_code: str) -> None:
    session = LiveCombatSession()
    validation = session.validate_restore_snapshot(snapshot)
    assert validation.eligible is False, validation
    assert any(expected_code in code for code in validation.rejection_codes), validation.rejection_codes

    try:
        session.restore_snapshot(snapshot)
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any(expected_code in code for code in exc.rejection_codes), exc.context
        assert exc.__cause__ is not None
        assert "SnapshotRestoreRejectedException" in exc.context.clr_exception_type

    assert session._session_faulted is False  # noqa: SLF001


def _set_restore_failure_injection(game, callback) -> None:
    ensure_loaded()
    from System import Action, String
    from System.Reflection import BindingFlags

    prop = game.GetType().GetProperty(
        "RestoreFailureInjectionForTesting",
        BindingFlags.Static | BindingFlags.NonPublic,
    )
    assert prop is not None, "RestoreFailureInjectionForTesting not found"
    prop.SetValue(None, Action[String](callback) if callback is not None else None)


def _force_restore_failure(session: LiveCombatSession, snapshot: CombatStateSnapshot) -> None:
    from System import InvalidOperationException

    def fail(phase):
        assert str(phase) == "after_teardown"
        raise InvalidOperationException("snapshot restore failure injection")

    session._game = session._game or shared_game_instance()  # noqa: SLF001
    _set_restore_failure_injection(session._game, fail)  # noqa: SLF001
    try:
        session.restore_snapshot(snapshot)
        raise AssertionError("expected SnapshotRestoreFailedError")
    except SnapshotRestoreFailedError as exc:
        assert exc.context.restore_phase == "restore_snapshot", exc.context
        assert exc.context.combat_session_id is not None, exc.context
        assert exc.context.schema_version is not None, exc.context
        assert exc.context.contract_version == "0.6", exc.context
        assert exc.context.snapshot_id is not None, exc.context
        assert exc.context.original_exception_type is not None, exc.context
        assert exc.__cause__ is not None
    finally:
        _set_restore_failure_injection(session._game, None)  # noqa: SLF001
    assert session._session_faulted is True  # noqa: SLF001


def test_unknown_schema_version_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.Metadata.SchemaVersion = "phase99.9"
    _assert_rejected(snapshot, "unknown_schema_version")


def test_unsupported_capture_boundary_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.Metadata.CaptureBoundary = "published_target"
    _assert_rejected(snapshot, "unsupported_capture_boundary")


def test_action_continuation_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.Metadata.ContinuationStepIndex = 1
    _assert_rejected(snapshot, "action_continuation_present")


def test_reference_integrity_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.Player.CardInstances[0].InstanceId = snapshot.Player.InstanceId
    _assert_rejected(snapshot, "reference_integrity")


def test_rng_owner_reference_integrity_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    assert snapshot.Rng.PlayerRng
    snapshot.Rng.PlayerRng[0].OwnerInstanceId = "player-999999"
    _assert_rejected(snapshot, "reference_integrity")


def test_unknown_combat_history_entry_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.CombatHistory.Entries = [
        CombatHistoryEntrySnapshot(
            EntryType="SyntheticUnknownEntry",
            RoundNumber=snapshot.RoundNumber,
            CurrentSide=snapshot.CurrentSide,
            PlayerTurnNumbers={},
            Fields={},
        )
    ]
    _assert_rejected(snapshot, "unknown_combat_history_entry_type:SyntheticUnknownEntry")


def test_dangling_combat_history_reference_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.CombatHistory.Entries = [
        CombatHistoryEntrySnapshot(
            EntryType="CardDrawnEntry",
            RoundNumber=snapshot.RoundNumber,
            CurrentSide=snapshot.CurrentSide,
            PlayerTurnNumbers={},
            Fields={
                "cardInstanceId": "SYNTHETIC_DANGLING_DRAWN_CARD",
                "fromHandDraw": True,
            },
        )
    ]
    validation = LiveCombatSession().validate_restore_snapshot(snapshot)
    assert validation.eligible is False, validation
    assert any("reference_integrity" in code for code in validation.rejection_codes)
    assert not any("pet_count" in code for code in validation.rejection_codes)


def test_unsupported_internal_data_rejected_from_typed_dto():
    snapshot = _fresh_snapshot()
    snapshot.Metadata.UnsupportedFields = [
        UnsupportedSnapshotField(
            FieldPath="Player.Powers[0].InternalData",
            Status="unsupported",
            Reason="synthetic test injection",
        )
    ]
    _assert_rejected(snapshot, "unsupported_internal_data")


def test_pending_choice_capture_is_rejected_without_raw_wire_fixture():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(relics=["TOOLBOX"]))
    snapshot = session.capture_snapshot()

    assert snapshot.PendingChoice is not None
    validation = session.validate_restore_snapshot(snapshot)
    assert validation.eligible is False, validation
    assert any("pending_choice_present" in code for code in validation.rejection_codes), validation.rejection_codes


def test_typed_and_json_rejection_report_same_schema_error():
    snapshot = _fresh_snapshot()
    snapshot.Metadata.SchemaVersion = "phase99.9"
    session = LiveCombatSession()

    typed = session.validate_restore_snapshot(snapshot)
    wire = session.validate_restore_snapshot_json(to_emulator_json(snapshot))

    assert typed.eligible is False
    assert wire.eligible is False
    assert any("unknown_schema_version" in code for code in typed.rejection_codes)
    assert any("unknown_schema_version" in code for code in wire.rejection_codes)


def test_rejected_restore_preserves_live_session_and_step_still_works():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()

    invalid = copy.deepcopy(_restorable_capture(session))
    invalid.Metadata.SchemaVersion = "phase99.9"

    try:
        session.restore_snapshot(invalid)
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError:
        pass

    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


def test_post_teardown_failure_faults_session_and_recovery_paths_clear_it():
    fixture_session = LiveCombatSession()
    fixture_session.start_combat(_simple_spec())
    snapshot = _restorable_capture(fixture_session)

    session = LiveCombatSession()
    good_state = session.restore_snapshot(snapshot)

    _force_restore_failure(session, snapshot)
    for call in (
        lambda: session.step(good_state, _strike_action(good_state), target_enemy_index=0),
        session.get_observation,
        session.get_legal_actions,
        session.capture_snapshot,
    ):
        try:
            call()
            raise AssertionError("expected FaultedCombatSessionError")
        except FaultedCombatSessionError:
            pass

    started = session.start_combat(_simple_spec())
    assert session._session_faulted is False  # noqa: SLF001

    _force_restore_failure(session, snapshot)
    resumed = session.resume_from(started)
    assert session._session_faulted is False  # noqa: SLF001
    assert resumed is not None

    _force_restore_failure(session, snapshot)
    restored = session.restore_snapshot(snapshot)
    assert session._session_faulted is False  # noqa: SLF001
    assert restored is not None
