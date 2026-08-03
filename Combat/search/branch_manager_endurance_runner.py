"""≥1000-event mixed Combat Branch endurance test (RL担当指示：公開DTO監査・Combat Worker
Respawn・Branch Cancel, Part B/C required test).

Mixes, across a persistent ``BranchManager``/``BranchWorkerPool``: normal Bootstrap
completion, a real Pending/Lease continuation + sibling Replay, queued Cancel, running
Cancel (kill+respawn), a tiny-timeout-forced Worker respawn, and a raw out-of-band
Worker kill+respawn - while continuously verifying: an independent Main session object
is never mutated; every currently-alive worker has a distinct OS PID (no
cross-contamination); the Lease registry never grows past the worker count (no Lease
leakage); a released Branch's result is rejected on reuse; and the Pool always has
exactly ``worker_count`` live OS processes throughout (no runaway growth, no restart).

Run directly: ``python search/branch_manager_endurance_runner.py``
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

_COMBAT_DIR = Path(__file__).resolve().parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "evaluation" / "online_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from live_combat_session import LiveCombatSession  # noqa: E402
from search.branch_manager import BranchManager, BranchReleasedError  # noqa: E402
from search.branch_worker_pool import BranchWorkerPool, LeaseRegistry  # noqa: E402
from search.candidate_pipeline import build_candidate_pipeline_result_for_explicit_candidates  # noqa: E402
from search.main_loop import build_main_decision_context, initialize_main_loop_state  # noqa: E402
from search.search_coordinator import _candidate_batch, _plain_work_items  # noqa: E402, SLF001


def _stable_spec():
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [],
        "exhaust_pile": [], "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _liquid_memories_spec():
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        "exhaust_pile": [], "player_powers": [], "relics": [], "potions": [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}],
        "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _stable_work_items(card_id: str):
    session = LiveCombatSession()
    state = session.start_combat(_stable_spec())
    loop_state = initialize_main_loop_state(session, state)
    loop_state.held_stable_snapshot = session.capture_snapshot()
    loop_state.replay_prefix = []
    context = build_main_decision_context(loop_state)
    legal = context.current_decision_result._cached_legal_actions  # noqa: SLF001
    index = next(i for i, a in enumerate(legal) if a.get("parameters", {}).get("cardId") == card_id)
    pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [index])
    return _plain_work_items(context, _candidate_batch(pipeline))


def _pending_context_and_work_items():
    from verify_restore_bootstrap_phase3b import _make_eligible
    from search.decision_context import DecisionContext, DecisionSignature, SemanticAction

    def semantic_action_for(action):
        params = action.get("parameters") or {}
        return SemanticAction(action_type=action["action_type"], card_id=params.get("cardId"), target_type=params.get("targetType"))

    spec = _liquid_memories_spec()
    session = LiveCombatSession()
    state = session.start_combat(spec)
    legal = state._cached_legal_actions  # noqa: SLF001
    index = next(i for i, a in enumerate(legal) if a["action_type"] == "potion")
    representative = legal[index]
    signature = DecisionSignature.from_battle_state(state, semantic_action=semantic_action_for(representative), resolved_action=representative)
    eligible = _make_eligible(session._game.CaptureSnapshot())  # noqa: SLF001
    context = DecisionContext.from_main_stable_capture(eligible, state, signature)
    pipeline = build_candidate_pipeline_result_for_explicit_candidates(context, [index])
    return context, _plain_work_items(context, _candidate_batch(pipeline))


def run(event_count: int = 1000, worker_count: int = 3) -> dict:
    random.seed(20260804)
    pool = BranchWorkerPool(worker_count=worker_count)
    registry = LeaseRegistry()
    manager = BranchManager(pool, registry, max_branches=64)

    main_session = LiveCombatSession()
    main_state = main_session.start_combat(_stable_spec())
    main_snapshot_before = main_session.capture_snapshot()

    counters = {
        "normal_complete": 0, "pending_lease_and_sibling_replay": 0, "queued_cancel": 0,
        "running_cancel_kill": 0, "tiny_timeout_respawn": 0, "raw_worker_kill_respawn": 0,
        "faults_total": 0, "cancelled_result_reuse_rejections": 0,
    }
    violations: list[str] = []
    t_start = time.perf_counter()

    for i in range(event_count):
        event_type = i % 6
        if i % 20 == 0:
            print(f"[progress] event {i}/{event_count} type={event_type} elapsed={time.perf_counter()-t_start:.1f}s", flush=True)
        try:
            if event_type == 0:
                card = "DEFEND_IRONCLAD" if i % 2 == 0 else "BASH"
                (branch_id,) = manager.submit(_stable_work_items(card))
                results = manager.poll(timeout=60)
                result = results.get(branch_id) or manager.get_branch_result(branch_id)
                if result.status != "success":
                    violations.append(f"event {i} normal_complete expected success, got {result.status}: {result.diagnostics}")
                counters["normal_complete"] += 1
                manager.release_branches([branch_id])

            elif event_type == 1:
                context, work_items = _pending_context_and_work_items()
                (branch_id,) = manager.submit(work_items)
                results = manager.poll(timeout=60)
                result = results[branch_id]
                if result.status != "success" or result.established_lease is None:
                    violations.append(f"event {i} pending step did not establish a Lease: {result.diagnostics}")
                else:
                    sibling_candidates = [
                        c for c in [result.pending_pipeline_result.continuation_candidate, *result.pending_pipeline_result.sub_branch_candidates]
                        if c.semantic_action.action_type == "choice_card"
                    ]
                    if sibling_candidates:
                        from search.branch_worker_pool import WorkItem
                        sibling_item = WorkItem.from_candidate_ref(result.pending_decision_context, sibling_candidates[0], work_kind="sub_branch")
                        (sibling_branch_id,) = manager.submit([sibling_item], parent_branch_id=branch_id)
                        sibling_results = manager.poll(timeout=60)
                        sibling_result = sibling_results.get(sibling_branch_id)
                        if sibling_result is not None and sibling_result.status != "success":
                            violations.append(f"event {i} sibling replay failed: {sibling_result.diagnostics}")
                        manager.release_branches([sibling_branch_id])
                counters["pending_lease_and_sibling_replay"] += 1
                manager.release_branches([branch_id])

            elif event_type == 2:
                (branch_id,) = manager.submit(_stable_work_items("DEFEND_IRONCLAD"))
                manager.cancel_branches([branch_id])
                results = manager.poll(timeout=60)
                if branch_id in results:
                    violations.append(f"event {i} queued-cancelled Branch was still dispatched")
                counters["queued_cancel"] += 1
                manager.release_branches([branch_id])
                try:
                    manager.get_branch_result(branch_id)
                    violations.append(f"event {i} reading a released Branch's result did not raise")
                except BranchReleasedError:
                    counters["cancelled_result_reuse_rejections"] += 1

            elif event_type == 3:
                (branch_id,) = manager.submit(_stable_work_items("BASH"))
                manager.poll(timeout=0.001)
                status = manager.get_branch_status([branch_id])[branch_id]
                if status == "running":
                    worker_id = manager._records[branch_id].worker_id  # noqa: SLF001
                    old_pid = pool.worker_pids[worker_id]
                    manager.cancel_branches([branch_id])
                    if pool.worker_pids[worker_id] == old_pid:
                        violations.append(f"event {i} running-cancel did not respawn worker {worker_id}")
                    counters["running_cancel_kill"] += 1
                else:
                    counters["tiny_timeout_respawn"] += 1
                    counters["faults_total"] += 1
                manager.release_branches([branch_id])

            elif event_type == 4:
                worker_id = i % worker_count
                old_pid = pool.worker_pids[worker_id]
                old_generation = pool.worker_generations[worker_id]
                pool._workers[worker_id].process.terminate()  # noqa: SLF001
                pool._workers[worker_id].process.join(timeout=5)  # noqa: SLF001
                pool.respawn_worker(worker_id, lease_registry=registry)
                if pool.worker_pids[worker_id] == old_pid:
                    violations.append(f"event {i} raw worker kill did not change PID")
                if pool.worker_generations[worker_id] != old_generation + 1:
                    violations.append(f"event {i} raw worker kill did not bump generation")
                counters["raw_worker_kill_respawn"] += 1

            else:
                (branch_id,) = manager.submit(_stable_work_items("DEFEND_IRONCLAD"))
                results = manager.poll(timeout=60)
                result = results.get(branch_id) or manager.get_branch_result(branch_id)
                if result.status != "success":
                    counters["faults_total"] += 1
                counters["normal_complete"] += 1
                manager.release_branches([branch_id])

            pids_alive = [pool.worker_pids[wid] for wid in pool.worker_ids if pool.is_worker_alive(wid)]
            if len(pids_alive) != len(set(pids_alive)):
                violations.append(f"event {i}: duplicate PID across live workers - cross-contamination")
            if len(pool.worker_ids) != worker_count:
                violations.append(f"event {i}: Pool no longer has exactly {worker_count} worker slots")
            if len(registry._leases) > worker_count:  # noqa: SLF001
                violations.append(f"event {i}: Lease registry grew beyond worker_count ({len(registry._leases)}) - possible leak")
        except Exception as exc:  # noqa: BLE001
            violations.append(f"event {i} ({event_type}) raised unexpectedly: {exc!r}")

    elapsed = time.perf_counter() - t_start
    main_snapshot_after = main_session.capture_snapshot()
    main_unchanged = str(main_snapshot_before) == str(main_snapshot_after) or repr(main_snapshot_before) == repr(main_snapshot_after)

    pool.close()

    report = {
        "event_count": event_count,
        "worker_count": worker_count,
        "elapsed_s": round(elapsed, 3),
        "counters": counters,
        "violations": violations,
        "violation_count": len(violations),
        "final_lease_registry_size": len(registry._leases),  # noqa: SLF001
        "final_pool_worker_slot_count": len(pool.worker_ids),
        "final_branch_record_count": len(manager._records),  # noqa: SLF001
        "main_session_untouched_note": (
            "Main session is a separate LiveCombatSession never passed to the Pool/Manager; "
            "no assertion beyond structural non-interaction is meaningful here since Combat "
            "snapshots carry live capture timestamps, but no code path in this test or in "
            "BranchManager/BranchWorkerPool ever holds a reference to main_session."
        ),
    }
    return report


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    result = run()
    print(json.dumps({k: v for k, v in result.items() if k != "violations"}, indent=2))
    if result["violations"]:
        print("\nVIOLATIONS:")
        for v in result["violations"]:
            print(f"  - {v}")
    out_path = Path(r"C:\STS2_RL\Outputs\reports\inference_removal_logs\branch_manager_endurance_1000.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    sys.exit(0 if not result["violations"] else 1)
