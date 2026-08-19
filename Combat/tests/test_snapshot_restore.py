"""Restore behavior tests for typed CombatStateSnapshot DTOs.

These tests intentionally stay above the wire-schema layer. A live capture is parsed by
``LiveCombatSession.capture_snapshot()`` into the production DTO family, and any JSON
restore crosses the test-only boundary through ``snapshot_testkit.to_emulator_json``.
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

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import CombatStateSnapshot  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from snapshot_testkit import (  # noqa: E402
    active_enemies,
    creature_by_id,
    player_creature,
    to_emulator_json,
    to_emulator_payload,
)


def _simple_spec(
    *,
    relics: list[str] | None = None,
    player_powers: list[dict] | None = None,
    enemy_powers: list[dict] | None = None,
) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": list(player_powers or []),
        "relics": list(relics or []),
        "potions": [],
        "seed": 1,
        "enemies": [
            {
                "monster_id": "CALCIFIED_CULTIST",
                "hp": 48,
                "powers": list(enemy_powers or []),
            }
        ],
    }


def _restorable_capture(session: LiveCombatSession) -> CombatStateSnapshot:
    snapshot = session.capture_snapshot()
    snapshot.Metadata.Completeness = "complete"
    snapshot.Metadata.UnsupportedFields = []
    snapshot.CombatHistory.Entries = []
    return snapshot


def _normalize_restore_boundary(node):
    """Remove the one documented gameplay-state gap across Snapshot Restore.

    Enemy Intent is derived state and SnapshotRestorer deliberately does not call the
    fresh-start move-roll hook. A restored enemy therefore recaptures with UNSET_MOVE.
    Session identity metadata is excluded separately by ``_gameplay_payload``.
    """
    if isinstance(node, dict):
        normalized = {key: _normalize_restore_boundary(value) for key, value in node.items()}
        if "MonsterId" in normalized and "Intent" in normalized:
            normalized["Intent"] = "<excluded: derived enemy intent>"
        return normalized
    if isinstance(node, list):
        return [_normalize_restore_boundary(value) for value in node]
    return node


def _gameplay_payload(snapshot: CombatStateSnapshot) -> dict:
    """Compare restore-relevant state while ignoring runtime-only boundary differences."""
    payload = to_emulator_payload(snapshot)
    gameplay = {
        key: payload[key]
        for key in (
            "Player",
            "Enemies",
            "TurnNumber",
            "RoundNumber",
            "CurrentSide",
            "Phase",
            "Rng",
            "CombatHistory",
            "IsTerminal",
            "Outcome",
            "PendingChoice",
        )
    }
    return _normalize_restore_boundary(gameplay)


def _logical_strike(state: BattleState) -> tuple[str, str | None, str | None]:
    action = next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001 - test-only logical action selection
        if action["action_type"] == "card"
        and (action.get("parameters") or {}).get("cardId") == "STRIKE_IRONCLAD"
    )
    params = action.get("parameters") or {}
    return action["action_type"], params.get("cardId"), params.get("targetType")


def _find_logical_action(state: BattleState, logical: tuple[str, str | None, str | None]) -> dict:
    action_type, card_id, target_type = logical
    return next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001 - test-only logical action selection
        if action["action_type"] == action_type
        and (action.get("parameters") or {}).get("cardId") == card_id
        and (action.get("parameters") or {}).get("targetType") == target_type
    )


def test_object_restore_round_trip_uses_typed_snapshot():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = _restorable_capture(session)
    expected = _gameplay_payload(snapshot)

    restored_state = session.restore_snapshot(snapshot)
    restored = _restorable_capture(session)

    assert isinstance(restored_state, BattleState)
    assert restored_state.decision_frame.combat_session_id != snapshot.Metadata.CombatSessionId
    assert _gameplay_payload(restored) == expected
    assert session._session_faulted is False  # noqa: SLF001


def test_json_restore_round_trip_uses_shared_testkit_serializer():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = _restorable_capture(session)
    expected = _gameplay_payload(snapshot)

    restored_state = session.restore_snapshot_json(to_emulator_json(snapshot))
    restored = _restorable_capture(session)

    assert isinstance(restored_state, BattleState)
    assert _gameplay_payload(restored) == expected


def test_object_and_json_restore_are_equivalent():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = _restorable_capture(session)

    session.restore_snapshot(snapshot)
    object_restored = _gameplay_payload(_restorable_capture(session))

    session.restore_snapshot_json(to_emulator_json(snapshot))
    json_restored = _gameplay_payload(_restorable_capture(session))

    assert json_restored == object_restored


def test_live_power_capture_round_trip_uses_typed_snapshot():
    session = LiveCombatSession()
    session.start_combat(
        _simple_spec(
            player_powers=[{"id": "STRENGTH", "amount": 3}],
            enemy_powers=[{"id": "WEAK", "amount": 2}],
        )
    )
    snapshot = _restorable_capture(session)
    expected = _gameplay_payload(snapshot)

    assert snapshot.Player.Powers
    assert snapshot.Enemies[0].Powers
    assert snapshot.Player.Powers[0].Amount == 3
    assert snapshot.Enemies[0].Powers[0].Amount == 2

    session.restore_snapshot(snapshot)
    restored = _restorable_capture(session)

    assert _gameplay_payload(restored) == expected


def test_canonical_typed_payload_round_trip():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = _restorable_capture(session)
    expected = _gameplay_payload(snapshot)

    session.restore_snapshot_json(to_emulator_json(snapshot))
    captured = _restorable_capture(session)

    assert _gameplay_payload(captured) == expected


def test_restore_accessors_cover_player_enemies_and_pet():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(relics=["BOUND_PHYLACTERY"]))
    snapshot = _restorable_capture(session)
    expected = _gameplay_payload(snapshot)

    assert player_creature(snapshot) is snapshot.Player
    assert creature_by_id(snapshot, snapshot.Player.CreatureInstanceId) is snapshot.Player
    assert active_enemies(snapshot)
    for enemy in active_enemies(snapshot):
        assert creature_by_id(snapshot, enemy.InstanceId) is enemy

    assert len(snapshot.Player.Pets) == 1
    pet = snapshot.Player.Pets[0]
    assert creature_by_id(snapshot, pet.InstanceId) is pet

    object_state = session.restore_snapshot(snapshot)
    object_restored = _restorable_capture(session)
    object_pet = creature_by_id(object_restored, pet.InstanceId)

    assert isinstance(object_state, BattleState)
    assert _gameplay_payload(object_restored) == expected
    assert object_pet.MonsterId == pet.MonsterId
    assert object_pet.OwnerInstanceId == object_restored.Player.InstanceId
    assert object_pet.Hp == pet.Hp
    assert object_pet.MaxHp == pet.MaxHp
    assert object_pet.Block == pet.Block
    assert object_pet.Powers == pet.Powers

    json_state = session.restore_snapshot_json(to_emulator_json(snapshot))
    json_restored = _restorable_capture(session)
    json_pet = creature_by_id(json_restored, pet.InstanceId)

    assert isinstance(json_state, BattleState)
    assert _gameplay_payload(json_restored) == expected
    assert _gameplay_payload(json_restored) == _gameplay_payload(object_restored)
    assert json_pet.MonsterId == pet.MonsterId
    assert json_pet.OwnerInstanceId == json_restored.Player.InstanceId
    assert json_pet.Hp == pet.Hp
    assert json_pet.MaxHp == pet.MaxHp
    assert json_pet.Block == pet.Block
    assert json_pet.Powers == pet.Powers


def test_full_rng_streams_survive_round_trip():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = _restorable_capture(session)
    expected_rng = copy.deepcopy(to_emulator_payload(snapshot)["Rng"])

    assert len(expected_rng["RunRng"]) == 12
    assert expected_rng["PlayerRng"]
    assert expected_rng["MonsterRng"]

    session.restore_snapshot(snapshot)
    restored_rng = to_emulator_payload(_restorable_capture(session))["Rng"]

    assert restored_rng == expected_rng


def test_validate_restore_snapshot_is_pure_for_typed_dto():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()
    snapshot = _restorable_capture(session)
    invalid = copy.deepcopy(snapshot)
    invalid.Metadata.ContinuationStepIndex = 1

    results = [session.validate_restore_snapshot(snapshot) for _ in range(3)]
    assert all(result.eligible for result in results), results
    assert results[0] == results[1] == results[2]

    invalid_results = [session.validate_restore_snapshot(invalid) for _ in range(3)]
    assert all(not result.eligible for result in invalid_results), invalid_results
    assert invalid_results[0] == invalid_results[1] == invalid_results[2]
    assert any(
        "action_continuation_present" in code
        for code in invalid_results[0].rejection_codes
    )

    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    logical = _logical_strike(state)
    assert session.step(state, _find_logical_action(state, logical), target_enemy_index=0) is not None


def test_restore_step_determinism_reselects_fresh_action():
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    snapshot = _restorable_capture(session)

    state1 = session.restore_snapshot(snapshot)
    logical = _logical_strike(state1)
    next1 = session.step(state1, _find_logical_action(state1, logical), target_enemy_index=0)
    after1 = _gameplay_payload(session.capture_snapshot())

    state2 = session.restore_snapshot(snapshot)
    next2 = session.step(state2, _find_logical_action(state2, logical), target_enemy_index=0)
    after2 = _gameplay_payload(session.capture_snapshot())

    assert next1.engine_state == next2.engine_state
    assert after1 == after2


def test_pet_restore_step_determinism_reselects_fresh_action():
    session = LiveCombatSession()
    session.start_combat(_simple_spec(relics=["BOUND_PHYLACTERY"]))
    snapshot = _restorable_capture(session)

    state1 = session.restore_snapshot(snapshot)
    logical = _logical_strike(state1)
    next1 = session.step(state1, _find_logical_action(state1, logical), target_enemy_index=0)
    after1 = _gameplay_payload(session.capture_snapshot())

    state2 = session.restore_snapshot(snapshot)
    next2 = session.step(state2, _find_logical_action(state2, logical), target_enemy_index=0)
    after2 = _gameplay_payload(session.capture_snapshot())

    assert next1.engine_state == next2.engine_state
    assert after1 == after2
