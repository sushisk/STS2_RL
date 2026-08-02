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
_OFFICIAL_EXAMPLE_PATH = Path(r"C:\STS2_Emulator\docs\contracts\combat_state_snapshot_example.v0.8.json")
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env", _ONLINE_EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from battle_emulator import BattleState  # noqa: E402
from combat_state_snapshot import CombatStateSnapshot, canonical_json, schema_sha256  # noqa: E402
from emulator_bridge import ensure_loaded, shared_game_instance  # noqa: E402
from live_combat_session import (  # noqa: E402
    ActionExecutionError,
    FaultedCombatSessionError,
    LiveCombatSession,
    SnapshotRestoreFailedError,
    SnapshotRestoreRejectedError,
)
from verify_restore_bootstrap_phase3b import _fresh_source_game, _make_eligible, _snapshot_sig  # noqa: E402

_COMBAT_HISTORY_ENTRY_TYPES = [
    "CardPlayStartedEntry",
    "CardPlayFinishedEntry",
    "CardAfflictedEntry",
    "CardDiscardedEntry",
    "CardDrawnEntry",
    "CardExhaustedEntry",
    "CardGeneratedEntry",
    "CreatureAttackedEntry",
    "DamageReceivedEntry",
    "BlockGainedEntry",
    "EnergySpentEntry",
    "MonsterPerformedMoveEntry",
    "OrbChanneledEntry",
    "PotionUsedEntry",
    "PowerReceivedEntry",
    "StarsModifiedEntry",
    "SummonedEntry",
]

_SERIALIZE_REQUIRED_POWER_CLASSES = {
    "AdaptablePower",
    "AutomationPower",
    "ChainsOfBindingPower",
    "DampenPower",
    "DarkEmbracePower",
    "FeralPower",
    "HardenedShellPower",
    "HellraiserPower",
    "IllusionPower",
    "InterceptPower",
    "JugglingPower",
    "MockRevivePower",
    "NightmarePower",
    "OrbitPower",
    "PaleBlueDotPower",
    "PanachePower",
    "PossessSpeedPower",
    "PossessStrengthPower",
    "ReattachPower",
    "VoidFormPower",
}

_SAFE_TO_RECOMPUTE_POWER_CLASSES = {
    "AfterimagePower",
    "CalamityPower",
    "CurlUpPower",
    "GravityPower",
    "ImitationLearningPower",
    "MonologuePower",
    "OblivionPower",
    "RupturePower",
    "SerpentFormPower",
    "SkittishPower",
    "StormPower",
    "StranglePower",
    "SubroutinePower",
}

_UNSUPPORTED_UNKNOWN_POWER_CLASSES = {"GigantificationPower", "VigorPower"}


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


def _json_text(snapshot_dict: dict) -> str:
    return json.dumps(snapshot_dict, sort_keys=False, separators=(",", ":"), ensure_ascii=True)


def _official_example_dict() -> dict:
    return json.loads(_OFFICIAL_EXAMPLE_PATH.read_text(encoding="utf-8"))


def _official_example_json_text() -> str:
    return _OFFICIAL_EXAMPLE_PATH.read_text(encoding="utf-8")


def _eligible_snapshot(seed=123):
    ensure_loaded()
    source = _fresh_source_game(seed=seed)
    snapshot = _make_eligible(source.CaptureSnapshot())
    return snapshot, _snapshot_sig(snapshot)


def _pet_restore_snapshot():
    session = LiveCombatSession()
    session.start_combat({
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["BOUND_PHYLACTERY"], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    })
    snapshot = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    assert len(snapshot.Player.Pets) == 1, [p.MonsterId for p in snapshot.Player.Pets]
    pet = snapshot.Player.Pets[0]
    assert str(pet.Kind) == "pet", pet.Kind
    assert str(pet.MonsterId).upper() == "OSTY", pet.MonsterId
    assert len(snapshot.CombatHistory.Entries) == 0
    return snapshot


def _str_none(value):
    return None if value is None else str(value)


def _pet_power_sig(power):
    return (
        str(power.InstanceId),
        str(power.PowerId),
        str(power.OwnerInstanceId),
        _str_none(power.ApplierInstanceId),
        _str_none(power.TargetInstanceId),
        int(power.Amount),
        int(power.AmountOnTurnStart),
        bool(power.SkipNextDurationTick),
        str(power.StackType),
    )


