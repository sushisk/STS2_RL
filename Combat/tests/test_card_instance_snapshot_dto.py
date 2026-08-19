from dataclasses import asdict
import sys
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from combat_state_snapshot import CardInstanceSnapshot


def test_phase3c8_card_instance_fields_are_preserved():
    payload = {
        "InstanceId": "card-000001", "CardId": "SOVEREIGN_BLADE",
        "Zone": "hand", "PileIndex": 0, "HasBeenRemovedFromState": False,
        "Type": "Attack", "Rarity": "Rare", "Cost": 2,
        "LocalCostModifiers": [], "TemporaryStarCosts": [],
        "TargetType": "AnyEnemy", "IsUpgraded": True, "UpgradeLevel": 1,
        "TinkerTimeType": None, "TinkerTimeRider": None,
        "DynamicVars": {"Damage": 17, "Forge": 3},
        "BaseReplayCount": 2, "BaseStarCost": 4, "LastStarsSpent": 1,
        "ExhaustOnNextPlay": True, "HasSingleTurnRetain": True,
        "HasSingleTurnSly": True, "Affliction": {"Id": "FRAIL", "Amount": 2, "Props": None},
        "CloneOfInstanceId": "card-000000", "IsDupe": True,
        "DeckVersionInstanceId": "deck-000001", "FloorAddedToDeck": 7,
        "SavedProperties": {"custom": "state"},
        "SovereignBladeCurrentDamage": 23.5,
        "SovereignBladeCurrentRepeats": 2.0,
        "SovereignBladeCreatedThroughForge": True,
    }

    snapshot = CardInstanceSnapshot.from_dict(payload)
    restored = asdict(snapshot)

    assert restored["DynamicVars"] == {"Damage": 17.0, "Forge": 3.0}
    for key in payload:
        if key not in {"LocalCostModifiers", "TemporaryStarCosts"}:
            assert restored[key] == payload[key]
