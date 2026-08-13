from __future__ import annotations

import copy
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from API.dto import DTO_VERSION, MASK_VERSION  # noqa: E402
from API.masking import (  # noqa: E402
    _FORBIDDEN_KEY_SUBSTRINGS,
    build_masked_emulator_dto,
    mask_legal_actions,
)


def _find_forbidden_keys(node, forbidden_substrings) -> list[str]:
    hits: list[str] = []

    def _walk(current, path: str) -> None:
        if isinstance(current, dict):
            for key, value in current.items():
                if not isinstance(key, str):
                    continue
                next_path = f"{path}.{key}" if path else key
                lowered = key.lower()
                if any(substr in lowered for substr in forbidden_substrings):
                    hits.append(next_path)
                _walk(value, next_path)
        elif isinstance(current, list):
            for index, item in enumerate(current):
                next_path = f"{path}[{index}]"
                _walk(item, next_path)

    _walk(node, "")
    return hits


def _multiset_record(
    card_id: str,
    count: int,
    *,
    type_: str | None = None,
    rarity: str | None = None,
    cost: int | None = None,
    target_type: str | None = None,
    upgraded: bool = False,
    upgrade_level: int = 0,
    tinker_time_type: str | None = None,
    tinker_time_rider: str | None = None,
    enchantment: dict | None = None,
) -> dict:
    return {
        "id": card_id,
        "type": type_,
        "rarity": rarity,
        "cost": cost,
        "targetType": target_type,
        "upgraded": upgraded,
        "upgradeLevel": upgrade_level,
        "tinkerTimeType": tinker_time_type,
        "tinkerTimeRider": tinker_time_rider,
        "enchantment": enchantment,
        "count": count,
    }


def _build_raw_state() -> dict:
    return {
        "someField": 1,
        "hp": 42,
        "boundary": "stable",
        "hand": [{"id": "STRIKE"}],
        "legal_actions": [
            {
                "action_id": "A-1",
                "action_type": "end_turn",
                "parameters": {"cardId": "STRIKE", "target": "self"},
            },
            {
                "action_id": "A-2",
                "action_type": "play_card",
                "parameters": {"cardId": "DEFEND", "target": "enemy"},
            },
        ],
        "drawPile": [
            {"id": "STRIKE", "type": "Attack", "rarity": "Basic", "cost": 1, "targetType": "AnyEnemy"},
            {"id": "DEFEND", "type": "Skill", "rarity": "Basic", "cost": 1, "targetType": "Self"},
            {
                "id": "STRIKE",
                "type": "Attack",
                "rarity": "Basic",
                "cost": 1,
                "targetType": "AnyEnemy",
                "upgraded": True,
                "upgradeLevel": 1,
                "enchantment": {"id": "SHARP", "amount": 1, "status": "Normal"},
            },
        ],
        "discardPile": [{"id": "BASH"}, {"id": "BASH"}, {"id": "DEFEND"}],
        "exhaustPile": [{"id": "WOUND"}, {"id": "WOUND"}, {"id": "WOUND"}],
        "playPile": [{"id": "BLOCK"}],
        "reward": {"gold": 99},
        "Metrics": {
            "approved": False,
            "nested": {"unknown_fields": {"still_bad": True}},
        },
        "extras": {"unapproved": 1},
        "info": {"unapproved": 2},
        "snapshotBlob": "top-secret",
        "workerPid": 12345,
        "nested": {
            "generation": 2,
            "cursor": "hidden",
            "encounterQueue": [{"id": "HIDDEN"}],
            "listOfDicts": [
                {"session_id": "sess-1", "visible": True},
                {"keep": "ok", "unknown_fields": "still bad"},
            ],
        },
        "transition": {
            "kind": "combat_completed",
            "final_observation": {
                "snapshotBlob": "x",
                "seed": 5,
                "drawPile": [{"id": "STRIKE"}, {"id": "STRIKE"}, {"id": "DEFEND"}],
                "discardPile": [{"id": "DEFEND"}, {"id": "DEFEND"}],
                "exhaustPile": [{"id": "BASH"}],
                "playPile": [{"id": "SHIV"}],
                "reward": {"gold": 3},
                "Metrics": {"bad": True},
                "extras": {"bad": True},
                "info": {"bad": True},
                "boundary": "stable",
                "hp": 42,
                "hand": [{"id": "STRIKE"}],
                "legal_actions": [
                    {
                        "action_id": "L-1",
                        "action_type": "end_turn",
                        "parameters": {"cardId": "STRIKE"},
                    }
                ],
                "nested_list": [
                    {"context_id": "ctx-1"},
                    {"queueCursor": 1},
                ],
            },
        },
        "dto_version": "bogus",
        "mask_version": "bogus",
    }


