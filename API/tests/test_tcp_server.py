from __future__ import annotations

import asyncio
import json
import threading
import unittest

from API.tcp_server import AsyncioTcpServer


class AsyncioTcpServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[dict] = []
        self.slow_started = threading.Event()
        self.release_slow = threading.Event()

        def handler(payload: dict) -> dict:
            self.requests.append(payload)
            if payload.get("operation") == "raise":
                raise RuntimeError("boom")
            if payload.get("operation") == "slow":
                self.slow_started.set()
                self.release_slow.wait(timeout=2.0)
            return {"echo": payload}

        self.server = AsyncioTcpServer(handler, port=0)
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )

    async def asyncTearDown(self) -> None:
        self.release_slow.set()
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

    async def test_ping_does_not_enter_api_dispatcher(self) -> None:
        response = await self._round_trip({"transport_operation": "ping"})
        self.assertEqual(response, {"transport_operation": "pong"})
        self.assertEqual(self.requests, [])

    async def test_ping_namespace_requires_exact_transport_message(self) -> None:
        request = {
            "schema_version": "0.5",
            "request_id": "req-mixed",
            "operation": "get_decision",
            "instance_id": "inst-1",
            "branch_id": "root",
            "transport_operation": "ping",
        }
        response = await self._round_trip(request)
        self.assertEqual(response, {"echo": request})
        self.assertEqual(self.requests, [request])

    async def test_api_request_is_dispatched_unchanged(self) -> None:
        request = {
            "schema_version": "0.5",
            "request_id": "req-1",
            "operation": "get_decision",
            "instance_id": "inst-1",
            "branch_id": "root",
        }
        response = await self._round_trip(request)
        self.assertEqual(response, {"echo": request})
        self.assertEqual(self.requests, [request])

    async def test_handler_error_uses_api_fault_response(self) -> None:
        request = {
            "schema_version": "0.5",
            "request_id": "req-2",
            "operation": "raise",
            "instance_id": "inst-1",
        }
        response = await self._round_trip(request)
        self.assertEqual(response["status"], "faulted")
        self.assertEqual(response["fault_kind"], "emulator_error")
        self.assertEqual(response["instance_id"], "inst-1")
        self.assertIn("RuntimeError: boom", response["error"])

    async def test_slow_api_handler_does_not_block_ping_on_other_connection(self) -> None:
        slow_request = {
            "schema_version": "0.5",
            "request_id": "req-slow",
            "operation": "slow",
            "instance_id": "inst-slow",
        }
        slow_task = asyncio.create_task(self._round_trip(slow_request))
        started = await asyncio.to_thread(self.slow_started.wait, 1.0)
        self.assertTrue(started)

        reader2, writer2 = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        try:
            writer2.write(b'{"transport_operation":"ping"}\n')
            await writer2.drain()
            pong = json.loads(await asyncio.wait_for(reader2.readline(), timeout=0.5))
            self.assertEqual(pong, {"transport_operation": "pong"})
        finally:
            self.release_slow.set()
            writer2.close()
            try:
                await writer2.wait_closed()
            except (ConnectionError, OSError):
                pass

        self.assertEqual((await slow_task)["echo"], slow_request)

    async def test_response_frame_limit_returns_transport_error(self) -> None:
        limited = AsyncioTcpServer(
            lambda payload: {"payload": "x" * 1024},
            port=0,
            max_message_bytes=256,
        )
        await limited.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", limited.bound_port)
        try:
            writer.write(b'{"request_id":"small"}\n')
            await writer.drain()
            line = await reader.readline()
            self.assertLessEqual(len(line), 256)
            response = json.loads(line)
            self.assertEqual(response["transport_error"], "message_too_large")
            self.assertEqual(response["direction"], "response")
            self.assertEqual(response["max_message_bytes"], 256)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            await limited.close()


if __name__ == "__main__":
    unittest.main()