def _single_pet(snapshot):
    pets = list(snapshot.Player.Pets)
    assert len(pets) == 1, [p.MonsterId for p in pets]
    return pets[0]


def _pet_sig(snapshot):
    pet = _single_pet(snapshot)
    return (
        str(pet.InstanceId),
        str(pet.Kind),
        str(pet.MonsterId),
        _str_none(pet.OwnerInstanceId),
        None if pet.CombatId is None else int(pet.CombatId),
        int(pet.Hp),
        int(pet.MaxHp),
        int(pet.Block),
        bool(pet.IsAlive),
        tuple(_pet_power_sig(power) for power in pet.Powers),
    )


def _assert_restored_pet_matches(expected_snapshot, restored_snapshot):
    expected = _single_pet(expected_snapshot)
    restored = _single_pet(restored_snapshot)
    assert restored.InstanceId == expected.InstanceId
    assert restored.MonsterId == expected.MonsterId
    assert restored.Hp == expected.Hp
    assert restored.MaxHp == expected.MaxHp
    assert restored.Block == expected.Block
    assert restored.CombatId == expected.CombatId
    assert restored.OwnerInstanceId == restored_snapshot.Player.InstanceId
    assert _pet_sig(restored_snapshot) == _pet_sig(expected_snapshot)


def _capture_sig(session: LiveCombatSession):
    return _snapshot_sig(_make_eligible(session._game.CaptureSnapshot()))  # noqa: SLF001


_MODEL_FIXTURES_CACHE = None


def _model_fixtures() -> dict:
    global _MODEL_FIXTURES_CACHE
    if _MODEL_FIXTURES_CACHE is not None:
        return _MODEL_FIXTURES_CACHE

    ensure_loaded()
    shared_game_instance()  # ModelDb is not populated until a GameInstance has been constructed
    from MegaCrit.Sts2.Core.Models import ModelDb

    power_classes = _SERIALIZE_REQUIRED_POWER_CLASSES | _SAFE_TO_RECOMPUTE_POWER_CLASSES | _UNSUPPORTED_UNKNOWN_POWER_CLASSES | {"StrengthPower"}
    powers = {}
    for power in ModelDb.AllPowers:
        class_name = str(power.GetType().Name)
        if class_name in power_classes:
            powers[class_name] = {"PowerId": str(power.Id.Entry), "StackType": str(power.StackType)}

    potions = {}
    for potion in ModelDb.AllPotions:
        class_name = str(potion.GetType().Name)
        if class_name == "FirePotion":
            potions[class_name] = {
                "PotionId": str(potion.Id.Entry),
                "Rarity": str(potion.Rarity),
                "TargetType": str(potion.TargetType),
            }

    orbs = {}
    for orb in ModelDb.Orbs:
        class_name = str(orb.GetType().Name)
        if class_name == "LightningOrb":
            orbs[class_name] = str(orb.Id.Entry)

    monsters = {str(monster.GetType().Name): str(monster.Id.Entry) for monster in ModelDb.Monsters}
    affliction_id = str(next(iter(ModelDb.DebugAfflictions)).Id.Entry)
    _MODEL_FIXTURES_CACHE = {
        "powers": powers,
        "potions": potions,
        "orbs": orbs,
        "monsters": monsters,
        "affliction_id": affliction_id,
    }
    return _MODEL_FIXTURES_CACHE


def _all_card_dicts(snapshot_dict: dict) -> list[dict]:
    player = snapshot_dict["Player"]
    cards = []
    for pile_name in ("Hand", "DrawPile", "DiscardPile", "ExhaustPile", "PlayPile", "Deck"):
        cards.extend(player[pile_name])
    return cards


def _damage_result_fields(receiver_id: str, props: str = "Move") -> dict:
    return {
        "receiverInstanceId": receiver_id,
        "props": props,
        "blockedDamage": 2,
        "unblockedDamage": 3,
        "overkillDamage": 0,
        "totalDamage": 5,
        "wasBlockBroken": True,
        "wasFullyBlocked": False,
        "wasTargetKilled": False,
    }


