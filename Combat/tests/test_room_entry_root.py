"""Real-Emulator coverage for the S8b room-entry root.

These tests intentionally use the run bridge, the real branch worker process, and the
singular relic ids.  They are not included in the whole-suite command; run this file
with the same Emulator/pythonnet environment as the other native integration tests.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_COMBAT = _ROOT / "Combat"
_RUN = _ROOT / "Run"
for _path in (_ROOT, _COMBAT, _RUN):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from choice_branch_runner import inject_relic, search_for_room_type  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
from API.combat_phase import CombatPhase, ROOT_BRANCHING_UNAVAILABLE_NO_ROOM_ENTRY_ANCHOR  # noqa: E402
from API.instance_whole_run import WholeRunInstance  # noqa: E402
from API.history_builder import HistoryBuilder  # noqa: E402
from API.identifiers import BranchIdRegistry, DecisionPointRegistry  # noqa: E402
from API.validation import RequestRejected  # noqa: E402
from search.branch_worker_pool import (  # noqa: E402
    BRANCH_STATUS_FAULT,
    BRANCH_STATUS_SUCCESS,
    BranchWorkerPool,
    LeaseRegistry,
    RoomEntryWorkerBootstrap,
    WorkItem,
    dispatch_work_items,
)
from search.candidate_pipeline import CandidatePipelineSuccess, build_candidate_pipeline_result  # noqa: E402
from search.decision_context import (  # noqa: E402
    BOUNDARY_PENDING,
    boundary_of_battle_state,
    BOUNDARY_STABLE,
    DecisionContext,
    DecisionSignature,
    RoomEntryReplayRoot,
    SemanticAction,
)


# The public DTO spells these boundaries differently from the internal constants:
# a published choice is "pending" inside the search code and "pending_choice" on the
# wire. Comparing a DTO string against BOUNDARY_PENDING silently never matches.
DTO_PENDING = "pending_choice"
DTO_STABLE = "stable"

def _root_context(relic_id: str):
    map_snapshot, room_id = search_for_room_type("CombatRoom", seed=1, max_hops=15)
    assert map_snapshot is not None
    root = RoomEntryReplayRoot(inject_relic(map_snapshot, relic_id), room_id)
    session = LiveCombatSession()
    state = RoomEntryWorkerBootstrap(session).bootstrap(root)
    # Without this the test passes on a room that opens Stable, i.e. without ever
    # exercising the case the room-entry root exists for. Both relics are chosen because
    # they publish a choice at combat start - TOOLBOX before the hand is drawn,
    # GAMBLING_CHIP after it - so a Stable board here means the relic never attached
    # (the id is singular; the plural degrades to DEPRECATED_RELIC and is silently absent).
    assert boundary_of_battle_state(state) == BOUNDARY_PENDING, (
        f"{relic_id} did not open the combat on a published choice: "
        f"{boundary_of_battle_state(state)}"
    )
    action = state._cached_legal_actions[0]  # noqa: SLF001
    signature = DecisionSignature.from_battle_state(
        state,
        semantic_action=SemanticAction(action["action_type"], action.get("semantic_key", "")),
        resolved_action=action,
    )
    context = DecisionContext(root, [], [], state, signature)
    pipeline = build_candidate_pipeline_result(context, width=1)
    assert isinstance(pipeline, CandidatePipelineSuccess), pipeline
    session.close()
    return context, pipeline.continuation_candidate


def test_spawned_worker_rebuilds_toolbox_and_gambling_chip_room_entry():
    for relic_id in ("TOOLBOX", "GAMBLING_CHIP"):
        context, candidate = _root_context(relic_id)
        item = WorkItem.from_candidate_ref(context, candidate, work_kind="continuation")
        with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
            result = dispatch_work_items([item], LeaseRegistry(), worker_pool=pool)[0]
        assert result.status == BRANCH_STATUS_SUCCESS, result.diagnostics


def test_spawned_worker_reports_room_entry_signature_mismatch():
    context, candidate = _root_context("TOOLBOX")
    bad_signature = dataclasses.replace(
        context.current_context_signature,
        boundary=BOUNDARY_STABLE,
        choice_scope=None,
        choice_kind=None,
        candidate_semantic_keys=None,
    )
    bad_context = dataclasses.replace(context, current_context_signature=bad_signature)
    bad_candidate = dataclasses.replace(candidate, current_context_signature=bad_signature)
    item = WorkItem.from_candidate_ref(bad_context, bad_candidate, work_kind="continuation")
    with BranchWorkerPool(worker_count=1, request_timeout_s=120.0) as pool:
        result = dispatch_work_items([item], LeaseRegistry(), worker_pool=pool)[0]
    assert result.status == BRANCH_STATUS_FAULT
    assert result.diagnostics["fault_kind"] == "replay_mismatch"
    assert result.diagnostics["stage"] == "context_signature"


def _whole_run_at_injected_combat(relic_id: str) -> WholeRunInstance:
    map_snapshot, room_id = search_for_room_type("CombatRoom", seed=1, max_hops=15)
    assert map_snapshot is not None
    effective_snapshot = inject_relic(map_snapshot, relic_id)

    instance = WholeRunInstance(
        f"room-entry-public-{relic_id}",
        {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0},
        branch_worker_count=1,
    )
    # The public instance has no relic-injection endpoint.  Loading the documented
    # Map Boundary snapshot is the same real-emulator setup used by the runner.
    instance._session.load_state(effective_snapshot)  # noqa: SLF001
    instance._map_snapshot = effective_snapshot  # noqa: SLF001
    instance._room_id = None  # noqa: SLF001
    instance._action_prefix = []  # noqa: SLF001
    return instance


@pytest.mark.parametrize("relic_id", ["TOOLBOX", "GAMBLING_CHIP"])
def test_public_whole_run_branch_is_usable_from_first_room_entry_decision(relic_id):
    instance = _whole_run_at_injected_combat(relic_id)
    try:
        start = instance.start_instance_response()
        room_action = next(
            action for action in start["masked_emulator_dto"]["legal_actions"]
            if action["parameters"]["point_type"] == "Monster"
        )
        entered = instance.commit_action(start["decision_point_id"], room_action["action_id"])
        assert entered["status"] == "completed", entered
        dto = entered["masked_emulator_dto"]
        assert dto["boundary"] == DTO_PENDING, (
            f"{relic_id} did not publish its room-entry choice: {dto['boundary']}"
        )
        assert instance._combat_phase._held_room_entry_root is not None  # noqa: SLF001

        action = dto["legal_actions"][0]
        branched = instance.emulate_actions(
            items=[{
                "parent_branch_id": "root",
                "branch_id": f"{relic_id.lower()}-first-choice-branch",
                "rng_id": 0,
                "decision_point_id": entered["decision_point_id"],
                "action_id": action["action_id"],
            }],
            simulation_options={"stop_condition": "next_decision"},
        )
        branch_id = f"{relic_id.lower()}-first-choice-branch"
        assert branched["branch_results"][branch_id]["status"] == "completed", branched
        child = instance.get_decision(branch_id)
        assert child["status"] == "completed", child
        assert child["masked_emulator_dto"]["boundary"] in {DTO_PENDING, DTO_STABLE}
    finally:
        instance.close()


@pytest.mark.parametrize("relic_id", ["TOOLBOX", "GAMBLING_CHIP"])
def test_room_entry_root_is_replaced_by_stable_snapshot_at_first_stable_board(relic_id):
    instance = _whole_run_at_injected_combat(relic_id)
    try:
        start = instance.start_instance_response()
        room_action = next(
            action for action in start["masked_emulator_dto"]["legal_actions"]
            if action["parameters"]["point_type"] == "Monster"
        )
        decision = instance.commit_action(start["decision_point_id"], room_action["action_id"])
        assert decision["masked_emulator_dto"]["boundary"] == DTO_PENDING

        for _ in range(8):
            phase = instance._combat_phase  # noqa: SLF001
            if phase._held_stable_snapshot is not None:  # noqa: SLF001
                break
            legal = decision["masked_emulator_dto"]["legal_actions"]
            decision = instance.commit_action(decision["decision_point_id"], legal[0]["action_id"])
            assert decision["status"] == "completed", decision
        else:
            raise AssertionError(f"{relic_id} did not reach a stable board")

        phase = instance._combat_phase  # noqa: SLF001
        assert phase._held_stable_snapshot is not None  # noqa: SLF001
        assert phase._held_room_entry_root is None  # noqa: SLF001
        assert phase.root_decision()[1] is not None
    finally:
        instance.close()


def test_pending_combat_without_map_snapshot_refuses_with_named_reason():
    map_snapshot, room_id = search_for_room_type("CombatRoom", seed=1, max_hops=15)
    session = LiveCombatSession(whole_run_mode=True)
    root_state = RoomEntryWorkerBootstrap(session).bootstrap(
        RoomEntryReplayRoot(inject_relic(map_snapshot, "TOOLBOX"), room_id)
    )
    phase = CombatPhase.adopt(
        session,
        root_state,
        worker_count=1,
        request_timeout_s=20.0,
        max_branches=4,
        worker_pool_backend="multiprocessing",
    )
    try:
        legal, context, boundary = phase.root_decision()
        assert boundary == BOUNDARY_PENDING
        assert legal
        assert context is None
        assert phase.root_branching_unavailable_reason == ROOT_BRANCHING_UNAVAILABLE_NO_ROOM_ENTRY_ANCHOR

        from API.instance_combat import CombatInstance

        facade = object.__new__(CombatInstance)
        facade._phase = phase  # noqa: SLF001
        facade._branch_ids = BranchIdRegistry()
        facade._decision_points = DecisionPointRegistry()
        facade._decision_points.issue("root")
        facade._root_branch_log = []
        facade._root_history = HistoryBuilder()
        facade._bookkeeping = {}
        facade._closed = False
        with pytest.raises(RequestRejected) as raised:
            facade.emulate_action(
                parent_branch_id="root",
                branch_id="no-room-entry-anchor",
                rng_id=0,
                decision_point_id=facade._decision_points.current("root"),
                action_id=str(legal[0]["action_id"]),
                simulation_options=None,
            )
        assert str(raised.value) == ROOT_BRANCHING_UNAVAILABLE_NO_ROOM_ENTRY_ANCHOR
    finally:
        phase.close()


if __name__ == "__main__":
    test_spawned_worker_rebuilds_toolbox_and_gambling_chip_room_entry()
    test_spawned_worker_reports_room_entry_signature_mismatch()
    print("PASS room-entry root integration tests")
