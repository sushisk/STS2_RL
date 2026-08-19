"""Shared helpers for tests that exercise CombatStateSnapshot restore behavior.

Restore-behavior tests should construct and inspect ``combat_state_snapshot`` DTOs,
then cross the Emulator JSON boundary through ``to_emulator_payload`` /
``to_emulator_json``. Field names and defaults come from the production DTOs; this
module deliberately does not maintain a second wire-schema table.

Schema-parser tests (raw dict -> DTO) intentionally do not use this module: those tests
are validating the wire schema itself and should keep their direct raw-dict fixtures.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from combat_state_snapshot import (
    CombatHistorySnapshot,
    CombatStateSnapshot,
    CreatureSnapshot,
    EnemySnapshot,
    PlayerSnapshot,
    PowerSnapshot,
    RngSnapshotSet,
    SnapshotMetadata,
    canonical_json,
)


def _wire_value(node: Any) -> Any:
    if is_dataclass(node):
        return _wire_value(asdict(node))
    if isinstance(node, dict):
        return {
            key: _wire_value(value)
            for key, value in node.items()
            if key != "unknown_fields"
        }
    if isinstance(node, (list, tuple)):
        return [_wire_value(value) for value in node]
    return node


def to_emulator_payload(snapshot: CombatStateSnapshot) -> dict[str, Any]:
    """Return the Emulator wire payload for a typed Python snapshot.

    Production DTOs preserve the Emulator's PascalCase field names and own all field
    defaults. The only Python-only state removed here is ``unknown_fields``; no schema
    name/default table is duplicated in tests.
    """
    if not isinstance(snapshot, CombatStateSnapshot):
        raise TypeError(f"expected CombatStateSnapshot, got {type(snapshot).__name__}")
    return _wire_value(snapshot)


def to_emulator_json(snapshot: CombatStateSnapshot) -> str:
    """Serialize with the same canonical JSON utility used by the production bridge."""
    return canonical_json(to_emulator_payload(snapshot), exclude_volatile=False)


def make_power(
    *,
    instance_id: str = "power-900000",
    power_id: str = "STRENGTH",
    owner_instance_id: str = "creature-900000",
    amount: int = 0,
    amount_on_turn_start: int = 0,
    skip_next_duration_tick: bool = False,
    stack_type: str = "None",
    applier_instance_id: str | None = None,
    target_instance_id: str | None = None,
    associated_card_instance_id: str | None = None,
    has_unsupported_internal_data: bool = False,
    internal_data: dict | None = None,
    internal_data_serializer_version: str | None = None,
) -> PowerSnapshot:
    return PowerSnapshot(
        InstanceId=instance_id,
        PowerId=power_id,
        Amount=amount,
        AmountOnTurnStart=amount_on_turn_start,
        SkipNextDurationTick=skip_next_duration_tick,
        StackType=stack_type,
        OwnerInstanceId=owner_instance_id,
        ApplierInstanceId=applier_instance_id,
        TargetInstanceId=target_instance_id,
        AssociatedCardInstanceId=associated_card_instance_id,
        HasUnsupportedInternalData=has_unsupported_internal_data,
        InternalData=internal_data,
        InternalDataSerializerVersion=internal_data_serializer_version,
    )


def make_creature(
    *,
    instance_id: str = "creature-900000",
    kind: str = "pet",
    name: str = "Test Creature",
    hp: int = 1,
    max_hp: int | None = None,
    block: int = 0,
    is_alive: bool = True,
    powers: list[PowerSnapshot] | None = None,
    state_log: list | None = None,
    owner_instance_id: str | None = None,
    combat_id: int | None = None,
    monster_id: str | None = None,
    slot_name: str | None = None,
    intent: dict | None = None,
) -> CreatureSnapshot:
    return CreatureSnapshot(
        InstanceId=instance_id,
        Kind=kind,
        Name=name,
        Hp=hp,
        MaxHp=hp if max_hp is None else max_hp,
        Block=block,
        IsAlive=is_alive,
        Powers=list(powers or []),
        StateLog=list(state_log or []),
        OwnerInstanceId=owner_instance_id,
        CombatId=combat_id,
        MonsterId=monster_id,
        SlotName=slot_name,
        Intent=intent,
    )


def _default_player() -> PlayerSnapshot:
    return PlayerSnapshot(
        InstanceId="player-900000",
        CreatureInstanceId="creature-900000",
        NetId=0,
        Hp=80,
        MaxHp=80,
        Block=0,
        Energy=3,
        MaxEnergy=3,
        Stars=0,
        Gold=0,
        OrbSlotCapacity=0,
        CardInstances=[],
        Relics=[],
        Powers=[],
        Potions=[],
        Orbs=[],
        Pets=[],
    )


def _default_metadata() -> SnapshotMetadata:
    return SnapshotMetadata(
        SchemaVersion="phase3c.8",
        SnapshotId="snapshot-testkit",
        StepIndex=0,
        CaptureBoundary="normal_player_decision",
        Completeness="complete",
        UnsupportedFields=[],
        CapturedAtUtc="1970-01-01T00:00:00Z",
        CombatSessionId="combat-session-testkit",
        ContinuationStepIndex=None,
        EmulatorCommit=None,
    )


def make_snapshot(
    *,
    metadata: SnapshotMetadata | None = None,
    player: PlayerSnapshot | None = None,
    enemies: list[EnemySnapshot] | None = None,
    turn_number: int = 1,
    round_number: int = 1,
    current_side: str = "Player",
    phase: str = "PlayerTurn",
    rng: RngSnapshotSet | None = None,
    combat_history: CombatHistorySnapshot | None = None,
    is_terminal: bool = False,
    outcome: str = "None",
    pending_choice: dict | None = None,
) -> CombatStateSnapshot:
    return CombatStateSnapshot(
        Metadata=metadata or _default_metadata(),
        Player=player or _default_player(),
        Enemies=list(enemies or []),
        TurnNumber=turn_number,
        RoundNumber=round_number,
        CurrentSide=current_side,
        Phase=phase,
        Rng=rng or RngSnapshotSet(RunRng={}, PlayerRng=[], MonsterRng=[]),
        CombatHistory=combat_history or CombatHistorySnapshot(Entries=[]),
        IsTerminal=is_terminal,
        Outcome=outcome,
        PendingChoice=pending_choice,
    )


def active_enemies(snapshot: CombatStateSnapshot) -> list[EnemySnapshot]:
    return [enemy for enemy in snapshot.Enemies if enemy.IsAlive]


def player_creature(snapshot: CombatStateSnapshot) -> PlayerSnapshot:
    """Return the flattened player-side creature view stored on ``PlayerSnapshot``."""
    return snapshot.Player


def creature_by_id(
    snapshot: CombatStateSnapshot,
    instance_id: str,
) -> PlayerSnapshot | EnemySnapshot | CreatureSnapshot:
    """Resolve a creature stable ID across player, enemies, and pets.

    ``PlayerSnapshot.InstanceId`` identifies the player entity, not its creature. The
    player-side creature is therefore resolved only through ``CreatureInstanceId``.
    """
    player = snapshot.Player
    if instance_id == player.CreatureInstanceId:
        return player
    for enemy in snapshot.Enemies:
        if enemy.InstanceId == instance_id:
            return enemy
    for pet in player.Pets:
        if pet.InstanceId == instance_id:
            return pet
    raise KeyError(instance_id)