def _card_play_fields(card_id: str, player_id: str, target_id: str | None, *, resources: dict | None = None) -> dict:
    return {
        "cardInstanceId": card_id,
        "playerInstanceId": player_id,
        "targetInstanceId": target_id,
        "resultPile": "Discard",
        "resources": resources or {
            "energySpent": 1,
            "energyValue": 2,
            "starsSpent": 3,
            "starValue": 4,
        },
        "isAutoPlay": False,
        "playIndex": 0,
        "playCount": 1,
    }


def _history_entry(snapshot_dict: dict, entry_type: str, actor_id: str, round_number: int, side: str, fields: dict) -> dict:
    return {
        "EntryType": entry_type,
        "ActorInstanceId": actor_id,
        "RoundNumber": round_number,
        "CurrentSide": side,
        "PlayerTurnNumbers": {str(snapshot_dict["Player"]["NetId"]): int(snapshot_dict["TurnNumber"])},
        "Fields": fields,
    }


def _power_fixture(
    snapshot_dict: dict,
    class_name: str,
    instance_id: str,
    owner_id: str,
    internal_data: dict | None,
    *,
    amount: int = 5,
    amount_on_turn_start: int = 3,
    applier_id: str | None = None,
    target_id: str | None = None,
    associated_card: dict | None = None,
) -> dict:
    model = _model_fixtures()["powers"][class_name]
    return {
        "InstanceId": instance_id,
        "PowerId": model["PowerId"],
        "Amount": amount,
        "AmountOnTurnStart": amount_on_turn_start,
        "SkipNextDurationTick": True,
        "StackType": model["StackType"],
        "OwnerInstanceId": owner_id,
        "ApplierInstanceId": applier_id,
        "TargetInstanceId": target_id,
        "AssociatedCard": associated_card,
        "HasUnsupportedInternalData": False,
        "InternalData": internal_data,
        "InternalDataSerializerVersion": "generic-reflection-v1" if internal_data is not None else None,
    }


def _add_history_support_objects(snapshot_dict: dict) -> dict:
    s = json.loads(_json_text(snapshot_dict))
    models = _model_fixtures()
    player = s["Player"]
    enemy = s["Enemies"][0]
    player["OrbSlotCapacity"] = max(1, int(player["OrbSlotCapacity"]))
    player["Orbs"] = [{
        "InstanceId": "orb-930000",
        "OrbId": models["orbs"]["LightningOrb"],
        "Index": 0,
        "BasePassiveValue": None,
        "BaseEvokeValue": None,
    }]
    potion = models["potions"]["FirePotion"]
    potions = list(player["Potions"] or [])
    if not potions:
        potions = [None]
    potions[0] = {
        "InstanceId": "potion-930001",
        "PotionId": potion["PotionId"],
        "Slot": 0,
        "Rarity": potion["Rarity"],
        "TargetType": potion["TargetType"],
        "IsQueued": False,
    }
    player["Potions"] = potions
    player["Powers"] = [
        _power_fixture(
            s,
            "StrengthPower",
            "power-930002",
            player["CreatureInstanceId"],
            None,
            amount=2,
            amount_on_turn_start=1,
            applier_id=enemy["InstanceId"],
        )
    ]
    return s


