"""CombatStateSnapshot: Python-side types for Canonical CombatStateSnapshot contract
v0.3 Phase 2A (`C:\\STS2_Emulator\\docs\\reports\\combat_state_snapshot_phase2a_emulator_
report_20260726.md`). Mirrors `Sts2Emulator.Dto.Snapshot.*` 1:1 - PascalCase field names
preserved exactly, matching the wire JSON `GameInstance.CaptureSnapshotJson()` produces
(`JsonOptions` has no naming policy on the C# side, so there is nothing to translate;
renaming to snake_case here would be a lossy, error-prone step this file deliberately
avoids).

Deliberately a SEPARATE type family from `BattleState`/Observation (Phase 1) - per this
task's instruction ("既存BattleState／Observationとは別型にする"). Never constructed by
`LiveCombatSession.step()`'s normal decision loop, never passed to
`BattleEmulator.apply_action()`/`HeuristicAgent`, never used for Policy/Value/Choice
Policy input. Exists solely for diagnostic/explicit-capture use via
`LiveCombatSession.capture_snapshot()`.

Validation principle (this task's "JSON検証" + contract v0.3 principle): this module
NEVER upgrades a non-`"complete"` `Completeness` verdict to `"complete"` - the Emulator's
own verdict is authoritative (`CombatStateSnapshot.completeness_is_complete()` reads it
verbatim). Unknown top-level fields are recorded (`unknown_fields`), never silently
dropped or treated as an error - only a missing REQUIRED field or an unrecognized
`SchemaVersion` raises `SnapshotValidationError`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

KNOWN_SCHEMA_VERSIONS = frozenset({"phase2a.1", "phase2b.1", "phase2b.2", "phase3c.3", "phase3c.4"})
COMPLETENESS_VALUES = frozenset({"complete", "partial_known_gaps", "unsupported_state", "capture_failed"})
CAPTURE_BOUNDARY_VALUES = frozenset({"normal_player_decision", "published_choice", "published_target", "terminal"})

# Phase 2B contract (`combat_state_contract.v0.4.md` §Stable ID principles): opaque
# per-Emulator-process id, format '<kind>-<sequence>' (e.g. 'card-000042'). Matches
# the Emulator's own `docs/schemas/combat_state_snapshot.schema.json` `StableInstanceId`
# pattern verbatim - never a memory address or reference hash.
STABLE_INSTANCE_ID_PATTERN = re.compile(r"^[a-z]+-[0-9]{6,}$")


class SnapshotValidationError(ValueError):
    """Raised when a captured Snapshot JSON fails required-field or schema-version
    validation. Never raised for merely-unknown extra fields (those are recorded, not
    rejected) - only for a missing required field or an unrecognized `SchemaVersion`."""


def _require(d: dict, keys: list[str], type_name: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise SnapshotValidationError(f"{type_name} missing required field(s): {missing}")


def _unknown(d: dict, known: set[str]) -> dict:
    return {k: v for k, v in d.items() if k not in known}


@dataclass
class UnsupportedSnapshotField:
    FieldPath: str
    Status: str
    Reason: str
    ClassName: "str | None" = None
    InstanceId: "str | None" = None
    SerializerVersion: "str | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "UnsupportedSnapshotField":
        _require(d, ["FieldPath", "Status", "Reason"], "UnsupportedSnapshotField")
        return cls(
            FieldPath=d["FieldPath"], Status=d["Status"], Reason=d["Reason"],
            ClassName=d.get("ClassName"), InstanceId=d.get("InstanceId"), SerializerVersion=d.get("SerializerVersion"),
        )


@dataclass
class CardInstanceSnapshot:
    InstanceId: str
    CardId: str
    Type: str
    Rarity: str
    Cost: int
    TargetType: str
    IsUpgraded: bool
    UpgradeLevel: int
    Zone: "str | None" = None
    TinkerTimeType: "str | None" = None
    TinkerTimeRider: "str | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "CardInstanceSnapshot":
        _require(d, ["InstanceId", "CardId", "Type", "Rarity", "Cost", "TargetType", "IsUpgraded", "UpgradeLevel"], "CardInstanceSnapshot")
        return cls(
            InstanceId=d["InstanceId"], CardId=d["CardId"], Type=d["Type"], Rarity=d["Rarity"], Cost=int(d["Cost"]),
            TargetType=d["TargetType"], IsUpgraded=bool(d["IsUpgraded"]), UpgradeLevel=int(d["UpgradeLevel"]),
            Zone=d.get("Zone"), TinkerTimeType=d.get("TinkerTimeType"), TinkerTimeRider=d.get("TinkerTimeRider"),
        )


@dataclass
class RelicSnapshot:
    InstanceId: str
    RelicId: str
    Rarity: str
    StackCount: int
    Status: str
    IsWax: bool
    IsMelted: bool
    HasBeenRemovedFromState: bool
    FloorAddedToDeck: int
    SavedProperties: dict
    DisplayAmount: "float | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "RelicSnapshot":
        _require(d, ["InstanceId", "RelicId", "Rarity", "StackCount", "Status", "IsWax", "IsMelted", "HasBeenRemovedFromState", "FloorAddedToDeck", "SavedProperties"], "RelicSnapshot")
        return cls(
            InstanceId=d["InstanceId"], RelicId=d["RelicId"], Rarity=d["Rarity"], StackCount=int(d["StackCount"]),
            Status=d["Status"], IsWax=bool(d["IsWax"]), IsMelted=bool(d["IsMelted"]),
            HasBeenRemovedFromState=bool(d["HasBeenRemovedFromState"]), FloorAddedToDeck=int(d["FloorAddedToDeck"]),
            SavedProperties=d["SavedProperties"], DisplayAmount=d.get("DisplayAmount"),
        )


@dataclass
class PowerSnapshot:
    InstanceId: str
    PowerId: str
    Amount: int
    AmountOnTurnStart: int
    SkipNextDurationTick: bool
    StackType: str
    OwnerInstanceId: str
    HasUnsupportedInternalData: bool
    ApplierInstanceId: "str | None" = None
    TargetInstanceId: "str | None" = None
    AssociatedCard: "CardInstanceSnapshot | None" = None
    # Phase 2B additions (schemaVersion "phase2b.1"): generic-reflection serialization of
    # the 17 `serialize_required` Power `_internalData` classes (contract v0.4 Power
    # classification table). `None` for the 16 `safe_to_recompute` classes (intentionally
    # not a gap) and for the 2 `unsupported_unknown` classes (GigantificationPower/
    # VigorPower - flagged instead via HasUnsupportedInternalData=True + an
    # UnsupportedSnapshotField entry, never silently null-as-if-absent).
    InternalData: "dict | None" = None
    InternalDataSerializerVersion: "str | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "PowerSnapshot":
        _require(d, ["InstanceId", "PowerId", "Amount", "AmountOnTurnStart", "SkipNextDurationTick", "StackType", "OwnerInstanceId", "HasUnsupportedInternalData"], "PowerSnapshot")
        return cls(
            InstanceId=d["InstanceId"], PowerId=d["PowerId"], Amount=int(d["Amount"]),
            AmountOnTurnStart=int(d["AmountOnTurnStart"]), SkipNextDurationTick=bool(d["SkipNextDurationTick"]),
            StackType=d["StackType"], OwnerInstanceId=d["OwnerInstanceId"],
            HasUnsupportedInternalData=bool(d["HasUnsupportedInternalData"]),
            ApplierInstanceId=d.get("ApplierInstanceId"), TargetInstanceId=d.get("TargetInstanceId"),
            AssociatedCard=CardInstanceSnapshot.from_dict(d["AssociatedCard"]) if d.get("AssociatedCard") else None,
            InternalData=d.get("InternalData"), InternalDataSerializerVersion=d.get("InternalDataSerializerVersion"),
        )


@dataclass
class PotionSnapshot:
    InstanceId: str
    PotionId: str
    Slot: int
    Rarity: str
    TargetType: str
    IsQueued: bool

    @classmethod
    def from_dict(cls, d: dict) -> "PotionSnapshot":
        _require(d, ["InstanceId", "PotionId", "Slot", "Rarity", "TargetType", "IsQueued"], "PotionSnapshot")
        return cls(
            InstanceId=d["InstanceId"], PotionId=d["PotionId"], Slot=int(d["Slot"]), Rarity=d["Rarity"],
            TargetType=d["TargetType"], IsQueued=bool(d["IsQueued"]),
        )


@dataclass
class OrbSnapshot:
    InstanceId: str
    OrbId: str
    Index: int
    BasePassiveValue: "float | None" = None
    BaseEvokeValue: "float | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "OrbSnapshot":
        _require(d, ["InstanceId", "OrbId", "Index"], "OrbSnapshot")
        return cls(
            InstanceId=d["InstanceId"], OrbId=d["OrbId"], Index=int(d["Index"]),
            BasePassiveValue=d.get("BasePassiveValue"), BaseEvokeValue=d.get("BaseEvokeValue"),
        )


@dataclass
class EnemySnapshot:
    InstanceId: str
    Index: int
    Name: str
    Hp: int
    MaxHp: int
    Block: int
    IsAlive: bool
    Powers: list  # list[PowerSnapshot]
    StateLog: list
    CombatId: "int | None" = None
    MonsterId: "str | None" = None
    SlotName: "str | None" = None
    Intent: "dict | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "EnemySnapshot":
        _require(d, ["InstanceId", "Index", "Name", "Hp", "MaxHp", "Block", "IsAlive", "Powers", "StateLog"], "EnemySnapshot")
        return cls(
            InstanceId=d["InstanceId"], Index=int(d["Index"]), Name=d["Name"], Hp=int(d["Hp"]), MaxHp=int(d["MaxHp"]),
            Block=int(d["Block"]), IsAlive=bool(d["IsAlive"]), Powers=[PowerSnapshot.from_dict(p) for p in d["Powers"]],
            StateLog=list(d["StateLog"]), CombatId=d.get("CombatId"), MonsterId=d.get("MonsterId"),
            SlotName=d.get("SlotName"), Intent=d.get("Intent"),
        )


@dataclass
class CreatureSnapshot:
    """Phase 2B pet-capture fix (SchemaVersion "phase2b.2"): generic live-Creature
    capture used for `PlayerSnapshot.Pets`. `Kind` is `"player" | "enemy" | "pet"` per
    the formal schema, but only `"pet"` is actually produced today - `EnemySnapshot`/
    `PlayerSnapshot`'s own existing flattened fields remain the representation for
    those two roles (deliberately not unified in this fix, per the Emulator report
    §2-B, to preserve Phase 2A/2B backward compatibility)."""
    InstanceId: str
    Kind: str
    Name: str
    Hp: int
    MaxHp: int
    Block: int
    IsAlive: bool
    Powers: list  # list[PowerSnapshot]
    StateLog: list
    OwnerInstanceId: "str | None" = None
    CombatId: "int | None" = None
    MonsterId: "str | None" = None
    SlotName: "str | None" = None
    Intent: "dict | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "CreatureSnapshot":
        _require(d, ["InstanceId", "Kind", "Name", "Hp", "MaxHp", "Block", "IsAlive", "Powers", "StateLog"], "CreatureSnapshot")
        return cls(
            InstanceId=d["InstanceId"], Kind=d["Kind"], Name=d["Name"], Hp=int(d["Hp"]), MaxHp=int(d["MaxHp"]),
            Block=int(d["Block"]), IsAlive=bool(d["IsAlive"]), Powers=[PowerSnapshot.from_dict(p) for p in d["Powers"]],
            StateLog=list(d["StateLog"]), OwnerInstanceId=d.get("OwnerInstanceId"), CombatId=d.get("CombatId"),
            MonsterId=d.get("MonsterId"), SlotName=d.get("SlotName"), Intent=d.get("Intent"),
        )


@dataclass
class PlayerSnapshot:
    InstanceId: str
    CreatureInstanceId: str
    NetId: int
    Hp: int
    MaxHp: int
    Block: int
    Energy: int
    MaxEnergy: int
    Stars: int
    Gold: int
    OrbSlotCapacity: int
    Hand: list
    DrawPile: list
    DiscardPile: list
    ExhaustPile: list
    PlayPile: list
    Deck: list
    Relics: list
    Powers: list
    Potions: list
    Orbs: list
    Pets: list = field(default_factory=list)  # list[CreatureSnapshot], Phase 2B pet-capture fix (phase2b.2+)

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerSnapshot":
        _require(
            d,
            ["InstanceId", "CreatureInstanceId", "NetId", "Hp", "MaxHp", "Block", "Energy", "MaxEnergy", "Stars",
             "Gold", "OrbSlotCapacity", "Hand", "DrawPile", "DiscardPile", "ExhaustPile", "PlayPile", "Deck",
             "Relics", "Powers", "Potions", "Orbs", "Pets"],
            "PlayerSnapshot",
        )
        return cls(
            InstanceId=d["InstanceId"], CreatureInstanceId=d["CreatureInstanceId"], NetId=int(d["NetId"]),
            Hp=int(d["Hp"]), MaxHp=int(d["MaxHp"]), Block=int(d["Block"]), Energy=int(d["Energy"]),
            MaxEnergy=int(d["MaxEnergy"]), Stars=int(d["Stars"]), Gold=int(d["Gold"]),
            OrbSlotCapacity=int(d["OrbSlotCapacity"]),
            Hand=[CardInstanceSnapshot.from_dict(c) for c in d["Hand"]],
            DrawPile=[CardInstanceSnapshot.from_dict(c) for c in d["DrawPile"]],
            DiscardPile=[CardInstanceSnapshot.from_dict(c) for c in d["DiscardPile"]],
            ExhaustPile=[CardInstanceSnapshot.from_dict(c) for c in d["ExhaustPile"]],
            PlayPile=[CardInstanceSnapshot.from_dict(c) for c in d["PlayPile"]],
            Deck=[CardInstanceSnapshot.from_dict(c) for c in d["Deck"]],
            Relics=[RelicSnapshot.from_dict(r) for r in d["Relics"]],
            Powers=[PowerSnapshot.from_dict(p) for p in d["Powers"]],
            Potions=[PotionSnapshot.from_dict(p) if p is not None else None for p in d["Potions"]],
            Orbs=[OrbSnapshot.from_dict(o) for o in d["Orbs"]],
            Pets=[CreatureSnapshot.from_dict(c) for c in d["Pets"]],
        )


@dataclass
class SerializableRngSnapshot:
    Counter: int
    State0: int
    State1: int
    State2: int
    State3: int

    @classmethod
    def from_dict(cls, d: dict) -> "SerializableRngSnapshot":
        _require(d, ["Counter", "State0", "State1", "State2", "State3"], "SerializableRngSnapshot")
        return cls(Counter=int(d["Counter"]), State0=int(d["State0"]), State1=int(d["State1"]), State2=int(d["State2"]), State3=int(d["State3"]))


@dataclass
class PlayerRngSnapshot:
    OwnerInstanceId: str
    Purposes: dict  # PlayerRngType name -> SerializableRngSnapshot

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerRngSnapshot":
        _require(d, ["OwnerInstanceId", "Purposes"], "PlayerRngSnapshot")
        return cls(
            OwnerInstanceId=d["OwnerInstanceId"],
            Purposes={k: SerializableRngSnapshot.from_dict(v) for k, v in d["Purposes"].items()},
        )


@dataclass
class MonsterRngSnapshot:
    OwnerInstanceId: str
    Rng: SerializableRngSnapshot

    @classmethod
    def from_dict(cls, d: dict) -> "MonsterRngSnapshot":
        _require(d, ["OwnerInstanceId", "Rng"], "MonsterRngSnapshot")
        return cls(OwnerInstanceId=d["OwnerInstanceId"], Rng=SerializableRngSnapshot.from_dict(d["Rng"]))


@dataclass
class RngSnapshotSet:
    RunRng: dict  # purpose name -> SerializableRngSnapshot
    PlayerRng: list  # list[PlayerRngSnapshot]
    MonsterRng: list  # list[MonsterRngSnapshot]

    @classmethod
    def from_dict(cls, d: dict) -> "RngSnapshotSet":
        _require(d, ["RunRng", "PlayerRng", "MonsterRng"], "RngSnapshotSet")
        return cls(
            RunRng={k: SerializableRngSnapshot.from_dict(v) for k, v in d["RunRng"].items()},
            PlayerRng=[PlayerRngSnapshot.from_dict(p) for p in d["PlayerRng"]],
            MonsterRng=[MonsterRngSnapshot.from_dict(m) for m in d["MonsterRng"]],
        )


@dataclass
class CombatHistoryEntrySnapshot:
    EntryType: str
    RoundNumber: int
    CurrentSide: str
    PlayerTurnNumbers: dict
    Fields: dict
    ActorInstanceId: "str | None" = None

    @classmethod
    def from_dict(cls, d: dict) -> "CombatHistoryEntrySnapshot":
        _require(d, ["EntryType", "RoundNumber", "CurrentSide", "PlayerTurnNumbers", "Fields"], "CombatHistoryEntrySnapshot")
        return cls(
            EntryType=d["EntryType"], RoundNumber=int(d["RoundNumber"]), CurrentSide=d["CurrentSide"],
            PlayerTurnNumbers=dict(d["PlayerTurnNumbers"]), Fields=d["Fields"], ActorInstanceId=d.get("ActorInstanceId"),
        )


@dataclass
class CombatHistorySnapshot:
    Entries: list  # list[CombatHistoryEntrySnapshot]

    @classmethod
    def from_dict(cls, d: dict) -> "CombatHistorySnapshot":
        _require(d, ["Entries"], "CombatHistorySnapshot")
        return cls(Entries=[CombatHistoryEntrySnapshot.from_dict(e) for e in d["Entries"]])


@dataclass
class SnapshotMetadata:
    SchemaVersion: str
    SnapshotId: str
    StepIndex: int
    CaptureBoundary: str
    Completeness: str
    UnsupportedFields: list  # list[UnsupportedSnapshotField]
    CapturedAtUtc: str
    CombatSessionId: "str | None" = None
    ContinuationStepIndex: "int | None" = None
    EmulatorCommit: "str | None" = None
    unknown_fields: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotMetadata":
        required = ["SchemaVersion", "SnapshotId", "StepIndex", "CaptureBoundary", "Completeness", "UnsupportedFields", "CapturedAtUtc"]
        _require(d, required, "SnapshotMetadata")
        if d["SchemaVersion"] not in KNOWN_SCHEMA_VERSIONS:
            raise SnapshotValidationError(
                f"unknown SnapshotMetadata.SchemaVersion={d['SchemaVersion']!r} (known: {sorted(KNOWN_SCHEMA_VERSIONS)})"
            )
        if d["Completeness"] not in COMPLETENESS_VALUES:
            raise SnapshotValidationError(f"unrecognized Completeness={d['Completeness']!r} (known: {sorted(COMPLETENESS_VALUES)})")
        if d["CaptureBoundary"] not in CAPTURE_BOUNDARY_VALUES:
            raise SnapshotValidationError(f"unrecognized CaptureBoundary={d['CaptureBoundary']!r} (known: {sorted(CAPTURE_BOUNDARY_VALUES)})")
        known = set(required) | {"CombatSessionId", "ContinuationStepIndex", "EmulatorCommit"}
        return cls(
            SchemaVersion=d["SchemaVersion"], SnapshotId=d["SnapshotId"], StepIndex=int(d["StepIndex"]),
            CaptureBoundary=d["CaptureBoundary"], Completeness=d["Completeness"],
            UnsupportedFields=[UnsupportedSnapshotField.from_dict(u) for u in d["UnsupportedFields"]],
            CapturedAtUtc=d["CapturedAtUtc"], CombatSessionId=d.get("CombatSessionId"),
            ContinuationStepIndex=d.get("ContinuationStepIndex"), EmulatorCommit=d.get("EmulatorCommit"),
            unknown_fields=_unknown(d, known),
        )

    def to_decision_frame(self):
        """Assembles this task's DecisionFrame (contract v0.3 §5/§6) from this Snapshot's
        own metadata - no second source needed (SnapshotMetadata already carries all 3
        fields, per the Emulator's own design note)."""
        from battle_emulator import DecisionFrame

        return DecisionFrame(
            combat_session_id=self.CombatSessionId, step_index=self.StepIndex,
            continuation_step_index=self.ContinuationStepIndex,
        )


@dataclass
class CombatStateSnapshot:
    Metadata: SnapshotMetadata
    Player: PlayerSnapshot
    Enemies: list  # list[EnemySnapshot]
    TurnNumber: int
    RoundNumber: int
    CurrentSide: str
    Phase: str
    Rng: RngSnapshotSet
    CombatHistory: CombatHistorySnapshot
    IsTerminal: bool
    Outcome: str
    PendingChoice: "dict | None" = None
    unknown_fields: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict) -> "CombatStateSnapshot":
        required = ["Metadata", "Player", "Enemies", "TurnNumber", "RoundNumber", "CurrentSide", "Phase", "Rng", "CombatHistory", "IsTerminal", "Outcome"]
        _require(d, required, "CombatStateSnapshot")
        known = set(required) | {"PendingChoice"}
        return cls(
            Metadata=SnapshotMetadata.from_dict(d["Metadata"]),
            Player=PlayerSnapshot.from_dict(d["Player"]),
            Enemies=[EnemySnapshot.from_dict(e) for e in d["Enemies"]],
            TurnNumber=int(d["TurnNumber"]), RoundNumber=int(d["RoundNumber"]), CurrentSide=d["CurrentSide"],
            Phase=d["Phase"], Rng=RngSnapshotSet.from_dict(d["Rng"]), CombatHistory=CombatHistorySnapshot.from_dict(d["CombatHistory"]),
            IsTerminal=bool(d["IsTerminal"]), Outcome=d["Outcome"], PendingChoice=d.get("PendingChoice"),
            unknown_fields=_unknown(d, known),
        )

    @classmethod
    def from_json(cls, json_text: str) -> "CombatStateSnapshot":
        return cls.from_dict(json.loads(json_text))

    def completeness_is_complete(self) -> bool:
        """Reads the Emulator's own verdict verbatim - NEVER upgrades a non-'complete'
        verdict, per contract v0.3's completeness principle ('Python側はEmulatorの判定を
        勝手に格上げしない')."""
        return self.Metadata.Completeness == "complete"


