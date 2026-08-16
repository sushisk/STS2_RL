"""Explicit execution modes for Combat's Main Loop - `external_control` and `zero_index`.

Per the "RL担当指示：推論処理撤去と受動実行基盤への整理" division of responsibility: Training
is now the sole decision-making authority. RL's `search/main_loop.py` never decides an
action on its own initiative in production - it either executes an action Training (or a
test/smoke script standing in for Training) explicitly specifies, or - only in the
`zero_index` connectivity-check mode below - takes the Emulator's own first-listed Legal
Action, verbatim, with no scoring of any kind.

## `external_control` (the mode Training integration will use)

There is no built-in "external_control selector" function here that decides anything -
that is the point. Training supplies its own `direct_selector`/`pending_selector`
callables to `search.main_loop.run_until_terminal_or_fault()`, each of which resolves ONE
externally-chosen action_id/SemanticAction against the current Decision Result's own
`legal_actions` and returns a `PlannedStep` - never picking among multiple real candidates
on its own. `make_external_action_selector()` below is a thin, non-deciding convenience
that does exactly that resolution step (the "指定されたActionの実行" responsibility), so
Training only has to hand over an action identifier per decision, not build a
`PlannedStep` by hand.

## `zero_index` (RL standalone connectivity-check fallback ONLY)

`zero_index_pending_selector` (this module) and `search.main_loop.
first_candidate_direct_selector` (Direct) both always take `legal_actions[0]`, in
whatever order the Emulator/RL bridge already publishes it - no reordering, no
score/logit/value/tier/heuristic of any kind, no comparison between candidates, no RNG
Hypothesis comparison, no "best" selection. This exists ONLY to verify RL's own
Episode/Emulator/Branch/Snapshot/Worker/Lease execution infrastructure works end to end
without a real decision-maker attached - it is explicitly NOT a strength-oriented Policy
and must never be presented, benchmarked, or shipped as one.
"""

from __future__ import annotations

from typing import Callable

from search.decision_context import SemanticAction
from search.main_loop import DirectSelector, PlannedStep, first_candidate_direct_selector

MODE_EXTERNAL_CONTROL = "external_control"
MODE_ZERO_INDEX = "zero_index"
EXECUTION_MODES = frozenset({MODE_EXTERNAL_CONTROL, MODE_ZERO_INDEX})


def zero_index_pending_selector(current_result) -> PlannedStep:
    """The Pending-boundary counterpart to `first_candidate_direct_selector`: always the
    first candidate in the Emulator's own reported order, no per-action-type tier
    (unlike `search.main_loop.pending_static_select`, which prefers resolving over
    skipping - that tiny tie-break rule is itself a decision RL no longer makes in
    `zero_index` mode). Connectivity-check fallback only, see module docstring.
    """
    legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001 - NOTE_NO_REREAD, matches main_loop.py's own selectors
    if not legal_actions:
        raise RuntimeError("zero_index: Current Decision Result reports no candidates to choose from")
    action = legal_actions[0]
    semantic_action = SemanticAction(
        action_type=action.get("action_type"), semantic_key=action.get("semantic_key", "")
    )
    return PlannedStep(semantic_action=semantic_action, target_index=None, target_enemy_index=None, expected_signature=None)


def make_external_action_selector(resolve_action_id: Callable[[list], int]) -> DirectSelector:
    """Builds a `direct_selector`/`pending_selector` for `external_control` mode.

    `resolve_action_id(legal_actions) -> int` is Training's own decision function -
    typically a thin transport shim (blocking on a queue/RPC/etc. for Training's answer),
    never a scoring/ranking function living in RL. The returned selector does nothing but
    call it and resolve the chosen action_id against the CURRENT `legal_actions` (raising
    if it's not a valid index) - RL performs zero comparison between candidates itself.
    """

    def _select(current_result) -> PlannedStep:
        legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001
        if not legal_actions:
            raise RuntimeError("external_control: Current Decision Result reports no candidates to choose from")
        action_id = resolve_action_id(legal_actions)
        matches = [a for a in legal_actions if a.get("action_id") == action_id]
        if len(matches) != 1:
            raise ValueError(
                f"external_control: resolve_action_id returned {action_id!r}, "
                f"not a unique action_id among {[a.get('action_id') for a in legal_actions]!r}"
            )
        action = matches[0]
        semantic_action = SemanticAction(
            action_type=action.get("action_type"), semantic_key=action.get("semantic_key", "")
        )
        return PlannedStep(semantic_action=semantic_action, target_index=None, target_enemy_index=None, expected_signature=None)

    return _select


ZERO_INDEX_DIRECT_SELECTOR: DirectSelector = first_candidate_direct_selector
ZERO_INDEX_PENDING_SELECTOR: DirectSelector = zero_index_pending_selector