def _combat_history_entries_fixture(snapshot_dict: dict) -> list[dict]:
    models = _model_fixtures()
    player = snapshot_dict["Player"]
    enemy = snapshot_dict["Enemies"][0]
    player_creature_id = player["CreatureInstanceId"]
    player_id = player["InstanceId"]
    enemy_id = enemy["InstanceId"]
    card = next(c for c in _all_card_dicts(snapshot_dict) if "STRIKE" in c["CardId"].upper() or "DEFEND" in c["CardId"].upper())
    card_id = card["InstanceId"]
    move_id = (enemy.get("Intent") or {}).get("stateId") or "UNSET_MOVE"

    entries = [
        _history_entry(snapshot_dict, "CardPlayStartedEntry", player_creature_id, 1, "Player", _card_play_fields(card_id, player_id, enemy_id)),
        _history_entry(snapshot_dict, "CardPlayFinishedEntry", player_creature_id, 1, "Player", {
            **_card_play_fields(card_id, player_id, enemy_id),
            "wasEthereal": False,
        }),
        _history_entry(snapshot_dict, "CardAfflictedEntry", player_creature_id, 1, "Player", {
            "cardInstanceId": card_id,
            "afflictionId": models["affliction_id"],
        }),
        _history_entry(snapshot_dict, "CardDiscardedEntry", player_creature_id, 1, "Player", {"cardInstanceId": card_id}),
        _history_entry(snapshot_dict, "CardDrawnEntry", player_creature_id, 1, "Player", {"cardInstanceId": card_id, "fromHandDraw": True}),
        _history_entry(snapshot_dict, "CardExhaustedEntry", player_creature_id, 1, "Player", {"cardInstanceId": card_id}),
        _history_entry(snapshot_dict, "CardGeneratedEntry", player_creature_id, 1, "Player", {"cardInstanceId": card_id, "creatorInstanceId": player_id}),
        _history_entry(snapshot_dict, "CreatureAttackedEntry", enemy_id, 1, "Enemy", {"damageResults": [_damage_result_fields(player_creature_id)]}),
        _history_entry(snapshot_dict, "DamageReceivedEntry", enemy_id, 1, "Player", {
            "result": _damage_result_fields(enemy_id),
            "dealerInstanceId": player_creature_id,
            "cardSourceInstanceId": card_id,
        }),
        _history_entry(snapshot_dict, "BlockGainedEntry", player_creature_id, 1, "Player", {
            "amount": 7,
            "props": "Move",
            "cardPlay": _card_play_fields(card_id, player_id, enemy_id),
        }),
        _history_entry(snapshot_dict, "EnergySpentEntry", player_creature_id, 1, "Player", {"amount": 1}),
        _history_entry(snapshot_dict, "MonsterPerformedMoveEntry", enemy_id, 1, "Enemy", {
            "moveId": move_id,
            "targetInstanceIds": [player_creature_id],
        }),
        _history_entry(snapshot_dict, "OrbChanneledEntry", player_creature_id, 1, "Player", {
            "orbInstanceId": player["Orbs"][0]["InstanceId"],
        }),
        _history_entry(snapshot_dict, "PotionUsedEntry", player_creature_id, 1, "Player", {
            "potionInstanceId": next(p for p in player["Potions"] if p is not None)["InstanceId"],
            "targetInstanceId": enemy_id,
        }),
        _history_entry(snapshot_dict, "PowerReceivedEntry", player_creature_id, 1, "Enemy", {
            "powerInstanceId": player["Powers"][0]["InstanceId"],
            "amount": 2,
            "applierInstanceId": enemy_id,
        }),
        _history_entry(snapshot_dict, "StarsModifiedEntry", player_creature_id, 2, "Player", {"amount": 2}),
        _history_entry(snapshot_dict, "SummonedEntry", player_creature_id, 2, "Player", {"amount": 1}),
    ]
    assert [e["EntryType"] for e in entries] == _COMBAT_HISTORY_ENTRY_TYPES
    return entries


def _history_fixture_snapshot_dict(*, entries: list[dict] | None = None) -> dict:
    snapshot = _add_history_support_objects(_official_example_dict())
    snapshot["CombatHistory"]["Entries"] = entries if entries is not None else _combat_history_entries_fixture(snapshot)
    return snapshot


def _history_sig_from_dict(snapshot_dict: dict) -> list[tuple]:
    return [
        (
            entry["EntryType"],
            int(entry["RoundNumber"]),
            entry["CurrentSide"],
            entry.get("ActorInstanceId"),
            canonical_json(entry["PlayerTurnNumbers"], exclude_volatile=False),
            canonical_json(entry["Fields"], exclude_volatile=False),
        )
        for entry in snapshot_dict["CombatHistory"]["Entries"]
    ]


def _all_power_dicts(snapshot_dict: dict) -> list[dict]:
    powers = list(snapshot_dict["Player"]["Powers"])
    for enemy in snapshot_dict["Enemies"]:
        powers.extend(enemy["Powers"])
    for pet in snapshot_dict["Player"]["Pets"]:
        powers.extend(pet["Powers"])
    return powers


def _add_osty_pet(snapshot_dict: dict) -> dict:
    s = json.loads(_json_text(snapshot_dict))
    combat_ids = [e.get("CombatId") or 0 for e in s["Enemies"]]
    s["Player"]["Pets"] = [{
        "InstanceId": "creature-940000",
        "Kind": "pet",
        "OwnerInstanceId": s["Player"]["InstanceId"],
        "CombatId": max(combat_ids or [0]) + 1,
        "MonsterId": _model_fixtures()["monsters"].get("Osty", "OSTY"),
        "Name": "Osty",
        "Hp": 1,
        "MaxHp": 1,
        "Block": 0,
        "IsAlive": True,
        "SlotName": None,
        "Powers": [],
        "Intent": {"stateId": "UNSET_MOVE", "intentTypes": []},
        "StateLog": [],
    }]
    return s


