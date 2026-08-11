"""Search-state identity for Emulator pending card choices.

This module is intentionally policy-free. It records the complete raw semantic descriptor
and decision-local option identity so search de-duplication cannot merge mechanically
different unresolved choices, including mechanics introduced by a future Emulator.
"""

from __future__ import annotations

from typing import Any


def _state_key_value(value: Any) -> Any:
    """Freeze plain Emulator data into a deterministic, hashable identity value."""
    if isinstance(value, dict):
        return tuple(
            sorted(
                ((str(key), _state_key_value(item)) for key, item in value.items()),
                key=lambda pair: pair[0],
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_state_key_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_state_key_value(item) for item in value), key=repr))
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return repr(value)


def pending_choice_state_key(raw_pending_choice: Any) -> "tuple | None":
    """Return a conservative hashable key for one unresolved pending choice.

    The complete raw semantic descriptor is retained internally. Public masking may map
    unsupported future semantics to ``unknown`` for Training, but search identity must not
    collapse two mechanics merely because this RL build does not understand their fields.
    """
    if not raw_pending_choice or not isinstance(raw_pending_choice, dict):
        return None

    raw_semantics = raw_pending_choice.get("choiceSemantics")
    if isinstance(raw_semantics, dict):
        semantic_identity = _state_key_value(raw_semantics)
        order_matters = raw_semantics.get("orderMatters")
    else:
        semantic_identity = (("version", None), ("operation", None))
        order_matters = None

    raw_selected_ids = raw_pending_choice.get("selectedOptionIds")
    if not isinstance(raw_selected_ids, (list, tuple)):
        raw_selected_ids = ()
    selected_option_ids = tuple(_state_key_value(value) for value in raw_selected_ids)
    if order_matters is False:
        selected_option_ids = tuple(sorted(selected_option_ids, key=repr))

    raw_options = raw_pending_choice.get("options")
    if not isinstance(raw_options, (list, tuple)):
        raw_options = ()
    options = tuple(
        (
            _state_key_value(option.get("optionId")),
            option.get("id"),
            option.get("upgraded"),
            option.get("upgradeLevel"),
            option.get("tinkerTimeType"),
            option.get("tinkerTimeRider"),
        )
        for option in raw_options
        if isinstance(option, dict)
    )
    return (
        raw_pending_choice.get("choiceType"),
        raw_pending_choice.get("scope"),
        raw_pending_choice.get("scenarioRestorable"),
        raw_pending_choice.get("minSelect"),
        raw_pending_choice.get("maxSelect"),
        raw_pending_choice.get("selectedCount"),
        semantic_identity,
        _state_key_value(raw_pending_choice.get("sourceEffectId")),
        selected_option_ids,
        options,
    )
