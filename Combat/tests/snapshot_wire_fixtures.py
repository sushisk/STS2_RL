"""Raw wire fixtures used only by ``test_snapshot_wire_contract``.

Unlike ``snapshot_testkit``, this module intentionally knows concrete wire fields: its
purpose is to test CombatHistory/Power payload contract details. Restore-behavior tests
must not import this module.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
for _path in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from combat_state_snapshot import canonical_json  # noqa: E402
from emulator_bridge import ensure_loaded, shared_game_instance  # noqa: E402

COMBAT_HISTORY_ENTRY_TYPES = [
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

_MODEL_FIXTURES_CACHE = None


def model_fixtures() -> dict:
    global _MODEL_FIXTURES_CACHE
    if _MODEL_FIXTURES_CACHE is not None:
        return _MODEL_FIXTURES_CACHE

    ensure_loaded()
    shared_game_instance()
    from MegaCrit.Sts2.Core.Models import ModelDb

    power_classes = {
        "StrengthPower",
        "FeralPower",
        "AfterimagePower",
        "VigorPower",
        "PossessStrengthPower",
        "AutomationPower",
        "DampenPower",
    }
    powers = {}
    for power in ModelDb.AllPowers:
        class_name = str(power.GetType().Name)
        if class_name in power_classes:
            powers[class_name] = {
                "PowerId": str(power.Id.Entry),
                "StackType": str(power.StackType),
            }

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

    monsters = {
        str(monster.GetType().Name): str(monster.Id.Entry)
        for monster in ModelDb.Monsters
    }
    affliction_id = str(next(iter(ModelDb.DebugAfflictions)).Id.Entry)
    _MODEL_FIXTURES_CACHE = {
        "powers": powers,
        "potions": potions,
        "orbs": orbs,
        "monsters": monsters,
        "affliction_id": affliction_id,
    }
    return _MODEL_FIXTURES_CACHE


def all_card_payloads(snapshot_payload: dict) -> list[dict]:
    return list(snapshot_payload["Player"]["CardInstances"])


def all_power_payloads(snapshot_payload: dict) -> list[dict]:
    powers = list(snapshot_payload["Player"]["Powers"])
    for enemy in snapshot_payload["Enemies"]:
        powers.extend(enemy["Powers"])
    for pet in snapshot_payload["Player"]["Pets"]:
        powers.extend(pet["Powers"])
    return powers


def damage_result_fields(receiver_id: str, props: str = "Move") -> dict:
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


def card_play_fields(
    card_id: str,
    player_id: str,
    target_id: str | None,
    *,
    resources: dict | None = None,
) -> dict:
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


def history_entry(
    snapshot_payload: dict,
    entry_type: str,
    actor_id: str,
    round_number: int,
    side: str,
    fields: dict,
) -> dict:
    return {
        "EntryType": entry_type,
        "ActorInstanceId": actor_id,
        "RoundNumber": round_number,
        "CurrentSide": side,
        "PlayerTurnNumbers": {
            str(snapshot_payload["Player"]["NetId"]): int(snapshot_payload["TurnNumber"])
        },
        "Fields": fields,
    }


def power_fixture(
    class_name: str,
    instance_id: str,
    owner_id: str,
    internal_data: dict | None,
    *,
    amount: int = 5,
    amount_on_turn_start: int = 3,
    applier_id: str | None = None,
    target_id: str | None = None,
    associated_card_instance_id: str | None = None,
) -> dict:
    model = model_fixtures()["powers"][class_name]
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
        "AssociatedCardInstanceId": associated_card_instance_id,
        "HasUnsupportedInternalData": False,
        "InternalData": internal_data,
        "InternalDataSerializerVersion": (
            "generic-reflection-v1" if internal_data is not None else None
        ),
    }


def add_history_support_objects(snapshot_payload: dict) -> dict:
    payload = copy.deepcopy(snapshot_payload)
    models = model_fixtures()
    player = payload["Player"]
    enemy = payload["Enemies"][0]

    player["OrbSlotCapacity"] = max(1, int(player["OrbSlotCapacity"]))
    player["Orbs"] = [
        {
            "InstanceId": "orb-930000",
            "OrbId": models["orbs"]["LightningOrb"],
            "Index": 0,
            "BasePassiveValue": None,
            "BaseEvokeValue": None,
        }
    ]

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
        power_fixture(
            "StrengthPower",
            "power-930002",
            player["CreatureInstanceId"],
            None,
            amount=2,
            amount_on_turn_start=1,
            applier_id=enemy["InstanceId"],
        )
    ]
    return payload


def combat_history_entries(snapshot_payload: dict) -> list[dict]:
    models = model_fixtures()
    player = snapshot_payload["Player"]
    enemy = snapshot_payload["Enemies"][0]
    player_creature_id = player["CreatureInstanceId"]
    player_id = player["InstanceId"]
    enemy_id = enemy["InstanceId"]
    card = next(
        card
        for card in all_card_payloads(snapshot_payload)
        if "STRIKE" in card["CardId"].upper() or "DEFEND" in card["CardId"].upper()
    )
    card_id = card["InstanceId"]
    move_id = (enemy.get("Intent") or {}).get("stateId") or "UNSET_MOVE"

    entries = [
        history_entry(
            snapshot_payload,
            "CardPlayStartedEntry",
            player_creature_id,
            1,
            "Player",
            card_play_fields(card_id, player_id, enemy_id),
        ),
        history_entry(
            snapshot_payload,
            "CardPlayFinishedEntry",
            player_creature_id,
            1,
            "Player",
            {
                **card_play_fields(card_id, player_id, enemy_id),
                "wasEthereal": False,
            },
        ),
        history_entry(
            snapshot_payload,
            "CardAfflictedEntry",
            player_creature_id,
            1,
            "Player",
            {"cardInstanceId": card_id, "afflictionId": models["affliction_id"]},
        ),
        history_entry(
            snapshot_payload,
            "CardDiscardedEntry",
            player_creature_id,
            1,
            "Player",
            {"cardInstanceId": card_id},
        ),
        history_entry(
            snapshot_payload,
            "CardDrawnEntry",
            player_creature_id,
            1,
            "Player",
            {"cardInstanceId": card_id, "fromHandDraw": True},
        ),
        history_entry(
            snapshot_payload,
            "CardExhaustedEntry",
            player_creature_id,
            1,
            "Player",
            {"cardInstanceId": card_id},
        ),
        history_entry(
            snapshot_payload,
            "CardGeneratedEntry",
            player_creature_id,
            1,
            "Player",
            {"cardInstanceId": card_id, "creatorInstanceId": player_id},
        ),
        history_entry(
            snapshot_payload,
            "CreatureAttackedEntry",
            enemy_id,
            1,
            "Enemy",
            {"damageResults": [damage_result_fields(player_creature_id)]},
        ),
        history_entry(
            snapshot_payload,
            "DamageReceivedEntry",
            enemy_id,
            1,
            "Player",
            {
                "result": damage_result_fields(enemy_id),
                "dealerInstanceId": player_creature_id,
                "cardSourceInstanceId": card_id,
            },
        ),
        history_entry(
            snapshot_payload,
            "BlockGainedEntry",
            player_creature_id,
            1,
            "Player",
            {
                "amount": 7,
                "props": "Move",
                "cardPlay": card_play_fields(card_id, player_id, enemy_id),
            },
        ),
        history_entry(
            snapshot_payload,
            "EnergySpentEntry",
            player_creature_id,
            1,
            "Player",
            {"amount": 1},
        ),
        history_entry(
            snapshot_payload,
            "MonsterPerformedMoveEntry",
            enemy_id,
            1,
            "Enemy",
            {"moveId": move_id, "targetInstanceIds": [player_creature_id]},
        ),
        history_entry(
            snapshot_payload,
            "OrbChanneledEntry",
            player_creature_id,
            1,
            "Player",
            {"orbInstanceId": player["Orbs"][0]["InstanceId"]},
        ),
        history_entry(
            snapshot_payload,
            "PotionUsedEntry",
            player_creature_id,
            1,
            "Player",
            {
                "potionInstanceId": next(
                    potion for potion in player["Potions"] if potion is not None
                )["InstanceId"],
                "targetInstanceId": enemy_id,
            },
        ),
        history_entry(
            snapshot_payload,
            "PowerReceivedEntry",
            player_creature_id,
            1,
            "Enemy",
            {
                "powerInstanceId": player["Powers"][0]["InstanceId"],
                "amount": 2,
                "applierInstanceId": enemy_id,
            },
        ),
        history_entry(
            snapshot_payload,
            "StarsModifiedEntry",
            player_creature_id,
            2,
            "Player",
            {"amount": 2},
        ),
        history_entry(
            snapshot_payload,
            "SummonedEntry",
            player_creature_id,
            2,
            "Player",
            {"amount": 1},
        ),
    ]
    assert [entry["EntryType"] for entry in entries] == COMBAT_HISTORY_ENTRY_TYPES
    return entries


def history_fixture_payload(snapshot_payload: dict, *, entries: list[dict] | None = None) -> dict:
    payload = add_history_support_objects(snapshot_payload)
    payload["CombatHistory"]["Entries"] = (
        entries if entries is not None else combat_history_entries(payload)
    )
    return payload


def history_signature(snapshot_payload: dict) -> list[tuple]:
    return [
        (
            entry["EntryType"],
            int(entry["RoundNumber"]),
            entry["CurrentSide"],
            entry.get("ActorInstanceId"),
            canonical_json(entry["PlayerTurnNumbers"], exclude_volatile=False),
            canonical_json(entry["Fields"], exclude_volatile=False),
        )
        for entry in snapshot_payload["CombatHistory"]["Entries"]
    ]


def add_osty_pet(snapshot_payload: dict) -> dict:
    payload = copy.deepcopy(snapshot_payload)
    combat_ids = [enemy.get("CombatId") or 0 for enemy in payload["Enemies"]]
    payload["Player"]["Pets"] = [
        {
            "InstanceId": "creature-940000",
            "Kind": "pet",
            "OwnerInstanceId": payload["Player"]["InstanceId"],
            "CombatId": max(combat_ids or [0]) + 1,
            "MonsterId": model_fixtures()["monsters"].get("Osty", "OSTY"),
            "Name": "Osty",
            "Hp": 1,
            "MaxHp": 1,
            "Block": 0,
            "IsAlive": True,
            "SlotName": None,
            "Powers": [],
            "Intent": {"stateId": "UNSET_MOVE", "intentTypes": []},
            "StateLog": [],
        }
    ]
    return payload


def internal_data_for(class_name: str, snapshot_payload: dict) -> dict:
    card = all_card_payloads(snapshot_payload)[0]
    player_creature_id = snapshot_payload["Player"]["CreatureInstanceId"]
    enemy_id = snapshot_payload["Enemies"][0]["InstanceId"]
    return {
        "AutomationPower": {"cardsLeft": 7},
        "FeralPower": {"zeroCostAttacksPlayed": 4},
        "PossessStrengthPower": {"stolenStrength": {player_creature_id: -3}},
        "DampenPower": {
            "casters": [enemy_id],
            "downgradedCardsToOldUpgradeLevels": {card["InstanceId"]: 1},
        },
    }[class_name]
