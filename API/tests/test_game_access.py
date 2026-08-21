"""S2 unit coverage for the process-wide GameAccess lease guard."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import game_access  # noqa: E402
import live_combat_session  # noqa: E402
import whole_run_session  # noqa: E402


class _FakeGame:
    pass


def test_non_holder_and_stale_generation_are_rejected() -> None:
    access = game_access.GameAccess(_FakeGame)
    holder = object()
    lease = access.claim(holder, game_access.LeaseState.RUN)

    with pytest.raises(game_access.GameAccessError, match="current holder/generation"):
        access.mutating_game(game_access.GameLease(object(), game_access.LeaseState.RUN, lease.generation))

    transfer = access.begin(lease)
    restored = access.rollback(transfer)
    with pytest.raises(game_access.GameAccessError, match="current holder/generation"):
        access.mutating_game(lease)
    assert access.mutating_game(restored) is access.observation_game()

    successor = access.claim(object(), game_access.LeaseState.COMBAT)
    with pytest.raises(game_access.GameAccessError, match="current holder/generation"):
        access.mutating_game(restored)
    assert access.mutating_game(successor) is access.observation_game()


def test_claim_supersedes_any_normal_state_and_release_invalidates_the_lease() -> None:
    access = game_access.GameAccess(_FakeGame)
    run_lease = access.claim(object(), game_access.LeaseState.RUN)
    combat_lease = access.claim(object(), game_access.LeaseState.COMBAT)

    assert access.state is game_access.LeaseState.COMBAT
    with pytest.raises(game_access.GameAccessError, match="current holder/generation"):
        access.mutating_game(run_lease)

    access.release(combat_lease)
    with pytest.raises(game_access.GameAccessError, match="current holder/generation"):
        access.mutating_game(combat_lease)
    assert access.mutating_game(access.claim(object(), game_access.LeaseState.RUN)) is access.observation_game()


def test_observation_is_allowed_without_a_lease_in_every_state() -> None:
    game = _FakeGame()
    access = game_access.GameAccess(lambda: game)
    lease = access.claim(object(), game_access.LeaseState.RUN)

    assert access.observation_game() is game
    access.begin(lease)
    assert access.observation_game() is game
    access.poison()
    assert access.observation_game() is game


def test_rollback_restores_the_prior_state_and_holder() -> None:
    access = game_access.GameAccess(_FakeGame)
    holder = object()
    run_lease = access.claim(holder, game_access.LeaseState.RUN)

    transfer = access.begin(run_lease)
    restored = access.rollback(transfer)

    assert access.state is game_access.LeaseState.RUN
    assert restored.holder is holder
    assert restored.state is game_access.LeaseState.RUN
    access.mutating_game(restored)


def test_commit_hands_the_next_generation_to_the_target_holder() -> None:
    access = game_access.GameAccess(_FakeGame)
    run_holder = object()
    combat_holder = object()
    run_lease = access.claim(run_holder, game_access.LeaseState.RUN)

    transfer = access.begin(run_lease)
    combat_lease = access.commit(transfer, game_access.LeaseState.COMBAT, combat_holder)

    assert access.state is game_access.LeaseState.COMBAT
    assert combat_lease.holder is combat_holder
    with pytest.raises(game_access.GameAccessError, match="current holder/generation"):
        access.mutating_game(run_lease)
    access.mutating_game(combat_lease)


def test_poison_is_terminal_for_mutations() -> None:
    access = game_access.GameAccess(_FakeGame)
    lease = access.claim(object(), game_access.LeaseState.COMBAT)

    access.poison()

    assert access.state is game_access.LeaseState.POISONED
    with pytest.raises(game_access.GameAccessError, match="POISONED"):
        access.mutating_game(lease)
    with pytest.raises(game_access.GameAccessError, match="POISONED"):
        access.claim(object(), game_access.LeaseState.RUN)


def test_run_and_combat_wrappers_receive_the_same_game(monkeypatch) -> None:
    game = _FakeGame()
    access = game_access.GameAccess(lambda: game)
    monkeypatch.setattr(game_access, "_process_game_access", access)
    monkeypatch.setattr(live_combat_session, "ensure_loaded", lambda repo_root=None: {})
    monkeypatch.setattr(live_combat_session, "BattleEmulator", lambda **kwargs: object())

    run_session = whole_run_session.WholeRunSession()
    combat_session = live_combat_session.LiveCombatSession()

    assert run_session._game is game  # noqa: SLF001 - verifies the wrapper boundary
    assert combat_session._game is game  # noqa: SLF001 - verifies the wrapper boundary
    assert not hasattr(run_session, "raw_game_instance")
