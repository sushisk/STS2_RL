from __future__ import annotations

import asyncio
import json
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

        self.server = AsyncioTcpServer(handler, port=0)
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )

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


if __name__ == "__main__":
    unittest.main()
