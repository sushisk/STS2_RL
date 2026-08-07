from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from unittest.mock import patch

from API.server import RLApiServer
from API.tcp_server import AsyncioTcpServer


class _FakeCombatInstance:
    created = 0

    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        type(self).created += 1
        self.instance_id = instance_id
        self.instance_config = dict(instance_config)
        self.closed = False

    def start_instance_response(self) -> dict:
        return {
            "status": "completed",
            "instance_id": self.instance_id,
        }

    def close(self) -> None:
        self.closed = True


class TcpDtoContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _FakeCombatInstance.created = 0
        self.dispatcher = RLApiServer()
        self.server = AsyncioTcpServer(self.dispatcher.handle_request, port=0)
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1",
            self.server.bound_port,
        )

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
            json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        await self.writer.drain()
        return json.loads(await self.reader.readline())

    async def test_valid_dto_is_correlated_across_tcp(self) -> None:
        request = {
            "schema_version": "0.5",
            "request_id": "req-001",
            "operation": "get_decision",
            "instance_id": "inst-missing",
            "branch_id": "root",
        }

        response = await self._round_trip(request)

        self.assertEqual(response["schema_version"], "0.5")
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["operation"], request["operation"])
        self.assertEqual(response["instance_id"], request["instance_id"])
        self.assertEqual(response["status"], "rejected")
        self.assertIn("unknown instance_id", response["error"])

    async def test_invalid_operation_fields_are_dto_rejections(self) -> None:
        request = {
            "schema_version": "0.5",
            "request_id": "req-002",
            "operation": "commit_action",
            "instance_id": "inst-001",
            "branch_id": "root",
            "rng_id": 1,
            "decision_point_id": "decision-001",
            "action_id": "action-001",
        }

        response = await self._round_trip(request)

        self.assertEqual(response["schema_version"], "0.5")
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["operation"], request["operation"])
        self.assertEqual(response["instance_id"], request["instance_id"])
        self.assertEqual(response["status"], "rejected")
        self.assertIn("commit_action.rng_id", response["error"])

    async def test_start_instance_dto_round_trips_over_tcp(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _FakeCombatInstance
        with patch.dict(sys.modules, {"API.instance_combat": fake_module}):
            request = {
                "schema_version": "0.5",
                "request_id": "req-start",
                "operation": "start_instance",
                "instance_config": {"instance_type": "combat"},
            }
            response = await self._round_trip(request)

        self.assertEqual(response["schema_version"], "0.5")
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["operation"], request["operation"])
        self.assertEqual(response["status"], "completed")
        self.assertTrue(response["instance_id"].startswith("inst-"))
        self.assertEqual(_FakeCombatInstance.created, 1)


if __name__ == "__main__":
    unittest.main()
