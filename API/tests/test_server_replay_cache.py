from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from API.identifiers import SessionLedger
from API.server import RLApiServer
from API.validation import RequestRejected


class _FakeCombatInstance:
    creations = 0

    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        type(self).creations += 1
        self.instance_id = instance_id
        self.closed = False
        self.commit_calls = 0

    def start_instance_response(self) -> dict:
        return {"status": "completed", "instance_id": self.instance_id}

    def get_decision(self, branch_id: str) -> dict:
        return {
            "status": "completed",
            "branch_id": branch_id,
            "decision_point_id": "decision-1",
            "masked_emulator_dto": {"ok": True},
        }

    def commit_action(self, decision_point_id: str, action_id: str) -> dict:
        self.commit_calls += 1
        raise RuntimeError("commit failed after mutation")

    def close(self) -> None:
        self.closed = True


class _FaultingStartCombatInstance(_FakeCombatInstance):
    def start_instance_response(self) -> dict:
        raise RuntimeError("start response construction failed")


class SessionSequencingTest(unittest.TestCase):
    def setUp(self) -> None:
        _FakeCombatInstance.creations = 0
        fake_module = types.ModuleType("API.instance_combat")
        fake_module.CombatInstance = _FakeCombatInstance
        self.module_patch = patch.dict(sys.modules, {"API.instance_combat": fake_module})
        self.module_patch.start()

    def tearDown(self) -> None:
        self.module_patch.stop()

    @staticmethod
    def _request(session: str, seq: int, operation: str, **fields) -> dict:
        return {
            "schema_version": "0.6",
            "client_session_id": session,
            "request_seq": seq,
            "request_id": f"{session}:{seq}",
            "operation": operation,
            **fields,
        }

    def _start(self, server: RLApiServer, session: str = "session-a") -> dict:
        return server.handle_request(
            self._request(
                session,
                1,
                "start_instance",
                instance_config={"instance_type": "combat"},
            )
        )

    def test_exact_same_sequence_replays_without_second_execution(self) -> None:
        server = RLApiServer(server_epoch="epoch-1")
        request = self._request(
            "session-a",
            1,
            "start_instance",
            instance_config={"instance_type": "combat"},
        )

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first, replay)
        self.assertEqual(first["server_epoch"], "epoch-1")
        self.assertEqual(_FakeCombatInstance.creations, 1)
        self.assertEqual(server.session_count(), 1)

    def test_same_sequence_with_different_payload_is_rejected(self) -> None:
        server = RLApiServer()
        self._start(server)
        conflict = self._request(
            "session-a",
            1,
            "start_instance",
            instance_config={"instance_type": "whole_run"},
        )

        response = server.handle_request(conflict)

        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["fault_kind"], "session_sequence_conflict")
        self.assertEqual(_FakeCombatInstance.creations, 1)

    def test_sequence_gap_fails_closed(self) -> None:
        server = RLApiServer()
        start = self._start(server)
        response = server.handle_request(
            self._request(
                "session-a",
                3,
                "get_decision",
                instance_id=start["instance_id"],
                branch_id="root",
            )
        )
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["fault_kind"], "session_sequence_gap")

    def test_faulted_state_change_is_terminal_and_replayed(self) -> None:
        server = RLApiServer()
        start = self._start(server)
        instance = server._instances[start["instance_id"]]
        request = self._request(
            "session-a",
            2,
            "commit_action",
            instance_id=start["instance_id"],
            branch_id="root",
            rng_id=0,
            decision_point_id="decision-1",
            action_id="action-1",
        )

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(instance.commit_calls, 1)

    def test_close_response_replays_after_instance_removed(self) -> None:
        server = RLApiServer()
        start = self._start(server)
        request = self._request(
            "session-a", 2, "close_instance", instance_id=start["instance_id"]
        )

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first["status"], "completed")
        self.assertEqual(first, replay)
        self.assertEqual(server.instance_count(), 0)

    def test_older_sequence_is_never_reexecuted_after_progress(self) -> None:
        server = RLApiServer()
        start_request = self._request(
            "session-a",
            1,
            "start_instance",
            instance_config={"instance_type": "combat"},
        )
        start = server.handle_request(start_request)
        decision = server.handle_request(
            self._request(
                "session-a",
                2,
                "get_decision",
                instance_id=start["instance_id"],
                branch_id="root",
            )
        )
        self.assertEqual(decision["status"], "completed")

        old = server.handle_request(start_request)
        self.assertEqual(old["status"], "rejected")
        self.assertEqual(old["fault_kind"], "session_sequence_gap")
        self.assertEqual(_FakeCombatInstance.creations, 1)

    def test_instance_is_owned_by_client_session(self) -> None:
        server = RLApiServer()
        start = self._start(server, "session-a")
        response = server.handle_request(
            self._request(
                "session-b",
                1,
                "get_decision",
                instance_id=start["instance_id"],
                branch_id="root",
            )
        )
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["fault_kind"], "session_instance_conflict")

    def test_session_capacity_does_not_evict_existing_sessions(self) -> None:
        server = RLApiServer(max_sessions=1)
        self._start(server, "session-a")
        response = server.handle_request(
            self._request(
                "session-b",
                1,
                "start_instance",
                instance_config={"instance_type": "combat"},
            )
        )
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["fault_kind"], "session_capacity_exceeded")
        self.assertEqual(server.session_count(), 1)

    def test_faulted_start_replays_without_second_instance_creation(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        _FaultingStartCombatInstance.creations = 0
        fake_module.CombatInstance = _FaultingStartCombatInstance
        request = self._request(
            "session-a",
            1,
            "start_instance",
            instance_config={"instance_type": "combat"},
        )
        with patch.dict(sys.modules, {"API.instance_combat": fake_module}):
            server = RLApiServer()
            first = server.handle_request(request)
            replay = server.handle_request(request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(_FaultingStartCombatInstance.creations, 1)
        self.assertEqual(server.instance_count(), 0)

    def test_session_ledger_rejects_matching_inflight_duplicate(self) -> None:
        ledger = SessionLedger()
        request = self._request(
            "session-a",
            1,
            "start_instance",
            instance_config={"instance_type": "combat"},
        )
        self.assertIsNone(ledger.begin(request))
        with self.assertRaisesRegex(RequestRejected, "already in flight"):
            ledger.begin(request)


if __name__ == "__main__":
    unittest.main()
