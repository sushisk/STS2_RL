"""1,000+ event mixed Choice-branch endurance test over a shared WholeRunWorkerPool.

Modeled on Combat/search/pending_lease_endurance_runner.py's established pattern
(StopConditionError for immediate-fail invariant violations, a dataclass observer,
periodic fault injection, a JSON summary written at the end).

Cycles through all 6 choice types across a small set of seeds, reusing cached
(map_snapshot, room_id) pairs discovered once per (seed, choice_type) via
`WholeRunWorkerPool.explore()`, and periodically kills+respawns a worker to verify the
pool recovers cleanly (fresh generation, old Leases invalidated, subsequent events still
succeed).
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from process_choice_branch_runner import (
    ALL_CHOICE_TYPES,
    CHOICE_COMBAT_PENDING,
    ROOM_TYPE_FOR_CHOICE,
    TOOLBOX_RELIC_ID,
    run_choice_branch,
)
from worker_fault_injection import kill_worker_process, respawn_and_verify
from worker_pool import ExploreRequest, LeaseRegistry, WholeRunWorkerPool

SEEDS = [18, 2, 4]
DEFAULT_OUTPUT_PATH = Path(r"C:\STS2_RL\Outputs\reports\whole_run_logs\worker_pool_endurance_1000.json")


class StopConditionError(RuntimeError):
    """Raised for an invariant violation that must halt the endurance run immediately."""


@dataclass
class EnduranceObserver:
    total_events: int = 0
    per_choice_type_counts: dict = field(default_factory=lambda: {ct: 0 for ct in ALL_CHOICE_TYPES})
    per_choice_type_ok: dict = field(default_factory=lambda: {ct: 0 for ct in ALL_CHOICE_TYPES})
    faults_injected: int = 0
    faults_recovered: int = 0
    pids_seen_by_slot: dict = field(default_factory=dict)
    process_table: list = field(default_factory=list)

    def note_pid(self, slot: Any, pid: "int | None") -> None:
        if pid is None:
            return
        self.pids_seen_by_slot.setdefault(slot, set()).add(pid)

    def record_event(self, event_index: int, choice_type: str, attempt) -> None:
        self.total_events += 1
        self.per_choice_type_counts[choice_type] += 1
        if attempt.ok:
            self.per_choice_type_ok[choice_type] += 1
        for entry in attempt.process_table:
            self.note_pid(entry["worker_slot"], entry["pid"])
            self.process_table.append(
                {
                    "event_index": event_index,
                    "choice_type": choice_type,
                    "role": entry["role"],
                    "worker_slot": entry["worker_slot"],
                    "worker_generation": entry["worker_generation"],
                    "pid": entry["pid"],
                    "context_id": attempt.context_id,
                }
            )

    def summary(self) -> dict:
        return {
            "total_events": self.total_events,
            "per_choice_type_counts": self.per_choice_type_counts,
            "per_choice_type_ok": self.per_choice_type_ok,
            "faults_injected": self.faults_injected,
            "faults_recovered": self.faults_recovered,
            "distinct_pids_per_slot": {str(k): sorted(v) for k, v in self.pids_seen_by_slot.items()},
        }


def _discover_snapshots(pool: WholeRunWorkerPool, seed: int) -> dict:
    target_room_types = sorted({rt for rt in ROOM_TYPE_FOR_CHOICE.values()})
    explored = pool.explore(
        ExploreRequest(
            seed=seed, character_id="Ironclad", ascension=0, min_rooms=10, max_steps=1500,
            target_room_types=target_room_types,
        )
    )
    return explored.found_snapshots


def run_endurance(
    *,
    total_events: int = 1000,
    branch_worker_count: int = 3,
    fault_every: int = 75,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> dict:
    observer = EnduranceObserver()
    started = time.time()

    with WholeRunWorkerPool(branch_worker_count=branch_worker_count) as pool:
        snapshots_by_seed: dict[int, dict] = {}
        for seed in SEEDS:
            snapshots_by_seed[seed] = _discover_snapshots(pool, seed)

        event_index = 0
        seed_cycle = 0
        while event_index < total_events:
            seed = SEEDS[seed_cycle % len(SEEDS)]
            seed_cycle += 1
            found = snapshots_by_seed[seed]
            for choice_type in ALL_CHOICE_TYPES:
                if event_index >= total_events:
                    break
                if choice_type == "map":
                    info = found.get("__first_map__")
                else:
                    info = found.get(ROOM_TYPE_FOR_CHOICE[choice_type])
                if info is None:
                    continue  # this seed didn't reach that room type - not a fault, just skip

                registry = LeaseRegistry()
                relic = TOOLBOX_RELIC_ID if choice_type == CHOICE_COMBAT_PENDING else None
                try:
                    attempt = run_choice_branch(
                        pool, registry, choice_type, info["map_snapshot"], info.get("room_id"), relic_injection=relic
                    )
                except Exception as exc:  # noqa: BLE001
                    raise StopConditionError(
                        f"event {event_index}: {choice_type} raised {type(exc).__name__}: {exc}"
                    ) from exc

                event_index += 1
                observer.record_event(event_index, choice_type, attempt)

                if not attempt.ok:
                    # combat_pending's Toolbox add-card path is documented as a known,
                    # low-frequency, single-process-era caveat - re-check whether it
                    # still occurs under process isolation; if ANY check other than
                    # content divergence fails, or if it fails for anything other than
                    # combat_pending, stop hard rather than silently continue.
                    failing = [k for k, v in attempt.checks.items() if not v]
                    if failing != ["different_choices_diverge"] or choice_type != CHOICE_COMBAT_PENDING:
                        raise StopConditionError(
                            f"event {event_index}: {choice_type} ok=False checks={attempt.checks}"
                        )

                pids = {e["pid"] for e in attempt.process_table if e["role"] in ("holder_resolve", "sibling_resolve")}
                if len(pids) < 2:
                    raise StopConditionError(
                        f"event {event_index}: {choice_type} holder/sibling PIDs did not differ: {attempt.process_table}"
                    )

                if event_index % fault_every == 0:
                    slot = pool.branch_worker_slots[event_index % len(pool.branch_worker_slots)]
                    observer.faults_injected += 1
                    kill_worker_process(pool, slot)
                    fi = respawn_and_verify(pool, LeaseRegistry(), slot)
                    if fi.new_generation != fi.old_generation + 1 or fi.new_pid == fi.old_pid:
                        raise StopConditionError(f"event {event_index}: respawn of slot {slot} did not produce a fresh generation/pid: {fi}")
                    # verify the pool is usable again immediately after respawn
                    probe_info = found.get("__first_map__")
                    from process_choice_branch_runner import CHOICE_MAP

                    recovery_attempt = run_choice_branch(pool, LeaseRegistry(), CHOICE_MAP, probe_info["map_snapshot"], None)
                    if not recovery_attempt.ok:
                        raise StopConditionError(f"event {event_index}: post-respawn recovery attempt failed: {recovery_attempt.checks}")
                    observer.faults_recovered += 1

    elapsed = time.time() - started
    summary = observer.summary()
    summary["elapsed_seconds"] = elapsed
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "process_table": observer.process_table}, f, indent=2)
    return summary


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    result = run_endurance(total_events=int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
    print(json.dumps(result, indent=2))
