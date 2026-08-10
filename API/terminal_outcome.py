from __future__ import annotations

from typing import Any

# Combat-only terminal outcomes (a single combat's win/loss). Used by `instance_combat.py`,
# where the concept of a whole Run (Acts, a final "run_victory" epilogue) doesn't exist.
VALID_COMBAT_TERMINAL_OUTCOMES = frozenset({"victory", "defeat"})

# Whole Run terminal outcomes. "run_victory" (Emulator commit 72ac8df, 2026-08-10) is the
# whole-Run analogue of clearing the game (defeating the final Act's boss and completing
# TheArchitect's epilogue) - deliberately a DIFFERENT value from combat-scoped "victory"
# (winning one combat is not the same event as winning the whole Run, and the two must
# never be conflated - see Outputs/reports/act_transition_maproom_fix_plan_20260810.md's
# Outcome-field discussion for why). Used by `instance_whole_run.py`.
VALID_WHOLE_RUN_TERMINAL_OUTCOMES = frozenset({"victory", "defeat", "run_victory"})


def require_terminal_outcome(
    value: Any, *, context: str, valid_outcomes: frozenset[str] = VALID_COMBAT_TERMINAL_OUTCOMES
) -> str:
    """Return a validated terminal verdict or fail the coordinator invariant.

    `valid_outcomes` defaults to the combat-only set - callers on the Whole Run path must
    pass `valid_outcomes=VALID_WHOLE_RUN_TERMINAL_OUTCOMES` explicitly so a whole-Run
    "run_victory" isn't rejected there, while combat-only callers keep rejecting it (that
    value has no meaning for a single combat).
    """
    if not isinstance(value, str) or value not in valid_outcomes:
        raise RuntimeError(
            f"{context} reached a terminal boundary without a valid outcome: {value!r}"
        )
    return value
