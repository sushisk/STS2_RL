"""Regression coverage for root-commit Branch release lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance, _BranchBookkeeping  # noqa: E402
from API.combat_phase import CombatPhase  # noqa: E402


class _FakeBranchManager:
    def __init__(self) -> None:
        self.released: list[list[str]] = []

    def release_branches(self, branch_ids: list[str]) -> dict[str, str]:
        self.released.append(list(branch_ids))
        return {branch_id: "released" for branch_id in branch_ids}


class _FakeDecisionPoints:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear(self, branch_id: str) -> None:
        self.cleared.append(branch_id)


def _book(internal_id: str, *, terminal: bool, view: object | None) -> _BranchBookkeeping:
    book = _BranchBookkeeping(
        internal_id,
        "root",
        [{"depth": 0}],
        object(),  # type: ignore[arg-type]
        1,
    )
    book.terminal = terminal
    book.view = view  # type: ignore[assignment]
    return book


def test_root_commit_cleanup_releases_terminal_and_active_speculative_branches() -> None:
    inst = CombatInstance.__new__(CombatInstance)
    manager = _FakeBranchManager()
    decision_points = _FakeDecisionPoints()
    terminal = _book("internal-terminal", terminal=True, view=None)
    active = _book("internal-active", terminal=False, view=object())

    inst._phase = object.__new__(CombatPhase)  # type: ignore[assignment]  # noqa: SLF001
    inst._phase._branch_manager = manager  # noqa: SLF001
    inst._decision_points = decision_points  # type: ignore[assignment]  # noqa: SLF001
    inst._bookkeeping = {  # noqa: SLF001
        "terminal": terminal,
        "active": active,
    }

    inst._cancel_and_release_all_branches()  # noqa: SLF001

    assert manager.released == [["internal-terminal", "internal-active"]]
    for branch_id, book in inst._bookkeeping.items():  # noqa: SLF001
        assert book.branch_log == []
        assert book.history is None
        assert book.view is None
        assert book.terminal is True
        assert branch_id in decision_points.cleared
