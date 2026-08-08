from __future__ import annotations

from typing import Any

VALID_TERMINAL_OUTCOMES = frozenset({"victory", "defeat"})


def require_terminal_outcome(value: Any, *, context: str) -> str:
    """Return a validated terminal verdict or fail the coordinator invariant."""
    if value not in VALID_TERMINAL_OUTCOMES:
        raise RuntimeError(
            f"{context} reached a terminal boundary without a valid outcome: {value!r}"
        )
    return value
