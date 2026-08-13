"""AssemblyLoadContext-backed Branch Worker Pool.

The original ``BranchWorkerPool`` uses one spawned OS process per live
``LiveCombatSession`` because the decompiled engine's singleton state made multiple
ordinary ``GameInstance`` objects unsafe inside one process. ``Sts2Emulator.Isolation``
now gives Python a different unit of isolation: each worker thread owns one
``IsolatedGameSession`` loaded into its own .NET ``AssemblyLoadContext``. That preserves
the same worker-affinity and lease semantics while avoiding per-worker process spawn and
full CoreCLR bootstrap costs.

This module is deliberately an additional, opt-in pool. It reuses
``branch_worker_pool._WorkerRuntime`` for request execution, replay, lease, pending
choice, terminal, and fault normalization behavior; only the worker transport changes
from multiprocessing queues/processes to Python queues/threads and isolated CLR sessions.
"""

from __future__ import annotations

import dataclasses
import json
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from battle_emulator import BattleEmulator, build_scenario_from_spec, build_scenario_from_state
from combat_state_snapshot import CombatStateSnapshot
from emulator_bridge import (
    DEFAULT_REPO_ROOT,
    ensure_loaded,
    legal_actions_to_list,
    to_plain,
)
from live_combat_session import LiveCombatSession
from search.branch_worker_pool import (
    BranchResult,
    LeaseRegistry,
    WorkerExecutionRequest,
    _WorkerRuntime,
    _fault_result,
    _is_process_tainting_fault,
    dispatch_work_items,
)


_isolation_types: dict[str, Any] | None = None


def ensure_isolation_loaded(repo_root: Path | None = None) -> dict[str, Any]:
    """Load ``Sts2Emulator.Isolation`` into pythonnet's default runtime once."""

    global _isolation_types
    if _isolation_types is not None:
        return _isolation_types

    root = repo_root or DEFAULT_REPO_ROOT
    ensure_loaded(root)
    isolation_path = root / "Sts2Emulator.Isolation" / "bin" / "Debug" / "net8.0" / "Sts2Emulator.Isolation.dll"
    if not isolation_path.exists():
        raise FileNotFoundError(f"Sts2Emulator.Isolation.dll was not found: {isolation_path}")

    import clr  # noqa: E402

    clr.AddReference(str(isolation_path))
    from Sts2Emulator.Isolation import IsolatedGameSessionFactory  # noqa: E402

    _isolation_types = {"IsolatedGameSessionFactory": IsolatedGameSessionFactory}
    return _isolation_types


def _emulator_output_dir(repo_root: Path | None = None) -> Path:
    return (repo_root or DEFAULT_REPO_ROOT) / "Sts2Emulator.Cli" / "bin" / "Debug" / "net8.0"


def _scenario_json_for_isolated_session(scenario_spec: dict) -> str:
    """Serialize through the existing DTO builder so isolated sessions get identical input."""

    return _scenario_json_from_clr(build_scenario_from_spec(scenario_spec))


def _scenario_json_from_clr(scenario: Any) -> str:
    types = ensure_loaded()
    return str(types["JsonSerializer"].Serialize(scenario))


def _snapshot_json(snapshot: Any) -> str:
    if isinstance(snapshot, str):
        try:
            payload = json.loads(snapshot)
        except json.JSONDecodeError:
            return snapshot
        from combat_state_snapshot import canonical_json

        return canonical_json(_strip_python_snapshot_only_fields(payload), exclude_volatile=False)
    if dataclasses.is_dataclass(snapshot):
        snapshot = dataclasses.asdict(snapshot)
    if isinstance(snapshot, dict):
        from combat_state_snapshot import canonical_json

        return canonical_json(_strip_python_snapshot_only_fields(snapshot), exclude_volatile=False)
    return str(ensure_loaded()["JsonSerializer"].Serialize(snapshot))


def _strip_python_snapshot_only_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_python_snapshot_only_fields(child)
            for key, child in value.items()
            if key != "unknown_fields"
        }
    if isinstance(value, list):
        return [_strip_python_snapshot_only_fields(child) for child in value]
    return value


