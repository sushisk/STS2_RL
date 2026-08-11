"""Policy-free Whole Run transport for Emulator-declared trivial reward actions.

This module deliberately does not inspect potion capacity, inventory value, card value,
or action ordering. The Emulator owns game legality. RL auto-commits only the exact
``choice_reward_potion_take`` action when it is uniquely present at ``reward_select``.
Saved action prefixes are replayed literally elsewhere; this helper is for NEW frontier
execution only and must never be called between entries of an already-recorded prefix.
"""

from __future__ import annotations

from dataclasses import dataclass

TRIVIAL_POTION_TAKE_ACTION = "choice_reward_potion_take"
REWARD_SELECT = "reward_select"
DEFAULT_MAX_AUTO_ACTIONS = 32


@dataclass(frozen=True)
class AutoDrainResult:
    auto_action_ids: tuple[int, ...]
    last_step_result: "dict | None"


def drain_trivial_reward_frontier(session, *, max_auto_actions: int = DEFAULT_MAX_AUTO_ACTIONS) -> AutoDrainResult:
    """Consume only Emulator-declared trivial Potion TAKE actions at a new frontier.

    The function is intentionally driven by LegalActions alone. In particular it does
    NOT re-infer ``state.potions`` capacity: if Emulator publishes TAKE, Emulator owns
    the legality contract; if TAKE is absent (full belt, temporary removal prohibition,
    required replacement, etc.), RL surfaces the decision unchanged to Training.
    """
    if max_auto_actions <= 0:
        raise ValueError("max_auto_actions must be positive")

    consumed: list[int] = []
    last_step_result: "dict | None" = None

    for _ in range(max_auto_actions):
        observation = session.get_observation()
        if observation.get("boundary") != REWARD_SELECT:
            return AutoDrainResult(tuple(consumed), last_step_result)

        legal_actions = session.get_legal_actions()
        takes = [
            action
            for action in legal_actions
            if action.get("is_available", True)
            and action.get("action_type") == TRIVIAL_POTION_TAKE_ACTION
        ]
        if not takes:
            return AutoDrainResult(tuple(consumed), last_step_result)
        if len(takes) != 1:
            raise RuntimeError(
                "Emulator published multiple trivial Potion TAKE actions at one reward_select boundary: "
                f"{[action.get('action_id') for action in takes]!r}"
            )

        action_id = int(takes[0]["action_id"])
        last_step_result = session.step(action_id)
        consumed.append(action_id)

    observation = session.get_observation()
    if observation.get("boundary") == REWARD_SELECT:
        takes = [
            action
            for action in session.get_legal_actions()
            if action.get("is_available", True)
            and action.get("action_type") == TRIVIAL_POTION_TAKE_ACTION
        ]
        if takes:
            raise RuntimeError(
                f"trivial reward auto-drain exceeded safety cap {max_auto_actions}; "
                f"consumed={consumed!r}"
            )

    return AutoDrainResult(tuple(consumed), last_step_result)
