"""Tests for the test-only typed DTO -> Emulator wire boundary.

Raw dict -> DTO parser tests intentionally live elsewhere and should not use this
module: those tests validate the schema reader itself.  These tests protect the shared
serialization seam used by restore-behavior tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from combat_state_snapshot import CardInstanceSnapshot, EnemySnapshot  # noqa: E402
from snapshot_testkit import (  # noqa: E402
    active_enemies,
    creature_by_id,
    make_creature,
    make_power,
    make_snapshot,
    player_creature,
    to_emulator_payload,
)


def _card() -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId="card-900000",
        CardId="STRIKE_IRONCLAD",
        Type="Attack",
        Rarity="Basic",
        Cost=1,
        TargetType="AnyEnemy",
        IsUpgraded=False,
        UpgradeLevel=0,
        LocalKeywords=None,
        LocalCostModifiers=[],
        TemporaryStarCosts=[],
        Zone="Hand",
        PileIndex=0,
        HasBeenRemovedFromState=False,
        Enchantment=None,
        EnchantmentStatus=None,
        TinkerTimeType=None,
        TinkerTimeRider=None,
    )


def _enemy(instance_id: str, *, alive: bool) -> EnemySnapshot:
    return EnemySnapshot(
        InstanceId=instance_id,
        Index=0,
        Name="Test Enemy",
        Hp=10 if alive else 0,
        MaxHp=10,
        Block=0,
        IsAlive=alive,
        Powers=[],
        StateLog=[],
        CombatId=1,
        MonsterId="CALCIFIED_CULTIST",
        SlotName=None,
        Intent=None,
    )


def test_to_emulator_payload_uses_dto_field_names_and_card_compat_defaults():
    snapshot = make_snapshot()
    snapshot.Player.CardInstances = [_card()]
    snapshot.Metadata.unknown_fields = {"FutureMetadataField": 123}
    snapshot.unknown_fields = {"FutureRootField": 456}

    payload = to_emulator_payload(snapshot)

    assert "Metadata" in payload
    assert "Player" in payload
    assert "unknown_fields" not in payload
    assert "unknown_fields" not in payload["Metadata"]
    card = payload["Player"]["CardInstances"][0]
    assert card["InstanceId"] == "card-900000"
    assert card["CardId"] == "STRIKE_IRONCLAD"
    assert card["DynamicVars"] == {}
    assert card["BaseReplayCount"] == 0
    assert card["SavedProperties"] is None


def test_testkit_builders_return_typed_dtos():
    power = make_power(
        instance_id="power-900001",
        owner_instance_id="creature-900001",
        amount=3,
    )
    pet = make_creature(
        instance_id="creature-900001",
        owner_instance_id="player-900000",
        powers=[power],
        monster_id="OSTY",
    )
    snapshot = make_snapshot()
    snapshot.Player.Pets = [pet]

    assert snapshot.Player.Pets[0] is pet
    assert pet.Powers[0] is power
    assert power.Amount == 3


def test_creature_accessors_hide_player_enemy_pet_storage_shape():
    alive = _enemy("creature-900010", alive=True)
    dead = _enemy("creature-900011", alive=False)
    pet = make_creature(
        instance_id="creature-900012",
        owner_instance_id="player-900000",
        monster_id="OSTY",
    )
    snapshot = make_snapshot(enemies=[alive, dead])
    snapshot.Player.Pets = [pet]

    assert active_enemies(snapshot) == [alive]
    assert player_creature(snapshot) is snapshot.Player
    assert creature_by_id(snapshot, snapshot.Player.CreatureInstanceId) is snapshot.Player
    assert creature_by_id(snapshot, alive.InstanceId) is alive
    assert creature_by_id(snapshot, pet.InstanceId) is pet


def test_creature_by_id_rejects_unknown_id():
    snapshot = make_snapshot()
    try:
        creature_by_id(snapshot, "creature-999999")
        raise AssertionError("expected KeyError")
    except KeyError as exc:
        assert exc.args == ("creature-999999",)
