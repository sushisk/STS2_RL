from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from API.server import RLApiServer


class _FaultingCloseCombatInstance:
    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        self.instance_id = instance_id
        self.close_calls = 0

    def start_instance_response(self) -> dict:
        return {"status": "completed", "instance_id": self.instance_id}

    def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("close failed after partial cleanup")


class CloseFaultQuarantineTest(unittest.TestCase):
    def test_faulted_close_quarantines_instance_and_replays_terminal_fault(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _FaultingCloseCombatInstance
        with patch.dict(sys.modules, {"API.instance_combat": fake_module}):
            server = RLApiServer(replay_cache_entries=4)
            start = server.handle_request(
                {
                    "schema_version": "0.5",
                    "request_id": "start-1",
                    "operation": "start_instance",
                    "instance_config": {"instance_type": "combat"},
                }
            )
            instance_id = start["instance_id"]
            instance = server._instances[instance_id]
            close_request = {
                "schema_version": "0.5",
                "request_id": "close-1",
                "operation": "close_instance",
                "instance_id": instance_id,
            }

            first = server.handle_request(close_request)
            replay = server.handle_request(close_request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(instance.close_calls, 1)
        self.assertEqual(server.instance_count(), 0)
        self.assertNotIn(instance_id, server._ledgers)
        self.assertIn(instance_id, server._closed_ledgers)

        stale_request = {
            "schema_version": "0.5",
            "request_id": "decision-after-close-fault",
            "operation": "get_decision",
            "instance_id": instance_id,
            "branch_id": "root",
        }
        rejected = server.handle_request(stale_request)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["fault_kind"], "unknown_instance")


if __name__ == "__main__":
    unittest.main()
