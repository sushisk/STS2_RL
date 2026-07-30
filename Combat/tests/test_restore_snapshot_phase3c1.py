"""Phase 3C.1 Python Restore API integration tests.

Native assertion runner, no pytest dependency. The default runner spawns one fresh
Python subprocess per test because the Emulator runtime uses process-wide singleton
state; pass `--case <test_name>` to run one test in the current process.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
_ONLINE_EVAL_DIR = _COMBAT_DIR / "evaluation" / "online_eval"
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _ONLINE_EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import CombatStateSnapshot, canonical_json, schema_sha256  # noqa: E402
from emulator_bridge import ensure_loaded  # noqa: E402
from live_combat_session import (  # noqa: E402
    ActionExecutionError,
    FaultedCombatSessionError,
    LiveCombatSession,
    SnapshotRestoreFailedError,
    SnapshotRestoreRejectedError,
)
from verify_restore_bootstrap_phase3b import _fresh_source_game, _make_eligible, _snapshot_sig  # noqa: E402


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


def _clr_snapshot_json(snapshot) -> str:
    ensure_loaded()
    from System.Text.Json import JsonSerializer

    return str(JsonSerializer.Serialize(snapshot))


def _snapshot_dict(snapshot) -> dict:
    payload = asdict(snapshot)

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "unknown_fields"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(payload)


def _eligible_snapshot(seed=123):
    ensure_loaded()
    source = _fresh_source_game(seed=seed)
    snapshot = _make_eligible(source.CaptureSnapshot())
    return snapshot, _snapshot_sig(snapshot)


def _capture_sig(session: LiveCombatSession):
    return _snapshot_sig(_make_eligible(session._game.CaptureSnapshot()))  # noqa: SLF001


def _strike_action(state: BattleState):
    return next(
        a for a in state._cached_legal_actions  # noqa: SLF001
        if a["action_type"] == "card" and a["parameters"].get("cardId") == "STRIKE_IRONCLAD"
    )


def _first_logical_action(state: BattleState):
    action = next(a for a in state._cached_legal_actions if a["action_type"] == "card")  # noqa: SLF001
    params = action.get("parameters") or {}
    return ("card", params.get("cardId"), params.get("targetType"))


def _find_logical_action(state: BattleState, logical):
    action_type, card_id, target_type = logical
    return next(
        a for a in state._cached_legal_actions  # noqa: SLF001
        if a["action_type"] == action_type
        and (a.get("parameters") or {}).get("cardId") == card_id
        and (a.get("parameters") or {}).get("targetType") == target_type
    )


def _assert_rejected(snapshot, expected_substring: str):
    session = LiveCombatSession()
    validation = session.validate_restore_snapshot(snapshot)
    assert validation.eligible is False, validation
    assert any(expected_substring in r for r in validation.rejection_codes), validation.rejection_codes
    try:
        session.restore_snapshot(snapshot)
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any(expected_substring in r for r in exc.rejection_codes), exc.context
        assert exc.__cause__ is not None
        assert "SnapshotRestoreRejectedException" in exc.context.clr_exception_type
    assert session._session_faulted is False  # noqa: SLF001


def _scenario_6546_21_snapshot():
    manifest_path = _ONLINE_EVAL_DIR / "choice_policy_online_eval_manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if r["trajectory_id"] == "6546-21")
    session = LiveCombatSession()
    session.start_combat(row["spec"])
    return session._game.CaptureSnapshot()  # noqa: SLF001


def _set_restore_failure_injection(game, callback):
    ensure_loaded()
    from System import Action, String
    from System.Reflection import BindingFlags

    prop = game.GetType().GetProperty(
        "RestoreFailureInjectionForTesting",
        BindingFlags.Static | BindingFlags.NonPublic,
    )
    assert prop is not None, "RestoreFailureInjectionForTesting not found"
    prop.SetValue(None, Action[String](callback) if callback is not None else None)


def _force_restore_failure(session: LiveCombatSession, snapshot):
    from System import InvalidOperationException

    def fail(phase):
        assert str(phase) == "after_teardown"
        raise InvalidOperationException("phase3c1 python restore failure injection")

    session._game = session._game or __import__("emulator_bridge").shared_game_instance()  # noqa: SLF001
    _set_restore_failure_injection(session._game, fail)  # noqa: SLF001
    try:
        session.restore_snapshot(snapshot)
        raise AssertionError("expected SnapshotRestoreFailedError")
    except SnapshotRestoreFailedError as exc:
        assert exc.context.restore_phase == "restore_snapshot", exc.context
        assert exc.context.combat_session_id is not None, exc.context
        assert exc.context.schema_version is not None, exc.context
        assert exc.context.contract_version == "0.5", exc.context
        assert exc.context.snapshot_id is not None, exc.context
        assert exc.context.original_exception_type is not None, exc.context
        assert exc.__cause__ is not None
    finally:
        _set_restore_failure_injection(session._game, None)  # noqa: SLF001
    assert session._session_faulted is True  # noqa: SLF001


def test_get_restore_capabilities_hashes():
    session = LiveCombatSession()
    caps = session.get_restore_capabilities()
    assert caps.restore_api_version == "phase3c.1", caps
    assert caps.milestone == "phase3c.1", caps
    assert caps.contract_version == "0.5", caps
    assert caps.snapshot_schema_version == "phase2b.2", caps
    assert caps.snapshot_schema_sha256 == schema_sha256(), caps

    import hashlib

    contract_path = Path(__file__).resolve().parents[2] / "Common" / "contracts" / "combat_state_contract.v0.5.md"
    assert caps.contract_sha256 == hashlib.sha256(contract_path.read_bytes()).hexdigest(), caps
    assert caps.supported_completeness == ["complete"], caps
    assert caps.supports_combat_history is False
    assert caps.supports_pets is False
    assert caps.supports_pending_choice is False
    assert caps.supports_pending_target is False
    assert caps.supports_action_continuation is False
    assert caps.transaction_model == "validate_before_destroy"
    assert caps.rollback_after_teardown is False
    assert caps.issues_new_combat_session is True
    assert caps.preserves_stable_ids is True
    assert "combat_history_non_empty" in caps.rejection_codes


def test_object_restore_round_trip():
    snapshot, sig_a = _eligible_snapshot()
    session = LiveCombatSession()
    state = session.restore_snapshot(snapshot)
    assert isinstance(state, BattleState)
    assert state.decision_frame.combat_session_id != str(snapshot.Metadata.CombatSessionId)
    assert _capture_sig(session) == sig_a
    assert session._session_faulted is False  # noqa: SLF001


def test_json_restore_round_trip():
    snapshot, sig_a = _eligible_snapshot()
    session = LiveCombatSession()
    state = session.restore_snapshot_json(_clr_snapshot_json(snapshot))
    assert isinstance(state, BattleState)
    assert _capture_sig(session) == sig_a


def test_python_snapshot_object_restore_round_trip_uses_existing_dto():
    snapshot, sig_a = _eligible_snapshot()
    py_snapshot = CombatStateSnapshot.from_json(_clr_snapshot_json(snapshot))
    session = LiveCombatSession()
    state = session.restore_snapshot(py_snapshot)
    assert isinstance(state, BattleState)
    assert _capture_sig(session) == sig_a


def test_object_vs_json_restore_equivalent():
    snapshot, _ = _eligible_snapshot()
    json_text = _clr_snapshot_json(snapshot)
    session = LiveCombatSession()
    session.restore_snapshot(snapshot)
    sig_object = _capture_sig(session)
    session.restore_snapshot_json(json_text)
    sig_json = _capture_sig(session)
    assert sig_object == sig_json


def _strip_known_restore_boundary_diffs(node, top=True):
    """Two DIFFERENT categories of legitimate, expected difference across a Restore
    boundary - neither is a bug in this Python integration:

    1. `EnemySnapshot.Intent`: `SnapshotRestorer` never touches it (confirmed by reading
       `SnapshotRestorer.cs` - zero occurrences of `Intent`/`RollMove` in that file).
       Intent is derived state only `RollMove()` computes, and `RollMove()` is one of the
       explicitly-forbidden fresh-start hooks Restore must never call (Phase 3B design).
       A restored-then-recaptured Snapshot legitimately has `Intent={"intentTypes":[],
       "stateId":"UNSET_MOVE"}` regardless of the original Intent - a genuine, confirmed,
       previously-undocumented scope gap (this test's original full-equality assertion
       is what surfaced it), not something either side's prior round-trip checks (which
       used a narrower field-by-field signature, not full canonical JSON) had compared.
    2. `Metadata.CombatSessionId`/`StepIndex`: Restore success is DOCUMENTED and REQUIRED
       to issue a brand new combat session identity (see this contract's own §6 "Restore
       success" requirements) - comparing these across the boundary would be asserting
       the WRONG thing.

    Excluded here so this test asserts the TRUE guarantee (everything else round-trips
    exactly) instead of a false completeness claim - see combat_state_contract.v0.6.md's
    known-limitations section for the Intent gap specifically."""
    if isinstance(node, dict):
        out = {k: _strip_known_restore_boundary_diffs(v, top=False) for k, v in node.items()}
        if "MonsterId" in out and "Intent" in out:
            out["Intent"] = "<excluded: not preserved across Restore, see test docstring>"
        if top and "Metadata" in out and isinstance(out["Metadata"], dict):
            out["Metadata"] = {
                k: ("<excluded: Restore issues a new session identity by design>" if k in ("CombatSessionId", "StepIndex") else v)
                for k, v in out["Metadata"].items()
            }
        return out
    if isinstance(node, list):
        return [_strip_known_restore_boundary_diffs(v, top=False) for v in node]
    return node


def test_canonical_json_round_trip():
    snapshot, _ = _eligible_snapshot()
    py_snapshot = CombatStateSnapshot.from_json(_clr_snapshot_json(snapshot))
    json_text = canonical_json(_snapshot_dict(py_snapshot), exclude_volatile=False)
    session = LiveCombatSession()
    session.restore_snapshot_json(json_text)
    captured = CombatStateSnapshot.from_json(_clr_snapshot_json(_make_eligible(session._game.CaptureSnapshot())))  # noqa: SLF001
    original = _strip_known_restore_boundary_diffs(_snapshot_dict(py_snapshot))
    restored = _strip_known_restore_boundary_diffs(_snapshot_dict(captured))
    assert canonical_json(original) == canonical_json(restored)


def test_validate_restore_snapshot_is_pure():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()
    snapshot = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    invalid = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    invalid.Metadata.ContinuationStepIndex = 1

    results = [session.validate_restore_snapshot(snapshot) for _ in range(3)]
    assert all(r.eligible for r in results), results
    assert results[0] == results[1] == results[2]

    invalid_results = [session.validate_restore_snapshot(invalid) for _ in range(3)]
    assert all(not r.eligible for r in invalid_results), invalid_results
    assert invalid_results[0] == invalid_results[1] == invalid_results[2]
    assert any("action_continuation_present" in r for r in invalid_results[0].rejection_codes)

    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    next_state = session.step(state, _strike_action(state), target_enemy_index=0)
    assert next_state is not None


def test_no_power_capture_round_trip():
    snapshot, sig_a = _eligible_snapshot()
    session = LiveCombatSession()
    session.restore_snapshot(snapshot)
    assert _capture_sig(session) == sig_a


def test_with_power_capture_round_trip():
    ensure_loaded()
    from MegaCrit.Sts2.Core.Models import ModelDb
    from System import Decimal
    from System.Reflection import BindingFlags

    source = _fresh_source_game()
    combat_state_field = source.GetType().GetField("_combatState", BindingFlags.Instance | BindingFlags.NonPublic)
    combat_state = combat_state_field.GetValue(source)
    creatures = list(combat_state.GetType().GetProperty("Creatures").GetValue(combat_state))
    player_creature = next(c for c in creatures if str(c.GetType().GetProperty("Side").GetValue(c)) == "Player")
    enemy_creature = next(c for c in creatures if str(c.GetType().GetProperty("Side").GetValue(c)) == "Enemy")
    strength = next(p for p in ModelDb.AllPowers if str(p.Id.Entry).upper() in ("STRENGTH", "STRENGTH_POWER"))
    weak = next(p for p in ModelDb.AllPowers if str(p.Id.Entry).upper() in ("WEAK", "WEAK_POWER"))
    strength.ToMutable().ApplyInternal(player_creature, Decimal(3))
    weak.ToMutable().ApplyInternal(enemy_creature, Decimal(2))
    snapshot = _make_eligible(source.CaptureSnapshot())
    sig_a = _snapshot_sig(snapshot)

    session = LiveCombatSession()
    session.restore_snapshot(snapshot)
    assert _capture_sig(session) == sig_a


def test_full_rng_stream_equality_across_round_trip():
    snapshot, sig_a = _eligible_snapshot()
    session = LiveCombatSession()
    session.restore_snapshot(snapshot)
    sig_b = _capture_sig(session)
    assert sig_a["run_rng_count"] == 12
    assert sig_a["run_rng"] == sig_b["run_rng"]
    assert sig_a["player_rng"] == sig_b["player_rng"]
    assert sig_a["monster_rng"] == sig_b["monster_rng"]


def test_restore_step_determinism_reselects_fresh_action():
    snapshot, _ = _eligible_snapshot()
    session = LiveCombatSession()
    state1 = session.restore_snapshot(snapshot)
    logical = _first_logical_action(state1)
    next1 = session.step(state1, _find_logical_action(state1, logical), target_enemy_index=0)
    sig1 = _snapshot_sig(session._game.CaptureSnapshot())  # noqa: SLF001

    state2 = session.restore_snapshot(snapshot)
    next2 = session.step(state2, _find_logical_action(state2, logical), target_enemy_index=0)
    sig2 = _snapshot_sig(session._game.CaptureSnapshot())  # noqa: SLF001

    assert next1.engine_state == next2.engine_state
    assert sig1 == sig2


def test_rejection_categories_via_public_python_api():
    ensure_loaded()
    from System import Array
    from Sts2Emulator.Dto.Snapshot import UnsupportedSnapshotField

    source = _fresh_source_game()
    _assert_rejected(source.CaptureSnapshot(), "combat_history_non_empty")

    pet_snapshot = _scenario_6546_21_snapshot()
    _assert_rejected(pet_snapshot, "pet_count")

    toolbox = LiveCombatSession()
    toolbox.start_combat({
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["TOOLBOX"], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    })
    _assert_rejected(toolbox._game.CaptureSnapshot(), "pending_choice_present")  # noqa: SLF001

    base = _make_eligible(_fresh_source_game().CaptureSnapshot())
    base.Metadata.CaptureBoundary = "published_target"
    _assert_rejected(base, "unsupported_capture_boundary")

    base = _make_eligible(_fresh_source_game().CaptureSnapshot())
    base.Metadata.ContinuationStepIndex = 1
    _assert_rejected(base, "action_continuation_present")

    base = _make_eligible(_fresh_source_game().CaptureSnapshot())
    base.Player.Hand[0].InstanceId = base.Player.InstanceId
    _assert_rejected(base, "reference_integrity")

    base = _make_eligible(_fresh_source_game().CaptureSnapshot())
    base.Rng.PlayerRng[0].OwnerInstanceId = "player-999999"
    _assert_rejected(base, "reference_integrity")

    base = _make_eligible(_fresh_source_game().CaptureSnapshot())
    bad = UnsupportedSnapshotField()
    bad.FieldPath = "Player.Powers[0].InternalData"
    bad.Status = "unsupported"
    bad.Reason = "synthetic test injection"
    base.Metadata.UnsupportedFields = Array[UnsupportedSnapshotField]([bad])
    _assert_rejected(base, "unsupported_internal_data")

    base = _make_eligible(_fresh_source_game().CaptureSnapshot())
    base.Metadata.SchemaVersion = "phase99.9"
    _assert_rejected(base, "unknown_schema_version")


def test_real_6546_21_rejected_via_public_api():
    snapshot = _scenario_6546_21_snapshot()
    session = LiveCombatSession()
    result = session.validate_restore_snapshot(snapshot)
    assert result.eligible is False, result
    assert any("reference_integrity" in r for r in result.rejection_codes), result.rejection_codes
    assert any("card-000065" in r or "card-000066" in r or "card-000067" in r for r in result.rejection_codes), result.rejection_codes
    try:
        session.restore_snapshot(snapshot)
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any("reference_integrity" in r for r in exc.rejection_codes), exc.rejection_codes


def test_rejected_restore_preserves_session_and_step_still_works():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()
    invalid = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
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


def test_post_teardown_failure_faults_and_all_recovery_paths_clear():
    snapshot, _ = _eligible_snapshot()
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


def _run_all() -> int:
    tests = sorted(name for name, obj in globals().items() if name.startswith("test_") and callable(obj))
    passed, failed = [], []
    for name in tests:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--case", name]
        proc = subprocess.run(cmd, text=True, capture_output=True)
        if proc.returncode == 0:
            passed.append(name)
            print(f"PASS {name}")
        else:
            failed.append(name)
            print(f"FAIL {name}")
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, end="")
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(name for name in globals() if name.startswith("test_")))
    args = parser.parse_args()
    if args.case:
        try:
            globals()[args.case]()
            return 0
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 1
    return _run_all()


if __name__ == "__main__":
    sys.exit(main())
