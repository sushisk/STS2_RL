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
    @staticmethod
    def _request(seq: int, operation: str, **fields) -> dict:
        return {
            "schema_version": "0.6",
            "client_session_id": "session-a",
            "request_seq": seq,
            "request_id": f"session-a:{seq}",
            "operation": operation,
            **fields,
        }

    def test_faulted_close_quarantines_instance_and_replays_terminal_fault(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _FaultingCloseCombatInstance
        with patch.dict(sys.modules, {"API.instance_combat": fake_module}):
            server = RLApiServer()
            start = server.handle_request(
                self._request(
                    1,
                    "start_instance",
                    instance_config={"instance_type": "combat"},
                )
            )
            instance_id = start["instance_id"]
            instance = server._instances[instance_id]
            close_request = self._request(2, "close_instance", instance_id=instance_id)

            first = server.handle_request(close_request)
            replay = server.handle_request(close_request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(instance.close_calls, 1)
        self.assertEqual(server.instance_count(), 0)
        self.assertIsNone(server._sessions["session-a"].active_instance_id)

        stale_request = self._request(
            3,
            "get_decision",
            instance_id=instance_id,
            branch_id="root",
        )
        rejected = server.handle_request(stale_request)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["fault_kind"], "session_instance_conflict")


if __name__ == "__main__":
    unittest.main()
