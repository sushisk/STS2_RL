from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from unittest.mock import patch

from API.dto import SCHEMA_VERSION
from API.server import RLApiServer
from API.tcp_server import AsyncioTcpServer


class _FakeCombatInstance:
    created = 0

    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        type(self).created += 1
        self.instance_id = instance_id
        self.closed = False

    def start_instance_response(self) -> dict:
        return {"status": "completed", "instance_id": self.instance_id}

    def close(self) -> None:
        self.closed = True


class TcpDtoContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _FakeCombatInstance.created = 0
        self.dispatcher = RLApiServer(server_epoch="epoch-contract")
        self.server = AsyncioTcpServer(
            self.dispatcher.handle_request,
            server_epoch=self.dispatcher.server_epoch,
            port=0,
        )
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        hello = await self._round_trip(self._hello())
        self.assertEqual(hello["server_epoch"], "epoch-contract")
        self.assertEqual(hello["schema_version"], SCHEMA_VERSION)

    @staticmethod
    def _hello() -> dict:
        return {
            "transport_operation": "hello",
            "schema_version": SCHEMA_VERSION,
            "client_session_id": "session-a",
        }

    async def asyncTearDown(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        await self.server.close()
        self.dispatcher.close_all()

    async def _round_trip(self, request: dict) -> dict:
        self.writer.write(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        await self.writer.drain()
        return json.loads(await self.reader.readline())

    @staticmethod
    def _request(seq: int, operation: str, **fields) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "client_session_id": "session-a",
            "request_seq": seq,
            "request_id": f"session-a:{seq}",
            "operation": operation,
            **fields,
        }

    async def test_rejected_dto_is_correlated_across_tcp(self) -> None:
        request = self._request(
            1,
            "start_instance",
            instance_config={"instance_type": "invalid"},
        )
        response = await self._round_trip(request)

        self.assertEqual(response["schema_version"], SCHEMA_VERSION)
        self.assertEqual(response["server_epoch"], "epoch-contract")
        self.assertEqual(response["client_session_id"], request["client_session_id"])
        self.assertEqual(response["request_seq"], request["request_seq"])
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["operation"], request["operation"])
        self.assertEqual(response["status"], "rejected")

    async def test_start_instance_same_sequence_replays_over_fresh_tcp_stream(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _FakeCombatInstance
        request = self._request(
            1,
            "start_instance",
            instance_config={"instance_type": "combat"},
        )
        with patch.dict(sys.modules, {"API.instance_combat": fake_module}):
            first = await self._round_trip(request)

            self.writer.close()
            await self.writer.wait_closed()
            self.reader, self.writer = await asyncio.open_connection(
                "127.0.0.1", self.server.bound_port
            )
            await self._round_trip(self._hello())
            replay = await self._round_trip(request)

        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(_FakeCombatInstance.created, 1)

    async def test_api_before_hello_is_rejected_at_transport_layer(self) -> None:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.server.bound_port
        )
        try:
            request = self._request(
                1,
                "start_instance",
                instance_config={"instance_type": "combat"},
            )
            writer.write(json.dumps(request).encode("utf-8") + b"\n")
            await writer.drain()
            response = json.loads(await reader.readline())
            self.assertEqual(response["transport_error"], "hello_required")
        finally:
            writer.close()
            await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
