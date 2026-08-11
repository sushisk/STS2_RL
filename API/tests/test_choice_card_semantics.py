from __future__ import annotations

import copy
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from API.choice_card_semantics import (  # noqa: E402
    PUBLIC_CHOICE_SEMANTICS_KEYS,
    PUBLIC_PENDING_CHOICE_KEYS,
    normalize_choice_semantics,
    normalize_pending_choice,
    pending_choice_state_key,
)
from API.dto import MASK_VERSION  # noqa: E402
from API.masking import build_masked_emulator_dto  # noqa: E402


def _known_choice() -> dict:
    return {
        "choiceType": "card_selection",
        "scope": "ActionContinuation",
        "scenarioRestorable": False,
        "minSelect": 1,
        "maxSelect": 2,
        "selectedCount": 1,
        "choiceSemantics": {
            "version": 1,
            "operation": "discard",
            "effect": "move",
            "sourceZone": "hand",
            "destinationZone": "discard",
            "modifier": None,
            "orderMatters": True,
            "replacementAllowed": False,
            "unapprovedSemanticField": "must-not-leak",
        },
        "sourceEffectId": "CARD_EFFECT.DISCARD",
        "selectedOptionIds": ["choice-7:0"],
        "options": [
            {
                "id": "STRIKE",
                "upgraded": False,
                "optionId": "choice-7:0",
                "seed": 999,
            },
            {
                "id": "STRIKE",
                "upgraded": True,
                "optionId": "choice-7:1",
            },
        ],
        "unapprovedPendingField": {"looksHarmless": True},
    }


def test_known_v1_semantics_are_normalized_with_explicit_allowlists():
    raw = _known_choice()
    normalized = normalize_pending_choice(raw)

    assert set(normalized) <= PUBLIC_PENDING_CHOICE_KEYS
    assert "unapprovedPendingField" not in normalized
    semantics = normalized["choiceSemantics"]
    assert set(semantics) <= PUBLIC_CHOICE_SEMANTICS_KEYS
    assert semantics == {
        "version": 1,
        "operation": "discard",
        "effect": "move",
        "sourceZone": "hand",
        "destinationZone": "discard",
        "orderMatters": True,
        "replacementAllowed": False,
    }
    assert normalized["sourceEffectId"] == "CARD_EFFECT.DISCARD"
    assert normalized["selectedOptionIds"] == ["choice-7:0"]
    assert [option["optionId"] for option in normalized["options"]] == [
        "choice-7:0",
        "choice-7:1",
    ]


def test_unknown_or_malformed_semantics_degrade_to_neutral_unknown():
    cases = [
        None,
        {},
        {"version": 2, "operation": "discard"},
        {"version": 1, "operation": "future_operation"},
        {"version": 1, "operation": "discard", "effect": "future_effect"},
        {"version": 1, "operation": "discard", "orderMatters": "yes"},
    ]
    for case in cases:
        assert normalize_choice_semantics(case) == {"version": 1, "operation": "unknown"}

    absent = normalize_pending_choice({"choiceType": "card_selection", "options": []})
    assert absent["choiceSemantics"] == {"version": 1, "operation": "unknown"}


def test_masking_normalizes_pending_choice_and_scrubs_retained_option_payloads():
    raw_choice = _known_choice()
    raw_copy = copy.deepcopy(raw_choice)

    masked = build_masked_emulator_dto({"pendingChoice": raw_choice})

    assert raw_choice == raw_copy
    pending = masked["pendingChoice"]
    assert "unapprovedPendingField" not in pending
    assert "unapprovedSemanticField" not in pending["choiceSemantics"]
    assert "seed" not in pending["options"][0]
    assert pending["options"][0]["optionId"] == "choice-7:0"
    assert pending["selectedOptionIds"] == ["choice-7:0"]
    assert masked["mask_version"] == MASK_VERSION == "1.1"


def test_hidden_looking_source_effect_id_is_not_published():
    raw = _known_choice()
    raw["sourceEffectId"] = "combat_session_rng_seed_17"

    normalized = normalize_pending_choice(raw)

    assert "sourceEffectId" not in normalized


def test_state_key_separates_semantics_and_duplicate_option_identity():
    base = _known_choice()
    discard_key = pending_choice_state_key(base)

    upgrade = copy.deepcopy(base)
    upgrade["choiceSemantics"] = {
        "version": 1,
        "operation": "upgrade",
        "effect": "modify",
        "modifier": "upgrade",
        "orderMatters": True,
    }
    assert pending_choice_state_key(upgrade) != discard_key

    changed_token = copy.deepcopy(base)
    changed_token["options"][1]["optionId"] = "choice-7:99"
    assert pending_choice_state_key(changed_token) != discard_key

    future_a = copy.deepcopy(base)
    future_a["choiceSemantics"] = {"version": 2, "operation": "future_a"}
    future_b = copy.deepcopy(base)
    future_b["choiceSemantics"] = {"version": 2, "operation": "future_b"}
    # Public normalization maps both to unknown, but internal search must still avoid
    # collapsing two mechanics it does not understand.
    assert normalize_pending_choice(future_a)["choiceSemantics"]["operation"] == "unknown"
    assert normalize_pending_choice(future_b)["choiceSemantics"]["operation"] == "unknown"
    assert pending_choice_state_key(future_a) != pending_choice_state_key(future_b)


def test_state_key_canonicalizes_selected_set_only_when_order_does_not_matter():
    left = _known_choice()
    right = copy.deepcopy(left)
    left["selectedOptionIds"] = ["choice-7:0", "choice-7:1"]
    right["selectedOptionIds"] = ["choice-7:1", "choice-7:0"]

    left["choiceSemantics"]["orderMatters"] = False
    right["choiceSemantics"]["orderMatters"] = False
    assert pending_choice_state_key(left) == pending_choice_state_key(right)

    left["choiceSemantics"]["orderMatters"] = True
    right["choiceSemantics"]["orderMatters"] = True
    assert pending_choice_state_key(left) != pending_choice_state_key(right)


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
