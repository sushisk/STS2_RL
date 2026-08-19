"""RL-side contract coverage for STS2_Emulator PR #32 STUNNED snapshots.

The Emulator owns STUNNED reconstruction semantics. RL's responsibility is to carry
EnemySnapshot.FollowUpStateId and EnemySnapshot.TransientMove losslessly through the
typed Snapshot DTO boundary rather than deriving or interpreting them locally.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
for _path in (_HERE, _COMBAT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from combat_state_snapshot import (  # noqa: E402
    CombatStateSnapshot,
    EnemySnapshot,
    SnapshotValidationError,
    TransientMoveSnapshot,
)
from snapshot_testkit import make_snapshot, to_emulator_payload  # noqa: E402


def _enemy_payload(
    *,
    follow_up_state_id: str | None,
    behavior_id: str | None = None,
    include_follow_up: bool = True,
    include_transient: bool = True,
) -> dict:
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
    if include_transient:
        payload["TransientMove"] = None if behavior_id is None else {
            "Kind": "stunned",
            "FollowUpStateId": follow_up_state_id,
            "BehaviorId": behavior_id,
        }
    return payload


def test_source_specific_stunned_contract_survives_typed_snapshot_round_trip():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(
            follow_up_state_id="BEAST_CRY_MOVE",
            behavior_id="ceremonial_beast_stunned",
        )
    )

    assert enemy.Intent == {"stateId": "STUNNED"}
    assert enemy.StateLog == ["STAMP_MOVE", "PLOW_MOVE"]
    assert enemy.FollowUpStateId == "BEAST_CRY_MOVE"
    assert enemy.TransientMove == TransientMoveSnapshot(
        Kind="stunned",
        FollowUpStateId="BEAST_CRY_MOVE",
        BehaviorId="ceremonial_beast_stunned",
    )

    wire = to_emulator_payload(make_snapshot(enemies=[enemy]))
    wire_enemy = wire["Enemies"][0]
    assert wire_enemy["Intent"] == {"stateId": "STUNNED"}
    assert wire_enemy["StateLog"] == ["STAMP_MOVE", "PLOW_MOVE"]
    assert wire_enemy["FollowUpStateId"] == "BEAST_CRY_MOVE"
    assert wire_enemy["TransientMove"] == {
        "Kind": "stunned",
        "FollowUpStateId": "BEAST_CRY_MOVE",
        "BehaviorId": "ceremonial_beast_stunned",
    }

    reparsed_enemy = CombatStateSnapshot.from_dict(wire).Enemies[0]
    assert reparsed_enemy.StateLog[-1] == "PLOW_MOVE"
    assert reparsed_enemy.FollowUpStateId == "BEAST_CRY_MOVE"
    assert reparsed_enemy.TransientMove == enemy.TransientMove


def test_generic_stun_behavior_is_not_rewritten_from_monster_identity():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(follow_up_state_id="PLOW_MOVE", behavior_id="generic")
    )

    wire_enemy = to_emulator_payload(make_snapshot(enemies=[enemy]))["Enemies"][0]
    assert wire_enemy["MonsterId"] == "CEREMONIAL_BEAST"
    assert wire_enemy["FollowUpStateId"] == "PLOW_MOVE"
    assert wire_enemy["TransientMove"]["BehaviorId"] == "generic"
    assert wire_enemy["TransientMove"]["FollowUpStateId"] == "PLOW_MOVE"


def test_custom_behavior_id_is_transported_without_rl_interpretation():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(
            follow_up_state_id="ROLL_OUT_MOVE",
            behavior_id="slumbering_beetle_wake_up",
        )
    )

    wire_enemy = to_emulator_payload(make_snapshot(enemies=[enemy]))["Enemies"][0]
    assert wire_enemy["TransientMove"] == {
        "Kind": "stunned",
        "FollowUpStateId": "ROLL_OUT_MOVE",
        "BehaviorId": "slumbering_beetle_wake_up",
    }


def test_follow_up_only_snapshot_remains_compatible():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(
            follow_up_state_id="BEAST_CRY_MOVE",
            include_transient=False,
        )
    )

    assert enemy.FollowUpStateId == "BEAST_CRY_MOVE"
    assert enemy.TransientMove is None
    wire_enemy = to_emulator_payload(make_snapshot(enemies=[enemy]))["Enemies"][0]
    assert wire_enemy["FollowUpStateId"] == "BEAST_CRY_MOVE"
    assert wire_enemy["TransientMove"] is None


def test_legacy_snapshot_without_transient_fields_remains_compatible():
    enemy = EnemySnapshot.from_dict(
        _enemy_payload(
            follow_up_state_id=None,
            include_follow_up=False,
            include_transient=False,
        )
    )

    assert enemy.FollowUpStateId is None
    assert enemy.TransientMove is None
    wire_enemy = to_emulator_payload(make_snapshot(enemies=[enemy]))["Enemies"][0]
    assert wire_enemy["FollowUpStateId"] is None
    assert wire_enemy["TransientMove"] is None


def test_non_null_transient_move_requires_all_contract_fields():
    payload = _enemy_payload(
        follow_up_state_id="BEAST_CRY_MOVE",
        behavior_id="ceremonial_beast_stunned",
    )
    del payload["TransientMove"]["BehaviorId"]

    with pytest.raises(SnapshotValidationError, match="TransientMoveSnapshot missing required field"):
        EnemySnapshot.from_dict(payload)
