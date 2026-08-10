from __future__ import annotations

from collections.abc import Container
from typing import Any

VALID_TERMINAL_OUTCOMES = frozenset({"victory", "defeat"})
# Preserve the pre-split name for callers that import the original vocabulary directly.
VALID_COMBAT_TERMINAL_OUTCOMES = VALID_TERMINAL_OUTCOMES
# Whole-run completion uses a distinct victory token while sharing defeat semantics.
VALID_WHOLE_RUN_TERMINAL_OUTCOMES = VALID_COMBAT_TERMINAL_OUTCOMES | {"run_victory"}


def require_terminal_outcome(
    value: Any,
    *,
    context: str,
    valid_outcomes: Container[str] = VALID_COMBAT_TERMINAL_OUTCOMES,
) -> str:
    """Validate a terminal outcome against the caller's domain vocabulary."""
    if not isinstance(value, str) or value not in valid_outcomes:
        raise RuntimeError(
            f"{context} reached a terminal boundary without a valid outcome: {value!r}"
        )
    return value
