"""Asyncio TCP entry point for the STS2_RL API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from API.faults import fault_response

JsonObject = dict[str, Any]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_MESSAGE_BYTES = 1024 * 1024


class AsyncioTcpServer:
    """Serve newline-delimited JSON requests to a synchronous handler."""

    def __init__(
        self,
        handler: Callable[[JsonObject], JsonObject],
        *,
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
        self._host = host
        self._port = port
        self._max_message_bytes = max_message_bytes
        self._server: asyncio.AbstractServer | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()

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

    def _client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while line := await self._read_line(reader, writer):
                try:
                    payload = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    await self._write_response(
                        writer,
                        {"transport_error": "invalid_json", "error": str(exc)},
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

                if payload.get("transport_operation") == "ping":
                    response: JsonObject = {"transport_operation": "pong"}
                else:
                    try:
                        response = self._handler(payload)
                    except Exception as exc:
                        response = fault_response(payload, exc)
                    if not isinstance(response, dict):
                        response = fault_response(
                            payload,
                            TypeError("RL handler returned a non-dict response"),
                        )

                await self._write_response(writer, response)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _read_line(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bytes:
        try:
            line = await reader.readline()
        except (ValueError, asyncio.LimitOverrunError):
            line = None
        if line is not None and len(line) <= self._max_message_bytes:
            return line
        await self._write_response(
            writer,
            {
                "transport_error": "message_too_large",
                "max_message_bytes": self._max_message_bytes,
            },
        )
        return b""

    @staticmethod
    async def _write_response(
        writer: asyncio.StreamWriter,
        response: JsonObject,
    ) -> None:
        writer.write(
            json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        await writer.drain()


async def run_rl_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
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

    dispatcher = RLApiServer()
    server = AsyncioTcpServer(
        dispatcher.handle_request,
        host=host,
        port=port,
        max_message_bytes=max_message_bytes,
    )
    await server.start()
    print(f"STS2_RL asyncio TCP listening on {host}:{server.bound_port}", flush=True)
    try:
        await server.serve_forever()
    finally:
        await server.close()
        dispatcher.close_all()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--max-message-bytes",
        type=int,
        default=DEFAULT_MAX_MESSAGE_BYTES,
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
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
