from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from API.server import RLApiServer


class _AdversarialCombatInstance:
    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        self.instance_id = instance_id
        self.start_status = instance_config.get("start_status", "completed")
        self.get_decision_calls = 0

    def start_instance_response(self) -> dict:
        return {
            "status": self.start_status,
            "instance_id": "spoofed-instance",
            "schema_version": "999",
            "server_epoch": "spoofed-epoch",
            "client_session_id": "other-session",
            "request_seq": 999,
            "request_id": "spoofed-request",
            "operation": "spoofed-operation",
        }

    def get_decision(self, branch_id: str):
        self.get_decision_calls += 1
        mode = getattr(self, "mode", "spoof")
        if mode == "non-dict":
            return ["not", "a", "dict"]
        if mode == "invalid-status":
            return {"status": "made-up-status"}
        return {
            "status": "completed",
            "instance_id": "other-instance",
            "branch_id": branch_id,
            "decision_point_id": "decision-1",
            "masked_emulator_dto": {"legal_actions": []},
            "schema_version": "999",
            "server_epoch": "spoofed-epoch",
            "client_session_id": "other-session",
            "request_seq": 999,
            "request_id": "spoofed-request",
            "operation": "spoofed-operation",
        }

    def close(self) -> None:
        pass


class ServerResponseFinalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _AdversarialCombatInstance
        self.module_patch = patch.dict(sys.modules, {"API.instance_combat": fake_module})
        self.module_patch.start()

    def tearDown(self) -> None:
        self.module_patch.stop()

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

    def _start(self, server: RLApiServer) -> tuple[dict, _AdversarialCombatInstance]:
        response = server.handle_request(
            self._request(
                1,
                "start_instance",
                instance_config={"instance_type": "combat"},
            )
        )
        instance = server._instances[response["instance_id"]]
        return response, instance

    def test_server_owned_start_identity_cannot_be_spoofed(self) -> None:
        server = RLApiServer(server_epoch="epoch-real")
        response, _ = self._start(server)

        self.assertEqual(response["instance_id"], "inst-000001")
        self.assertEqual(response["schema_version"], "0.6")
        self.assertEqual(response["server_epoch"], "epoch-real")
        self.assertEqual(response["client_session_id"], "session-a")
        self.assertEqual(response["request_seq"], 1)
        self.assertEqual(response["request_id"], "session-a:1")
        self.assertEqual(response["operation"], "start_instance")
        self.assertEqual(server._sessions["session-a"].active_instance_id, "inst-000001")

    def test_failed_start_does_not_claim_session_ownership(self) -> None:
        server = RLApiServer()
        request = self._request(
            1,
            "start_instance",
            instance_config={"instance_type": "combat", "start_status": "running"},
        )

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertIsNone(server._sessions["session-a"].active_instance_id)
        self.assertEqual(server.instance_count(), 0)

    def test_instance_scoped_response_cannot_spoof_correlation_envelope(self) -> None:
        server = RLApiServer(server_epoch="epoch-real")
        start, _ = self._start(server)
        response = server.handle_request(
            self._request(
                2,
                "get_decision",
                instance_id=start["instance_id"],
                branch_id="root",
            )
        )

        self.assertEqual(response["instance_id"], start["instance_id"])
        self.assertEqual(response["schema_version"], "0.6")
        self.assertEqual(response["server_epoch"], "epoch-real")
        self.assertEqual(response["client_session_id"], "session-a")
        self.assertEqual(response["request_seq"], 2)
        self.assertEqual(response["request_id"], "session-a:2")
        self.assertEqual(response["operation"], "get_decision")

    def test_non_dict_handler_result_becomes_terminal_replayable_fault(self) -> None:
        server = RLApiServer()
        start, instance = self._start(server)
        instance.mode = "non-dict"
        request = self._request(
            2,
            "get_decision",
            instance_id=start["instance_id"],
            branch_id="root",
        )

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(instance.get_decision_calls, 1)

    def test_invalid_status_becomes_terminal_replayable_fault(self) -> None:
        server = RLApiServer()
        start, instance = self._start(server)
        instance.mode = "invalid-status"
        request = self._request(
            2,
            "get_decision",
            instance_id=start["instance_id"],
            branch_id="root",
        )

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(instance.get_decision_calls, 1)


if __name__ == "__main__":
    unittest.main()
