"""Unit tests for `API/instance_whole_run.py`'s `_View` dataclass,
`_build_child_view()`, and `_choice_type_from_boundary()` (RL担当指示 code-improvement
pass items 4/5/6). Pure in-process tests, no real Emulator session or Worker Pool needed.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_whole_run import _build_child_view, _choice_type_from_boundary, _View  # noqa: E402
from whole_run_session import EVENT_CHOICE, MAP_SELECT, RUN_TERMINAL, SHOP_CHOICE  # noqa: E402
from worker_pool import BranchResult, ChoiceStepResult, ChoiceWorkItem, EXECUTION_MODE_BOOTSTRAP_STEP, WORK_KIND_SUB_BRANCH  # noqa: E402


def _dummy_work_item(choice_type: str, target_boundary: str) -> ChoiceWorkItem:
    return ChoiceWorkItem(
        work_id="w1",
        context_id="ctx",
        choice_type=choice_type,
        map_snapshot="snap",
        room_id=1,
        action_prefix=[],
        relic_injection=None,
        target_boundary=target_boundary,
        work_kind=WORK_KIND_SUB_BRANCH,
    )


def _success_result(step_result: dict, *, choice_type: str, target_boundary: str) -> BranchResult:
    settled_legal_actions = step_result.get("legal_actions")
    if settled_legal_actions is None:
        settled_legal_actions = step_result.get("room_enter_result", {}).get("legal_actions", [])
    return BranchResult(
        status="success",
        work_item=_dummy_work_item(choice_type, target_boundary),
        execution_mode=EXECUTION_MODE_BOOTSTRAP_STEP,
        worker_slot=0,
        worker_generation=1,
        pid=123,
        step=ChoiceStepResult(
            step_result=step_result,
            run_state={},
            settled_observation=step_result["observation"],
            settled_legal_actions=settled_legal_actions,
            settled_room_context=step_result["room_context"],
        ),
    )


def _base_view(**overrides) -> _View:
    fields = dict(
        legal_actions_raw=[{"action_id": 0}],
        boundary=SHOP_CHOICE,
        observation={"boundary": SHOP_CHOICE},
        room_context={},
        map_snapshot="snap",
        room_id=7,
        action_prefix=(1, 2),
        choice_type=SHOP_CHOICE,
        chain_blocked=False,
        event_rng_state=None,
    )
    fields.update(overrides)
    return _View(**fields)


def test_view_missing_field_raises_typeerror():
    raised = False
    try:
        _View(legal_actions_raw=[], boundary=MAP_SELECT)  # type: ignore[call-arg]
    except TypeError:
        raised = True
    assert raised, "_View construction with missing required fields must raise TypeError"


def test_choice_type_from_boundary_table():
    assert _choice_type_from_boundary(MAP_SELECT) == "map"
    assert _choice_type_from_boundary(EVENT_CHOICE) == EVENT_CHOICE
    assert _choice_type_from_boundary(RUN_TERMINAL) == RUN_TERMINAL
    assert _choice_type_from_boundary(SHOP_CHOICE) == SHOP_CHOICE


def test_build_child_view_from_map_choice_resets_prefix():
    parent = _base_view(choice_type="map", action_prefix=(9, 9, 9))
    step_result = {
        "observation": {"boundary": EVENT_CHOICE},
        "room_context": {"room": "event"},
        "room_enter_result": {"legal_actions": [{"action_id": 0}, {"action_id": 1}]},
    }
    result = _success_result(step_result, choice_type="map", target_boundary=MAP_SELECT)

    child = _build_child_view(parent, chosen_action_id=42, branch_result=result)

    assert child.action_prefix == ()
    assert child.room_id == 42
    assert child.legal_actions_raw == step_result["room_enter_result"]["legal_actions"]
    assert child.choice_type == EVENT_CHOICE
    assert child.chain_blocked is False


def test_build_child_view_from_non_map_choice_appends_prefix():
    parent = _base_view(choice_type=SHOP_CHOICE, action_prefix=(1, 2), room_id=7)
    step_result = {
        "observation": {"boundary": SHOP_CHOICE},
        "room_context": {"room": "shop"},
        "legal_actions": [{"action_id": 0}],
    }
    result = _success_result(step_result, choice_type=SHOP_CHOICE, target_boundary=SHOP_CHOICE)

    child = _build_child_view(parent, chosen_action_id=5, branch_result=result)

    assert child.action_prefix == (1, 2, 5)
    assert child.room_id == 7
    assert child.legal_actions_raw == step_result["legal_actions"]


def test_chain_blocked_preserved_on_map_transition():
    parent = _base_view(choice_type=SHOP_CHOICE, action_prefix=(1,))
    step_result = {
        "observation": {"boundary": MAP_SELECT},
        "room_context": {},
        "legal_actions": [],
    }
    result = _success_result(step_result, choice_type=SHOP_CHOICE, target_boundary=SHOP_CHOICE)

    child = _build_child_view(parent, chosen_action_id=3, branch_result=result)

    assert child.boundary == MAP_SELECT
    assert child.chain_blocked is True
    assert child.choice_type == "map"


def test_child_action_prefix_not_shared_with_parent():
    parent = _base_view(choice_type=SHOP_CHOICE, action_prefix=(1, 2))
    step_result = {
        "observation": {"boundary": SHOP_CHOICE},
        "room_context": {},
        "legal_actions": [],
    }
    result = _success_result(step_result, choice_type=SHOP_CHOICE, target_boundary=SHOP_CHOICE)

    child = _build_child_view(parent, chosen_action_id=99, branch_result=result)

    assert type(child.action_prefix) is tuple
    assert child.action_prefix is not parent.action_prefix
    assert parent.action_prefix == (1, 2), "parent's action_prefix must be untouched by child construction"


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