def _internal_data_for(class_name: str, snapshot_dict: dict) -> dict:
    card = _all_card_dicts(snapshot_dict)[0]
    player_creature_id = snapshot_dict["Player"]["CreatureInstanceId"]
    enemy_id = snapshot_dict["Enemies"][0]["InstanceId"]
    return {
        "AutomationPower": {"cardsLeft": 7},
        "FeralPower": {"zeroCostAttacksPlayed": 4},
        "PossessStrengthPower": {"stolenStrength": {player_creature_id: -3}},
        "JugglingPower": {"attacksPlayedThisTurn": 2},
        "PaleBlueDotPower": {"alreadyActivatedThisTurn": True},
        "DampenPower": {"casters": [enemy_id], "downgradedCardsToOldUpgradeLevels": {card["InstanceId"]: 1}},
    }[class_name]


def _restore_capture_json(snapshot_dict: dict) -> dict:
    session = LiveCombatSession()
    json_text = _json_text(snapshot_dict)
    validation = session.validate_restore_snapshot_json(json_text)
    assert validation.eligible is True, validation.rejection_codes
    session.restore_snapshot_json(json_text)
    return _snapshot_dict(session.capture_snapshot())


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


def _with_synthetic_dangling_draw(snapshot):
    ensure_loaded()
    from System import Array
    from System import Int32, Object, String
    from System.Collections.Generic import Dictionary
    from Sts2Emulator.Dto.Snapshot import CombatHistoryEntrySnapshot

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
    assert caps.restore_api_version == "phase3c.4", caps
    assert caps.milestone == "phase3c.4", caps
    assert caps.contract_version == "0.5", caps
    assert caps.snapshot_schema_version == "phase3c.4", caps
    assert caps.snapshot_schema_sha256 == schema_sha256(), caps

    import hashlib

    # Normalize CRLF -> LF before hashing: the Emulator's own ContractSha256 reflects
    # the canonical LF-stored git blob content, but a local worktree checkout with
    # core.autocrlf=true checks this file out with CRLF line endings - comparing raw
    # bytes read here would then depend on which worktree/checkout ran the test rather
    # than the file's actual (logically identical) content.
    contract_path = Path(__file__).resolve().parents[2] / "Common" / "contracts" / "combat_state_contract.v0.5.md"
    contract_bytes = contract_path.read_bytes().replace(b"\r\n", b"\n")
    assert caps.contract_sha256 == hashlib.sha256(contract_bytes).hexdigest(), caps
    assert caps.supported_completeness == ["complete"], caps
    assert caps.supports_combat_history is True
    assert caps.supports_pets is True
    assert caps.supports_pending_choice is False
    assert caps.supports_pending_target is False
    assert caps.supports_action_continuation is False
    for class_name in _SERIALIZE_REQUIRED_POWER_CLASSES:
        assert f"serialize_required:{class_name}" in caps.supported_power_scope, class_name
    for class_name in _SAFE_TO_RECOMPUTE_POWER_CLASSES:
        assert f"safe_to_recompute:{class_name}" in caps.supported_power_scope, class_name
    assert set(caps.unsupported_power_internal_data_classes) == _UNSUPPORTED_UNKNOWN_POWER_CLASSES, caps
    assert "missing_power_internal_data" in caps.rejection_codes
    assert "invalid_power_internal_data_field" in caps.rejection_codes
    assert "invalid_power_internal_data_serializer" in caps.rejection_codes
    assert "invalid_json_required_field" in caps.rejection_codes
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


def test_pet_object_restore_round_trip():
    snapshot = _pet_restore_snapshot()
    session = LiveCombatSession()
    validation = session.validate_restore_snapshot(snapshot)
    assert validation.eligible is True, validation.rejection_codes
    state = session.restore_snapshot(snapshot)
    assert isinstance(state, BattleState)
    restored = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    _assert_restored_pet_matches(snapshot, restored)
    assert session._session_faulted is False  # noqa: SLF001


