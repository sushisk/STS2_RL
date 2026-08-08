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
