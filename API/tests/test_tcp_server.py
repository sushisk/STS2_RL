from __future__ import annotations

import asyncio
import json
import threading
import unittest

from API.tcp_server import AsyncioTcpServer


class AsyncioTcpServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[dict] = []

        def handler(payload: dict) -> dict:
            self.requests.append(payload)
            if payload.get("operation") == "raise":
                raise RuntimeError("boom")
            return {"echo": payload}

        self.server = AsyncioTcpServer(handler, server_epoch="epoch-test", port=0)
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        hello = await self._round_trip(self._hello("session-a"))
        self.assertEqual(hello["server_epoch"], "epoch-test")

    @staticmethod
    def _hello(session_id: str, schema_version: str = "0.6") -> dict:
        return {
            "transport_operation": "hello",
            "schema_version": schema_version,
            "client_session_id": session_id,
        }

    async def asyncTearDown(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        await self.server.close()

    async def _round_trip(self, payload: dict) -> dict:
        self.writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        await self.writer.drain()
        return json.loads(await self.reader.readline())

    async def test_hello_returns_schema_and_server_epoch_without_dispatch(self) -> None:
        response = await self._round_trip(self._hello("session-a"))
        self.assertEqual(
            response,
            {
                "transport_operation": "hello",
                "schema_version": "0.6",
                "client_session_id": "session-a",
                "server_epoch": "epoch-test",
            },
        )
        self.assertEqual(self.requests, [])

    async def test_hello_rejects_unsupported_schema_before_api_traffic(self) -> None:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        try:
            writer.write(json.dumps(self._hello("session-old", "0.5")).encode() + b"\n")
            await writer.drain()
            response = json.loads(await reader.readline())
            self.assertEqual(response["transport_error"], "unsupported_schema")
            self.assertEqual(response["supported_schema_version"], "0.6")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_ping_does_not_enter_api_dispatcher(self) -> None:
        response = await self._round_trip({"transport_operation": "ping"})
        self.assertEqual(
            response,
            {"transport_operation": "pong", "server_epoch": "epoch-test"},
        )
        self.assertEqual(self.requests, [])

    async def test_ping_namespace_requires_exact_transport_message(self) -> None:
        request = {
            "schema_version": "0.6",
            "client_session_id": "session-a",
            "request_seq": 1,
            "request_id": "session-a:1",
            "operation": "get_decision",
            "instance_id": "inst-1",
            "branch_id": "root",
            "transport_operation": "ping",
        }
        response = await self._round_trip(request)
        self.assertEqual(response, {"echo": request})
        self.assertEqual(self.requests, [request])

    async def test_handler_error_uses_api_fault_response(self) -> None:
        request = {
            "schema_version": "0.6",
            "client_session_id": "session-a",
            "request_seq": 1,
            "request_id": "session-a:1",
            "operation": "raise",
            "instance_id": "inst-1",
        }
        response = await self._round_trip(request)
        self.assertEqual(response["status"], "faulted")
        self.assertEqual(response["fault_kind"], "emulator_error")
        self.assertEqual(response["instance_id"], "inst-1")
        self.assertIn("RuntimeError: boom", response["error"])

    async def test_partial_frame_at_eof_is_not_dispatched(self) -> None:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        try:
            writer.write(
                json.dumps(
                    {
                        "schema_version": "0.6",
                        "client_session_id": "session-a",
                        "request_seq": 1,
                        "request_id": "session-a:1",
                        "operation": "start_instance",
                    }
                ).encode("utf-8")
            )
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await asyncio.wait_for(reader.read(), timeout=1.0), b"")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def test_api_before_hello_is_rejected(self) -> None:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        try:
            writer.write(
                json.dumps(
                    {
                        "schema_version": "0.6",
                        "client_session_id": "session-b",
                        "request_seq": 1,
                        "request_id": "session-b:1",
                        "operation": "start_instance",
                    }
                ).encode("utf-8")
                + b"\n"
            )
            await writer.drain()
            response = json.loads(await reader.readline())
            self.assertEqual(response["transport_error"], "hello_required")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_ping_remains_responsive_while_api_handler_runs(self) -> None:
        started = threading.Event()
        release = threading.Event()
        fallback_fired = threading.Event()

        def blocking_handler(payload: dict) -> dict:
            if payload.get("operation") == "block":
                started.set()
                release.wait(timeout=2.0)
            return {"echo": payload}

        server = AsyncioTcpServer(
            blocking_handler, server_epoch="epoch-block", port=0
        )
        await server.start()
        first_reader, first_writer = await asyncio.open_connection(
            "127.0.0.1", server.bound_port
        )
        second_reader, second_writer = await asyncio.open_connection(
            "127.0.0.1", server.bound_port
        )
        timer = threading.Timer(1.0, lambda: (fallback_fired.set(), release.set()))
        timer.start()
        try:
            first_writer.write(json.dumps(self._hello("session-block")).encode() + b"\n")
            await first_writer.drain()
            await first_reader.readline()

            first_writer.write(
                json.dumps(
                    {
                        "client_session_id": "session-block",
                        "operation": "block",
                    }
                ).encode("utf-8")
                + b"\n"
            )
            await first_writer.drain()
            self.assertTrue(await asyncio.to_thread(started.wait, 0.5))

            second_writer.write(b'{"transport_operation":"ping"}\n')
            await second_writer.drain()
            pong = json.loads(
                await asyncio.wait_for(second_reader.readline(), timeout=0.5)
            )
            self.assertEqual(
                pong,
                {"transport_operation": "pong", "server_epoch": "epoch-block"},
            )
            self.assertFalse(fallback_fired.is_set())

            release.set()
            response = json.loads(
                await asyncio.wait_for(first_reader.readline(), timeout=1.0)
            )
            self.assertEqual(
                response,
                {
                    "echo": {
                        "client_session_id": "session-block",
                        "operation": "block",
                    }
                },
            )
        finally:
            release.set()
            timer.cancel()
            for writer in (first_writer, second_writer):
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            await server.close()


if __name__ == "__main__":
    unittest.main()
