"""Search-state identity for Emulator pending card choices.

This module is intentionally policy-free. It records the canonical semantic fields and
decision-local option identity so search de-duplication cannot merge mechanically
different unresolved choices.
"""

from __future__ import annotations

from typing import Any

CHOICE_SEMANTIC_KEY_NAMES = (
    "destinationZone",
    "effect",
    "modifier",
    "operation",
    "orderMatters",
    "replacementAllowed",
    "sourceZone",
    "version",
)


def _state_key_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return repr(value)


def pending_choice_state_key(raw_pending_choice: Any) -> "tuple | None":
    """Return a conservative hashable key for one unresolved pending choice.

    Unsupported future semantic versions/operations are intentionally retained as raw
    approved scalar fields here. Public masking may map them to ``unknown`` for Training,
    but internal search must not collapse two mechanics merely because this RL build does
    not understand either one yet.
    """
    if not raw_pending_choice or not isinstance(raw_pending_choice, dict):
        return None

    raw_semantics = raw_pending_choice.get("choiceSemantics")
    if isinstance(raw_semantics, dict):
        semantic_tuple = tuple(
            (key, _state_key_value(raw_semantics.get(key)))
            for key in CHOICE_SEMANTIC_KEY_NAMES
        )
        order_matters = raw_semantics.get("orderMatters")
    else:
        semantic_tuple = (("version", None), ("operation", None))
        order_matters = None

    selected_option_ids = tuple(
        _state_key_value(value)
        for value in (raw_pending_choice.get("selectedOptionIds") or [])
    )
    if order_matters is False:
        selected_option_ids = tuple(sorted(selected_option_ids, key=repr))

    options = tuple(
        (
            _state_key_value(option.get("optionId")),
            option.get("id"),
            option.get("upgraded"),
            option.get("upgradeLevel"),
            option.get("tinkerTimeType"),
            option.get("tinkerTimeRider"),
        )
        for option in (raw_pending_choice.get("options") or [])
        if isinstance(option, dict)
    )
    return (
        raw_pending_choice.get("choiceType"),
        raw_pending_choice.get("scope"),
        raw_pending_choice.get("scenarioRestorable"),
        raw_pending_choice.get("minSelect"),
        raw_pending_choice.get("maxSelect"),
        raw_pending_choice.get("selectedCount"),
        semantic_tuple,
        _state_key_value(raw_pending_choice.get("sourceEffectId")),
        selected_option_ids,
        options,
    )
