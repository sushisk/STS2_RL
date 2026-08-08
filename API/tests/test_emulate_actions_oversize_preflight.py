from __future__ import annotations

from types import SimpleNamespace

import pytest

from API.instance_combat import CombatInstance
from API.validation import RequestRejected


def test_oversized_batch_rejects_before_phase_a_item_validation() -> None:
    instance = object.__new__(CombatInstance)
    instance._closed = False  # noqa: SLF001
    instance._branch_manager = SimpleNamespace(max_branches=2)  # noqa: SLF001
    validated: list[dict] = []

    def _record_validation(item: dict):
        validated.append(item)
        raise AssertionError("oversized batch must be rejected before item validation")

    instance._validate_emulate_actions_item = _record_validation  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(RequestRejected, match="max batch size 2"):
        instance.emulate_actions(items=[{}, {}, {}], simulation_options=None)

    assert validated == []
