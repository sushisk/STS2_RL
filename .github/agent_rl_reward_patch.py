from __future__ import annotations

from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, got {count}")
    return updated


# --- whole_run_session.py -----------------------------------------------------------
path = Path("Run/whole_run_session.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "`reward_select` (new - a pending card reward), `map_select` (via\n",
    "`reward_select` (a pending card or potion reward), `map_select` (via\n",
    "reward_select doc",
)
path.write_text(text, encoding="utf-8")


# --- legal action identity ----------------------------------------------------------
path = Path("Run/choice_branch_runner.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from typing import Any\n\nfrom room_progression_driver import pick_room\n",
    "from typing import Any\n\nfrom legal_action_identity import legal_action_semantic_key\nfrom room_progression_driver import pick_room\n",
    "choice semantic import",
)
text = regex_once(
    text,
    r"\ndef legal_action_semantic_key\(action: dict\) -> tuple:\n.*?\n    return \(action\[\"action_type\"\], action\.get\(\"label\"\), key_params\)\n\n",
    "\n",
    "remove local semantic key",
)
path.write_text(text, encoding="utf-8")


# --- worker_pool.py -----------------------------------------------------------------
path = Path("Run/worker_pool.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from typing import Any, Callable, Optional\n\nWORK_KIND_CONTINUATION",
    "from typing import Any, Callable, Optional\n\nfrom legal_action_identity import legal_action_semantic_key_text\nfrom reward_auto_progress import drain_trivial_reward_frontier\n\nWORK_KIND_CONTINUATION",
    "worker helper imports",
)
text = replace_once(
    text,
    '''@dataclass(frozen=True)\nclass ChoiceStepResult:\n    """What a worker reports after resolving a Choice with one action."""\n\n    step_result: dict\n    run_state: dict\n''',
    '''@dataclass(frozen=True)\nclass ChoiceStepResult:\n    """Visible-action result plus the settled frontier after RL-only auto transport."""\n\n    # Raw primary result of the Training-visible action. Keep this for transition/info\n    # semantics even when hidden auto actions are consumed afterward.\n    step_result: dict\n    run_state: dict\n    settled_observation: dict\n    settled_legal_actions: list[dict]\n    settled_room_context: dict\n    auto_action_ids: tuple[int, ...] = ()\n''',
    "ChoiceStepResult contract",
)
text = replace_once(
    text,
    '''                            "legal_keys": sorted(\n                                f"{a['action_type']}|{a['label']}" for a in reach.legal_actions\n                            ),\n''',
    '''                            "legal_keys": sorted(\n                                legal_action_semantic_key_text(action) for action in reach.legal_actions\n                            ),\n''',
    "Lease semantic signature",
)
old_execute = '''            if work_item.choice_type == "map":\n                # Map's "Step" is ChooseRoom(roomId), not Step(actionId) - resolve_action_id\n                # carries the chosen room_id in this case. Normalized into the same\n                # {"observation", "room_context", "transition", "info"} shape every other\n                # choice type's step_result dict has, so downstream comparison code needs\n                # no Map-specific branch.\n                entered = self.session.choose_room(work_item.resolve_action_id)\n                step_result = {\n                    "action_id": work_item.resolve_action_id,\n                    "observation": self.session.get_observation(),\n                    "room_context": self.session.get_room_context(),\n                    "transition": None,\n                    "info": entered.get("info", {}),\n                    "room_enter_result": entered,\n                }\n            else:\n                step_result = self.session.step(work_item.resolve_action_id)\n            self.current_context_id = None\n            return BranchResult(\n                status=BRANCH_STATUS_SUCCESS,\n                work_item=work_item,\n                execution_mode=request.execution_mode,\n                worker_slot=self.worker_slot,\n                worker_generation=self.worker_generation,\n                pid=self.pid,\n                reach=reach,\n                step=ChoiceStepResult(step_result=step_result, run_state=self.session.get_run_state()),\n            )\n'''
new_execute = '''            if work_item.choice_type == "map":\n                # Map's visible action is ChooseRoom(roomId). Preserve its raw result, then\n                # treat the entered room as a NEW frontier where trivial reward transport\n                # is allowed. The resulting hidden suffix belongs to the new room prefix.\n                entered = self.session.choose_room(work_item.resolve_action_id)\n                step_result = {\n                    "action_id": work_item.resolve_action_id,\n                    "observation": self.session.get_observation(),\n                    "room_context": self.session.get_room_context(),\n                    "transition": None,\n                    "info": entered.get("info", {}),\n                    "room_enter_result": entered,\n                }\n            else:\n                step_result = self.session.step(work_item.resolve_action_id)\n\n            auto = drain_trivial_reward_frontier(self.session)\n            settled_observation = self.session.get_observation()\n            settled_legal_actions = (\n                _map_rooms_as_legal_actions(self.session)\n                if settled_observation["boundary"] == "map_select"\n                else self.session.get_legal_actions()\n            )\n            settled_room_context = self.session.get_room_context()\n\n            self.current_context_id = None\n            return BranchResult(\n                status=BRANCH_STATUS_SUCCESS,\n                work_item=work_item,\n                execution_mode=request.execution_mode,\n                worker_slot=self.worker_slot,\n                worker_generation=self.worker_generation,\n                pid=self.pid,\n                reach=reach,\n                step=ChoiceStepResult(\n                    step_result=step_result,\n                    run_state=self.session.get_run_state(),\n                    settled_observation=settled_observation,\n                    settled_legal_actions=settled_legal_actions,\n                    settled_room_context=settled_room_context,\n                    auto_action_ids=auto.auto_action_ids,\n                ),\n            )\n'''
text = replace_once(text, old_execute, new_execute, "worker frontier drain")
path.write_text(text, encoding="utf-8")


# --- instance_whole_run.py ----------------------------------------------------------
path = Path("API/instance_whole_run.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "import run_emulator_bridge as bridge\nfrom whole_run_session import EVENT_CHOICE, MAP_SELECT, RUN_TERMINAL, WholeRunSession\n",
    "import run_emulator_bridge as bridge\nfrom reward_auto_progress import drain_trivial_reward_frontier\nfrom whole_run_session import EVENT_CHOICE, MAP_SELECT, RUN_TERMINAL, WholeRunSession\n",
    "instance auto drain import",
)
text = regex_once(
    text,
    r"def _build_child_view\(parent_view: _View, chosen_action_id: int, branch_result: BranchResult\) -> _View:\n.*?\n\nclass _BranchBookkeeping:",
    '''def _build_child_view(parent_view: _View, chosen_action_id: int, branch_result: BranchResult) -> _View:\n    """Build the Branch view from the SETTLED frontier, not the primary raw StepResult."""\n    step = branch_result.step\n    if step is None:\n        raise RuntimeError("successful branch result is missing ChoiceStepResult")\n\n    new_observation = step.settled_observation\n    new_boundary = new_observation["boundary"]\n    new_legal = step.settled_legal_actions\n    if parent_view.choice_type == "map":\n        new_room_id = chosen_action_id\n        # choose_room is outside a room's action prefix; hidden actions immediately\n        # after entry are the FIRST actions of the new room prefix.\n        new_action_prefix = tuple(step.auto_action_ids)\n    else:\n        new_room_id = parent_view.room_id\n        new_action_prefix = parent_view.action_prefix + (chosen_action_id,) + tuple(step.auto_action_ids)\n\n    return _View(\n        legal_actions_raw=new_legal,\n        boundary=new_boundary,\n        observation=new_observation,\n        room_context=step.settled_room_context,\n        map_snapshot=parent_view.map_snapshot,\n        room_id=new_room_id,\n        action_prefix=new_action_prefix,\n        choice_type=_choice_type_from_boundary(new_boundary),\n        chain_blocked=(new_boundary == MAP_SELECT),\n        event_rng_state=None,\n    )\n\n\nclass _BranchBookkeeping:''',
    "child view settled frontier",
)
text = replace_once(
    text,
    '''        self._bookkeeping: dict[str, _BranchBookkeeping] = {}\n        self._closed = False\n\n        self._maybe_capture_map_snapshot()\n''',
    '''        self._bookkeeping: dict[str, _BranchBookkeeping] = {}\n        self._closed = False\n\n        # StartRun itself normally reaches Neow/Event, but keep the same NEW-frontier\n        # rule here so any future start-of-run trivial PotionReward cannot leak to Training.\n        initial_auto = drain_trivial_reward_frontier(self._session)\n        self._action_prefix.extend(initial_auto.auto_action_ids)\n        self._maybe_capture_map_snapshot()\n''',
    "initial frontier drain",
)
old_commit = '''        try:\n            if view.boundary == MAP_SELECT:\n                self._session.choose_room(chosen["action_id"])\n                self._room_id = chosen["action_id"]\n                self._action_prefix = []\n            else:\n                self._session.step(chosen["action_id"])\n                self._action_prefix.append(chosen["action_id"])\n        except Exception as exc:  # noqa: BLE001\n            return {"status": STATUS_FAULTED, "error": str(exc), "fault_kind": FAULT_EMULATOR_ERROR}\n'''
new_commit = '''        try:\n            if view.boundary == MAP_SELECT:\n                self._session.choose_room(chosen["action_id"])\n                self._room_id = chosen["action_id"]\n                auto = drain_trivial_reward_frontier(self._session)\n                # Map choice is represented by room_id, not action_prefix. Hidden actions\n                # after entry are the first raw actions inside the new room.\n                self._action_prefix = list(auto.auto_action_ids)\n            else:\n                self._session.step(chosen["action_id"])\n                self._action_prefix.append(chosen["action_id"])\n                auto = drain_trivial_reward_frontier(self._session)\n                self._action_prefix.extend(auto.auto_action_ids)\n        except Exception as exc:  # noqa: BLE001\n            return {"status": STATUS_FAULTED, "error": str(exc), "fault_kind": FAULT_EMULATOR_ERROR}\n'''
text = replace_once(text, old_commit, new_commit, "root frontier drain")
text = replace_once(
    text,
    '''        step = result.step\n        new_observation = step.step_result["observation"]\n        new_boundary = new_observation["boundary"]\n        new_room_context = step.step_result["room_context"]\n''',
    '''        step = result.step\n        new_observation = step.settled_observation\n        new_boundary = new_observation["boundary"]\n        new_room_context = step.settled_room_context\n''',
    "branch settled result",
)
path.write_text(text, encoding="utf-8")

print("RL reward transport patch applied")
