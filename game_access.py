"""Process-wide ownership of the CLR ``GameInstance``.

The lease makes a RUN/COMBAT hand-off inside one run safe and makes a stale holder
fail loudly on its next mutating access.  It does not prevent a second instance from
taking over; that exclusion is enforced at the server layer in a later step.

This module is deliberately neutral: it does not import ``Combat`` or ``Run``.  Those
layers may both depend on this small access guard without creating a Combat -> Run
dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class LeaseState(str, Enum):
    RUN = "RUN"
    COMBAT = "COMBAT"
    TRANSFERRING = "TRANSFERRING"
    POISONED = "POISONED"


class GameAccessError(RuntimeError):
    """Raised when a caller does not hold the current mutation lease."""


@dataclass(frozen=True)
class GameLease:
    """Capability for one owner to mutate the game at one lease generation."""

    holder: object
    state: LeaseState
    generation: int


class GameAccess:
    """Owns one game and the thin lease state machine protecting mutations."""

    def __init__(self, game_factory: Callable[[], Any]) -> None:
        self._game_factory = game_factory
        self._game: Any | None = None
        self._state = LeaseState.RUN
        self._holder: object | None = None
        self._generation = 0
        self._transfer_origin: tuple[LeaseState, object] | None = None

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    def observation_game(self) -> Any:
        """Returns the game for a side-effect-free observation operation.

        Observation is intentionally not lease-gated.  Wrapper methods retain the raw
        object only privately; callers are never given a public raw-game escape hatch.
        """
        if self._game is None:
            self._game = self._game_factory()
        return self._game

    def claim(self, holder: object, state: LeaseState) -> GameLease:
        """Claims the mutable game lease, superseding any normal-state holder.

        The superseded holder's lease becomes stale immediately.  Controlled
        RUN/COMBAT hand-offs must instead use begin/commit or begin/rollback.
        """
        if self._state is LeaseState.POISONED:
            raise GameAccessError("Game access is POISONED; no mutation lease can be claimed.")
        if self._state is LeaseState.TRANSFERRING:
            raise GameAccessError("Game access is TRANSFERRING; no mutation lease can be claimed.")
        self._state = state
        self._holder = holder
        self._generation += 1
        return GameLease(holder, state, self._generation)

    def release(self, lease: GameLease) -> None:
        """Releases the current lease and invalidates it for all future mutation."""
        self._validate_current(lease)
        self._holder = None
        self._generation += 1

    def mutating_game(self, lease: GameLease) -> Any:
        """Validates ``lease`` and returns the game for one mutating operation."""
        self._validate_current(lease)
        return self.observation_game()

    def begin(self, lease: GameLease) -> GameLease:
        """Begins a hand-off, making all mutation unavailable until completion."""
        self._validate_current(lease)
        self._transfer_origin = (self._state, self._holder)
        self._state = LeaseState.TRANSFERRING
        self._generation += 1
        return GameLease(lease.holder, LeaseState.TRANSFERRING, self._generation)

    def commit(self, transfer: GameLease, target: LeaseState, holder: object) -> GameLease:
        """Completes a hand-off to ``holder`` in RUN or COMBAT."""
        self._validate_transfer(transfer)
        if target not in (LeaseState.RUN, LeaseState.COMBAT):
            raise ValueError("A transfer can only commit to RUN or COMBAT.")
        self._state = target
        self._holder = holder
        self._transfer_origin = None
        self._generation += 1
        return GameLease(holder, target, self._generation)

    def rollback(self, transfer: GameLease) -> GameLease:
        """Restores the exact pre-transfer state and holder after a failed hand-off."""
        self._validate_transfer(transfer)
        assert self._transfer_origin is not None
        self._state, self._holder = self._transfer_origin
        self._transfer_origin = None
        self._generation += 1
        return GameLease(self._holder, self._state, self._generation)

    def poison(self) -> None:
        """Terminally rejects all future mutations, from any current state."""
        self._state = LeaseState.POISONED
        self._holder = None
        self._transfer_origin = None
        self._generation += 1

    def _validate_current(self, lease: GameLease) -> None:
        if self._state is LeaseState.POISONED:
            raise GameAccessError("Game access is POISONED; mutating access is terminally rejected.")
        if self._state is LeaseState.TRANSFERRING:
            raise GameAccessError("Game access is TRANSFERRING; no holder may mutate it.")
        if (
            lease.generation != self._generation
            or lease.holder is not self._holder
            or lease.state is not self._state
        ):
            raise GameAccessError(
                "Rejected mutating access: lease is not the current holder/generation "
                f"(state={self._state.value}, generation={self._generation})."
            )

    def _validate_transfer(self, transfer: GameLease) -> None:
        if (
            self._state is not LeaseState.TRANSFERRING
            or transfer.state is not LeaseState.TRANSFERRING
            or transfer.generation != self._generation
            or self._transfer_origin is None
            or transfer.holder is not self._transfer_origin[1]
        ):
            raise GameAccessError(
                "Rejected transfer completion: lease is not the current TRANSFERRING generation."
            )


_process_game_access: GameAccess | None = None


def process_game_access(game_factory: Callable[[], Any]) -> GameAccess:
    """Returns the sole process-wide ``GameAccess`` without invoking later factories."""
    global _process_game_access
    if _process_game_access is None:
        _process_game_access = GameAccess(game_factory)
    return _process_game_access