class IsolatedLiveCombatSession(LiveCombatSession):
    """``LiveCombatSession`` facade over one thread-affine ``IsolatedGameSession``."""

    def __init__(self, isolated_session, repo_root: Any | None = None, use_legal_action_cache: bool = True) -> None:
        super().__init__(repo_root=repo_root, use_legal_action_cache=use_legal_action_cache)
        self._isolated_session = isolated_session
        self._game = isolated_session
        self._emulator = BattleEmulator(repo_root=repo_root, use_legal_action_cache=use_legal_action_cache)

    def start_combat(self, scenario_spec: dict):
        scenario = self._isolated_session.DeserializeCombatScenario(_scenario_json_for_isolated_session(scenario_spec))
        reset_result = self._isolated_session.ResetFromScenario(scenario)
        obs = reset_result.Observation
        enemy_max_hps = {e["index"]: e["maxHp"] for e in to_plain(obs.State).get("enemies") or []}
        legal_actions = legal_actions_to_list(reset_result.LegalActions)
        battle_state = self._emulator._wrap(obs, turn=1, enemy_max_hps=enemy_max_hps, legal_actions=legal_actions)  # noqa: SLF001
        self._current_frame = battle_state.decision_frame
        self._session_faulted = False
        return battle_state

    def restore_snapshot_json(self, json_text: str):
        result = self._isolated_session.RestoreSnapshotJson(json_text)
        return self._finish_restore(result)

    def restore_snapshot(self, snapshot):
        return self.restore_snapshot_json(_snapshot_json(snapshot))

    def resume_from(self, battle_state):
        # ``build_scenario_from_state`` returns a CLR DTO, not a spec dict. Use the
        # default serializer directly here to keep the isolated DTO graph compatible.
        scenario_json = _scenario_json_from_clr(build_scenario_from_state(battle_state.engine_state, battle_state.shuffle_rng_seed))
        result = self._isolated_session.ResetFromScenario(self._isolated_session.DeserializeCombatScenario(scenario_json))
        obs = result.Observation
        legal_actions = legal_actions_to_list(result.LegalActions)
        fresh_state = self._emulator._wrap(  # noqa: SLF001
            obs,
            turn=battle_state.turn,
            enemy_max_hps=battle_state.enemy_max_hps,
            shuffle_rng_seed=battle_state.shuffle_rng_seed,
            legal_actions=legal_actions,
        )
        self._current_frame = fresh_state.decision_frame
        self._session_faulted = False
        return fresh_state

    def get_observation(self):
        self._ensure_session_not_faulted()
        return self._isolated_session.GetObservation()

    def get_legal_actions(self) -> list[dict]:
        self._ensure_session_not_faulted()
        return legal_actions_to_list(self._isolated_session.GetLegalActions())

    def capture_snapshot(self):
        self._ensure_session_not_faulted()
        return CombatStateSnapshot.from_json(str(self._isolated_session.CaptureSnapshotJson()))

    def _is_still_current(self) -> bool:
        obs = self._isolated_session.GetObservation()
        return (
            getattr(obs, "CombatSessionId", None) == self._current_frame.combat_session_id
            and int(getattr(obs, "StepIndex", -1)) == self._current_frame.step_index
        )

    def _resynchronize(self, battle_state) -> None:
        scenario_json = _scenario_json_from_clr(build_scenario_from_state(battle_state.engine_state, battle_state.shuffle_rng_seed))
        self._isolated_session.ResetFromScenario(self._isolated_session.DeserializeCombatScenario(scenario_json))
        self.last_step_resynchronized = True
        self.resynchronize_count += 1
        self._session_faulted = False


@dataclass
class _ThreadWorkerHandle:
    worker_id: int
    worker_generation: int
    thread: threading.Thread
    in_queue: queue.Queue
    runtime: _WorkerRuntime
    pid: Optional[int] = None