def test_masked_emulator_dto_scrubs_forbidden_keys_everywhere_and_does_not_mutate_input():
    raw = _build_raw_state()
    pre_call = copy.deepcopy(raw)

    masked = build_masked_emulator_dto(raw)

    assert raw == pre_call
    assert _find_forbidden_keys(masked, _FORBIDDEN_KEY_SUBSTRINGS) == []

    final_observation = masked["transition"]["final_observation"]
    assert "snapshotBlob" not in final_observation
    assert "seed" not in final_observation
    assert isinstance(final_observation["drawPile"], list)
    assert final_observation["drawPile"] == [
        _multiset_record("DEFEND", 1),
        _multiset_record("STRIKE", 2),
    ]


def test_piles_reward_and_public_fields_are_masked_as_documented():
    masked = build_masked_emulator_dto(_build_raw_state())

    assert masked["drawPile"] == [
        _multiset_record("DEFEND", 1, type_="Skill", rarity="Basic", cost=1, target_type="Self"),
        _multiset_record("STRIKE", 1, type_="Attack", rarity="Basic", cost=1, target_type="AnyEnemy"),
        _multiset_record(
            "STRIKE",
            1,
            type_="Attack",
            rarity="Basic",
            cost=1,
            target_type="AnyEnemy",
            upgraded=True,
            upgrade_level=1,
            enchantment={"id": "SHARP", "amount": 1, "status": "Normal"},
        ),
    ]
    assert masked["discardPile"] == [
        _multiset_record("BASH", 2),
        _multiset_record("DEFEND", 1),
    ]
    assert masked["exhaustPile"] == [_multiset_record("WOUND", 3)]
    assert isinstance(masked["drawPile"], list)
    assert isinstance(masked["discardPile"], list)
    assert isinstance(masked["exhaustPile"], list)

    assert "playPile" not in masked
    assert "reward" not in masked
    assert "playPile" not in masked["transition"]["final_observation"]
    assert "reward" not in masked["transition"]["final_observation"]

    assert masked["hp"] == 42
    assert masked["hand"] == [{"id": "STRIKE"}]
    assert masked["legal_actions"] == [
        {
            "action_id": "A-1",
            "action_type": "end_turn",
            "parameters": {"cardId": "STRIKE", "target": "self"},
        },
        {
            "action_id": "A-2",
            "action_type": "play_card",
            "parameters": {"cardId": "DEFEND", "target": "enemy"},
        },
    ]
    assert masked["boundary"] == "stable"


def test_metrics_extras_info_are_reduced_to_empty_dicts_and_versions_are_present():
    masked = build_masked_emulator_dto(_build_raw_state())

    assert masked["Metrics"] == {}
    assert masked["extras"] == {}
    assert masked["info"] == {}

    final_observation = masked["transition"]["final_observation"]
    assert final_observation["Metrics"] == {}
    assert final_observation["extras"] == {}
    assert final_observation["info"] == {}

    assert masked["dto_version"] == DTO_VERSION
    assert masked["mask_version"] == MASK_VERSION


def test_mask_legal_actions_stringifies_action_ids_and_scrubs_nested_parameters():
    legal_actions = [
        {
            "action_id": 17,
            "action_type": "play_card",
            "parameters": {
                "cardId": "STRIKE",
                "target": "enemy",
                "snapshotBlob": "hidden",
                "seed": 123,
            },
        },
        {
            "action_id": 18,
            "action_type": "end_turn",
            "parameters": {"cardId": "DEFEND"},
        },
    ]

    masked = mask_legal_actions(legal_actions)

    assert masked[0]["action_id"] == "17"
    assert masked[1]["action_id"] == "18"
    assert "snapshotBlob" not in masked[0]["parameters"]
    assert "seed" not in masked[0]["parameters"]
    assert masked[0]["parameters"] == {"cardId": "STRIKE", "target": "enemy"}
    assert masked[1]["parameters"] == {"cardId": "DEFEND"}


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())


def test_pile_multiset_does_not_collapse_different_public_cost():
    masked = build_masked_emulator_dto({"drawPile": [
        {"id": "STRIKE", "type": "Attack", "rarity": "Basic", "cost": 0, "targetType": "AnyEnemy", "upgraded": False, "upgradeLevel": 0},
        {"id": "STRIKE", "type": "Attack", "rarity": "Basic", "cost": 1, "targetType": "AnyEnemy", "upgraded": False, "upgradeLevel": 0},
    ]})
    assert len(masked["drawPile"]) == 2
    assert sorted(record["cost"] for record in masked["drawPile"]) == [0, 1]
    assert all(record["count"] == 1 for record in masked["drawPile"])
