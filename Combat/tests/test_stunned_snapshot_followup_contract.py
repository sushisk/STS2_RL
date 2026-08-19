"""RL-side contract coverage for STS2_Emulator PR #32 STUNNED snapshots.

The Emulator owns STUNNED reconstruction semantics.  RL's responsibility is to carry
EnemySnapshot.FollowUpStateId losslessly through the typed Snapshot DTO boundary rather
than deriving it from StateLog or monster identity.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
for _path in (_HERE, _COMBAT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from combat_state_snapshot import CombatStateSnapshot, EnemySnapshot  # noqa: E402
from snapshot_testkit import make_snapshot, to_emulator_payload  # noqa: E402


def _enemy_payload(*, follow_up_state_id: str | None, include_follow_up: bool = True) -> dict:
    payload = {
        "InstanceId": "creature-900010",
        "Index": 0,
        "Name": "Ceremonial Beast",
        "Hp": 252,
        "MaxHp": 252,
        "Block": 0,
        "IsAlive": True,
        "Powers": [],
        # Deliberately ends in PLOW_MOVE: explicit STUNNED follow-up is independent
        # from move history and must not be inferred from StateLog[-1].
        "StateLog": ["STAMP_MOVE", "PLOW_MOVE"],
        "CombatId": 1,
        "MonsterId": "CEREMONIAL_BEAST",
        "SlotName": None,
        "Intent": {"stateId": "STUNNED"},
    }
    if include_follow_up:
        payload["FollowUpStateId"] = follow_up_state_id
    return payload


def test_explicit_stunned_follow_up_survives_typed_snapshot_round_trip():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(follow_up_state_id="BEAST_CRY_MOVE")
    )

    assert enemy.Intent == {"stateId": "STUNNED"}
    assert enemy.StateLog == ["STAMP_MOVE", "PLOW_MOVE"]
    assert enemy.FollowUpStateId == "BEAST_CRY_MOVE"

    wire = to_emulator_payload(make_snapshot(enemies=[enemy]))
    wire_enemy = wire["Enemies"][0]
    assert wire_enemy["Intent"] == {"stateId": "STUNNED"}
    assert wire_enemy["StateLog"] == ["STAMP_MOVE", "PLOW_MOVE"]
    assert wire_enemy["FollowUpStateId"] == "BEAST_CRY_MOVE"

    reparsed = CombatStateSnapshot.from_dict(wire)
    reparsed_enemy = reparsed.Enemies[0]
    assert reparsed_enemy.StateLog[-1] == "PLOW_MOVE"
    assert reparsed_enemy.FollowUpStateId == "BEAST_CRY_MOVE"


def test_generic_stun_follow_up_is_not_rewritten_from_monster_identity():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(follow_up_state_id="PLOW_MOVE")
    )

    wire_enemy = to_emulator_payload(make_snapshot(enemies=[enemy]))["Enemies"][0]
    assert wire_enemy["MonsterId"] == "CEREMONIAL_BEAST"
    assert wire_enemy["FollowUpStateId"] == "PLOW_MOVE"


def test_legacy_snapshot_without_follow_up_remains_compatible():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(follow_up_state_id=None, include_follow_up=False)
    )

    assert enemy.FollowUpStateId is None
    wire_enemy = to_emulator_payload(make_snapshot(enemies=[enemy]))["Enemies"][0]
    # The Emulator schema makes this property optional + nullable.  RL may emit null
    # after parsing an older payload; the Emulator remains responsible for its legacy
    # generic/Tunneler fallback when no explicit follow-up is available.
    assert wire_enemy["FollowUpStateId"] is None
