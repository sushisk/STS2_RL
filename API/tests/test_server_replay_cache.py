from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from API.identifiers import RequestLedger
from API.server import RLApiServer
from API.validation import RequestRejected


class _FakeCombatInstance:
    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        self.instance_id = instance_id
        self.closed = False
        self.commit_calls = 0

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

    def commit_action(self, decision_point_id: str, action_id: str) -> dict:
        self.commit_calls += 1
        raise RuntimeError("commit failed after mutation")

    def close(self) -> None:
        self.closed = True


class _FaultingStartCombatInstance(_FakeCombatInstance):
    creations = 0

    def __init__(self, instance_id: str, instance_config: dict, **kwargs) -> None:
        super().__init__(instance_id, instance_config, **kwargs)
        type(self).creations += 1

    def start_instance_response(self) -> dict:
        raise RuntimeError("start response construction failed")


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

    @staticmethod
    def _commit_request(request_id: str, instance_id: str) -> dict:
        return {
            "schema_version": "0.5",
            "request_id": request_id,
            "operation": "commit_action",
            "instance_id": instance_id,
            "branch_id": "root",
            "rng_id": 0,
            "decision_point_id": "decision-1",
            "action_id": "action-1",
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

    def test_faulted_state_change_is_cached_and_not_reexecuted(self) -> None:
        server = RLApiServer(replay_cache_entries=4)
        start = server.handle_request(self._start_request("start-1"))
        instance_id = start["instance_id"]
        instance = server._instances[instance_id]
        commit_request = self._commit_request("commit-1", instance_id)

        first = server.handle_request(commit_request)
        replay = server.handle_request(commit_request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(instance.commit_calls, 1)

    def test_faulted_start_is_cached_and_does_not_create_second_instance(self) -> None:
        fake_module = types.ModuleType("API.instance_combat")
        _FaultingStartCombatInstance.creations = 0
        fake_module.CombatInstance = _FaultingStartCombatInstance
        start_request = self._start_request("start-fault")

        with patch.dict(sys.modules, {"API.instance_combat": fake_module}):
            server = RLApiServer(replay_cache_entries=4)
            first = server.handle_request(start_request)
            replay = server.handle_request(start_request)

        self.assertEqual(first["status"], "faulted")
        self.assertEqual(first, replay)
        self.assertEqual(_FaultingStartCombatInstance.creations, 1)
        self.assertEqual(server.instance_count(), 0)

    def test_request_ledger_rejects_matching_inflight_duplicate(self) -> None:
        ledger = RequestLedger()
        request = self._start_request("same-id")

        self.assertIsNone(ledger.begin(request))
        with self.assertRaisesRegex(RequestRejected, "already in flight"):
            ledger.begin(request)

    def test_unknown_instance_rejection_has_machine_readable_fault_kind(self) -> None:
        server = RLApiServer(replay_cache_entries=1)
        response = server.handle_request(self._close_request("close-1", "missing"))

        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["fault_kind"], "unknown_instance")


if __name__ == "__main__":
    unittest.main()
