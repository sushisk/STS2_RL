"""Real-Emulator coverage for the S8b room-entry root.

These tests intentionally use the run bridge, the real branch worker process, and the
singular relic ids.  They are not included in the whole-suite command; run this file
with the same Emulator/pythonnet environment as the other native integration tests.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_COMBAT = _ROOT / "Combat"
_RUN = _ROOT / "Run"
for _path in (_ROOT, _COMBAT, _RUN):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from choice_branch_runner import inject_relic, search_for_room_type  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402
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


if __name__ == "__main__":
    test_spawned_worker_rebuilds_toolbox_and_gambling_chip_room_entry()
    test_spawned_worker_reports_room_entry_signature_mismatch()
    print("PASS room-entry root integration tests")
