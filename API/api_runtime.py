"""OS-process transport for the RL-Training Communication contract v0.5.

Training and the RL Runtime are separate OS processes (`multiprocessing.get_context
("spawn")`, Windows-safe, matching every other Worker Pool in this repo). Every message
that crosses the Queue is a plain JSON-safe dict - no CLR objects, no dataclasses.

Critical: `pythonnet`/CLR is initialized ONLY inside `_rl_runtime_process_main`, which
runs in the SPAWNED CHILD process - `RLApiServerProcess` itself (constructed and held by
the PARENT/Training-side process) imports `API.server`/`instance_combat`/
`instance_whole_run` LAZILY, only inside the child's entry point, so simply
constructing/holding an `RLApiServerProcess` in the Training process never touches CLR.
"""

from __future__ import annotations

import multiprocessing
import queue
from pathlib import Path
from typing import Any, Optional

from API.dto import FAULT_TASK_TIMEOUT, SCHEMA_VERSION, STATUS_FAULTED

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _rl_runtime_process_main(in_queue, out_queue, repo_root: str) -> None:
    import sys

    for sub in ("Combat", "Run"):
        p = str(Path(repo_root) / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from API.server import RLApiServer

    server = RLApiServer()
    try:
        while True:
            message = in_queue.get()
            if message is None:
                return
            internal_id, payload = message
            try:
                response = server.handle_request(payload)
            except BaseException as exc:  # noqa: BLE001 - the RL Runtime process must never crash silently.
                response = {
                    "schema_version": SCHEMA_VERSION,
                    "request_id": payload.get("request_id") if isinstance(payload, dict) else None,
                    "operation": payload.get("operation") if isinstance(payload, dict) else None,
                    "status": STATUS_FAULTED,
                    "error": f"{type(exc).__name__}: {exc}",
                    "fault_kind": "emulator_error",
                }
                # Contract §2.3: Instance対象RequestのResponseは同じinstance_idを必須とする。
                if isinstance(payload, dict) and payload.get("instance_id") is not None:
                    response["instance_id"] = payload["instance_id"]
            out_queue.put((internal_id, response))
    finally:
        server.close_all()


class RLApiServerProcess:
    """Training-side handle: owns the spawned RL Runtime OS process and provides a
    synchronous, timeout-bounded `call(request_dict) -> response_dict`.
    """

    def __init__(self, *, repo_root: Optional[Path] = None, request_timeout_s: float = 60.0) -> None:
        self.request_timeout_s = request_timeout_s
        self._ctx = multiprocessing.get_context("spawn")
        self._in_queue = self._ctx.Queue()
        self._out_queue = self._ctx.Queue()
        self._process = self._ctx.Process(
            target=_rl_runtime_process_main,
            args=(self._in_queue, self._out_queue, str(repo_root or _REPO_ROOT)),
            # NOT daemonic: this process itself spawns Combat/Whole Run Branch Worker
            # child processes (BranchWorkerPool/WholeRunWorkerPool), and Python disallows
            # a daemonic process from having children of its own.
            daemon=False,
        )
        self._process.start()
        self._next_internal_id = 0
        self._closed = False

    @property
    def pid(self) -> "int | None":
        return self._process.pid

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def call(self, payload: dict) -> dict:
        if self._closed:
            raise RuntimeError("RLApiServerProcess is closed")
        self._next_internal_id += 1
        internal_id = self._next_internal_id
        self._in_queue.put((internal_id, payload))
        try:
            received_id, response = self._out_queue.get(timeout=self.request_timeout_s)
        except queue.Empty:
            response = {
                "schema_version": SCHEMA_VERSION,
                "request_id": payload.get("request_id"),
                "operation": payload.get("operation"),
                "status": STATUS_FAULTED,
                "error": "RL Runtime process did not respond within request_timeout_s",
                "fault_kind": FAULT_TASK_TIMEOUT,
            }
            # Contract §2.3: Instance対象RequestのResponseは同じinstance_idを必須とする。
            if payload.get("instance_id") is not None:
                response["instance_id"] = payload["instance_id"]
            return response
        if received_id != internal_id:
            raise RuntimeError("RLApiServerProcess.call() received an out-of-order response")
        return response

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._in_queue.put(None)
        except Exception:  # noqa: BLE001
            pass
        self._process.join(timeout=20)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)

    def __enter__(self) -> "RLApiServerProcess":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
