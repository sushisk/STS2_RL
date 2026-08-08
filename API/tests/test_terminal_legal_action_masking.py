from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from API.masking import build_masked_emulator_dto  # noqa: E402


def test_combat_terminal_forces_stale_legal_actions_empty() -> None:
    dto = build_masked_emulator_dto(
        {},
        extra={
            "terminal": True,
            "outcome": "victory",
            "legal_actions": [{"action_id": "stale"}],
        },
    )

    assert dto["legal_actions"] == []


def test_whole_run_terminal_forces_present_stale_legal_actions_empty() -> None:
    dto = build_masked_emulator_dto(
        {},
        extra={
            "run_terminal": True,
            "outcome": "defeat",
            "legal_actions": [{"action_id": "stale"}],
        },
    )

    assert dto["legal_actions"] == []


def test_whole_run_terminal_shortcut_may_still_omit_legal_actions() -> None:
    dto = build_masked_emulator_dto(
        {},
        extra={"run_terminal": True, "outcome": "victory"},
    )

    assert "legal_actions" not in dto
