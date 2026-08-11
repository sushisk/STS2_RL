from __future__ import annotations

import copy
import sys
import traceback
from pathlib import Path

_COMBAT = Path(__file__).resolve().parents[1]
if str(_COMBAT) not in sys.path:
    sys.path.insert(0, str(_COMBAT))

from battle_emulator import BattleState, battle_state_key  # noqa: E402


def _state(*, operation: str, selected_ids: list[str], order_matters: bool, second_option_id: str = "opt-2") -> BattleState:
    pending = {
        "choiceType": "card_selection",
        "scope": "ActionContinuation",
        "scenarioRestorable": False,
        "minSelect": 1,
        "maxSelect": 2,
        "selectedCount": len(selected_ids),
        "choiceSemantics": {
            "version": 1,
            "operation": operation,
            "effect": "move" if operation == "discard" else "modify",
            "sourceZone": "hand",
            "destinationZone": "discard" if operation == "discard" else None,
            "modifier": "upgrade" if operation == "upgrade" else None,
            "orderMatters": order_matters,
            "replacementAllowed": False,
        },
        "sourceEffectId": f"CARD_EFFECT.{operation.upper()}",
        "selectedOptionIds": selected_ids,
        "options": [
            {"id": "STRIKE", "upgraded": False, "optionId": "opt-1"},
            {"id": "STRIKE", "upgraded": True, "optionId": second_option_id},
        ],
    }
    return BattleState(
        engine_state={
            "hp": 80,
            "maxHp": 80,
            "block": 0,
            "energy": 3,
            "stars": 0,
            "pendingChoice": pending,
            "enemies": [],
            "relics": [],
        },
        is_terminal=False,
        outcome="in_progress",
        turn=1,
    )


def test_battle_state_key_distinguishes_choice_operation():
    discard = _state(operation="discard", selected_ids=["opt-1"], order_matters=True)
    upgrade = _state(operation="upgrade", selected_ids=["opt-1"], order_matters=True)
    assert battle_state_key(discard) != battle_state_key(upgrade)


def test_battle_state_key_distinguishes_duplicate_card_option_identity():
    left = _state(operation="discard", selected_ids=["opt-1"], order_matters=True)
    right = _state(
        operation="discard",
        selected_ids=["opt-1"],
        order_matters=True,
        second_option_id="opt-99",
    )
    assert battle_state_key(left) != battle_state_key(right)


def test_battle_state_key_canonicalizes_selected_set_only_when_order_is_irrelevant():
    left = _state(
        operation="discard",
        selected_ids=["opt-1", "opt-2"],
        order_matters=False,
    )
    right = _state(
        operation="discard",
        selected_ids=["opt-2", "opt-1"],
        order_matters=False,
    )
    assert battle_state_key(left) == battle_state_key(right)

    left.engine_state["pendingChoice"]["choiceSemantics"]["orderMatters"] = True
    right.engine_state["pendingChoice"]["choiceSemantics"]["orderMatters"] = True
    assert battle_state_key(left) != battle_state_key(right)


def test_battle_state_key_keeps_unknown_future_operations_distinct():
    left = _state(operation="discard", selected_ids=[], order_matters=False)
    right = copy.deepcopy(left)
    left.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_a",
        "orderMatters": False,
    }
    right.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_b",
        "orderMatters": False,
    }
    assert battle_state_key(left) != battle_state_key(right)


def test_battle_state_key_keeps_unknown_future_fields_distinct():
    left = _state(operation="discard", selected_ids=[], order_matters=False)
    right = copy.deepcopy(left)
    left.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_move",
        "orderMatters": False,
        "futureFactor": "left",
    }
    right.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_move",
        "orderMatters": False,
        "futureFactor": "right",
    }
    assert battle_state_key(left) != battle_state_key(right)


def test_battle_state_key_canonicalizes_future_semantic_mapping_order():
    left = _state(operation="discard", selected_ids=[], order_matters=False)
    right = copy.deepcopy(left)
    left.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_move",
        "futureFactor": {"a": 1, "b": True},
        "orderMatters": False,
    }
    right.engine_state["pendingChoice"]["choiceSemantics"] = {
        "orderMatters": False,
        "futureFactor": {"b": True, "a": 1},
        "operation": "future_move",
        "version": 2,
    }
    assert battle_state_key(left) == battle_state_key(right)


def test_battle_state_key_keeps_future_scalar_types_distinct():
    left = _state(operation="discard", selected_ids=[], order_matters=False)
    right = copy.deepcopy(left)
    left.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_move",
        "futureFactor": True,
        "orderMatters": False,
    }
    right.engine_state["pendingChoice"]["choiceSemantics"] = {
        "version": 2,
        "operation": "future_move",
        "futureFactor": 1,
        "orderMatters": False,
    }
    assert battle_state_key(left) != battle_state_key(right)


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