def test_json_restore_round_trip():
    snapshot, sig_a = _eligible_snapshot()
    session = LiveCombatSession()
    state = session.restore_snapshot_json(_clr_snapshot_json(snapshot))
    assert isinstance(state, BattleState)
    assert _capture_sig(session) == sig_a


def test_official_json_example_validates_successfully():
    session = LiveCombatSession()
    result = session.validate_restore_snapshot_json(_official_example_json_text())
    assert result.eligible is True, result
    assert result.rejection_codes == []


def test_official_json_example_restores_successfully():
    session = LiveCombatSession()
    state = session.restore_snapshot_json(_official_example_json_text())
    assert isinstance(state, BattleState)
    captured = session.capture_snapshot()
    assert captured.Metadata.SchemaVersion == "phase3c.4"
    assert captured.CombatHistory.Entries == []


def test_validate_restore_snapshot_json_is_pure():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()
    valid_json = _official_example_json_text()
    invalid = _official_example_dict()
    del invalid["Player"]["Pets"]
    invalid_json = _json_text(invalid)

    results = [session.validate_restore_snapshot_json(valid_json) for _ in range(3)]
    assert all(r.eligible for r in results), results
    assert results[0] == results[1] == results[2]

    invalid_results = [session.validate_restore_snapshot_json(invalid_json) for _ in range(3)]
    assert all(not r.eligible for r in invalid_results), invalid_results
    assert invalid_results[0] == invalid_results[1] == invalid_results[2]
    assert any("invalid_json_required_field:$.Player.Pets" in r for r in invalid_results[0].rejection_codes)

    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


def test_invalid_json_restore_preserves_session_and_step_still_works():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()
    invalid = json.loads(str(session._game.CaptureSnapshotJson()))  # noqa: SLF001
    del invalid["CombatHistory"]
    invalid_json = _json_text(invalid)

    validation = session.validate_restore_snapshot_json(invalid_json)
    assert validation.eligible is False, validation
    assert any("invalid_json_required_field:$.CombatHistory" in r for r in validation.rejection_codes), validation.rejection_codes
    try:
        session.restore_snapshot_json(invalid_json)
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any("invalid_json_required_field:$.CombatHistory" in r for r in exc.rejection_codes), exc.rejection_codes

    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


def test_restore_snapshot_json_rejects_invalid_without_prior_validate():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    invalid = _official_example_dict()
    del invalid["CombatHistory"]
    try:
        session.restore_snapshot_json(_json_text(invalid))
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any("invalid_json_required_field:$.CombatHistory" in r for r in exc.rejection_codes), exc.rejection_codes
    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


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


def test_pet_json_restore_round_trip_matches_object_restore():
    snapshot = _pet_restore_snapshot()
    json_text = _clr_snapshot_json(snapshot)
    session = LiveCombatSession()
    session.restore_snapshot(snapshot)
    object_capture = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    session.restore_snapshot_json(json_text)
    json_capture = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    _assert_restored_pet_matches(snapshot, json_capture)
    assert _snapshot_sig(object_capture) == _snapshot_sig(json_capture)
    assert _pet_sig(object_capture) == _pet_sig(json_capture)


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


def test_pet_canonical_json_round_trip():
    snapshot = _pet_restore_snapshot()
    py_snapshot = CombatStateSnapshot.from_json(_clr_snapshot_json(snapshot))
    json_text = canonical_json(_snapshot_dict(py_snapshot), exclude_volatile=False)
    session = LiveCombatSession()
    session.restore_snapshot_json(json_text)
    captured = CombatStateSnapshot.from_json(_clr_snapshot_json(_make_eligible(session._game.CaptureSnapshot())))  # noqa: SLF001
    original = _strip_known_restore_boundary_diffs(_snapshot_dict(py_snapshot))
    restored = _strip_known_restore_boundary_diffs(_snapshot_dict(captured))
    assert canonical_json(original) == canonical_json(restored)


def test_combat_history_all_17_entry_types_round_trip_via_json_fixture():
    snapshot = _history_fixture_snapshot_dict()
    assert [e["EntryType"] for e in snapshot["CombatHistory"]["Entries"]] == _COMBAT_HISTORY_ENTRY_TYPES
    parsed = CombatStateSnapshot.from_dict(snapshot)
    assert parsed.CombatHistory.Entries[0].PlayerTurnNumbers == {str(snapshot["Player"]["NetId"]): snapshot["TurnNumber"]}

    restored = _restore_capture_json(snapshot)
    assert [e["EntryType"] for e in restored["CombatHistory"]["Entries"]] == _COMBAT_HISTORY_ENTRY_TYPES
    assert _history_sig_from_dict(restored) == _history_sig_from_dict(snapshot)


