"""Canonical public normalization for Emulator ``pendingChoice`` card semantics.

The Emulator owns the mechanic that produced a pending card choice. RL only exposes a
small, versioned, explicitly-approved public view and never infers semantics from labels,
card IDs, selector names, or other incidental fields.
"""

from __future__ import annotations

import copy
import re
from typing import Any

CHOICE_SEMANTICS_VERSION = 1

CHOICE_OPERATIONS = frozenset(
    {
        "gain",
        "discard",
        "exhaust",
        "upgrade",
        "retrieve",
        "play",
        "replay",
        "remove",
        "transform",
        "unknown",
    }
)
CHOICE_EFFECTS = frozenset({"move", "modify", "play", "replace"})
CHOICE_ZONES = frozenset(
    {
        "hand",
        "draw",
        "draw_pile",
        "discard",
        "discard_pile",
        "exhaust",
        "exhaust_pile",
        "play",
        "play_pile",
        "deck",
        "master_deck",
        "reward",
        "generated",
        "none",
        "unknown",
    }
)
CHOICE_MODIFIERS = frozenset({"upgrade"})

PUBLIC_PENDING_CHOICE_KEYS = frozenset(
    {
        "choiceType",
        "scope",
        "scenarioRestorable",
        "minSelect",
        "maxSelect",
        "selectedCount",
        "choiceSemantics",
        "sourceEffectId",
        "selectedOptionIds",
        "options",
    }
)
PUBLIC_CHOICE_SEMANTICS_KEYS = frozenset(
    {
        "version",
        "operation",
        "effect",
        "sourceZone",
        "destinationZone",
        "modifier",
        "orderMatters",
        "replacementAllowed",
    }
)

_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SOURCE_EFFECT_HIDDEN_MARKERS = (
    "rng",
    "seed",
    "snapshot",
    "savestate",
    "save_state",
    "session",
    "worker",
    "pid",
    "lease",
    "cursor",
    "generation",
    "context_id",
    "contextid",
)


def _unknown_semantics() -> dict:
    return {"version": CHOICE_SEMANTICS_VERSION, "operation": "unknown"}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonnegative_int_or_none(value: Any) -> "int | None":
    if not _is_int(value) or value < 0:
        return None
    return value


def _valid_token(value: Any) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_TOKEN_RE.fullmatch(value))


def _safe_source_effect_id(value: Any) -> "str | None":
    if not _valid_token(value):
        return None
    lowered = value.lower()
    if any(marker in lowered for marker in _SOURCE_EFFECT_HIDDEN_MARKERS):
        return None
    return value


def _enum_or_none(value: Any, allowed: frozenset[str]) -> "str | None":
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("invalid semantic enum value")
    return value


def normalize_choice_semantics(raw_semantics: Any) -> dict:
    """Return a safe v1 public descriptor; malformed/new semantics become ``unknown``."""
    if not isinstance(raw_semantics, dict):
        return _unknown_semantics()

    version = raw_semantics.get("version")
    operation = raw_semantics.get("operation")
    if version != CHOICE_SEMANTICS_VERSION or not isinstance(operation, str) or operation not in CHOICE_OPERATIONS:
        return _unknown_semantics()
    if operation == "unknown":
        return _unknown_semantics()

    try:
        effect = _enum_or_none(raw_semantics.get("effect"), CHOICE_EFFECTS)
        source_zone = _enum_or_none(raw_semantics.get("sourceZone"), CHOICE_ZONES)
        destination_zone = _enum_or_none(raw_semantics.get("destinationZone"), CHOICE_ZONES)
        modifier = _enum_or_none(raw_semantics.get("modifier"), CHOICE_MODIFIERS)
        order_matters = raw_semantics.get("orderMatters")
        replacement_allowed = raw_semantics.get("replacementAllowed")
        if order_matters is not None and not isinstance(order_matters, bool):
            raise ValueError("orderMatters must be bool")
        if replacement_allowed is not None and not isinstance(replacement_allowed, bool):
            raise ValueError("replacementAllowed must be bool")
    except ValueError:
        return _unknown_semantics()

    result = {"version": CHOICE_SEMANTICS_VERSION, "operation": operation}
    optional = {
        "effect": effect,
        "sourceZone": source_zone,
        "destinationZone": destination_zone,
        "modifier": modifier,
        "orderMatters": order_matters,
        "replacementAllowed": replacement_allowed,
    }
    for key, value in optional.items():
        if value is not None:
            result[key] = value
    return result


def normalize_pending_choice(raw_pending_choice: Any) -> dict:
    """Normalize one public ``pendingChoice`` with an explicit top-level allowlist.

    Existing option/card objects are retained for backwards compatibility, but every
    pending-choice-level field outside ``PUBLIC_PENDING_CHOICE_KEYS`` is dropped. The
    masking layer still recursively scrubs option/card objects for hidden key names.
    """
    if not isinstance(raw_pending_choice, dict):
        return {}

    result: dict[str, Any] = {}
    for key in ("choiceType", "scope"):
        value = raw_pending_choice.get(key)
        if isinstance(value, str):
            result[key] = value

    scenario_restorable = raw_pending_choice.get("scenarioRestorable")
    if isinstance(scenario_restorable, bool):
        result["scenarioRestorable"] = scenario_restorable

    for key in ("minSelect", "maxSelect", "selectedCount"):
        value = _nonnegative_int_or_none(raw_pending_choice.get(key))
        if value is not None:
            result[key] = value

    semantics = normalize_choice_semantics(raw_pending_choice.get("choiceSemantics"))
    result["choiceSemantics"] = semantics

    source_effect_id = _safe_source_effect_id(raw_pending_choice.get("sourceEffectId"))
    if source_effect_id is not None and semantics["operation"] != "unknown":
        result["sourceEffectId"] = source_effect_id

    raw_selected_ids = raw_pending_choice.get("selectedOptionIds")
    if isinstance(raw_selected_ids, list):
        result["selectedOptionIds"] = [value for value in raw_selected_ids if _valid_token(value)]

    raw_options = raw_pending_choice.get("options")
    if isinstance(raw_options, list):
        options: list[Any] = []
        for raw_option in raw_options:
            if not isinstance(raw_option, dict):
                continue
            option = copy.deepcopy(raw_option)
            if "optionId" in option and not _valid_token(option.get("optionId")):
                option.pop("optionId", None)
            options.append(option)
        result["options"] = options

    return result


def _state_key_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return repr(value)


def pending_choice_state_key(raw_pending_choice: Any) -> "tuple | None":
    """Hashable identity for search de-duplication, including raw canonical semantics.

    Public masking intentionally collapses unsupported semantics to ``unknown``. Search
    de-duplication must be more conservative: two future/unsupported operations must not
    collapse merely because this RL build cannot interpret them yet. Therefore the key
    records the approved raw semantic fields without assigning policy meaning to them.
    """
    if not raw_pending_choice or not isinstance(raw_pending_choice, dict):
        return None

    raw_semantics = raw_pending_choice.get("choiceSemantics")
    if isinstance(raw_semantics, dict):
        semantic_tuple = tuple(
            (key, _state_key_value(raw_semantics.get(key)))
            for key in sorted(PUBLIC_CHOICE_SEMANTICS_KEYS)
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
