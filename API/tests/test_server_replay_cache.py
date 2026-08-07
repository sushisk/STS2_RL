from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from API.server import RLApiServer


class _FakeCombatInstance:
    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        self.instance_id = instance_id
        self.closed = False

    def start_instance_response(self) -> dict:
        return {
            "status": "completed",
            "instance_id": self.instance_id,
        }

    def get_decision(self, branch_id: str) -> dict:
        return {
            "status": "completed",
            "branch_id": branch_id,
            "decision_point_id": "decision-1",
            "masked_emulator_dto": {"payload": "x" * 4096},
        }

    def close(self) -> None:
        self.closed = True


class ServerReplayCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _FakeCombatInstance
        self.module_patch = patch.dict(
            sys.modules,
            {"API.instance_combat": fake_module},
        )
        self.module_patch.start()

    def tearDown(self) -> None:
        self.module_patch.stop()

    @staticmethod
    def _start_request(request_id: str) -> dict:
        return {
            "schema_version": "0.5",
            "request_id": request_id,
            "operation": "start_instance",
            "instance_config": {"instance_type": "combat"},
        }

    @staticmethod
    def _close_request(request_id: str, instance_id: str) -> dict:
        return {
            "schema_version": "0.5",
            "request_id": request_id,
            "operation": "close_instance",
            "instance_id": instance_id,
        }

    def test_close_retry_replays_after_instance_is_removed(self) -> None:
        server = RLApiServer(replay_cache_entries=4)
        start = server.handle_request(self._start_request("start-1"))
        instance_id = start["instance_id"]
        close_request = self._close_request("close-1", instance_id)

        first = server.handle_request(close_request)
        replay = server.handle_request(close_request)

        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(server.instance_count(), 0)

    def test_closed_instance_retains_only_close_tombstone(self) -> None:
        server = RLApiServer(replay_cache_entries=4)
        start = server.handle_request(self._start_request("start-1"))
        instance_id = start["instance_id"]

        decision_request = {
            "schema_version": "0.5",
            "request_id": "decision-1",
            "operation": "get_decision",
            "instance_id": instance_id,
            "branch_id": "root",
        }
        decision = server.handle_request(decision_request)
        self.assertEqual(decision["status"], "completed")

        close_request = self._close_request("close-1", instance_id)
        server.handle_request(close_request)

        tombstone = server._closed_ledgers[instance_id]
        self.assertEqual(len(tombstone), 1)
        self.assertEqual(tombstone.replay(close_request)["status"], "completed")
        self.assertIsNone(tombstone.replay(decision_request))

    def test_closed_tombstones_are_bounded(self) -> None:
        server = RLApiServer(replay_cache_entries=2)
        closed_ids: list[str] = []
        for index in range(3):
            start = server.handle_request(self._start_request(f"start-{index}"))
            instance_id = start["instance_id"]
            closed_ids.append(instance_id)
            server.handle_request(
                self._close_request(f"close-{index}", instance_id)
            )

        self.assertEqual(len(server._closed_ledgers), 2)
        self.assertNotIn(closed_ids[0], server._closed_ledgers)
        self.assertIn(closed_ids[1], server._closed_ledgers)
        self.assertIn(closed_ids[2], server._closed_ledgers)

    def test_start_replay_entry_is_released_when_instance_closes(self) -> None:
        server = RLApiServer(replay_cache_entries=2)
        start_request = self._start_request("start-1")
        first = server.handle_request(start_request)
        replay = server.handle_request(start_request)

        self.assertEqual(first, replay)
        self.assertEqual(len(server._pre_instance_ledger), 1)

        server.handle_request(
            self._close_request("close-1", first["instance_id"])
        )

        self.assertEqual(len(server._pre_instance_ledger), 0)
        self.assertNotIn(first["instance_id"], server._start_request_ids_by_instance)


if __name__ == "__main__":
    unittest.main()
