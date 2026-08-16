"""Emulator cardInstanceId is RL-internal replay evidence, never a Training feature."""

from API.masking import build_masked_emulator_dto


def test_card_instance_ids_are_redacted_from_training_facing_choice_and_piles() -> None:
    raw_state = {
        "pendingChoice": {
            "choiceType": "TestCardChoice",
            "scope": "TopLevel",
            "scenarioRestorable": False,
            "minSelect": 1,
            "maxSelect": 1,
            "selectedCount": 0,
            "selectedOptionIds": [],
            "options": [
                {
                    "id": "DEFEND_SILENT",
                    "optionId": "opt-1",
                    "cardInstanceId": "cardv-0123456789abcdef0123456789abcdef",
                }
            ],
        },
        "drawPile": [
            {
                "id": "DEFEND_SILENT",
                "cardInstanceId": "card-hidden",
                "type": "Skill",
                "rarity": "Basic",
                "cost": 1,
                "targetType": "None",
                "upgraded": False,
                "upgradeLevel": 0,
            }
        ],
    }
    legal_actions = [
        {
            "action_id": 0,
            "action_type": "choice_card",
            "parameters": {
                "cardId": "DEFEND_SILENT",
                "optionId": "opt-1",
                "cardInstanceId": "cardv-0123456789abcdef0123456789abcdef",
            },
            "semantic_key": "opt-1:DEFEND_SILENT",
        }
    ]

    masked = build_masked_emulator_dto(raw_state, extra={"legal_actions": legal_actions})

    assert "cardInstanceId" not in masked["pendingChoice"]["options"][0]
    assert "cardInstanceId" not in masked["legal_actions"][0]["parameters"]
    assert "cardInstanceId" not in masked["drawPile"][0]