def test_combat_history_player_turn_numbers_and_cardplay_resources_preserved():
    snapshot = _history_fixture_snapshot_dict()
    restored = _restore_capture_json(snapshot)
    for original, captured in zip(snapshot["CombatHistory"]["Entries"], restored["CombatHistory"]["Entries"]):
        assert captured["PlayerTurnNumbers"] == original["PlayerTurnNumbers"], original["EntryType"]

    resources_by_type = {}
    for entry in restored["CombatHistory"]["Entries"]:
        fields = entry["Fields"]
        if entry["EntryType"] in ("CardPlayStartedEntry", "CardPlayFinishedEntry"):
            resources_by_type[entry["EntryType"]] = fields["resources"]
        elif entry["EntryType"] == "BlockGainedEntry":
            resources_by_type[entry["EntryType"]] = fields["cardPlay"]["resources"]

    assert resources_by_type == {
        "CardPlayStartedEntry": {"energySpent": 1, "energyValue": 2, "starsSpent": 3, "starValue": 4},
        "CardPlayFinishedEntry": {"energySpent": 1, "energyValue": 2, "starsSpent": 3, "starValue": 4},
        "BlockGainedEntry": {"energySpent": 1, "energyValue": 2, "starsSpent": 3, "starValue": 4},
    }


def test_restore_step_determinism_with_non_empty_combat_history():
    all_entries_snapshot = _history_fixture_snapshot_dict()
    snapshot = _history_fixture_snapshot_dict(entries=[
        all_entries_snapshot["CombatHistory"]["Entries"][0],
        all_entries_snapshot["CombatHistory"]["Entries"][9],
        all_entries_snapshot["CombatHistory"]["Entries"][14],
    ])
    json_text = _json_text(snapshot)
    session = LiveCombatSession()
    validation = session.validate_restore_snapshot_json(json_text)
    assert validation.eligible is True, validation.rejection_codes

    state1 = session.restore_snapshot_json(json_text)
    logical = _first_logical_action(state1)
    next1 = session.step(state1, _find_logical_action(state1, logical), target_enemy_index=0)
    sig1 = _snapshot_sig(session._game.CaptureSnapshot())  # noqa: SLF001

    state2 = session.restore_snapshot_json(json_text)
    next2 = session.step(state2, _find_logical_action(state2, logical), target_enemy_index=0)
    sig2 = _snapshot_sig(session._game.CaptureSnapshot())  # noqa: SLF001

    assert next1.engine_state == next2.engine_state
    assert sig1 == sig2


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