class AlcBranchWorkerPool:
    """Persistent Branch Worker Pool backed by ALC-isolated sessions and threads."""

    def __init__(
        self,
        *,
        worker_count: int = 2,
        repo_root: Optional[Path | str] = None,
        request_timeout_s: float = 120.0,
    ) -> None:
        if worker_count <= 0:
            raise ValueError("worker_count must be positive")
        self.worker_count = worker_count
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else DEFAULT_REPO_ROOT
        self.request_timeout_s = request_timeout_s
        self._result_queue: queue.Queue = queue.Queue()
        self._workers: dict[int, _ThreadWorkerHandle] = {}
        self._next_bootstrap_index = 0
        self._next_request_id = 0
        self._closed = False

        factory = ensure_isolation_loaded(self.repo_root)["IsolatedGameSessionFactory"]
        sessions = list(factory.CreateMany(str(_emulator_output_dir(self.repo_root)), worker_count, "STS2_RL.ALCWorker"))
        for worker_id, isolated_session in enumerate(sessions):
            self._spawn_worker(worker_id, generation=1, isolated_session=isolated_session)

    def _spawn_worker(self, worker_id: int, *, generation: int, isolated_session) -> None:
        in_queue: queue.Queue = queue.Queue()
        runtime = _WorkerRuntime(
            worker_id,
            generation,
            str(self.repo_root),
            session_factory=lambda s=isolated_session: IsolatedLiveCombatSession(s, repo_root=self.repo_root),
        )
        thread = threading.Thread(target=self._worker_main, args=(runtime, in_queue), daemon=True, name=f"alc-branch-worker-{worker_id}")
        thread.start()
        self._workers[worker_id] = _ThreadWorkerHandle(worker_id, generation, thread, in_queue, runtime)

    def _worker_main(self, runtime: _WorkerRuntime, in_queue: queue.Queue) -> None:
        while True:
            message = in_queue.get()
            if message is None:
                return
            request_id, request = message
            try:
                result = runtime.execute(request)
            except BaseException as exc:  # noqa: BLE001
                result = _fault_result(request.work_item, request.execution_mode, runtime.worker_id, runtime.worker_generation, exc)
            self._result_queue.put((request_id, result))

    @property
    def worker_generations(self) -> dict[int, int]:
        return {worker_id: handle.worker_generation for worker_id, handle in self._workers.items()}

    @property
    def worker_ids(self) -> list[int]:
        return list(self._workers)

    @property
    def worker_pids(self) -> dict[int, Optional[int]]:
        return {worker_id: None for worker_id in self._workers}

    def is_worker_alive(self, worker_id: int) -> bool:
        handle = self._workers.get(worker_id)
        return handle is not None and handle.thread.is_alive()

    def respawn_worker(self, worker_id: int, lease_registry: Optional[LeaseRegistry] = None) -> None:
        if self._closed:
            raise RuntimeError("AlcBranchWorkerPool is closed")
        handle = self._workers[worker_id]
        old_generation = handle.worker_generation
        handle.in_queue.put(None)
        factory = ensure_isolation_loaded(self.repo_root)["IsolatedGameSessionFactory"]
        isolated_session = factory.Create(str(_emulator_output_dir(self.repo_root)), f"STS2_RL.ALCWorker.{worker_id}.gen{old_generation + 1}")
        self._spawn_worker(worker_id, generation=old_generation + 1, isolated_session=isolated_session)
        if lease_registry is not None:
            lease_registry.invalidate_worker(worker_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in self._workers.values():
            handle.in_queue.put(None)
        for handle in self._workers.values():
            handle.thread.join(timeout=10)

    def __enter__(self) -> "AlcBranchWorkerPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _submit(self, worker_id: int, request: WorkerExecutionRequest) -> int:
        if self._closed:
            raise RuntimeError("AlcBranchWorkerPool is closed")
        self._next_request_id += 1
        request_id = self._next_request_id
        self._workers[worker_id].in_queue.put((request_id, request))
        return request_id

    def execute(
        self,
        worker_id: int,
        request: WorkerExecutionRequest,
        *,
        lease_registry: Optional[LeaseRegistry] = None,
    ) -> BranchResult:
        stale_generation = self._workers[worker_id].worker_generation
        request_id = self._submit(worker_id, request)
        while True:
            try:
                received_id, result = self._result_queue.get(timeout=self.request_timeout_s)
            except queue.Empty:
                self.respawn_worker(worker_id, lease_registry=lease_registry)
                return _fault_result(
                    request.work_item,
                    request.execution_mode,
                    worker_id,
                    stale_generation,
                    TimeoutError(f"timed out waiting for worker request {request_id}"),
                    fault_kind="task_timeout",
                )
            if received_id != request_id:
                continue
            if result.status == "fault" and result.worker_id is not None and _is_process_tainting_fault(result.diagnostics):
                self.respawn_worker(result.worker_id, lease_registry=lease_registry)
            return result

    def dispatch_work_items(self, work_items, lease_registry: LeaseRegistry) -> list[BranchResult]:
        return dispatch_work_items(work_items, lease_registry, worker_pool=self)
