"""Visible cardInstanceId survives masking only at the already-public choice boundary."""

from API.masking import build_masked_emulator_dto


def test_visible_pending_and_choice_action_card_instance_ids_survive_masking_without_pile_leak() -> None:
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
                    "cardInstanceId": "card-000123",
                }
            ],
        },
        # Hidden pile order/instance identity must remain masked.
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
                "cardInstanceId": "card-000123",
            },
            "semantic_key": "opt-1:DEFEND_SILENT",
        }
    ]

    masked = build_masked_emulator_dto(raw_state, extra={"legal_actions": legal_actions})

    assert masked["pendingChoice"]["options"][0]["cardInstanceId"] == "card-000123"
    assert masked["legal_actions"][0]["parameters"]["cardInstanceId"] == "card-000123"
    assert "cardInstanceId" not in masked["drawPile"][0]
