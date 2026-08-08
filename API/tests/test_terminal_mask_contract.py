from __future__ import annotations

from API.masking import build_masked_emulator_dto


def test_terminal_mask_publishes_empty_legal_actions_without_run_terminal() -> None:
    raw = {"terminal": True, "outcome": "victory"}

    masked = build_masked_emulator_dto(raw)

    assert masked["terminal"] is True
    assert masked["outcome"] == "victory"
    assert masked["legal_actions"] == []
    assert "run_terminal" not in masked
    assert raw == {"terminal": True, "outcome": "victory"}


def test_combat_terminal_mask_discards_stale_legal_actions() -> None:
    raw = {
        "terminal": True,
        "outcome": "victory",
        "legal_actions": [{"action_id": "stale"}],
    }

    masked = build_masked_emulator_dto(raw)

    assert masked["legal_actions"] == []
    assert raw["legal_actions"] == [{"action_id": "stale"}]


def test_whole_run_terminal_mask_discards_present_stale_legal_actions() -> None:
    masked = build_masked_emulator_dto(
        {},
        extra={
            "run_terminal": True,
            "outcome": "defeat",
            "legal_actions": [{"action_id": "stale"}],
        },
    )

    assert masked["legal_actions"] == []


def test_whole_run_terminal_shortcut_may_omit_legal_actions() -> None:
    masked = build_masked_emulator_dto(
        {},
        extra={"run_terminal": True, "outcome": "victory"},
    )

    assert "legal_actions" not in masked
