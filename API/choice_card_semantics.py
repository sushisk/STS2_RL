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
# Public source-effect namespaces are explicit. New producer namespaces must be reviewed
# and added here rather than relying on negative matching of internal-looking names.
_PUBLIC_SOURCE_EFFECT_PREFIXES = ("card:",)


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
    if not any(value.startswith(prefix) and len(value) > len(prefix) for prefix in _PUBLIC_SOURCE_EFFECT_PREFIXES):
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
    if (
        not _is_int(version)
        or version != CHOICE_SEMANTICS_VERSION
        or not isinstance(operation, str)
        or operation not in CHOICE_OPERATIONS
    ):
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

    Canonical identity is all-or-nothing for known semantics. Malformed/duplicate IDs,
    selected-count mismatch, or overlap between selected and remaining option IDs causes
    semantics to degrade to ``unknown`` rather than publishing a partially repaired
    identity with mechanic meaning still attached.
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

    identity_valid = True
    selected_ids: list[str] = []
    raw_selected_ids = raw_pending_choice.get("selectedOptionIds")
    if isinstance(raw_selected_ids, list):
        for value in raw_selected_ids:
            if not _valid_token(value):
                identity_valid = False
                continue
            selected_ids.append(value)
        result["selectedOptionIds"] = selected_ids
    else:
        identity_valid = False

    options: list[Any] = []
    option_ids: list[str] = []
    raw_options = raw_pending_choice.get("options")
    if isinstance(raw_options, list):
        for raw_option in raw_options:
            if not isinstance(raw_option, dict):
                identity_valid = False
                continue
            option = copy.deepcopy(raw_option)
            option_id = option.get("optionId")
            if not _valid_token(option_id):
                identity_valid = False
                option.pop("optionId", None)
            else:
                option_ids.append(option_id)
            options.append(option)
        result["options"] = options
    else:
        identity_valid = False

    selected_count = result.get("selectedCount")
    if selected_count is None or selected_count != len(selected_ids):
        identity_valid = False
    if len(set(selected_ids)) != len(selected_ids):
        identity_valid = False
    if len(set(option_ids)) != len(option_ids):
        identity_valid = False
    if not set(selected_ids).isdisjoint(option_ids):
        identity_valid = False

    if not identity_valid and semantics["operation"] != "unknown":
        semantics = _unknown_semantics()
    result["choiceSemantics"] = semantics

    source_effect_id = _safe_source_effect_id(raw_pending_choice.get("sourceEffectId"))
    if source_effect_id is not None and semantics["operation"] != "unknown":
        result["sourceEffectId"] = source_effect_id

    return result
