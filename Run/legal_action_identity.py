"""Stable semantic identity for Whole Run LegalActions.

Opaque ``action_id`` values are only valid at one concrete decision. Replay/Lease
verification instead compares the action's visible semantics. Potion replacement adds
slot identity, so ``potionSlot`` and ``replacedPotionId`` are part of the key.
"""

from __future__ import annotations

KEY_PARAMETER_NAMES = (
    "cardId",
    "potionId",
    "potionSlot",
    "replacedPotionId",
    "eventId",
    "choiceId",
    "enemyIndex",
    "cost",
    "optionId",
)


def legal_action_semantic_key(action: dict) -> tuple:
    params = action.get("parameters") or {}
    key_params = tuple(sorted((name, params[name]) for name in KEY_PARAMETER_NAMES if name in params))
    return (action.get("action_type"), action.get("label"), key_params)


def legal_action_semantic_key_text(action: dict) -> str:
    """Deterministic text form suitable for sorting/hash payloads."""
    action_type, label, params = legal_action_semantic_key(action)
    return repr((action_type, label, params))
