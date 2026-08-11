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
        "sourceEffectId": "card:SURVIVOR",
        "selectedOptionIds": ["choice-7:0"],
        "options": [
            {
                "id": "STRIKE",
                "upgraded": False,
                "optionId": "choice-7:1",
                "seed": 999,
            },
            {
                "id": "STRIKE",
                "upgraded": True,
                "optionId": "choice-7:2",
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
    assert normalized["sourceEffectId"] == "card:SURVIVOR"
    assert normalized["selectedOptionIds"] == ["choice-7:0"]
    assert [option["optionId"] for option in normalized["options"]] == [
        "choice-7:1",
        "choice-7:2",
    ]


def test_unknown_or_malformed_semantics_degrade_to_neutral_unknown():
    cases = [
        None,
        {},
        {"version": True, "operation": "discard"},
        {"version": 1.0, "operation": "discard"},
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
    assert pending["options"][0]["optionId"] == "choice-7:1"
    assert pending["selectedOptionIds"] == ["choice-7:0"]
    assert masked["mask_version"] == MASK_VERSION == "1.1"


def test_invalid_choice_identity_degrades_known_semantics_to_unknown():
    cases = []

    malformed_selected = _known_choice()
    malformed_selected["selectedOptionIds"] = ["bad token"]
    cases.append(malformed_selected)

    count_mismatch = _known_choice()
    count_mismatch["selectedCount"] = 2
    cases.append(count_mismatch)

    duplicate_selected = _known_choice()
    duplicate_selected["selectedCount"] = 2
    duplicate_selected["selectedOptionIds"] = ["choice-7:0", "choice-7:0"]
    cases.append(duplicate_selected)

    overlap = _known_choice()
    overlap["options"][0]["optionId"] = "choice-7:0"
    cases.append(overlap)

    malformed_remaining = _known_choice()
    malformed_remaining["options"][0]["optionId"] = "bad token"
    cases.append(malformed_remaining)

    duplicate_remaining = _known_choice()
    duplicate_remaining["options"][1]["optionId"] = "choice-7:1"
    cases.append(duplicate_remaining)

    for raw in cases:
        normalized = normalize_pending_choice(raw)
        assert normalized["choiceSemantics"] == {"version": 1, "operation": "unknown"}
        assert "sourceEffectId" not in normalized


def test_source_effect_id_requires_an_explicit_public_namespace():
    for hidden_or_unapproved in (
        "combat_session_rng_seed_17",
        "sessionId:abc",
        "workerId-123",
        "pid123",
        "CARD_EFFECT.DISCARD",
    ):
        raw = _known_choice()
        raw["sourceEffectId"] = hidden_or_unapproved
        assert "sourceEffectId" not in normalize_pending_choice(raw)

    raw = _known_choice()
    raw["sourceEffectId"] = "card:SURVIVOR"
    assert normalize_pending_choice(raw)["sourceEffectId"] == "card:SURVIVOR"


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