# ---------------------------------------------------------------------------
# Phase 2B additions: canonical serialization, stable-ID reference integrity,
# and strict JSON-Schema conformance. None of this changes the lenient
# `from_dict`/`from_json` parsing behavior above (which still records-but-does-
# not-reject unknown fields, per the Phase 2A design this task explicitly keeps
# unchanged) - these are additive, separate utilities for the Phase 2B tasks
# ("canonical serializationを固定", "stable instance IDの重複・参照切れ検証",
# schema-conformance rejection tests).
# ---------------------------------------------------------------------------

_VOLATILE_TOP_LEVEL_METADATA_KEYS = frozenset({"SnapshotId", "CapturedAtUtc"})


def canonical_json(snapshot_dict: dict, *, exclude_volatile: bool = True) -> str:
    """Fixes ONE canonical serialization for a Snapshot's plain-dict form (e.g. the
    output of `dataclasses.asdict(snapshot)`): recursively sorted keys, no extra
    whitespace, so two captures of the same underlying state hash identically
    regardless of dict insertion order. When `exclude_volatile` is True (the default -
    used for equality/hash comparisons), `Metadata.SnapshotId`/`Metadata.CapturedAtUtc`
    are dropped first, since those two fields are expected to differ between two
    captures of the *same* combat state (new snapshot identity/timestamp each call) and
    are not part of the combat-state content itself."""

    def _strip_volatile(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: _strip_volatile(v)
                for k, v in node.items()
                if not (exclude_volatile and k in _VOLATILE_TOP_LEVEL_METADATA_KEYS)
            }
        if isinstance(node, list):
            return [_strip_volatile(v) for v in node]
        return node

    return json.dumps(_strip_volatile(snapshot_dict), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


@dataclass
class DanglingReference:
    field_path: str
    referenced_instance_id: str
    entry_type: "str | None"
    cause: str  # "source_live_state_inconsistency" | "capture_bug"


@dataclass
class SnapshotReferenceReport:
    duplicate_instance_ids: "list[str]"
    dangling_references: "list[DanglingReference]"

    @property
    def has_issues(self) -> bool:
        return bool(self.duplicate_instance_ids or self.dangling_references)


def _walk_instance_id_refs(node: Any, path: str):
    """Recursively yields (field_path, instance_id) for every key ending in
    'InstanceId'/'InstanceIds' found anywhere under `node` - mirrors the Emulator's own
    `SnapshotReferenceValidator.cs` traversal convention (§6 of the Phase 2B Emulator
    report: 'Fields内の全*InstanceId/*InstanceIds値(再帰的にDictionary/配列を走査)')."""
    if isinstance(node, dict):
        for k, v in node.items():
            sub_path = f"{path}.{k}" if path else k
            if k.endswith("InstanceId") and isinstance(v, str):
                yield sub_path, v
            elif k.endswith("InstanceIds") and isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        yield sub_path, item
            else:
                yield from _walk_instance_id_refs(v, sub_path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk_instance_id_refs(item, f"{path}[{i}]")


def _collect_known_instance_ids(snapshot: "CombatStateSnapshot") -> "dict[str, list[str]]":
    """Returns {instance_id: [field_paths where it was DEFINED]} - a list (not a single
    path) so duplicate definitions are visible, not silently overwritten."""
    ids: "dict[str, list[str]]" = {}

    def _add(instance_id: "str | None", path: str) -> None:
        if instance_id is None:
            return
        ids.setdefault(instance_id, []).append(path)

    p = snapshot.Player
    _add(p.InstanceId, "Player.InstanceId")
    _add(p.CreatureInstanceId, "Player.CreatureInstanceId")
    for pile_name in ("Hand", "DrawPile", "DiscardPile", "ExhaustPile", "PlayPile", "Deck"):
        for i, c in enumerate(getattr(p, pile_name)):
            _add(c.InstanceId, f"Player.{pile_name}[{i}].InstanceId")
    for i, r in enumerate(p.Relics):
        _add(r.InstanceId, f"Player.Relics[{i}].InstanceId")
    for i, pw in enumerate(p.Powers):
        _add(pw.InstanceId, f"Player.Powers[{i}].InstanceId")
        if pw.AssociatedCard is not None:
            _add(pw.AssociatedCard.InstanceId, f"Player.Powers[{i}].AssociatedCard.InstanceId")
    for i, pot in enumerate(p.Potions):
        if pot is not None:
            _add(pot.InstanceId, f"Player.Potions[{i}].InstanceId")
    for i, o in enumerate(p.Orbs):
        _add(o.InstanceId, f"Player.Orbs[{i}].InstanceId")
    for pi, pet in enumerate(p.Pets):
        _add(pet.InstanceId, f"Player.Pets[{pi}].InstanceId")
        for wi, pw in enumerate(pet.Powers):
            _add(pw.InstanceId, f"Player.Pets[{pi}].Powers[{wi}].InstanceId")
            if pw.AssociatedCard is not None:
                _add(pw.AssociatedCard.InstanceId, f"Player.Pets[{pi}].Powers[{wi}].AssociatedCard.InstanceId")
    for ei, e in enumerate(snapshot.Enemies):
        _add(e.InstanceId, f"Enemies[{ei}].InstanceId")
        for pi, pw in enumerate(e.Powers):
            _add(pw.InstanceId, f"Enemies[{ei}].Powers[{pi}].InstanceId")
            if pw.AssociatedCard is not None:
                _add(pw.AssociatedCard.InstanceId, f"Enemies[{ei}].Powers[{pi}].AssociatedCard.InstanceId")
    return ids


_CONFIRMED_SOURCE_LIVE_STATE_INCONSISTENCY_ENTRY_TYPES = frozenset({
    # Both confirmed via direct source inspection (not relic-list correlation alone -
    # see contract v0.4 §9-F's "generalized finding"), as the identical "a real hook
    # fires once during ResetFromScenario's natural setup, then the scenario's
    # authoritative pile/board overwrite discards the created instance" mechanism:
    "CardDrawnEntry",       # natural turn-1 draw, discarded by the scenario's specified hand
    "CardGeneratedEntry",   # confirmed instance: FUNERARY_MASK.BeforeHandDraw() generating
                            # SOUL into the draw pile, discarded by the scenario's pile overwrite
                            # (Emulator investigation report, commit b9109bf)
})


def validate_snapshot_references(snapshot: "CombatStateSnapshot") -> SnapshotReferenceReport:
    """Python-side counterpart to the Emulator's `GameInstance.ValidateSnapshotReferences`
    (Phase 2B report §6) - operates purely on an already-parsed `CombatStateSnapshot`
    (works on archived/diagnostic JSON with no live GameInstance needed). Detects:

    * duplicate InstanceId definitions (same stable ID assigned to >1 entity)
    * dangling references (an `*InstanceId`/`*InstanceIds` field pointing at an ID that
      is not defined anywhere in this Snapshot)

    Each dangling reference is classified per this task's required cause taxonomy:
    `source_live_state_inconsistency` for entry types confirmed (via direct source
    inspection, not mere relic-list correlation) to follow the `ResetFromScenario`
    natural-hook-then-overwrite mechanism (`CardDrawnEntry`, `CardGeneratedEntry` - see
    `_CONFIRMED_SOURCE_LIVE_STATE_INCONSISTENCY_ENTRY_TYPES`), or `capture_bug` for any
    other, unconfirmed dangling pattern (conservative default - e.g. a `PowerReceivedEntry`
    dangling reference is NOT assumed benign merely because a similar-looking one
    (Osty/`BOUND_PHYLACTERY`) turned out to be a genuine SnapshotBuilder gap rather than a
    live-state-inconsistency case)."""
    known = _collect_known_instance_ids(snapshot)
    duplicate_ids = sorted(k for k, paths in known.items() if len(paths) > 1)
    known_ids = set(known.keys())

    def _cause_for(entry_type: str) -> str:
        return "source_live_state_inconsistency" if entry_type in _CONFIRMED_SOURCE_LIVE_STATE_INCONSISTENCY_ENTRY_TYPES else "capture_bug"

    dangling: "list[DanglingReference]" = []
    for entry_index, entry in enumerate(snapshot.CombatHistory.Entries):
        entry_path_prefix = f"CombatHistory.Entries[{entry_index}]"
        if entry.ActorInstanceId is not None and entry.ActorInstanceId not in known_ids:
            dangling.append(DanglingReference(
                field_path=f"{entry_path_prefix}.ActorInstanceId", referenced_instance_id=entry.ActorInstanceId,
                entry_type=entry.EntryType, cause=_cause_for(entry.EntryType),
            ))
        for field_path, ref_id in _walk_instance_id_refs(entry.Fields, f"{entry_path_prefix}.Fields"):
            if ref_id not in known_ids:
                dangling.append(DanglingReference(
                    field_path=field_path, referenced_instance_id=ref_id, entry_type=entry.EntryType,
                    cause=_cause_for(entry.EntryType),
                ))

    # Powers'/Rng's own reference fields (outside CombatHistory) - these are not
    # expected to dangle under the known ResetFromScenario pattern, so any hit here is
    # always `capture_bug`.
    def _check_direct(instance_id: "str | None", path: str) -> None:
        if instance_id is not None and instance_id not in known_ids:
            dangling.append(DanglingReference(field_path=path, referenced_instance_id=instance_id, entry_type=None, cause="capture_bug"))

    for i, pw in enumerate(snapshot.Player.Powers):
        _check_direct(pw.ApplierInstanceId, f"Player.Powers[{i}].ApplierInstanceId")
        _check_direct(pw.TargetInstanceId, f"Player.Powers[{i}].TargetInstanceId")
    for pi, pet in enumerate(snapshot.Player.Pets):
        _check_direct(pet.OwnerInstanceId, f"Player.Pets[{pi}].OwnerInstanceId")
        for wi, pw in enumerate(pet.Powers):
            _check_direct(pw.ApplierInstanceId, f"Player.Pets[{pi}].Powers[{wi}].ApplierInstanceId")
            _check_direct(pw.TargetInstanceId, f"Player.Pets[{pi}].Powers[{wi}].TargetInstanceId")
    for ei, e in enumerate(snapshot.Enemies):
        for pi, pw in enumerate(e.Powers):
            _check_direct(pw.ApplierInstanceId, f"Enemies[{ei}].Powers[{pi}].ApplierInstanceId")
            _check_direct(pw.TargetInstanceId, f"Enemies[{ei}].Powers[{pi}].TargetInstanceId")
    for i, mr in enumerate(snapshot.Rng.MonsterRng):
        _check_direct(mr.OwnerInstanceId, f"Rng.MonsterRng[{i}].OwnerInstanceId")
    for i, pr in enumerate(snapshot.Rng.PlayerRng):
        _check_direct(pr.OwnerInstanceId, f"Rng.PlayerRng[{i}].OwnerInstanceId")

    return SnapshotReferenceReport(duplicate_instance_ids=duplicate_ids, dangling_references=dangling)


def restore_input_eligibility(snapshot: "CombatStateSnapshot") -> "tuple[bool, list[str]]":
    """Combined Phase-3-readiness gate. Does NOT mutate/override `Metadata.Completeness`
    (that field remains the Emulator's own verbatim verdict, per the never-upgrade
    principle) - this is a SEPARATE, additional check layered on top: even a Snapshot the
    Emulator marked `complete` is not eligible as Restore input if this Python-side
    reference-integrity check finds a dangling reference or duplicate stable ID, per this
    task's requirement ('dangling referenceが1件でも存在する場合completeness != complete
    とし、Phase 3 Restore入力として使用可能とは判定しない')."""
    reasons = []
    if not snapshot.completeness_is_complete():
        reasons.append(f"completeness={snapshot.Metadata.Completeness!r} (Emulator verdict, not 'complete')")
    ref_report = validate_snapshot_references(snapshot)
    if ref_report.duplicate_instance_ids:
        reasons.append(f"duplicate_instance_ids={ref_report.duplicate_instance_ids}")
    if ref_report.dangling_references:
        reasons.append(f"dangling_references={len(ref_report.dangling_references)} (see validate_snapshot_references for detail)")
    return (len(reasons) == 0, reasons)


_SCHEMA_PATH = r"C:\STS2_Emulator\docs\schemas\combat_state_snapshot.schema.json"
_schema_cache: "dict | None" = None


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _schema_cache = json.load(f)
    return _schema_cache


def schema_sha256() -> str:
    import hashlib
    with open(_SCHEMA_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def validate_against_formal_schema(snapshot_dict: dict) -> "list[str]":
    """STRICT conformance check against the formal, jointly-fixed JSON Schema
    (`docs/schemas/combat_state_snapshot.schema.json`, `additionalProperties: false`
    throughout) - this is deliberately STRICTER than `CombatStateSnapshot.from_dict`
    above (which stays lenient/record-not-reject, per the unchanged Phase 2A design).
    Returns a list of human-readable validation error messages (empty list = conforms).
    Used for this task's conformance verification and rejection tests - never called
    from `LiveCombatSession.capture_snapshot()`'s normal path."""
    import jsonschema

    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in validator.iter_errors(snapshot_dict)]
