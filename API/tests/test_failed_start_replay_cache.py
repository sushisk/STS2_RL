from __future__ import annotations

import unittest

from API.server import RLApiServer


class FailedStartReplayCacheTest(unittest.TestCase):
    @staticmethod
    def _request(request_id: str) -> dict:
        return {
            "schema_version": "0.5",
            "request_id": request_id,
            "operation": "start_instance",
            "instance_config": {"instance_type": "invalid"},
        }

    def test_failed_start_replays_without_reexecuting_active_start_path(self) -> None:
        server = RLApiServer(replay_cache_entries=2)
        request = self._request("failed-1")

        first = server.handle_request(request)
        replay = server.handle_request(request)

        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(len(server._pre_instance_ledger), 0)
        self.assertEqual(len(server._failed_start_ledger), 1)

    def test_failed_start_replay_cache_is_bounded(self) -> None:
        server = RLApiServer(replay_cache_entries=2)

        for index in range(3):
            response = server.handle_request(self._request(f"failed-{index}"))
            self.assertEqual(response["status"], "rejected")

        self.assertEqual(len(server._pre_instance_ledger), 0)
        self.assertEqual(len(server._failed_start_ledger), 2)
        self.assertIsNone(server._failed_start_ledger.replay(self._request("failed-0")))
        self.assertEqual(
            server._failed_start_ledger.replay(self._request("failed-1"))["status"],
            "rejected",
        )
        self.assertEqual(
            server._failed_start_ledger.replay(self._request("failed-2"))["status"],
            "rejected",
        )


if __name__ == "__main__":
    unittest.main()
