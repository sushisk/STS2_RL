"""Asyncio TCP entry point for the STS2_RL API v0.6."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from API.dto import SCHEMA_VERSION
from API.faults import fault_response

JsonObject = dict[str, Any]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_MESSAGE_BYTES = 1024 * 1024


class AsyncioTcpServer:
    """Serve UTF-8 NDJSON API requests while keeping emulator work off the event loop."""

    def __init__(
        self,
        handler: Callable[[JsonObject], JsonObject],
        *,
        server_epoch: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be positive")
        self._handler = handler
        self._server_epoch = server_epoch or str(uuid.uuid4())
        self._host = host
        self._port = port
        self._max_message_bytes = max_message_bytes
        self._handler_lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()

    @property
    def server_epoch(self) -> str:
        return self._server_epoch

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("server has not been started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("server is already started")
        self._server = await asyncio.start_server(
            self._client_connected,
            self._host,
            self._port,
            limit=self._max_message_bytes + 1,
        )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        current = asyncio.current_task()
        tasks = [task for task in self._client_tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _client_connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        hello_session_id: str | None = None
        try:
            while line := await self._read_line(reader, writer):
                try:
                    payload = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    await self._write_response(
                        writer, {"transport_error": "invalid_json", "error": str(exc)}
                    )
                    continue
                if not isinstance(payload, dict):
                    await self._write_response(
                        writer,
                        {
                            "transport_error": "invalid_message",
                            "error": "top-level JSON value must be an object",
                        },
                    )
                    continue

                if payload == {"transport_operation": "ping"}:
                    response: JsonObject = {
                        "transport_operation": "pong",
                        "server_epoch": self._server_epoch,
                    }
                elif payload.get("transport_operation") == "hello":
                    response, bound_session = self._handle_hello(
                        payload, hello_session_id
                    )
                    if bound_session is not None:
                        hello_session_id = bound_session
                else:
                    if hello_session_id is None:
                        await self._write_response(
                            writer,
                            {
                                "transport_error": "hello_required",
                                "error": "send transport hello before API traffic",
                            },
                        )
                        continue
                    if payload.get("client_session_id") != hello_session_id:
                        await self._write_response(
                            writer,
                            {
                                "transport_error": "session_mismatch",
                                "error": "API client_session_id differs from stream hello",
                            },
                        )
                        continue
                    try:
                        response = await self._call_handler(payload)
                    except Exception as exc:
                        response = fault_response(payload, exc)
                    if not isinstance(response, dict):
                        response = fault_response(
                            payload, TypeError("RL handler returned a non-dict response")
                        )
                await self._write_response(writer, response)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _handle_hello(
        self,
        payload: JsonObject,
        current_session_id: str | None,
    ) -> tuple[JsonObject, str | None]:
        required_keys = {"transport_operation", "schema_version", "client_session_id"}
        if set(payload) != required_keys:
            return (
                {
                    "transport_error": "invalid_hello",
                    "error": "hello must contain exactly transport_operation, schema_version, and client_session_id",
                },
                None,
            )
        session_id = payload.get("client_session_id")
        if not isinstance(session_id, str) or not session_id:
            return (
                {
                    "transport_error": "invalid_hello",
                    "error": "client_session_id must be a non-empty string",
                },
                None,
            )
        if payload.get("schema_version") != SCHEMA_VERSION:
            return (
                {
                    "transport_error": "unsupported_schema",
                    "requested_schema_version": payload.get("schema_version"),
                    "supported_schema_version": SCHEMA_VERSION,
                },
                None,
            )
        if current_session_id is not None and session_id != current_session_id:
            return (
                {
                    "transport_error": "session_mismatch",
                    "error": "one TCP stream may bind only one client_session_id",
                },
                None,
            )
        return (
            {
                "transport_operation": "hello",
                "schema_version": SCHEMA_VERSION,
                "server_epoch": self._server_epoch,
                "client_session_id": session_id,
            },
            session_id,
        )

    async def _call_handler(self, payload: JsonObject) -> JsonObject:
        async with self._handler_lock:
            handler_task = asyncio.create_task(asyncio.to_thread(self._handler, payload))
            try:
                return await asyncio.shield(handler_task)
            except asyncio.CancelledError:
                try:
                    await handler_task
                except Exception:
                    pass
                raise

    async def _read_line(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bytes:
        try:
            line = await reader.readline()
        except (ValueError, asyncio.LimitOverrunError):
            line = None
        if line == b"":
            return b""
        if line is not None and len(line) <= self._max_message_bytes:
            if not line.endswith(b"\n"):
                return b""
            return line
        await self._write_response(
            writer,
            {
                "transport_error": "message_too_large",
                "direction": "request",
                "max_message_bytes": self._max_message_bytes,
            },
        )
        return b""

    @staticmethod
    async def _write_response(writer: asyncio.StreamWriter, response: JsonObject) -> None:
        writer.write(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        await writer.drain()


async def run_rl_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    combat_worker_pool_backend: str | None = None,
    combat_worker_count: int | None = None,
    combat_max_branches: int | None = None,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for subdirectory in ("Combat", "Run"):
        path = str(repo_root / subdirectory)
        if path not in sys.path:
            sys.path.insert(0, path)
    root_text = str(repo_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    from API.server import RLApiServer

    dispatcher = RLApiServer(
        instance_factory_kwargs=_combat_instance_factory_kwargs(
            worker_pool_backend=combat_worker_pool_backend,
            worker_count=combat_worker_count,
            max_branches=combat_max_branches,
        )
    )
    server = AsyncioTcpServer(
        dispatcher.handle_request,
        server_epoch=dispatcher.server_epoch,
        host=host,
        port=port,
        max_message_bytes=max_message_bytes,
    )
    await server.start()
    print(
        f"STS2_RL asyncio TCP listening on {host}:{server.bound_port} epoch={dispatcher.server_epoch}",
        flush=True,
    )
    try:
        await server.serve_forever()
    finally:
        await server.close()
        dispatcher.close_all()


def _positive_int_from_env(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _combat_instance_factory_kwargs(
    *,
    worker_pool_backend: str | None = None,
    worker_count: int | None = None,
    max_branches: int | None = None,
) -> dict | None:
    backend = worker_pool_backend or os.environ.get("STS2_COMBAT_BRANCH_POOL")
    resolved_worker_count = worker_count
    backend_key = (backend or "").strip().lower()
    alc_selected = backend_key in {"alc", "assemblyloadcontext", "assembly_load_context", "isolated"}
    if resolved_worker_count is None and alc_selected:
        resolved_worker_count = _positive_int_from_env("STS2_COMBAT_ALC_WORKERS")
    resolved_max_branches = max_branches
    if resolved_max_branches is None:
        resolved_max_branches = _positive_int_from_env("STS2_COMBAT_MAX_BRANCHES")

    combat_kwargs: dict[str, Any] = {}
    if backend is not None and backend.strip():
        combat_kwargs["worker_pool_backend"] = backend
    if resolved_worker_count is not None:
        combat_kwargs["worker_count"] = resolved_worker_count
    if resolved_max_branches is not None:
        combat_kwargs["max_branches"] = resolved_max_branches
    if not combat_kwargs:
        return None
    return {"combat": combat_kwargs}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--max-message-bytes", type=int, default=DEFAULT_MAX_MESSAGE_BYTES)
    parser.add_argument(
        "--combat-worker-pool-backend",
        default=None,
        help="Combat branch pool backend; defaults to STS2_COMBAT_BRANCH_POOL or multiprocessing.",
    )
    parser.add_argument(
        "--combat-worker-count",
        type=int,
        default=None,
        help="Combat branch worker_count; defaults to STS2_COMBAT_ALC_WORKERS for ALC or backend default.",
    )
    parser.add_argument(
        "--combat-max-branches",
        type=int,
        default=None,
        help="Combat max_branches rejection cap; defaults to STS2_COMBAT_MAX_BRANCHES or 64.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        asyncio.run(
            run_rl_server(
                host=args.host,
                port=args.port,
                max_message_bytes=args.max_message_bytes,
                combat_worker_pool_backend=args.combat_worker_pool_backend,
                combat_worker_count=args.combat_worker_count,
                combat_max_branches=args.combat_max_branches,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