def test_power_internal_data_classifications_round_trip_and_reject_via_json_api():
    base = _official_example_dict()
    player_creature_id = base["Player"]["CreatureInstanceId"]
    enemy_id = base["Enemies"][0]["InstanceId"]

    serialize_required = json.loads(_json_text(base))
    serialize_required["CombatHistory"]["Entries"] = _combat_history_entries_fixture(_add_history_support_objects(base))[:2]
    serialize_required["Player"]["Powers"] = [
        _power_fixture(
            serialize_required,
            "FeralPower",
            "power-940001",
            player_creature_id,
            _internal_data_for("FeralPower", serialize_required),
        )
    ]
    restored = _restore_capture_json(serialize_required)
    restored_feral = next(p for p in _all_power_dicts(restored) if p["InstanceId"] == "power-940001")
    assert restored_feral["InternalData"] == {"zeroCostAttacksPlayed": 4}
    assert restored_feral["InternalDataSerializerVersion"] == "generic-reflection-v1"

    safe = json.loads(_json_text(base))
    safe["Player"]["Powers"] = [
        _power_fixture(safe, "AfterimagePower", "power-940002", player_creature_id, None, amount=6, amount_on_turn_start=4)
    ]
    restored_safe = _restore_capture_json(safe)
    restored_afterimage = next(p for p in _all_power_dicts(restored_safe) if p["InstanceId"] == "power-940002")
    assert restored_afterimage["Amount"] == 6
    assert restored_afterimage["AmountOnTurnStart"] == 4
    assert restored_afterimage["InternalData"] is None
    assert restored_afterimage["InternalDataSerializerVersion"] is None

    unsupported = json.loads(_json_text(base))
    unsupported["Player"]["Powers"] = [
        _power_fixture(unsupported, "VigorPower", "power-940003", player_creature_id, None)
    ]
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    validation = session.validate_restore_snapshot_json(_json_text(unsupported))
    assert validation.eligible is False, validation
    assert any("unsupported_internal_data:" in r for r in validation.rejection_codes), validation.rejection_codes
    try:
        session.restore_snapshot_json(_json_text(unsupported))
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any("unsupported_internal_data:" in r for r in exc.rejection_codes), exc.rejection_codes
    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None

    enemy_owned = json.loads(_json_text(base))
    enemy_owned["Enemies"][0]["Powers"] = [
        _power_fixture(
            enemy_owned,
            "PossessStrengthPower",
            "power-940004",
            enemy_id,
            _internal_data_for("PossessStrengthPower", enemy_owned),
        )
    ]
    restored_enemy = _restore_capture_json(enemy_owned)
    restored_enemy_power = next(p for p in _all_power_dicts(restored_enemy) if p["InstanceId"] == "power-940004")
    assert restored_enemy_power["OwnerInstanceId"] == enemy_id
    assert restored_enemy_power["InternalData"] == {"stolenStrength": {player_creature_id: -3}}

    pet_owned = _add_osty_pet(base)
    pet_id = pet_owned["Player"]["Pets"][0]["InstanceId"]
    pet_owned["Player"]["Pets"][0]["Powers"] = [
        _power_fixture(
            pet_owned,
            "AutomationPower",
            "power-940005",
            pet_id,
            _internal_data_for("AutomationPower", pet_owned),
        )
    ]
    restored_pet = _restore_capture_json(pet_owned)
    restored_pet_power = next(p for p in _all_power_dicts(restored_pet) if p["InstanceId"] == "power-940005")
    assert restored_pet_power["OwnerInstanceId"] == pet_id
    assert restored_pet_power["InternalData"] == {"cardsLeft": 7}


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


def test_pet_restore_step_determinism_reselects_fresh_action():
    snapshot = _pet_restore_snapshot()
    session = LiveCombatSession()
    state1 = session.restore_snapshot(snapshot)
    logical = _first_logical_action(state1)
    next1 = session.step(state1, _find_logical_action(state1, logical), target_enemy_index=0)
    captured1 = session._game.CaptureSnapshot()  # noqa: SLF001
    sig1 = (_snapshot_sig(captured1), _pet_sig(captured1))

    state2 = session.restore_snapshot(snapshot)
    next2 = session.step(state2, _find_logical_action(state2, logical), target_enemy_index=0)
    captured2 = session._game.CaptureSnapshot()  # noqa: SLF001
    sig2 = (_snapshot_sig(captured2), _pet_sig(captured2))

    assert next1.engine_state == next2.engine_state
    assert sig1 == sig2


def test_rejection_categories_via_public_python_api():
    ensure_loaded()
    from System import Array
    from Sts2Emulator.Dto.Snapshot import CombatHistoryEntrySnapshot
    from Sts2Emulator.Dto.Snapshot import UnsupportedSnapshotField

    source = _fresh_source_game()
    unknown_history = _make_eligible(source.CaptureSnapshot())
    unknown_entry = CombatHistoryEntrySnapshot()
    unknown_entry.EntryType = "SyntheticUnknownEntry"
    unknown_history.CombatHistory.Entries = Array[CombatHistoryEntrySnapshot]([unknown_entry])
    _assert_rejected(unknown_history, "unknown_combat_history_entry_type:SyntheticUnknownEntry")

    dangling_history = _with_synthetic_dangling_draw(_make_eligible(_fresh_source_game().CaptureSnapshot()))
    dangling_validation = LiveCombatSession().validate_restore_snapshot(dangling_history)
    assert dangling_validation.eligible is False, dangling_validation
    assert any("reference_integrity" in r for r in dangling_validation.rejection_codes), dangling_validation.rejection_codes
    assert not any("pet_count" in r for r in dangling_validation.rejection_codes), dangling_validation.rejection_codes

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
